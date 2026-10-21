"""Exercise the production live wrapper's verdict parsing with a synthetic runner."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class LiveShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        shutil.copy2(SCRIPTS / "s5-live.sh", self.scripts)
        self.repo = self.root / "repo"
        recomp = self.repo / "recomp"
        binary = recomp / "target/release/s5verify"
        binary.parent.mkdir(parents=True)
        binary.write_text('#!/bin/bash\nprintf "%s\\n" "$TEST_LIVE_LINE"\nexit "${TEST_LIVE_RC:-1}"\n')
        binary.chmod(0o755)
        (recomp / "verify.py").write_text('def remap_for(*args): return ""\n')
        self.nf = self.root / "newfix"
        (self.scripts / "s5-env.sh").write_text(
            f'export S5="{self.root}/s5" S5_REPO="{self.repo}" S5_NF="{self.nf}" '
            f'S5_V="{self.root}/verify"\n')

    def tearDown(self):
        self.temp.cleanup()

    def run_live(self, name, line, rc=1):
        directory = self.nf / "fixtures" / name
        directory.mkdir(parents=True)
        (directory / "scenario.json").write_text(json.dumps({"image": str(self.nf / "start.img")}))
        return subprocess.run(["bash", self.scripts / "s5-live.sh", name], capture_output=True, text=True,
                              env={**os.environ, "TEST_LIVE_LINE": line, "TEST_LIVE_RC": str(rc)}, timeout=10)

    def test_numeric_campaign_and_existing_names_keep_divergence(self):
        for name in ("01-newbeginning", "campaign-01-newbeginning", "combat-waves"):
            with self.subTest(name=name):
                result = self.run_live(name, f"{name}: DIVERGENT turn 21")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout.strip(), f"live {name} exit=1 DIVERGENT turn 21")

    def test_numeric_name_keeps_identical_result(self):
        result = self.run_live("01-newbeginning", "01-newbeginning: IDENTICAL 1200 ticks", rc=0)
        self.assertEqual(result.returncode, 0)
        self.assertIn("IDENTICAL 1200 ticks", result.stdout)

    def test_failure_without_measurement_does_not_invent_a_verdict(self):
        result = self.run_live("01-newbeginning", "loader could not load image")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("DIVERGENT", result.stdout)
        self.assertNotIn("IDENTICAL", result.stdout)


if __name__ == "__main__":
    unittest.main()
