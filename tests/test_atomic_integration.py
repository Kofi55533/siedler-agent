"""Exercise the production integration writer against a real local bare remote.

Native/game gates are injected receipts in these tests. This proves orchestration
and Git race handling, not new game-code bit identity.
"""
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
import autonomy_state as core
import gate_contract as contract
from test_autonomy import fixture


class AtomicIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "repo"
        subprocess.run(["git", "init", "--bare", "-q", self.remote], check=True)
        subprocess.run(["git", "clone", "-q", self.remote, self.repo], check=True, capture_output=True)
        self.g("config", "user.name", "fleet-test")
        self.g("config", "user.email", "fleet@test.invalid")
        self.write("crates/s5clean/src/base.rs", "base\n")
        self.write("docs/AGENT_BOARD.md", "existing board\n")
        self.g("add", "."); self.g("commit", "-qm", "base")
        self.base = self.g("rev-parse", "HEAD")
        self.g("push", "-q", "origin", "HEAD:refs/heads/clean/integration")
        self.heads = []
        for i in (1, 2):
            self.g("checkout", "-q", "--detach", self.base)
            self.write(f"crates/s5clean/src/part{i}.rs", f"part{i}\n")
            self.g("add", "."); self.g("commit", "-qm", f"part{i}")
            self.heads.append(self.g("rev-parse", "HEAD"))
            self.g("push", "-q", "origin", f"HEAD:refs/heads/agent/unit-{i}-e01")
        self.g("checkout", "-q", "--detach", self.base)
        for head in self.heads:
            self.g("merge", "-q", "--no-ff", "--no-edit", head)
        self.candidate = self.g("rev-parse", "HEAD")
        self.tree = self.g("rev-parse", "HEAD^{tree}")
        self.state = fixture()
        for i, uid in ((1, "battle"), (2, "ai")):
            tid = str(i)
            self.state["tickets"][tid] = {"id": tid, "unit": uid, "pr": i, "head": self.heads[i - 1],
                "resources": [f"crates/s5clean/src/part{i}.rs"], "status": "verified", "proofs": [],
                "created": i, "receipt": {"base": self.base, "tree": self.tree,
                "fixtures": {"combat": {"before": 315, "after": 315}}, "scripts": contract.script_digest(runtime.HERE)}}
            self.state["leases"][uid] = {"owner": "gate:" + tid, "run": None, "resources": []}
        self.state["integration"] = {"nonce": "batch", "run": None, "tickets": ["1", "2"], "at": 1}
        self.combined = {"base": self.base, "commit": self.candidate, "tree": self.tree, "fixtures": {"combat": {}}}
        self.patches = []
        self.patches.append(patch.object(runtime, "REPO", self.repo))
        self.patches.append(patch.object(runtime, "REF", "main"))
        self.patches.append(patch.dict(os.environ, {"FLEET_V2_ENABLED": "true", "GH_TOKEN": "offline-test-only",
                                                  "RUNNER_TEMP": str(self.root)}))
        self.patches.append(patch.object(runtime, "private_record"))
        self.patches.append(patch.object(runtime, "update", side_effect=lambda op, *a, **kw: op(self.state)))
        self.patches.append(patch.object(runtime.fs, "api", side_effect=self.api))
        self.real_command = runtime.command
        self.command_hook = None
        self.patches.append(patch.object(runtime, "command", side_effect=self.wrapped_command))
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def write(self, path, text):
        dest = self.repo / path; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_text(text)

    def g(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True, check=True).stdout.strip()

    def tip(self):
        return subprocess.run(["git", "--git-dir", self.remote, "rev-parse", "refs/heads/clean/integration"],
                              capture_output=True, text=True, check=True).stdout.strip()

    def api(self, path, *args):
        if "/pulls/" in path:
            n = int(path.rsplit("/", 1)[1])
            return {"state": "open", "body": "", "head": {"sha": self.heads[n - 1]},
                    "base": {"ref": "clean/integration"}}
        if "/git/ref/heads/" in path:
            return {"object": {"sha": self.tip()}}
        self.fail("Unexpected network request")

    def wrapped_command(self, args, *a, **kw):
        if list(map(str, args[:3])) == ["git", "remote", "set-url"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if self.command_hook:
            self.command_hook(list(map(str, args)))
        return self.real_command(args, *a, **kw)

    def competitor(self, docs_only=False):
        old = self.g("rev-parse", "HEAD")
        self.g("checkout", "-q", "--detach", self.base)
        self.write("docs/AGENT_BOARD.md" if docs_only else "crates/s5clean/src/base.rs", "concurrent change\n")
        self.g("add", "."); self.g("commit", "-qm", "external integration")
        tip = self.g("rev-parse", "HEAD")
        self.g("push", "-q", "origin", "HEAD:refs/heads/clean/integration")
        self.g("checkout", "-q", "--detach", old)
        return tip

    def test_exact_combined_candidate_lands_both_heads(self):
        with patch.object(runtime, "run_gate", return_value=("verified", self.combined)) as gate:
            runtime.integrate("batch")
        self.assertEqual(self.tip(), self.candidate)
        self.assertEqual([t["status"] for t in self.state["tickets"].values()], ["accepted", "accepted"])
        self.assertEqual(gate.call_args.args[2], [self.heads[1]])
        for head in self.heads:
            self.g("merge-base", "--is-ancestor", head, self.tip())

    def test_changed_code_base_never_reuses_old_measurement(self):
        external = []
        def measured(*_):
            external.append(self.competitor())
            return "verified", self.combined
        with patch.object(runtime, "run_gate", side_effect=measured):
            runtime.integrate("batch")
        self.assertEqual(self.tip(), external[0])
        self.assertTrue(all(t["status"] == "queued" for t in self.state["tickets"].values()))

    def test_board_only_move_preserves_board_and_verified_input(self):
        with patch.object(runtime, "run_gate", side_effect=lambda *_: (self.competitor(True) and ("verified", self.combined))):
            runtime.integrate("batch")
        self.assertEqual(self.g("show", self.tip() + ":docs/AGENT_BOARD.md"), "concurrent change")
        self.assertEqual(contract.input_tree(self.repo, self.tip()), contract.input_tree(self.repo, self.candidate))
        self.assertTrue(all(t["status"] == "accepted" for t in self.state["tickets"].values()))

    def test_race_after_last_base_read_is_rejected_by_git(self):
        raced = []
        def hook(args):
            if args[:3] == ["git", "push", "-q"] and not raced:
                raced.append(self.competitor())
        self.command_hook = hook
        with patch.object(runtime, "run_gate", return_value=("verified", self.combined)):
            runtime.integrate("batch")
        self.assertEqual(self.tip(), raced[0])
        self.assertFalse(any(t["status"] == "accepted" for t in self.state["tickets"].values()))

    def test_combined_regression_does_not_merge_or_repeat_same_batch(self):
        with patch.object(runtime, "run_gate", return_value=("repair", {"error": "REGRESSION"})):
            runtime.integrate("batch")
        self.assertEqual(self.tip(), self.base)
        self.assertEqual(core.disjoint_batch(self.state), ["1"])

    def test_changed_head_never_gets_tested_or_merged(self):
        self.heads[0] = "f" * 40
        with patch.object(runtime, "run_gate") as gate:
            runtime.integrate("batch")
        gate.assert_not_called()
        self.assertEqual(self.tip(), self.base)

    def test_changed_gate_scripts_invalidate_receipts(self):
        self.state["tickets"]["1"]["receipt"]["scripts"] = "stale"
        with patch.object(runtime, "run_gate") as gate:
            runtime.integrate("batch")
        gate.assert_not_called()
        self.assertEqual(self.tip(), self.base)

    def test_json_ledger_changes_verification_input(self):
        before = contract.input_tree(self.repo, "HEAD")
        self.write("docs/subsystems/DS01-battle/corpus.json", '{"changed":true}\n')
        self.g("add", "."); self.g("commit", "-qm", "test input")
        self.assertNotEqual(before, contract.input_tree(self.repo, "HEAD"))

    def test_wrong_nonce_never_claims_integration(self):
        with patch.object(runtime, "run_gate") as gate:
            runtime.integrate("other")
        gate.assert_not_called()
        self.assertEqual(self.tip(), self.base)


if __name__ == "__main__":
    unittest.main()
