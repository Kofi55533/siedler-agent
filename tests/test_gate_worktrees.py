"""Real local Git checkouts exercise the gate's history guard and fixture installer."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def git(directory, *args):
    return subprocess.check_output(
        ["git", "-C", str(directory), *args], text=True, stderr=subprocess.DEVNULL
    ).strip()


class GateWorktreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "fixture-source")
        git(self.repo, "config", "user.name", "Gate test")
        git(self.repo, "config", "user.email", "gate-test@example.invalid")
        (self.repo / "history").write_text("first\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "Initial local test history")
        self.first = git(self.repo, "rev-parse", "HEAD")
        image = self.root / "image" / "start.img"
        image.parent.mkdir()
        image.write_bytes(b"synthetic test placeholder")
        Path(str(image) + ".state.json").write_text("{}")
        fixture = self.repo / "fixtures-gen" / "gen-linked"
        fixture.mkdir(parents=True)
        (fixture / "scenario.json").write_text(json.dumps({
            "name": "gen-linked", "image": str(image), "commands": "commands.jsonl"
        }))
        (fixture / "commands.jsonl").write_text("")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "Add synthetic fixture metadata")

    def test_both_gate_history_guards_restore_normal_and_linked_shallow_checkouts(self):
        guards = [line.strip() for line in (SCRIPTS / "s5-gate.sh").read_text().splitlines()
                  if "git fetch -q --unshallow origin" in line]
        self.assertEqual(len(guards), 2)
        for index, guard in enumerate(guards):
            for linked in (False, True):
                with self.subTest(guard=index, linked=linked):
                    clone = self.root / f"shallow-{index}-{linked}"
                    subprocess.run(["git", "clone", "--quiet", "--depth", "1",
                                    self.repo.as_uri(), str(clone)], check=True)
                    checkout = clone
                    if linked:
                        checkout = self.root / f"linked-{index}"
                        git(clone, "worktree", "add", "--detach", str(checkout), "HEAD")
                        self.assertTrue((checkout / ".git").is_file())
                    self.assertEqual(git(checkout, "rev-parse", "--is-shallow-repository"), "true")
                    subprocess.run(["bash", "-eu", "-c", guard + "\n:"], cwd=checkout, check=True)
                    self.assertEqual(git(checkout, "rev-parse", "--is-shallow-repository"), "false")
                    self.assertEqual(git(checkout, "merge-base", "HEAD", self.first), self.first)

    def install(self, checkout, label):
        case = self.root / label
        scripts = case / "scripts"
        scripts.mkdir(parents=True)
        newfix = case / "newfix"
        for name in ("s5-fixture-install.sh", "fixture_bundle.py"):
            shutil.copyfile(SCRIPTS / name, scripts / name)
        (scripts / "s5-env.sh").write_text("\n".join([
            "export S5_REPO=" + shlex.quote(str(checkout)),
            "export S5_NF=" + shlex.quote(str(newfix)),
            "export S5_V=" + shlex.quote(str(case / "verify")),
        ]) + "\n")
        # No network fallback can provide the fixture: the passed Git ref must supply it.
        (scripts / "gh").write_text("#!/bin/sh\nexit 97\n")
        (scripts / "gh").chmod(0o755)
        env = dict(os.environ, PATH=str(scripts) + os.pathsep + os.environ["PATH"])
        result = subprocess.run(["bash", str(scripts / "s5-fixture-install.sh"),
                                 "gen-linked", "", "HEAD"], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result, newfix / "fixtures" / "gen-linked"

    def test_git_fixture_metadata_installs_from_normal_and_linked_checkouts(self):
        linked = self.root / "fixture-linked"
        git(self.repo, "worktree", "add", "--detach", str(linked), "HEAD")
        for label, checkout in (("normal", self.repo), ("linked", linked)):
            with self.subTest(checkout=label):
                result, fixture = self.install(checkout, label)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("installed from git HEAD", result.stdout)
                self.assertEqual((fixture / "commands.jsonl").read_text(), "")
                self.assertEqual(json.loads((fixture / "scenario.json").read_text())["name"], "gen-linked")

    def test_absent_repository_and_release_do_not_install_or_pass(self):
        absent = self.root / "not-a-repository"
        absent.mkdir()
        result, fixture = self.install(absent, "absent")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not installable", result.stdout)
        self.assertFalse((fixture / "scenario.json").exists())


if __name__ == "__main__":
    unittest.main()
