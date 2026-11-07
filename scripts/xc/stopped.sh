#!/usr/bin/env bash
# stopped.sh: exit 0 when this run's ID is listed in .xc-stop on branch claude/stop (read from the public raw file), else
# 1. The loops of long jobs ask it between kinds, so an agent that may push but not cancel runs can still end them
# (a cancel needs actions:write, which the agents' GitHub App token lacks). Prints nothing.
f=$(curl -fsS -m 20 "https://raw.githubusercontent.com/${GITHUB_REPOSITORY:-Kofi55533/siedler-agent}/claude/stop/.xc-stop" 2>/dev/null) || exit 1
grep -qxF "${GITHUB_RUN_ID:-none}" <<<"$f"
