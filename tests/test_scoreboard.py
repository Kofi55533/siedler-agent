"""Scoreboard (scripts/scoreboard/build.py): both v2 accounts, brother-origin packages, real value timestamp."""
import importlib.util
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # scripts/scoreboard/__pycache__ is tracked: never rewrite it from a test run
_P = Path(__file__).resolve().parents[1] / "scripts/scoreboard/build.py"
_spec = importlib.util.spec_from_file_location("sb_build", _P)
b = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(b)


def run(i, path, status="in_progress", title="x"):
    return {"id": i, "path": f".github/workflows/{path}", "status": status, "display_title": title,
            "created_at": "2026-10-21T18:00:00Z", "html_url": f"https://github.com/x/actions/runs/{i}"}


class AccountView(unittest.TestCase):
    def test_counts_per_account_and_total(self):
        main = {1: run(1, "fleet-worker.yml"), 2: run(2, "fleet-worker.yml", "queued"), 3: run(3, "fleet-verify.yml"),
                4: run(4, "fleet-verify.yml"), 5: run(5, "fleet-integrate.yml"), 6: run(6, "scoreboard.yml")}
        fork = {10 + i: run(10 + i, "fleet-worker.yml") for i in range(20)}
        fork[99] = run(99, "fleet-pool.yml")
        accts, total = b.v2_account_view(main, fork, 10)
        m, f = accts
        self.assertEqual((m["account"], m["used"], m["running"], m["waiting"], m["capacity"]), ("Kofi55533", 6, 5, 1, 20))
        parts = {p["key"]: p["used"] for p in m["parts"]}
        self.assertEqual(parts, {"worker": 2, "preflight": 2, "integration": 1, "regate": 0, "control": 1})
        self.assertEqual((m["worker_limit"], m["gate_limit"], m["gate_used"]), (10, 10, 3))
        self.assertEqual((f["account"], f["used"], f["capacity"], f["parts"][0]["used"]), ("Dominickofi00", 21, 20, 20))
        self.assertEqual((total["used"], total["capacity"], total["workers"], total["worker_limit"]), (27, 40, 22, 30))

    def test_unreadable_fork_counts_zero_and_is_flagged(self):
        accts, total = b.v2_account_view({1: run(1, "fleet-worker.yml")}, None, 10)
        self.assertFalse(accts[1]["readable"])
        self.assertEqual(total["used"], 1)

    def test_capacity_matches_autonomy_state(self):
        sys.path.insert(0, str(_P.parents[1]))
        import autonomy_state as state
        self.assertEqual((b.V2_CAPACITY, b.V2_GATE), (state.CAPACITY, state.VERIFY_SLOTS))
        self.assertEqual(b.FORK_CAPACITY, 20)


class Packages(unittest.TestCase):
    def test_brother_packages_by_author_then_lease_pool(self):
        av = {"units": {"a": {}, "b": {}, "c": {}},
              "leases": {"b": {"run": "7", "pool": b.FORK}},
              "tickets": {"1": {"unit": "a", "pr": 748, "status": "queued"}, "2": {"unit": "c", "pr": 700, "status": "accepted"}}}
        prs = {748: {"author": "Dominickofi00", "state": "open", "head": "agent/unit-a-e01"},
               753: {"author": "someone-else", "state": "open", "head": "agent/unit-b-e01"},  # author unknown -> lease pool
               700: {"author": "Kofi55533", "state": "merged", "head": "agent/unit-c-e01"},
               699: {"author": "Kofi55533", "state": "merged", "head": "codex/not-a-package"}}
        pk = b.v2_packages(av, prs)
        by = {p["number"]: (p["account"], p["identified_by"], p["unit"]) for p in pk["prs"]}
        self.assertEqual(by, {748: ("Dominickofi00", "PR-Autor", "a"), 753: ("Dominickofi00", "Lease-Pool", "b"),
                              700: ("Kofi55533", "PR-Autor", "c")})
        self.assertEqual(pk["by_account"]["Dominickofi00"]["open"], 2)
        self.assertEqual(pk["by_account"]["Kofi55533"]["merged"], 1)
        self.assertEqual(pk["by_account"]["Kofi55533"]["tickets"], {"accepted": 1})


class ValuesAsOf(unittest.TestCase):
    def test_newest_measurement_not_matrix_entry(self):
        rows = [{"name": "build", "measured": True, "when": "2026-10-17 18:50", "source": "Opus Live-Matrix"},
                {"name": "wsecon", "measured": True, "when": "2026-10-21 19:30", "source": "Gate #697 (Merge 19:30)"},
                {"name": "x", "measured": False, "when": None, "source": None}]
        r = b.values_as_of(rows, {"time": "2026-10-17 18:50"})
        self.assertEqual((r["as_of"], r["as_of_scenario"]), ("2026-10-21 19:30", "wsecon"))
        self.assertEqual(b.values_as_of([], {"time": "2026-10-17 18:50"})["as_of"], "2026-10-17 18:50")


class Overview(unittest.TestCase):
    def setUp(self):
        self.now = b.NOW.timestamp()
        b._FLEET_STATE[:] = [{"autonomy_v2": {
            "total": 10, "leases": {}, "events": [], "units": {
                "u": {"status": "gating", "addresses": ["a1", "a2", "b1"],
                      "verified": {"a1": {"status": "accepted"}}}},
            "tickets": {
                "1": {"status": "queued", "created": self.now - 7200},
                "2": {"status": "repair", "receipt": {"error": "GATE FAIL: subsystem PR cites 3 function(s) no fixture executes"}},
                "3": {"status": "repair", "receipt": {"error": "GATE FAIL: frontier PR without live before/after turn numbers"}},
                "4": {"status": "repair", "receipt": {"error": "GATE FAIL: subsystem PR cites 1 function(s) no fixture executes"}},
                "5": {"status": "accepted"}},
            "integration": None}}]

    def tearDown(self):
        b._FLEET_STATE[:] = []

    def data(self, head_hours):
        head = (b.NOW - b.timedelta(hours=head_hours)).astimezone(b.BERLIN).strftime("%Y-%m-%d %H:%M")
        return {"fleet_v2": {"total": {"workers": 30, "worker_limit": 30}, "groups": [{"key": "gate", "used": 10, "limit": 10}],
                             "enabled": True, "domain": {"provider_hold": False}},
                "head": {"time": head}, "errors": [],
                "percent": {"identical_turns": {"as_of": head}},
                "scenarios": [{"new": False, "identical": True, "measured": True}, {"new": True, "identical": False, "measured": True}]}

    def test_repair_causes_and_scope_counts(self):
        o = b.overview(self.data(1), [])
        self.assertEqual([(c["label"], c["count"]) for c in o["pipeline"]["repair_causes"]],
                         [("Differential-Test fehlt", 2), ("Live-Zahlen fehlen", 1)])
        a = next(c for c in o["goal"]["scope"] if c["key"] == "A")
        self.assertEqual((o["goal"]["done"], o["pipeline"]["pending"]), (1, 1))
        self.assertTrue(a["now"])
        self.assertEqual((o["scenarios"]["base_identical"], o["scenarios"]["gen_identical"]), (1, 0))

    def test_no_merge_for_hours_with_pending_tickets_is_critical(self):
        o = b.overview(self.data(14), [])
        self.assertEqual(o["health"], "critical")
        self.assertIn("nichts Neues integriert", o["attention"][0]["title"])
        self.assertEqual(b.overview(self.data(1), [])["health"], "warning")  # 3 of 4 open tickets in repair

    def test_merges_per_day_and_last_24h(self):
        t = lambda h: b.utc(b.NOW - b.timedelta(hours=h))
        merges = [{"time": t(1)}, {"time": t(2)}, {"time": t(30)}, {"time": t(24 * 20)}]
        ig = b.overview(self.data(1), merges)["integrations"]
        self.assertEqual((ig["last_24h"], ig["prev_24h"], len(ig["per_day"])), (2, 1, 7))
        self.assertEqual(sum(d["count"] for d in ig["per_day"]), 3)


if __name__ == "__main__":
    unittest.main()
