#!/usr/bin/env python3
"""Fresh lane claims on docs/AGENT_BOARD.md that hold a PR (Opus decision 2026-10-18, narrowed by Dominic 2026-10-20 10:22).

usage: lane-claims.py <board.md> <files.txt> [--owners Opus,Codex] [--hours 6] [--now 'YYYY-MM-DD HH:MM']
                      [--repo <git dir> --diff <pr.diff> --old-rev <merge-base> --new-rev <pr head> --pr-created <ISO time>]

Claim format (one line anywhere on the board, Berlin time; leading '- ' and backticks allowed):
    LANE-CLAIM <Owner> <YYYY-MM-DD HH:MM> <file>[::<function>] [<file>[::<function>] ...] [-- note]
A claim expires <hours> after its timestamp. Directory claims (path ending in '/') are ignored: lanes are file/function based.

Without --repo/--diff (old behaviour, also used by the planner): every fresh claim on a touched file holds the PR.
With them (the gate):
  * <file>::<item> holds the PR only if the PR's diff changes lines inside that item (fn/struct/enum/impl/trait/const/... of that
    name, old or new version). If the item cannot be located in either version the claim holds the whole file (safe side).
  * a plain <file> claim holds the PR, EXCEPT when the PR was opened before the claim was posted AND the owner has since pushed
    his work on that file (a commit on a <owner>/* branch after the claim time touching the file): then his work is not
    uncommitted any more and he rebases onto the merge. Before he pushes, the older PR stays held (never merge onto a file
    with possibly uncommitted owner work).
Heartbeat (Dominic 2026-10-20 11:14): with --heartbeat <json> a claim of an owner that publishes a heartbeat (Codex: the box
loop run_loop.sh uploads codex-heartbeat.json to release `heartbeat` of Kofi55533/siedler-agent) holds a PR only while that
owner's loop is ACTIVE: heartbeat state "active" and updated less than HB_MAX_AGE_MIN (default 25) minutes ago. A claim of an
idle, stopped or quota-blocked loop does not hold (printed to stderr as 'stale claim ignored'). Owners without a heartbeat entry
(Opus) keep the old rule. --heartbeat-unknown (heartbeat download failed) keeps the old rule for everybody (fail-safe: hold).
Prints one line per holding claim ('<owner> <time> <file>[::<item>] (age <h>h) <reason>'); exit 0 always.
"""
import json, os, re, subprocess, sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BER = ZoneInfo("Europe/Berlin")
RX = re.compile(r"^\s*(?:[-*]\s*)?`?LANE-CLAIM\s+([A-Za-z]+)\s+(\d{4}-\d{2}-\d{2})[ T](\d{1,2}:\d{2})(?:\s+Berlin)?\s+(.*)$")
PREFIX = {"Codex": "codex/", "Opus": "opus/"}


def parse_items(lines):
    """yields owner, ts, [(file, item or None)]"""
    for line in lines:
        m = RX.match(line.rstrip("\n"))
        if not m:
            continue
        owner, d, t, rest = m.groups()
        try:
            ts = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M").replace(tzinfo=BER)
        except ValueError:
            continue
        rest = rest.replace("`", " ")
        rest = re.split(r"\s--\s|\s--$|\s—\s", " " + rest + " ", maxsplit=1)[0]
        items = []
        for tok in rest.replace(",", " ").split():
            f, _, fn = tok.partition("::")
            f = f.strip(":;.()[]")
            fn = re.sub(r"[^A-Za-z0-9_].*$", "", fn.split("::")[-1]) if fn else ""
            if "/" in f and not f.endswith("/"):
                items.append((f.lstrip("./"), fn or None))
        if items:
            yield owner, ts, items


def parse(lines):  # file-level view (planner_state.py, old callers)
    for owner, ts, items in parse_items(lines):
        yield owner, ts, sorted({f for f, _ in items})


def git(repo, *a):
    r = subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


ITEM = r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:(?:async|const|unsafe|default|extern\s+\"[^\"]*\")\s+)*(?:fn|struct|enum|trait|type|const|static|mod|union|macro_rules!)\s+{n}\b"
IMPL = r"^\s*impl\b[^{{;]*\b{n}\b"


def item_ranges(text, name):
    """1-based inclusive line ranges of every Rust item called <name> (incl. `impl ... name`)."""
    if text is None:
        return []
    lines = text.split("\n")
    rx = re.compile(ITEM.format(n=re.escape(name)) + "|" + IMPL.format(n=re.escape(name)))
    out = []
    for i, l in enumerate(lines):
        if not rx.match(l):
            continue
        depth, opened, j = 0, False, i
        while j < len(lines):
            s = re.sub(r'"(?:\\.|[^"\\])*"', '""', lines[j].split("//", 1)[0])
            s = re.sub(r"'(?:\\.|[^'\\])'", "''", s)
            for ch in s:
                if ch == "{":
                    depth += 1; opened = True
                elif ch == "}":
                    depth -= 1
            if (opened and depth <= 0) or (not opened and s.rstrip().endswith(";")):
                break
            j += 1
        out.append((i + 1, j + 1))
    return out


def changed_lines(diff_text, path):
    """(old line numbers removed, new line numbers added/changed) of <path> in a unified diff; an insertion also
    marks the old line it sits after, so an added line inside an item counts on both sides."""
    old, new, cur = set(), set(), None
    o = n = 0
    for l in diff_text.split("\n"):
        if l.startswith("diff --git"):
            cur = None
        elif l.startswith("+++ "):
            cur = l[4:].strip()
            cur = cur[2:] if cur.startswith("b/") else cur
        elif l.startswith("@@") and cur == path:
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", l)
            o, n = int(m.group(1)), int(m.group(2))
        elif cur == path and l[:1] in "+- " and not l.startswith(("+++", "---")):
            if l[:1] == "+":
                new.add(n); old.add(max(o - 1, 1)); n += 1
            elif l[:1] == "-":
                old.add(o); new.add(max(n - 1, 1)); o += 1
            else:
                o += 1; n += 1
    return old, new


def load_heartbeats(path, now):
    """{owner: (live: bool, why)} from a heartbeat json ({"owner":..,"state":..,"updated_utc":..} or a list of them)."""
    try:
        d = json.load(open(path))
    except Exception:
        return None
    out = {}
    for h in (d if isinstance(d, list) else [d]):
        try:
            owner, state = h["owner"], h.get("state", "?")
            upd = datetime.fromisoformat(h["updated_utc"].replace("Z", "+00:00"))
        except Exception:
            continue
        mins = (now - upd).total_seconds() / 60
        live = state == "active" and mins < float(os.environ.get("HB_MAX_AGE_MIN", "25"))
        out[owner] = (live, f"heartbeat {state} {mins:.0f} min ago" + (f", blocked until {h['until']}" if h.get("until") else ""))
    for o in os.environ.get("HB_OWNERS", "Codex").split(","):  # owners whose loop publishes a heartbeat: none = not active
        out.setdefault(o, (False, "no heartbeat published"))
    return out


def main(a):
    board, flist = a[0], a[1]
    owners = {"Opus", "Codex"}; hours = 6.0; now = datetime.now(BER)
    repo = diff = old_rev = new_rev = created = None
    hb = None
    i = 2
    while i < len(a):
        k, v = a[i], (a[i + 1] if i + 1 < len(a) else None)
        if k == "--owners": owners = set(v.split(","))
        elif k == "--hours": hours = float(v)
        elif k == "--now": now = datetime.strptime(v, "%Y-%m-%d %H:%M").replace(tzinfo=BER)
        elif k == "--repo": repo = v
        elif k == "--diff": diff = open(v, encoding="utf-8", errors="replace").read()
        elif k == "--old-rev": old_rev = v
        elif k == "--new-rev": new_rev = v
        elif k == "--pr-created": created = datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None
        elif k == "--heartbeat": hb = v
        elif k == "--heartbeat-unknown": hb = None; i -= 1
        else: sys.exit(f"bad arg {k}")
        i += 2
    beats = load_heartbeats(hb, now) if hb else None
    touched = {l.strip() for l in open(flist) if l.strip()}
    win = timedelta(hours=hours)
    fine = bool(repo and diff is not None and old_rev and new_rev)
    for owner, ts, items in parse_items(open(board, encoding="utf-8", errors="replace")):
        if owner not in owners:
            continue
        age = now - ts
        if age >= win or age <= -win:  # a future timestamp counts as fresh unless absurd
            continue
        tag = f"{owner} {ts:%Y-%m-%d %H:%M}"
        if beats is not None and owner in beats and not beats[owner][0]:
            if any(f in touched for f, _ in items):
                sys.stderr.write(f"stale claim ignored: {tag} ({beats[owner][1]})\n")
            continue
        for f, fn in items:
            if f not in touched:
                continue
            h = f"(age {age.total_seconds() / 3600:.1f}h)"
            if not fine:
                print(f"{tag} {f} {h}")
                continue
            if fn:
                ro = item_ranges(git(repo, "show", f"{old_rev}:{f}"), fn)
                rn = item_ranges(git(repo, "show", f"{new_rev}:{f}"), fn)
                if not ro and not rn:
                    print(f"{tag} {f}::{fn} {h} item not found -> whole file held")
                    continue
                co, cn = changed_lines(diff, f)
                if any(a_ <= x <= b_ for x in co for a_, b_ in ro) or any(a_ <= x <= b_ for x in cn for a_, b_ in rn):
                    print(f"{tag} {f}::{fn} {h} PR changes the claimed item")
                continue  # PR leaves the claimed item alone -> not held
            if created and created < ts.astimezone(timezone.utc):
                pref = PREFIX.get(owner)
                pushed = pref and git(repo, "log", "-1", "--format=%H", f"--since={ts.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}",
                                      f"--remotes=origin/{pref}*", "--", f)
                if pushed and pushed.strip():
                    continue  # PR older than the claim and the owner's work on the file is pushed -> not held
                print(f"{tag} {f} {h} file claim newer than the PR; held until {owner} pushes his work on it or the claim expires")
                continue
            print(f"{tag} {f} {h}")


if __name__ == "__main__":
    main(sys.argv[1:])
