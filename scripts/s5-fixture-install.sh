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
# start images that are derived from shipped ones and therefore not in data-v2, built on demand:
#  - weather-time: the wsecon image with every player's weather energy set (docs/clean/new-fixtures/gen_weather_time.py)
#  - camp-crawford-sabotage: the camp-crawford start (the sabotage fixtures' start.jsonl equals camp-crawford's)
case "$x" in
  gen-weather-time*) [ -f "$S5_NF/images/weather-time/start.img" ] || ( cd "$S5_REPO" && python3 \
      docs/clean/new-fixtures/gen_weather_time.py --out "$S5_NF/.dl/weather-time-gen" >/dev/null 2>&1 ) \
      || echo "$x: weather-time image patch failed" ;;
  gen-sabotage-bomb*) cs=$S5_NF/images/camp-crawford-sabotage
    [ -f "$cs/start.img" ] || { mkdir -p "$cs" && ln -sf ../camp-crawford/start.img "$cs/start.img" \
      && ln -sf ../camp-crawford/start.img.state.json "$cs/start.img.state.json"; } ;;
esac
# 0. private map-start images captured on runners (scripts/s5-mapgen.sh): siedler-data release mapimg, asset <fixture>.tzst
#    = fixtures/<fixture>/{scenario.json,commands.jsonl} + images/<fixture>/start.img{,.state.json}
mkdir -p "$S5_NF/.dl"
if gh release download mapimg -R Kofi55533/siedler-data -p "$x.tzst" -D "$S5_NF/.dl" --clobber >/dev/null 2>&1 && [ -s "$S5_NF/.dl/$x.tzst" ]; then
  zstd -dcq "$S5_NF/.dl/$x.tzst" | tar -x -C "$S5_NF" 2>/dev/null && rm -f "$S5_NF/.dl/$x.tzst"
  [ -f "$dst/scenario.json" ] && fix "release mapimg" && exit 0
  rm -rf "$dst"
fi
mkdir -p "$S5_NF/.dl"
meta=$(mktemp -d "$S5_NF/.dl/metadata-$x.XXXXXX")
trap 'rm -rf "$meta"' EXIT
# 1. git
if [ -d "$S5_REPO/.git" ]; then
  cd "$S5_REPO"
  git fetch -q origin '+refs/heads/agent/fixtures-*:refs/remotes/origin/agent/fixtures-*' 2>/dev/null || true
  for ref in "${refs[@]}" origin/clean/integration $(git for-each-ref --format='%(refname:short)' 'refs/remotes/origin/agent/fixtures-*'); do
    # fixtures-gen/<x>: captured fixture; docs/clean/new-fixtures/scenarios/<x>: recipe only (scenario + commands on a
    # runner start image, e.g. the gen-a-* scenarios of agent/fixtures-gen-a), enough for s5-live.sh
    for sub in "fixtures-gen/$x" "docs/clean/new-fixtures/scenarios/$x"; do
      git cat-file -e "$ref:$sub/scenario.json" 2>/dev/null || continue
      rm -rf "$dst"; mkdir -p "$dst"
      if git archive "$ref" "$sub" | tar -x -C "$dst" --strip-components=$(( $(tr -cd / <<<"$sub" | wc -c) + 1 )); then
        # Keep the PR's metadata while looking for its missing image in the
        # release. Historical workers renamed numeric fixtures to campaign-*
        # to work around the parser; the image bundle retains its original name.
        [ -f "$meta/scenario.json" ] || cp -r "$dst/." "$meta/"
        fix "git $ref:$sub" && exit 0
      fi
    done
  done
fi
# 2. releases
R=Kofi55533/siedler-sim-rs
# candidate (tag asset) pairs: tags named in the PR body first, then releases whose tag or asset name matches the fixture
# (genb-*/genc-* fixtures live in family archives such as genc-longmixed.tar.gz), newest first
pairs=$( { [ -n "$body" ] && [ -f "$body" ] && grep -oE 'fixtures-gen-[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]+' "$body" | sed 's/[-.]*$//' | sed 's/$/ */'
           for pg in 1 2 3 4 5; do gh api "repos/$R/releases?per_page=100&page=$pg" 2>/dev/null; echo; done | python3 -c '
import json, sys, re
x = sys.argv[1]; fam = x.split("-")[0]
# all release pages (more than 100 releases: the older fixtures-gen-2026-10-1x ones are on page 2)
rels, txt, i, dec = [], sys.stdin.read(), 0, json.JSONDecoder()
while True:
    while i < len(txt) and txt[i].isspace():
        i += 1
    if i >= len(txt):
        break
    try:
        doc, i = dec.raw_decode(txt, i)
    except ValueError:
        break
    if isinstance(doc, list):
        rels += doc
for r in rels:
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
    if [ -f "$meta/scenario.json" ]; then
      rm -rf "$dst"; cp -r "$meta" "$dst"
      fix "git metadata + release $t/$a" "$ex" && exit 0
    fi
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
