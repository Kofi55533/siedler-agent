#!/usr/bin/env python3
"""Fresh lane claims on docs/AGENT_BOARD.md that cover files a PR touches (Opus decision 2026-10-18).

usage: lane-claims.py <board.md> <files.txt> [--owners Opus,Codex] [--hours 6] [--now 'YYYY-MM-DD HH:MM']

Claim format (one line anywhere on the board, Berlin time; leading '- ' and backticks allowed):
    LANE-CLAIM <Owner> <YYYY-MM-DD HH:MM> <file>[::<function>] [<file>[::<function>] ...] [-- note]
e.g. LANE-CLAIM Codex 2026-10-18 12:30 crates/s5clean/src/world/alarms.rs::WorkerAlarm -- bc-full 1501 shelter
A claim expires <hours> after its timestamp. Claims are per FILE for the gate (a ::function suffix still blocks the
whole file while fresh). Directory claims (path ending in '/') are ignored on purpose: lanes are file/function based.
Prints one line per conflicting fresh claim ('<owner> <time> <file> (age <h>h)'); exit 0 always.
"""
import re, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BER = ZoneInfo("Europe/Berlin")
RX = re.compile(r"^\s*(?:[-*]\s*)?`?LANE-CLAIM\s+([A-Za-z]+)\s+(\d{4}-\d{2}-\d{2})[ T](\d{1,2}:\d{2})(?:\s+Berlin)?\s+(.*)$")


def parse(lines):
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
        files = []
        for tok in rest.replace(",", " ").split():
            f = tok.split("::", 1)[0].strip(":;.()[]")
            if "/" in f and not f.endswith("/"):
                files.append(f.lstrip("./"))
        if files:
            yield owner, ts, files


def main(a):
    board, flist = a[0], a[1]
    owners = {"Opus", "Codex"}; hours = 6.0; now = datetime.now(BER)
    i = 2
    while i < len(a):
        if a[i] == "--owners": owners = set(a[i + 1].split(",")); i += 2
        elif a[i] == "--hours": hours = float(a[i + 1]); i += 2
        elif a[i] == "--now": now = datetime.strptime(a[i + 1], "%Y-%m-%d %H:%M").replace(tzinfo=BER); i += 2
        else: sys.exit(f"bad arg {a[i]}")
    touched = {l.strip() for l in open(flist) if l.strip()}
    win = timedelta(hours=hours)
    for owner, ts, files in parse(open(board, encoding="utf-8", errors="replace")):
        if owner not in owners:
            continue
        age = now - ts
        # fresh = younger than the window; a future timestamp counts as fresh unless it is absurd (> window ahead)
        if age >= win or age <= -win:
            continue
        for f in files:
            if f in touched:
                print(f"{owner} {ts:%Y-%m-%d %H:%M} {f} (age {age.total_seconds() / 3600:.1f}h)")


if __name__ == "__main__":
    main(sys.argv[1:])
