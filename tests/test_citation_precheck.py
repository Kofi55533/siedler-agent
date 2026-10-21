"""The worker runs the gate's own citation rule (subsys-xcheck.py) before a ticket.

Real Git repositories, the real subsys-xcheck.py script and the real function
inventory; only the public coverage-map download is replaced by an empty map
(the gate's stricter fallback).
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import autonomy as runtime

ADDR = "0x40101A"  # a logic function of the authoritative inventory


class CitationPrecheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.remote, self.repo = root / "remote.git", root / "repo"
        subprocess.run(["git", "init", "--bare", "-q", self.remote], check=True)
        subprocess.run(["git", "clone", "-q", self.remote, self.repo], check=True, capture_output=True)
        self.g("config", "user.name", "t"); self.g("config", "user.email", "t@test.invalid")
        self.write("crates/s5clean/src/unit.rs", "pub fn base() {}\n")
        self.g("add", "."); self.g("commit", "-qm", "base")
        self.g("push", "-q", "origin", "HEAD:refs/heads/clean/integration")
        self.base = self.g("rev-parse", "HEAD")
        self.resources = ["crates/s5clean/src/unit.rs", "crates/s5xcheck/tests/unit.rs"]
        self.write("crates/s5clean/src/unit.rs", f"pub fn base() {{}}\n/// native {ADDR}\npub fn ported() {{}}\n")
        self.patches = [patch.object(runtime, "REPO", self.repo),
                        patch.dict(os.environ, {"GH_TOKEN": "offline-test-only", "RUNNER_TEMP": str(root)}),
                        patch.object(runtime.urllib.request, "urlopen", side_effect=OSError("offline"))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def g(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True, check=True).stdout.strip()

    def write(self, path, text):
        dest = self.repo / path; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_text(text)

    def test_uncovered_citation_reports_the_gate_verdict(self):
        gaps = runtime.citation_gaps(self.base, self.resources, "Fixture-request: reach it\n")
        self.assertEqual(len(gaps), 1)
        self.assertIn("cites 1 function(s) no fixture executes", gaps[0])
        self.assertIn("Differential-tests", gaps[0])
        # The precheck never commits, stages or moves the worker's branch.
        self.assertEqual(self.g("rev-parse", "HEAD"), self.base)
        self.assertIn("unit.rs", self.g("status", "--porcelain"))

    def test_named_differential_target_with_reference_call_passes(self):
        self.write("crates/s5xcheck/tests/unit.rs",
                   f"#[test]\nfn ported() {{ let n = s5xcheck::call({ADDR.lower()}, &[]); assert_eq!(n, 0); }}\n")
        body = "Differential-tests: --test unit\nFixture-request: reach it\n"
        self.assertEqual(runtime.citation_gaps(self.base, self.resources, body), [])

    def test_test_file_without_reference_call_still_fails(self):
        self.write("crates/s5xcheck/tests/unit.rs", f"// {ADDR}\n#[test]\nfn ported() {{}}\n")
        body = "Differential-tests: --test unit\nFixture-request: reach it\n"
        gaps = runtime.citation_gaps(self.base, self.resources, body)
        self.assertTrue(gaps and "reference call" in gaps[0])

    def test_package_without_new_citations_is_not_held(self):
        self.write("crates/s5clean/src/unit.rs", "pub fn base() {}\npub fn helper() {}\n")
        self.assertEqual(runtime.citation_gaps(self.base, self.resources, ""), [])


if __name__ == "__main__":
    unittest.main()
