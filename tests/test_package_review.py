"""Adopted packages expose their actual gate feedback without model credentials."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import autonomy as runtime


class PackageReviewTests(unittest.TestCase):
    def test_adopted_review_contains_latest_gate_and_later_manual_findings(self):
        comments = [
            {"body": "self-merge gate: FAIL missing fixture", "createdAt": "one"},
            {"body": "self-merge gate: FAIL missing native test", "createdAt": "two"},
            {"body": "Integration review: keep the general native contract", "createdAt": "three"},
        ]
        response = {"data": {"repository": {"pullRequest": {"state": "OPEN", "headRefOid": "a"*40,
                    "body": "original proof and fixture declarations", "comments": {"nodes": comments}}}}}
        u = {"wip": {"pr": 17}}
        with tempfile.TemporaryDirectory() as td, patch.object(runtime.fs, "api", return_value=response) as api:
            runtime.package_review(u, Path(td))
            saved = json.loads(Path(u["review_input"]).read_text())
        self.assertEqual(saved["latest_gate"], comments[1])
        self.assertEqual(saved["recent_comments"][-1], comments[2])
        self.assertEqual(saved["head"], "a"*40)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(api.call_args.args[2]["variables"]["number"], 17)

    def test_new_packages_do_not_need_an_extra_network_read(self):
        with patch.object(runtime.fs, "api") as api:
            runtime.package_review({}, Path("unused"))
        api.assert_not_called()

    def test_failed_review_reads_are_not_fabricated_as_a_clean_gate(self):
        with tempfile.TemporaryDirectory() as td, patch.object(runtime.fs, "api", return_value={"errors": []}), \
                self.assertRaises(RuntimeError):
            runtime.package_review({"wip": {"pr": 17}}, Path(td))
        self.assertIn("review_input", (runtime.HERE.parent/"prompts/domain-plan.md").read_text())
