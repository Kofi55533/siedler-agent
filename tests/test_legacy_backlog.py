"""A live v2 fleet must not strand the old fixture queue or double its gates."""
import copy
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import autonomy as runtime
import autonomy_state as core
import legacy_backlog as backlog
from test_autonomy import fixture


def package(number=1):
    return {"number": number, "title": f"Coverage: synthetic map {number}",
            "headRefName": f"agent/coverage-synthetic-{number}", "headRefOid": "b" * 40,
            "body": "New-code-path: synthetic native family\nFrontier-fixture: synthetic-map\n"
                    "live synthetic-map: before turn 12 -> after turn 12",
            "files": [f"fixtures-gen/synthetic-{number}/scenario.json"], "comments": []}


def run(number=1, title=None, path="gate-baseline"):
    return {"id": number, "status": "in_progress", "path": f".github/workflows/{path}.yml",
            "display_title": title or f"gate-baseline regate {number}"}


class BacklogTests(unittest.TestCase):
    def setUp(self):
        self.state = fixture()

    def select(self, prs=None, runs=None, at=100, slots=4, epoch="input", scripts="gate"):
        return backlog.select(self.state, prs if prs is not None else [package()], runs or {},
                              at, epoch, scripts, slots)

    def test_fixture_backlog_has_two_slots_and_needs_no_model_lease(self):
        self.assertEqual([p["pr"] for p in self.select([package(n) for n in (3, 1, 2)])], [1, 2])
        self.assertFalse(self.state["leases"])
        self.assertEqual(self.select(slots=0), [])

    def test_source_hard_paths_non_agent_heads_and_incomplete_declarations_are_excluded(self):
        for changes in ({"files": ["crates/s5clean/src/world.rs"]}, {"files": ["recomp/model.rs"]},
                        {"headRefName": "codex/fixture"}, {"body": "New-code-path: alone"}):
            p = package(); p.update(changes)
            with self.subTest(changes=changes):
                self.assertEqual(self.select([p]), [])

    def test_managed_tickets_and_adopted_wip_are_left_with_their_domain(self):
        self.state["tickets"]["1"] = {"pr": 1}
        self.state["units"]["battle"]["wip"] = {"pr": 2}
        self.assertEqual([p["pr"] for p in self.select([package(n) for n in (1, 2, 3)])], [3])

    def test_running_queued_and_pending_regates_are_counted_without_duplicate_reservations(self):
        candidate = self.select()[0]
        self.assertTrue(backlog.reserve(self.state, candidate, 100))
        self.assertEqual(backlog.pending(self.state, {}, 101), 1)
        self.assertEqual(backlog.pending(self.state, {"1": run()}, 101), 0)
        self.assertEqual(self.select([package(1), package(2), package(3)], {"1": run()}, at=101),
                         self.select([package(2)], {"1": run()}, at=101))
        self.assertEqual(self.select([package(3)], {"1": run(), "2": run(2)}), [])

    def test_active_legacy_writer_and_rebase_jobs_are_not_duplicated(self):
        for title in (package()["title"], "Rebase PR #1 PR #22: repair fixtures"):
            self.assertEqual(self.select(runs={"7": run(7, title, "opencode")}), [])

    def test_unchanged_correctness_failure_waits_for_an_author_fix(self):
        p = package()
        p["comments"] = [{"body": "self-merge gate: FAIL\nhead=agent/test@bbbbbbbb\n"
                                   "GATE FAIL: first divergence earlier", "createdAt": "now"}]
        self.assertEqual(self.select([p]), [])
        p["headRefOid"] = "c" * 40
        self.assertEqual(len(self.select([p])), 1)

    def test_repaired_infrastructure_and_metadata_failures_reenter_the_full_gate(self):
        for reason in ("DEFERRED", "no result (ERROR/TIMEOUT)", "could not be installed",
                       "invalid required fixture declaration", "without a new code path",
                       "without live before/after turn numbers", "fresh lane claim"):
            p = package(); p["comments"] = [{"body": "self-merge gate: " + reason}]
            with self.subTest(reason=reason):
                self.assertEqual(len(self.select([p])), 1)

    def test_same_head_body_inputs_and_scripts_are_attempted_once(self):
        candidate = self.select()[0]
        self.assertTrue(backlog.reserve(self.state, candidate, 100))
        self.state["backlog_attempts"]["1"]["status"] = "dispatched"
        self.assertEqual(self.select(at=10000), [])
        self.assertFalse(backlog.reserve(self.state, candidate, 10000))
        self.assertEqual(len(self.select(at=10000, scripts="fixed-gate")), 1)
        self.assertEqual(len(self.select(at=10000, epoch="changed-input")), 1)
        changed = package(); changed["body"] += "\nFixed fixture declaration"
        self.assertEqual(len(self.select([changed], at=10000)), 1)

    def test_unknown_dispatch_retains_capacity_and_waits_before_retry(self):
        candidate = self.select()[0]
        backlog.reserve(self.state, candidate, 100)
        self.assertEqual(self.select(at=101), [])
        self.assertEqual(len(self.select(at=1301)), 1)
        self.assertEqual(backlog.pending(self.state, {}, 1301), 0)

    def test_markdown_does_not_requeue_but_code_json_or_incomplete_comparisons_do(self):
        old = {"head": "a", "input": "original"}
        result = backlog.epoch(old, "b", {"status": "ahead", "files": [{"filename": "docs/AGENT_BOARD.md"}]})
        self.assertEqual(result, {"head": "b", "input": "original"})
        for files in ([], [{"filename": "docs/ledger.json"}], [{"filename": "crates/clean.rs"}],
                      [{"filename": "docs/prose.md"}] * 300):
            with self.subTest(files=files[:1]):
                self.assertEqual(backlog.epoch(old, "b", {"status": "ahead", "files": files})["input"], "b")

    def test_legacy_gates_use_reserved_capacity_without_reserving_four_more_slots(self):
        free = core.CAPACITY - core.VERIFY_SLOTS
        self.assertEqual(core.select(self.state, 18), [])
        self.assertEqual(len(core.select(self.state, free + 2, external_gates=2)), 0)
        self.assertEqual(len(core.select(self.state, free + 1, external_gates=2)), 1)
        self.assertEqual(len(core.select(self.state, 19, external_gates=core.VERIFY_SLOTS)), 1)
        self.assertEqual(backlog.gate_count({"1": run(1, path="fleet-verify")}), 0)

    def test_control_dispatches_the_existing_full_gate_and_shadow_never_writes(self):
        snap = {"runs": {}, "prs": [package()], "at": 100}
        # This code epoch's baseline warm-up was already dispatched.
        self.state["base_warm"] = {"input": "a" * 40, "at": 100, "attempts": 1}
        def update(operation, *args, **kwargs):
            return operation(self.state)
        with patch.object(runtime, "snapshot", return_value=snap), \
                patch.object(runtime, "read", side_effect=lambda: copy.deepcopy(self.state)), \
                patch.object(runtime.fs, "api", return_value={"object": {"sha": "a" * 40}}), \
                patch.object(runtime, "update", side_effect=update) as write, \
                patch.object(runtime, "dispatch") as dispatch, \
                patch.dict(os.environ, {"FLEET_V2_WORKER_LIMIT": "0"}), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            runtime.control(shadow=True)
            self.assertEqual(json.loads(output.getvalue())["would_regate"], [1])
            write.assert_not_called(); dispatch.assert_not_called()
            runtime.control()
            dispatch.assert_called_once_with("gate-baseline.yml", {"mode": "regate", "pr": "1"})
        self.assertEqual(self.state["backlog_attempts"]["1"]["status"], "dispatched")

    def test_twenty_live_runs_or_four_gate_reservations_prevent_new_dispatches(self):
        for runs in ({str(i): run(i, "unrelated", "other") for i in range(20)},
                     {str(i): run(i, "gate-baseline warm") for i in range(4)}):
            with self.subTest(count=len(runs)), \
                    patch.object(runtime, "snapshot", return_value={"runs": runs, "prs": [package()], "at": 100}), \
                    patch.object(runtime, "read", return_value=copy.deepcopy(self.state)), \
                    patch.object(runtime.fs, "api", return_value={"object": {"sha": "a" * 40}}), \
                    patch.object(runtime, "update"), patch.object(runtime, "dispatch") as dispatch, \
                    patch.dict(os.environ, {"FLEET_V2_WORKER_LIMIT": "0"}), \
                    patch("sys.stdout", new_callable=io.StringIO) as output:
                runtime.control(shadow=True)
                self.assertEqual(json.loads(output.getvalue())["would_regate"], [])
                dispatch.assert_not_called()

    def test_gate_completion_supplies_a_controller_wakeup(self):
        self.assertFalse(runtime.controller_needs_wakeup({"provider_hold": None}, {"1": run()}))

    def test_metadata_read_is_paginated_and_incomplete_results_fail_closed(self):
        first = {"number": 1, "files": {"nodes": [{"path": "fixtures-gen/one/scenario.json"}],
                 "pageInfo": {"hasNextPage": False}}, "comments": {"nodes": []}}
        second = {"number": 2, "files": {"nodes": [], "pageInfo": {"hasNextPage": True}},
                  "comments": {"nodes": []}}
        def page(node, more):
            return {"data": {"repository": {"pullRequests": {"nodes": [node],
                    "pageInfo": {"hasNextPage": more, "endCursor": "cursor" if more else None}}}}}
        with patch.object(backlog.fs, "api", side_effect=[page(first, True), page(second, False)]), \
                patch.object(backlog.fs, "paged", return_value=[{"filename": "fixtures-gen/two/scenario.json"}]):
            self.assertEqual([p["files"] for p in backlog.packages()],
                             [["fixtures-gen/one/scenario.json"], ["fixtures-gen/two/scenario.json"]])
        for response in ({"errors": [{"message": "no access"}]}, {"data": {"repository": None}}):
            with self.subTest(response=response), patch.object(backlog.fs, "api", return_value=response), \
                    self.assertRaises(RuntimeError):
                backlog.packages()


if __name__ == "__main__":
    unittest.main()
