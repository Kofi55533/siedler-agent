"""Legacy-merge base lock (scripts/merge-lock.sh): no merge into clean/integration while fleet-integrate-v2 runs."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

LOCK = Path(__file__).resolve().parents[1] / "scripts" / "merge-lock.sh"
# Fake gh: the active-run count comes from $TEST_STATE (one line per poll, last line repeats); "fail" = API error.
GH = '''#!/bin/bash
[ "$1 $2" = "api repos/Kofi55533/siedler-agent/actions/workflows/fleet-integrate.yml/runs?per_page=30" ] || exit 1
n=$(cat "$TEST_COUNT" 2>/dev/null || echo 0); echo $((n+1)) > "$TEST_COUNT"
v=$(sed -n "$((n+1))p" "$TEST_STATE"); [ -n "$v" ] || v=$(tail -n1 "$TEST_STATE")
[ "$v" = fail ] && exit 1; echo "$v"
'''


class MergeLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        (self.root / "bin").mkdir(); gh = self.root / "bin" / "gh"; gh.write_text(GH); gh.chmod(0o755)
        (self.root / "g").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def run_lock(self, states, call, **env):
        (self.root / "state").write_text("\n".join(states) + "\n")
        script = f'BASE=clean/integration; G="{self.root}/g"; GT0=$(date +%s); source "{LOCK}"; {call}; echo "rc=$?"'
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                           env={**os.environ, "PATH": f"{self.root}/bin:" + os.environ["PATH"], "S5_INTEGRATE_POLL_S": "0",
                                "TEST_STATE": str(self.root / "state"), "TEST_COUNT": str(self.root / "count"), **env})
        report = (self.root / "g" / "report.txt")
        return r.stdout.strip(), report.read_text() if report.exists() else "", int((self.root / "count").read_text())

    def test_idle_merges_immediately(self):
        out, report, polls = self.run_lock(["0"], "integrate_wait")
        self.assertEqual((out, report, polls), ("rc=0", "", 1))

    def test_active_run_defers_until_idle(self):
        out, report, polls = self.run_lock(["1", "1", "0"], "integrate_wait")
        self.assertEqual(out, "rc=0"); self.assertEqual(polls, 3)
        self.assertIn("merge into clean/integration waits", report); self.assertIn("no fleet-integrate-v2 run active", report)

    def test_unknown_state_counts_as_active(self):
        self.assertEqual(self.run_lock(["fail"], "integrate_active")[0], "rc=0")
        self.assertEqual(self.run_lock(["0"], "integrate_active")[0], "rc=1")

    def test_still_active_after_cap_returns_failure_without_merge(self):
        out, report, _ = self.run_lock(["1"], "integrate_wait", S5_INTEGRATE_WAIT_MIN="0")
        self.assertEqual(out, "rc=1"); self.assertIn("waits", report)

    def test_gate_budget_limits_the_wait(self):
        out, _, polls = self.run_lock(["1"], "integrate_wait", S5_GATE_BUDGET_MIN="5")
        self.assertEqual((out, polls), ("rc=1", 1))

    def test_gate_sources_lock_before_every_merge(self):
        gate = (LOCK.parent / "s5-gate.sh").read_text()
        merge = gate.index('gh pr merge "$PR"')
        self.assertLess(gate.index('source "$D/merge-lock.sh"'), merge)
        self.assertLess(gate.index("|| integrate_wait || blocked"), gate.index("nb=$(git ls-remote origin refs/heads/$BASE"))
        self.assertLess(gate.index("integrate_active && blocked"), merge)


if __name__ == "__main__":
    unittest.main()
