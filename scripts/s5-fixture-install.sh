#!/usr/bin/env bash
# s5-fixture-install.sh <fixture> [pr-body.txt] [git-ref ...]
# Installs a generated fixture (gen-*, genb-*, genc-*, *-long, ...) into $S5_NF/fixtures/<fixture> so s5-live.sh can run it
# (gate: "Frontier-fixture: <name>" of a PR is measured on base AND PR instead of "not installed (skipped)").
# Sources, first hit wins:
#   1. git: fixtures-gen/<fixture>/ on the given refs (gate: the PR head), origin/clean/integration, origin/agent/fixtures-*
#   2. private release assets of Kofi55533/siedler-sim-rs: tags named in the PR body (fixtures-gen-YYYY-MM-DD-*), then
#      fixtures-gen-* releases whose tag/asset matches the fixture name (newest first); the archive dir whose
#      scenario.json has "name": <fixture> (or whose dir name is <fixture>) is installed.
# Box paths in scenario.json are mapped to the runner paths (as s5-setup.sh does); the start image must exist on the runner
# (data-v2 newfix), otherwise nothing is installed. Prints one line "<fixture>: installed from <source>" or
# "<fixture>: not installable (<reason>)"; exit 0 = installed. Never prints file contents.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/s5-env.sh"
x=$1; body=${2:-}; shift; [ $# -gt 0 ] && shift; refs=("$@")
[[ "$x" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "$x: not installable (bad name)"; exit 1; }
dst=$S5_NF/fixtures/$x
fix(){ # $1 = source label; validates the installed dir
  if ! python3 "$D/fixture_bundle.py" "$dst" "${2:-$dst}" "$S5_NF" "$S5_V" 2>/dev/null; then
    echo "$x: not installable (start image/state missing or ambiguous; from $1)"; rm -rf "$dst"; return 1; fi
  echo "$x: installed from $1"; return 0; }
[ ! -f "$dst/scenario.json" ] || { fix "already present" && exit 0; }
mkdir -p "$S5_NF/fixtures"
# 1. git
if [ -d "$S5_REPO/.git" ]; then
  cd "$S5_REPO"
  git fetch -q origin '+refs/heads/agent/fixtures-*:refs/remotes/origin/agent/fixtures-*' 2>/dev/null || true
  for ref in "${refs[@]}" origin/clean/integration $(git for-each-ref --format='%(refname:short)' 'refs/remotes/origin/agent/fixtures-*'); do
    git cat-file -e "$ref:fixtures-gen/$x/scenario.json" 2>/dev/null || continue
    rm -rf "$dst"; mkdir -p "$dst"
    git archive "$ref" "fixtures-gen/$x" | tar -x -C "$dst" --strip-components=2 && fix "git $ref:fixtures-gen/$x" && exit 0
  done
fi
# 2. releases
R=Kofi55533/siedler-sim-rs
# candidate (tag asset) pairs: tags named in the PR body first, then releases whose tag or asset name matches the fixture
# (genb-*/genc-* fixtures live in family archives such as genc-longmixed.tar.gz), newest first
pairs=$( { [ -n "$body" ] && [ -f "$body" ] && grep -oE 'fixtures-gen-[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]+' "$body" | sed 's/[-.]*$//' | sed 's/$/ */'
           gh api "repos/$R/releases?per_page=100" 2>/dev/null | python3 -c '
import json, sys, re
x = sys.argv[1]; fam = x.split("-")[0]
for r in json.load(sys.stdin):
    t = r["tag_name"]
    if not t.startswith("fixtures-gen-"):
        continue
    suf = re.sub(r"^fixtures-gen-\d{4}-\d{2}-\d{2}-", "", t)
    names = [a["name"] for a in r.get("assets", [])]
    if "scenario.json" in names and (suf in x or x in suf):  # loose fixture files as assets (no archive)
        print(t, "__loose__")
    for a in r.get("assets", []):
        n = a["name"]; base = re.sub(r"(-full)?\.(tar\.gz|tgz|tar|tzst|tar\.zst|tzst\.tar\.gz)$", "", n)
        if suf in x or x in suf or base in x or x.startswith(base) or (fam in ("genb", "genc") and n.startswith(fam)):
            print(t, n)' "$x"; } | awk '!s[$0]++' | head -10)
DL=$S5_NF/.dl; mkdir -p "$DL"   # downloaded archives are kept for the other fixtures of this runner (family archives)
while read -r t pat; do
  [ -n "$t" ] || continue
  if [ "$pat" = __loose__ ]; then
    lo=$DL/loose-$t; [ -f "$lo/scenario.json" ] || { rm -rf "$lo"; gh release download "$t" -R $R -D "$lo" --clobber >/dev/null 2>&1 || continue; }
    n=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('name',''))" "$lo/scenario.json" 2>/dev/null)
    [ "$n" = "$x" ] || continue
    rm -rf "$dst"; cp -r "$lo" "$dst" && fix "release $t (loose files)" "$lo" && exit 0
    continue
  fi
  for a in $(gh release view "$t" -R $R --json assets -q '.assets[]|select(.size < 400000000)|.name' 2>/dev/null); do
    [ "$pat" = "*" ] || [ "$a" = "$pat" ] || continue
    case "$a" in *.tar.gz|*.tgz|*.tar|*.tzst|*.tar.zst) ;; *) continue ;; esac
    [ -s "$DL/$a" ] || gh release download "$t" -R $R -p "$a" -D "$DL" --clobber >/dev/null 2>&1 || continue
    ex=$DL/x-${a%%.*}
    if [ ! -d "$ex" ]; then mkdir -p "$ex.tmp"
      case "$a" in *.tzst|*.tar.zst) zstd -dcq "$DL/$a" | tar -x -C "$ex.tmp" 2>/dev/null ;; *) tar -xf "$DL/$a" -C "$ex.tmp" 2>/dev/null ;; esac
      mv "$ex.tmp" "$ex"; fi
    s=$(python3 - "$ex" "$x" <<'PY'
import json, os, sys
root, x = sys.argv[1], sys.argv[2]
hits = []
for d, _, fs in os.walk(root):
    if "scenario.json" in fs:
        try: n = json.load(open(os.path.join(d, "scenario.json"))).get("name")
        except Exception: n = None
        if n == x or os.path.basename(d) == x: hits.append((n != x, len(d), d))
print(sorted(hits)[0][2] if hits else "")
PY
)
    [ -n "$s" ] || continue
    rm -rf "$dst"; cp -r "$s" "$dst" && fix "release $t/$a" "$ex" && exit 0
  done
done <<<"$pairs"
echo "$x: not installable (no fixtures-gen/$x in git and no matching private release asset)"; exit 1
