import re
import sys

s = sys.argv[1] if len(sys.argv) > 1 else ""
m = re.match(r"^(IDENTICAL \d+ ticks \([^)]+\))", s)
if m:
    print(m.group(1))
    raise SystemExit
m = re.match(r"^(DEEP-DIVERGENT \d+ ticks \(snapshot\+tasks\+rng IDENTICAL \d+, \d+ draws); (.+)\)$", s)
if m:
    tags = re.findall(r"(?:world|lua|spatial|ui_meta)@\d+", m.group(2))
    print(f"{m.group(1)}; cats={','.join(tags)})")
    raise SystemExit
m = re.match(r"^(DIVERGENT (?:entering )?turn \d+|DIVERGENT [^:]+)", s)
if m:
    print(m.group(1)[:80])
    raise SystemExit
print(s[:60])
