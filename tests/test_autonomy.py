import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import autonomy as runtime
import autonomy_state as state
import gate_contract as contract


def fixture():
    inv = {"logic_functions": 6, "source_sha256": "native-source",
           "functions": [f"{0x400000 + i:x}+10L" for i in range(6)] + ["500000+10"]}
    index = {"units": {"battle": {"domain": "DS01"}, "ai": {"domain": "DS04"}}, "addr": {}}
    catalog = {"battle": {"addresses": ["400000", "400001"]},
               "ai": {"addresses": ["400002", "400003"]}}
    return state.initialize(inv, index, catalog, 1)


def plan(resource="crates/s5clean/src/battle.rs", addr="400000"):
    return {"goal": "resume the native transition", "native_family": "battle", "addresses": [addr],
            "resources": [resource], "minutes": 90, "acceptance": "native differential and real entry",
            "next_step": "run the native branch corpus"}


class StateTests(unittest.TestCase):
    def test_inventory_keeps_unmapped_logic(self):
        s = fixture()
        self.assertEqual(s["total"], 6)
        self.assertEqual({a for u in s["units"].values() for a in u["addresses"]},
                         {f"{0x400000 + i:x}" for i in range(6)})

    def test_real_inventory_partition_is_complete(self):
        inventory = json.loads((runtime.HERE / "scoreboard/function_inventory.json").read_text())
        s = state.initialize(inventory, {"units": {}, "addr": {}}, {}, 1)
        self.assertEqual(s["total"], 29723)
        self.assertEqual(sum(len(u["addresses"]) for u in s["units"].values()), 29723)

    def test_incomplete_inventory_is_an_error(self):
        with self.assertRaises(ValueError):
            state.inventory_addresses({"functions": ["400000+10L"], "logic_functions": 2})

    def test_overlapping_map_has_exactly_one_owner(self):
        inv = {"logic_functions": 1, "source_sha256": "x", "functions": ["400000+1L"]}
        s = state.initialize(inv, {"units": {"a": {}, "b": {}}, "addr": {}},
                             {"a": {"addresses": ["400000"]}, "b": {"addresses": ["400000"]}}, 1)
        self.assertEqual(sum(len(u["addresses"]) for u in s["units"].values()), 1)

    def test_claim_is_exclusive(self):
        s = fixture()
        self.assertTrue(state.claim_unit(s, "battle", "one", 4, 2))
        self.assertFalse(state.claim_unit(s, "battle", "two", 5, 2))

    def test_unknown_or_stale_heartbeat_does_not_free_running_writer(self):
        s = fixture()
        state.claim_unit(s, "battle", "one", 4, 2)
        state.reconcile_runs(s, {}, 50000)
        self.assertIn("battle", s["leases"])
        state.reconcile_runs(s, {"4": "in_progress"}, 90000)
        self.assertIn("battle", s["leases"])

    def test_confirmed_completed_writer_is_recoverable(self):
        s = fixture()
        state.claim_unit(s, "battle", "one", 4, 2)
        s["units"]["battle"]["wip"] = {"head": "saved"}
        state.reconcile_runs(s, {"4": "completed"}, 100)
        self.assertNotIn("battle", s["leases"])
        self.assertEqual(s["units"]["battle"]["status"], "repair")

    def test_item_lease_and_file_lease_conflicts(self):
        self.assertTrue(state.resource_overlap("a.rs", "a.rs::run"))
        self.assertTrue(state.resource_overlap("a.rs::run", "a.rs::run"))
        self.assertFalse(state.resource_overlap("a.rs::run", "a.rs::stop"))
        self.assertFalse(state.resource_overlap("a.rs", "b.rs"))

    def test_atomic_reservation_refuses_other_writer(self):
        s = fixture()
        state.claim_unit(s, "battle", "one", 1, 1)
        state.claim_unit(s, "ai", "two", 2, 1)
        self.assertEqual(state.reserve(s, "battle", "one", plan(), 2), [])
        self.assertEqual(state.reserve(s, "ai", "two", plan(addr="400002"), 3),
                         ["crates/s5clean/src/battle.rs"])
        self.assertEqual(s["leases"]["ai"]["resources"], [])

    def test_external_package_scope_is_respected(self):
        s = fixture()
        state.claim_unit(s, "battle", "one", 1, 1)
        self.assertTrue(state.reserve(s, "battle", "one", plan(), 2, ["crates/s5clean/src/battle.rs"]))

    def test_plan_cannot_steal_native_ownership(self):
        with self.assertRaises(ValueError):
            state.validate_plan(plan(addr="400002"), fixture()["units"]["battle"])

    def test_invalid_scope_is_rejected(self):
        for path in ("recomp/a.rs", "oracle/a.py", "crates/s5diff/a.rs", ".github/a.yml",
                     "crates/s5clean/", "crates/s5clean/src/*", "crates/s5clean/../other", "docs/AGENT_BOARD.md"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                state.validate_plan(plan(path), fixture()["units"]["battle"])

    def test_too_large_estimate_is_not_admitted(self):
        p = plan(); p["minutes"] = 200
        with self.assertRaises(ValueError):
            state.validate_plan(p, fixture()["units"]["battle"])

    def test_capacity_counts_infrastructure_and_legacy(self):
        s = fixture()
        free = state.CAPACITY - state.VERIFY_SLOTS
        self.assertEqual(state.select(s, free - 2, legacy_jobs=2), [])
        self.assertEqual(state.select(s, free), [])
        self.assertEqual(len(state.select(s, free - 2)), 2)

    def test_pending_dispatch_is_not_dispatched_twice(self):
        s = fixture(); s["units"]["battle"]["dispatch"] = {"nonce": "already"}
        self.assertNotIn("battle", state.select(s, 0))

    def test_a_live_legacy_writer_is_not_duplicated_during_migration(self):
        self.assertNotIn("battle", state.select(fixture(), 1, unavailable_units={"battle"}))

    def test_verification_capacity_is_not_counted_twice(self):
        s = fixture()
        for n in range(state.VERIFY_SLOTS):
            s["tickets"][str(n)] = {"status": "verifying"}
        self.assertEqual(len(state.select(s, 18)), 2)

    def test_long_workers_cannot_starve_the_first_gate_jobs(self):
        s = fixture()
        free = state.CAPACITY - state.VERIFY_SLOTS
        self.assertEqual(state.select(s, free), [])
        self.assertEqual(len(state.select(s, free - 1)), 1)

    def test_main_split_is_ten_workers_and_ten_gates(self):
        # Gate throughput (Dominic 2026-10-21): the fork's 20 workers keep the
        # queue full, so the main account gives half its runners to gates.
        self.assertEqual((state.CAPACITY, state.VERIFY_SLOTS), (20, 10))
        self.assertEqual(state.LEGACY_GATE_SLOTS, 4)
        board = (Path(__file__).resolve().parents[1] / "scripts/scoreboard/build.py").read_text()
        self.assertIn(f"V2_GATE = {state.VERIFY_SLOTS}  #", board)

    def test_provider_error_backoff_is_not_a_new_runner_loop(self):
        s = fixture(); state.claim_unit(s, "battle", "one", 1, 1)
        state.finish(s, "battle", "one", {"outcome": "provider_error"}, 2)
        self.assertEqual(state.select(s, 0, at=100), [])
        self.assertTrue(state.select(s, 0, at=1000))

    def test_no_heavy_owner_or_dependency_blocks_selection(self):
        s = fixture()
        s["units"]["battle"]["depends_on"] = ["Codex", "Opus"]
        s["units"]["battle"]["board_claim"] = "Opus"
        self.assertIn("battle", state.select(s, 0))

    def test_provider_refusal_holds_without_new_runner_retry(self):
        s = fixture(); state.claim_unit(s, "battle", "one", 1, 1)
        state.finish(s, "battle", "one", {"outcome": "access_refusal"}, 2)
        self.assertEqual(state.select(s, 0, at=3), [])
        self.assertFalse(state.claim_unit(s, "ai", "other", 2, 3))
        # Legitimate timed backoff: the hold expires instead of freezing the fleet.
        self.assertTrue(state.select(s, 0, at=2 + state.REFUSAL_BACKOFF))

    def test_two_no_gain_attempts_resplit_without_losing_addresses(self):
        s = fixture()
        for at in (2, 4):
            state.claim_unit(s, "battle", "one", at, at)
            state.finish(s, "battle", "one", {"outcome": "evidence"}, at + 1)
        self.assertEqual(s["units"]["battle"]["status"], "split")
        self.assertEqual(s["units"]["battle-a"]["addresses"] + s["units"]["battle-b"]["addresses"],
                         ["400000", "400001"])
        self.assertNotEqual(s["units"]["battle-a"]["overview"], s["units"]["battle-b"]["overview"])

    def test_too_large_wip_is_repaired_instead_of_abandoned(self):
        s = fixture(); state.claim_unit(s, "battle", "one", 1, 1)
        state.finish(s, "battle", "one", {"outcome": "too_large", "checkpoint": {"head": "saved"}}, 2)
        self.assertEqual(s["units"]["battle"]["status"], "repair")
        self.assertNotIn("battle-a", s["units"])

    def test_ready_retains_resources_until_gate(self):
        s = fixture(); state.claim_unit(s, "battle", "one", 1, 1)
        state.reserve(s, "battle", "one", plan(), 2)
        state.finish(s, "battle", "one", {"outcome": "ready", "pr": 1, "head": "a" * 40}, 3)
        self.assertEqual(s["leases"]["battle"]["owner"], "gate:1")
        self.assertNotIn("battle", state.select(s, 0))

    def test_fake_ready_head_is_not_a_ticket(self):
        s = fixture(); state.claim_unit(s, "battle", "one", 1, 1)
        with self.assertRaises(ValueError):
            state.finish(s, "battle", "one", {"outcome": "ready", "pr": 1, "head": "main"}, 3)

    def test_overview_complete_does_not_exhaust_inventory(self):
        s = fixture(); s["units"]["battle"]["overview_text"] = "Status: complete"
        self.assertEqual(state.dashboard(s)["remaining"], 6)

    def test_accept_requires_gate_receipt(self):
        s = fixture(); s["tickets"]["1"] = {"status": "queued"}
        with self.assertRaises(ValueError):
            state.accept(s, ["1"], "a" * 40, 3)

    def test_accept_counts_proofs_and_rotates_epoch(self):
        s = fixture()
        s["tickets"]["1"] = {"status": "verified", "unit": "battle", "proofs": [
            {"address": "400000", "reference_test": "suite::native", "clean_entry": "World::tick"},
            {"address": "400001"}]}
        state.accept(s, ["1"], "a" * 40, 3)
        self.assertEqual(state.dashboard(s)["reported_reference_verified_and_integrated"], 1)
        self.assertEqual(s["units"]["battle"]["branch"], "agent/unit-battle-e02")

    def test_disjoint_batch_never_mixes_files_but_spans_receipt_bases(self):
        # The writer re-measures the union on the newest base, so receipts of
        # different (older) bases may share one batch; files never overlap.
        s = fixture()
        for n, path, base in ((1, "a.rs", "base"), (2, "a.rs::run", "base"), (3, "b.rs", "other"), (4, "c.rs", "base")):
            s["tickets"][str(n)] = {"id": str(n), "status": "verified", "created": n,
                                     "resources": [path], "receipt": {"base": base}}
        self.assertEqual(state.disjoint_batch(s), ["1", "3", "4"])

    def ticket(self, n, pr, head, status="queued", resources=("crates/s5clean/src/a.rs",)):
        return {"id": str(n), "unit": "battle", "pr": pr, "head": head, "branch": "agent/unit-battle-e01",
                "resources": list(resources), "status": status, "created": n, "proofs": [], "attempts": 0}

    def test_queued_ticket_follows_moved_pr_head_within_its_resources(self):
        s = fixture(); s["tickets"]["1"] = self.ticket(1, 7, "a" * 40)
        prs = [{"number": 7, "headRefName": "agent/unit-battle-e01", "headRefOid": "b" * 40,
                "files": ["crates/s5clean/src/a.rs"]}]
        state.repin_queued_heads(s, prs, 5)
        self.assertEqual(s["tickets"]["1"]["head"], "b" * 40)
        self.assertEqual(s["tickets"]["1"]["repinned_from"], "a" * 40)
        self.assertEqual(s["tickets"]["1"]["status"], "queued")

    def test_moved_head_outside_resources_or_measuring_ticket_is_not_repinned(self):
        s = fixture()
        s["tickets"]["1"] = self.ticket(1, 7, "a" * 40)
        s["tickets"]["2"] = self.ticket(2, 8, "c" * 40, status="verifying")
        s["tickets"]["3"] = self.ticket(3, 9, "e" * 40)
        s["tickets"]["3"]["dispatch"] = {"nonce": "n", "at": 1}
        prs = [{"number": 7, "headRefName": "agent/unit-battle-e01", "headRefOid": "b" * 40,
                "files": ["crates/s5clean/src/a.rs", "crates/s5clean/src/other.rs"]},
               {"number": 8, "headRefName": "agent/unit-battle-e01", "headRefOid": "d" * 40,
                "files": ["crates/s5clean/src/a.rs"]},
               {"number": 9, "headRefName": "agent/unit-battle-e01", "headRefOid": "f" * 40,
                "files": ["crates/s5clean/src/a.rs"]}]
        state.repin_queued_heads(s, prs, 5)
        self.assertEqual([s["tickets"][k]["head"][0] for k in "123"], ["a", "c", "e"])

    def test_older_queued_ticket_of_same_pr_is_superseded(self):
        s = fixture()
        s["tickets"]["1"] = self.ticket(1, 7, "a" * 40)
        s["tickets"]["2"] = self.ticket(2, 7, "b" * 40, status="repair")
        state.repin_queued_heads(s, [{"number": 7, "headRefName": "agent/unit-battle-e01",
                                      "headRefOid": "b" * 40, "files": ["crates/s5clean/src/a.rs"]}], 5)
        self.assertEqual(s["tickets"]["1"]["status"], "superseded")
        self.assertEqual(s["tickets"]["2"]["status"], "repair")

    def test_batch_takes_up_to_eight_disjoint_verified_tickets(self):
        s = fixture()
        for n in range(1, 13):
            s["tickets"][str(n)] = {"id": str(n), "status": "verified", "created": n,
                                     "resources": [f"f{n}.rs"], "receipt": {"base": "b", "measured_at": 0}}
        s["tickets"]["3"]["resources"] = ["f2.rs::other"]
        self.assertEqual(state.BATCH_MAX, 8)
        self.assertEqual(state.disjoint_batch(s), ["1", "2", "4", "5", "6", "7", "8", "9"])
        # A full batch never waits in the coalescing window.
        s["tickets"]["13"] = {"id": "13", "status": "verifying"}
        self.assertEqual(len(state.integration_ready(s, 1)), 8)

    def test_failed_union_is_not_repeated(self):
        s = fixture(); s["incompatible_batches"] = [["1", "2"]]
        for n in (1, 2):
            s["tickets"][str(n)] = {"id": str(n), "status": "verified", "created": n,
                                     "resources": [str(n)], "receipt": {"base": "same"}}
        self.assertEqual(state.disjoint_batch(s), ["1"])

    def test_parallel_receipts_get_a_bounded_coalescing_window(self):
        s = fixture(); s["tickets"] = {
            "1": {"id": "1", "status": "verified", "created": 1, "resources": ["one.rs"],
                  "receipt": {"base": "same", "measured_at": 100}},
            "2": {"id": "2", "status": "verifying"}}
        self.assertEqual(state.integration_ready(s, 200), [])
        self.assertEqual(state.integration_ready(s, 300), ["1"])


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.args = ("b" * 40, "c" * 40, "d" * 40, "e" * 40, ["combat", "bc-full"])
        self.rows = "\n".join(f"{f} same {n} {n} {'b'*40} {'c'*40} {'d'*40}"
                              for f, n in (("combat", 315), ("bc-full", 1002000)))

    def test_typed_receipt(self):
        self.assertEqual(len(contract.receipt(self.rows, *self.args)["fixtures"]), 2)

    def test_missing_fixture_is_not_a_pass(self):
        with self.assertRaises(ValueError):
            contract.receipt(self.rows.splitlines()[0], *self.args)

    def test_zero_error_timeout_or_regression_is_not_a_pass(self):
        for replacement in ("same 0 315", "ERROR 315 315", "TIMEOUT 315 315", "same 315 314"):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                contract.receipt(self.rows.replace("same 315 315", replacement), *self.args)

    def test_other_tree_or_head_is_not_a_pass(self):
        for old in ("b" * 40, "c" * 40, "d" * 40):
            with self.subTest(old=old), self.assertRaises(ValueError):
                contract.receipt(self.rows.replace(old, "f" * 40, 1), *self.args)

    def test_duplicate_fixture_is_not_a_pass(self):
        with self.assertRaises(ValueError):
            contract.receipt(self.rows + "\n" + self.rows.splitlines()[0], *self.args)

    def test_fixture_lists_are_fully_required(self):
        self.assertEqual(contract.fixtures("Frontier-fixture: ai-builds-oldkings, ai-builds-active\n"
                                           "Frontier-fixture: combat\nordinary prose"),
                         ["ai-builds-active", "ai-builds-oldkings", "combat"])

    def test_fixture_command_injection_is_an_error(self):
        with self.assertRaises(ValueError):
            contract.fixtures("Frontier-fixture: a;rm")


class RuntimeInterlockTests(unittest.TestCase):
    def test_branch_cannot_mutate_even_when_enable_variable_is_set(self):
        with patch.dict(os.environ, {"FLEET_V2_ENABLED": "true"}), patch.object(runtime, "REF", "codex/review"):
            with self.assertRaises(RuntimeError):
                runtime.enabled()

    def test_main_cannot_mutate_without_approval_variable(self):
        with patch.dict(os.environ, {"FLEET_V2_ENABLED": "false"}), patch.object(runtime, "REF", "main"):
            with self.assertRaises(RuntimeError):
                runtime.enabled()

    def test_model_is_fixed(self):
        self.assertEqual(state.MODEL, "opencode/deepseek-v4.1-flash-free")

    def test_gate_always_uses_readonly_pinned_mode(self):
        with patch.dict(os.environ, {"GH_TOKEN": "test-only"}):
            env = runtime.gate_environment("b" * 40, "h" * 40)
        self.assertEqual(env["S5_GATE_COMMENT"], "0")
        self.assertEqual(env["S5_GATE_PIN_BASE"], "b" * 40)


if __name__ == "__main__":
    unittest.main()
