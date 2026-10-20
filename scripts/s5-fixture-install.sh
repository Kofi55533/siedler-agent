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
[ -f "$dst/scenario.json" ] && { echo "$x: installed (already present)"; exit 0; }
fix(){ # $1 = source label; validates the installed dir
  sed -i -e "s#/workspace/siedler/artifacts/new-fixtures#$S5_NF#g" -e "s#/workspace/siedler/artifacts/codex-verify-infra#$S5_V#g" "$dst/scenario.json"
  local img; img=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('image',''))" "$dst/scenario.json" 2>/dev/null)
  if [ -z "$img" ] || [ ! -f "$img" ]; then echo "$x: not installable (start image ${img##*/data/} missing on this runner; from $1)"; rm -rf "$dst"; return 1; fi
  echo "$x: installed from $1"; return 0; }
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
tags=$( { [ -n "$body" ] && [ -f "$body" ] && grep -oE 'fixtures-gen-[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]+' "$body" | sed 's/[-.]*$//'
          gh release list -R $R --limit 200 --json tagName -q '.[].tagName' 2>/dev/null | grep '^fixtures-gen-' |
            awk -v x="$x" '{t=$0; sub(/^fixtures-gen-[0-9-]{11}/,"",t); fam=x; sub(/-.*/,"",fam);
                           if (index(x,t)||index(t,x)||(fam~/^gen[bc]$/ && index(t,fam))) print $0}'; } | awk '!s[$0]++' | head -8)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
for t in $tags; do
  for a in $(gh release view "$t" -R $R --json assets -q '.assets[]|select(.size < 400000000)|.name' 2>/dev/null); do
    case "$a" in *.tar.gz|*.tgz|*.tar|*.tzst|*.tar.zst) ;; *) continue ;; esac
    rm -rf "$tmp/x"; mkdir -p "$tmp/x"
    gh release download "$t" -R $R -p "$a" -D "$tmp" --clobber >/dev/null 2>&1 || continue
    case "$a" in *.tzst|*.tar.zst) zstd -dcq "$tmp/$a" | tar -x -C "$tmp/x" 2>/dev/null ;; *) tar -xf "$tmp/$a" -C "$tmp/x" 2>/dev/null ;; esac
    rm -f "$tmp/$a"
    s=$(python3 - "$tmp/x" "$x" <<'PY'
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
    rm -rf "$dst"; cp -r "$s" "$dst" && fix "release $t/$a" && exit 0
  done
done
echo "$x: not installable (no fixtures-gen/$x in git and no matching private release asset)"; exit 1
