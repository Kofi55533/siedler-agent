"""Run the real gate shell with fake platform tools and synthetic Git inputs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
MATRIX = ("bc-full wintersturm economy wsecon build production combat combat-waves combat-big combat-long "
          "ai-long late-ai late-economy camp-crawford map-winterisland wintersturm-mapstart economy-mapstart")


class GateShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"; self.repo.mkdir()
        self.remote = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", self.remote], check=True)
        self.g("init", "-q"); self.g("config", "user.name", "test"); self.g("config", "user.email", "test@test.invalid")
        for path, text in (("docs/AGENT_BOARD.md", "board\n"), ("crates/s5clean/src/model.rs", "pub fn model() {}\n")):
            dest = self.repo / path; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_text(text)
        self.g("add", "."); self.g("commit", "-qm", "base")
        self.base = self.g("rev-parse", "HEAD")
        self.g("remote", "add", "origin", str(self.remote))
        self.g("push", "-q", "origin", "HEAD:refs/heads/clean/integration")
        (self.repo / "crates/s5clean/src/model.rs").write_text("pub fn model() { /* candidate */ }\n")
        self.g("commit", "-qam", "candidate")
        self.head = self.g("rev-parse", "HEAD")
        self.g("push", "-q", "origin", "HEAD:refs/pull/1/head")
        self.d = self.root / "scripts"; shutil.copytree(SCRIPTS, self.d, ignore=shutil.ignore_patterns("__pycache__"))
        self.bin = self.root / "bin"; self.bin.mkdir()
        self.s5 = self.root / "s5"; self.s5.mkdir()
        self.nf = self.root / "nf"; self.nf.mkdir()
        (self.d / "s5-env.sh").write_text(f'export S5="{self.s5}" S5_REPO="{self.repo}" S5_NF="{self.nf}"\n'
                                          f'export S5_GATE_FIXTURES="{MATRIX}"\n')
        self.script(self.d / "s5-build.sh", '#!/bin/bash\nexit 0\n')
        self.script(self.d / "heartbeat.sh", '#!/bin/bash\necho --heartbeat-unknown\n')
        self.script(self.d / "s5-fixture-install.sh", '''#!/bin/bash
if [ "${TEST_INSTALL:-yes}" = yes ]; then
  mkdir -p "$S5_NF/fixtures/$1"; echo '{}' > "$S5_NF/fixtures/$1/scenario.json"
fi
echo "$1: fixture installation attempted"
''')
        self.script(self.d / "s5-live.sh", '''#!/bin/bash
f=$1
if git grep -q candidate HEAD -- crates/s5clean/src/model.rs; then
  if [ "$f" = combat ] && [ "${TEST_MODE:-}" = regression ]; then echo 'DIVERGENT turn 20'; exit 1; fi
  if [ "$f" = combat ] && [ "${TEST_MODE:-}" = timeout ]; then echo 'DIVERGENT turn 315'; exit 124; fi
fi
echo "live $f exit=1 DIVERGENT turn 315"; exit 1
''')
        self.script(self.bin / "cargo", "#!/bin/bash\necho 'test result: ok. 4 passed; 0 failed; 0 ignored'\n")
        self.script(self.bin / "gh", '''#!/usr/bin/env python3
import os,json,sys,subprocess
a=sys.argv[1:]
if a[:2]==['release','view']:
 print('data.tzst 100 updated');sys.exit(0)
if a[:2]==['pr','diff']:
 print(subprocess.check_output(['git','diff',os.environ['TEST_BASE'],os.environ['TEST_HEAD']],text=True));sys.exit(0)
if a[:2]==['pr','view']:
 if 'title' in a: print('Subsystem synthetic: actual gate control flow')
 elif 'body' in a: print(os.environ.get('TEST_BODY',''))
 else: print(json.dumps({'baseRefName':'clean/integration','headRefName':'agent/unit-synthetic-e01',
   'headRefOid':os.environ['TEST_HEAD'],'state':'OPEN','isCrossRepository':False,
   'createdAt':'2026-10-20T00:00:00Z','files':[{'path':'crates/s5clean/src/model.rs'}]}))
 sys.exit(0)
sys.exit(1)
''')
        self.env = {**os.environ, "PATH": str(self.bin) + ":" + os.environ["PATH"],
                    "S5_GATE_CACHE": "0", "S5_GATE_COMMENT": "0", "S5_GATE_FAILFAST": "0",
                    "S5_GATE_PARALLEL": "2", "S5_GATE_PIN_BASE": self.base,
                    "TEST_BASE": self.base, "TEST_HEAD": self.head}

    def tearDown(self):
        self.temp.cleanup()

    def script(self, path, text):
        path.write_text(text); path.chmod(0o755)

    def g(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True, check=True).stdout.strip()

    def run_gate(self, **env):
        return subprocess.run(["bash", self.d / "s5-gate.sh", "1", "--dry-run"], cwd=self.repo,
                              env={**self.env, **env}, capture_output=True, text=True, timeout=30)

    def test_actual_gate_runs_full_matrix_and_writes_immutable_receipt(self):
        r = self.run_gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        receipt = json.loads((self.s5 / "gate/1/receipt.json").read_text())
        self.assertEqual(set(receipt["fixtures"]), set(MATRIX.split()))
        self.assertEqual(receipt["base"], self.base)
        self.assertEqual(receipt["head"], self.head)
        self.assertEqual(self.g("ls-remote", "origin", "refs/heads/clean/integration").split()[0], self.base)

    def test_actual_gate_covers_both_comma_separated_fixture_names(self):
        r = self.run_gate(TEST_BODY="Frontier-fixture: extra-one, extra-two\nlive extra-one: before turn 315 -> after turn 315")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        receipt = json.loads((self.s5 / "gate/1/receipt.json").read_text())
        self.assertTrue({"extra-one", "extra-two"} <= set(receipt["fixtures"]))

    def test_actual_gate_rejects_a_required_uninstallable_fixture(self):
        r = self.run_gate(TEST_INSTALL="no", TEST_BODY="Frontier-fixture: missing\nlive missing: before turn 1 -> after turn 2")
        self.assertEqual(r.returncode, 1)
        self.assertIn("could not be installed", r.stdout)

    def test_actual_gate_rejects_earlier_divergence(self):
        r = self.run_gate(TEST_MODE="regression")
        self.assertEqual(r.returncode, 1)
        self.assertIn("REGRESSION", r.stdout)

    def test_actual_gate_rejects_timeout_even_with_a_verdict_line(self):
        r = self.run_gate(TEST_MODE="timeout")
        self.assertEqual(r.returncode, 1)
        self.assertIn("TIMEOUT", r.stdout)

    def test_pinned_candidate_cannot_use_a_merge_entry(self):
        r = subprocess.run(["bash", self.d / "s5-gate.sh", "1"], cwd=self.repo, env=self.env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn("require --dry-run", r.stdout)


if __name__ == "__main__":
    unittest.main()
