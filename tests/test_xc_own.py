"""Own-repo mode (scripts/xc_own): the partition, the pool's pick and the main side's import discovery read the fleet
rules but write only where they may; the import stand-in and import.sh hand exactly the pool's change to worker.sh;
the gh guard and the push hook refuse writes to the main account."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "xc_own"))
sys.path.insert(0, str(ROOT / "scripts" / "xc"))
import own  # noqa: E402
import xc  # noqa: E402

NOW = 2_000_000_000
own.MAIN_AGENT, own.MAIN_PROJECT = "Kofi55533/siedler-agent", "Kofi55533/siedler-sim-rs"
xc.AGENT, xc.PROJECT = own.MAIN_AGENT, own.MAIN_PROJECT
PINNED = "2033-05-18T03:00:00Z"  # 1_999_998_000, before NOW


def worklist(path, kinds, sha="c" * 40):
    """FN kinds failing in <failing> scenarios: the higher the count, the better the fleet's rank"""
    doc = {"sha": sha, "kinds": {k: {"failing": n, "samples": 5, "entries": 5,
                                     "scenarios": {f"s{i}": {"entries": 1, "samples": 1, "failing": 1} for i in range(n)}}
                                 for k, n in kinds.items()}}
    path.write_text(json.dumps(doc))
    return doc


class FakeGitHub:
    """xc.gh stand-in: open tracking issues and PRs per repo, the pinned commit; records every write"""

    def __init__(self, issues=None, prs=None):
        self.issues = issues or {}   # repo -> {kind: body}
        self.prs = prs or {}         # repo -> [head ref]
        self.writes = []
        self.reads = []

    def __call__(self, *args):
        if any(a in ("-f", "-X") for a in args):
            path = next(a for a in args if a.startswith("repos/"))
            self.writes.append(path)
            repo = "/".join(path.split("/")[1:3])
            body = next((a[5:] for a in args if a.startswith("body=")), "")
            title = next((a[6:] for a in args if a.startswith("title=")), None)
            if title:
                self.issues.setdefault(repo, {})[title.removeprefix("[xc] ")] = body
            elif path.split("/")[-2] == "issues":
                num = int(path.split("/")[-1])
                kind = sorted(self.issues[repo])[num - 1]
                self.issues[repo][kind] = body
            return {}
        path = args[0]
        self.reads.append(path)
        repo = "/".join(path.split("/")[1:3])
        if "/issues?" in path:
            if "page=1" not in path.split("&"):
                return []
            return [{"title": f"[xc] {k}", "number": i + 1, "body": b}
                    for i, (k, b) in enumerate(sorted(self.issues.get(repo, {}).items()))]
        if "/pulls?" in path:
            return [{"head": {"ref": r}} for r in self.prs.get(repo, [])] if "page=1" in path.split("&") else []
        if "/commits/" in path:
            return {"commit": {"committer": {"date": PINNED}}}
        if "/actions/runs/" in path:
            return {"status": "in_progress"}
        return None


class PartitionTest(unittest.TestCase):
    def test_complementary_specs_split_every_kind_once(self):
        kinds = [f"FN_{i:06X}" for i in range(0x4A0000, 0x4A0000 + 400)]
        a = {k for k in kinds if own.part(k, "0/2")}
        b = {k for k in kinds if own.part(k, "1/2")}
        self.assertEqual(a | b, set(kinds))
        self.assertFalse(a & b)
        self.assertTrue(150 < len(a) < 250, len(a))   # roughly half
        self.assertEqual({k for k in kinds if own.part(k, "0,1/3")} | {k for k in kinds if own.part(k, "2/3")}, set(kinds))
        self.assertTrue(all(own.part(k, "all") and own.part(k, "") for k in kinds))

    def test_partition_is_deterministic(self):
        self.assertEqual(own.part("FN_58E2C0", "1/2"), own.part("FN_58E2C0", "1/2"))
        out = subprocess.run([sys.executable, str(ROOT / "scripts/xc_own/own.py"), "part", "FN_58E2C0", "1/2"])
        self.assertEqual(out.returncode == 0, own.part("FN_58E2C0", "1/2"))


class PoolPickTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = mock.patch.dict(os.environ, {"XC_OWN_SKIP": "2", "XC_OWN_PARTITION": "all"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.saved = (xc.ended, xc.POOL, xc.TOKEN, own.RESULTS)
        xc.POOL, xc.TOKEN, own.RESULTS = "Dom/siedler-agent-pool", "", "Dom/results"
        xc.RUNS.clear()

    def tearDown(self):
        xc.ended, xc.POOL, xc.TOKEN, own.RESULTS = self.saved

    def pick(self, fake, n=10, claim=True, limit=None):
        with mock.patch.object(xc, "gh", fake), contextlib.redirect_stdout(io.StringIO()):
            return own.pick(self.tmp / "wl.json", n, claim=claim, limit=limit, now=NOW)

    def test_takes_what_the_fleet_leaves_and_writes_only_its_own_repo(self):
        # rank: A > B > C > D > E > F > G (more failing scenarios first)
        worklist(self.tmp / "wl.json", {"FN_A": 9, "FN_B": 8, "FN_C": 7, "FN_D": 6, "FN_E": 5, "FN_F": 4, "FN_G": 3})
        fake = FakeGitHub(
            issues={"Kofi55533/siedler-agent": {"FN_C": f"claimed {NOW - 60} 123 Kofi55533/siedler-agent 123/FN_C\n",
                                                "FN_G": f"merged cccccccccccc {NOW - 9000}\n"},
                    "Dom/results": {"FN_E": f"claimed {NOW - 60} 77 Dom/siedler-agent-pool 77/FN_E\n",
                                    "FN_F": f"merged aaaaaaaaaaaa {NOW - 3600}\n"}},
            prs={"Kofi55533/siedler-sim-rs": ["xc/fn-d"]})
        kinds = self.pick(fake)
        # A, B: the fleet's next (skip 2); C claimed by the fleet; D has a PR there; E claimed by the pool itself;
        # F passed by the pool an hour ago (waits for the import); G merged on the main project since the census
        self.assertEqual(kinds, [])
        worklist(self.tmp / "wl.json", {"FN_A": 9, "FN_B": 8, "FN_C": 7, "FN_D": 6, "FN_E": 5, "FN_F": 4, "FN_G": 3,
                                         "FN_H": 2, "FN_I": 1})
        kinds = self.pick(fake)
        self.assertEqual(kinds, ["FN_H", "FN_I"])
        self.assertTrue(fake.writes and all(w.startswith("repos/Dom/results/") for w in fake.writes), fake.writes)
        self.assertIn("claimed", fake.issues["Dom/results"]["FN_H"])
        self.assertNotIn("FN_H", fake.issues["Kofi55533/siedler-agent"])
        # no run lookups of other pools' claims (fewer calls)
        self.assertFalse([r for r in fake.reads if "/actions/runs/" in r and "Kofi55533" in r])

    def test_own_limit_and_partition(self):
        worklist(self.tmp / "wl.json", {f"FN_{c}": 20 - i for i, c in enumerate("ABCDEFGHIJKLMNOP")})
        fake = FakeGitHub(issues={"Dom/results": {"FN_P": f"claimed {NOW - 60} 77 Dom/siedler-agent-pool tokP\n"}})
        self.assertEqual(len(self.pick(fake, claim=False, limit=3)), 2)   # one own job running
        with mock.patch.dict(os.environ, {"XC_OWN_PARTITION": "1/2"}):
            kinds = self.pick(fake, claim=False)
        self.assertTrue(kinds and all(own.part(k, "1/2") for k in kinds))

    def test_refuses_a_results_repo_of_the_main_account(self):
        own.RESULTS = "Kofi55533/results"
        worklist(self.tmp / "wl.json", {"FN_A": 1})
        with self.assertRaises(SystemExit):
            self.pick(FakeGitHub())


class ImportDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (xc.ended, xc.POOL, xc.TOKEN)
        xc.POOL, xc.TOKEN = "Kofi55533/siedler-agent", ""
        xc.ended = lambda run, pool: False

    def tearDown(self):
        xc.ended, xc.POOL, xc.TOKEN = self.saved

    def imports(self, fake, heads, n=5):
        (self.tmp / "heads.txt").write_text("".join(f"{s}\trefs/heads/{b}\n" for b, s in heads.items()))
        with mock.patch.object(xc, "gh", fake), contextlib.redirect_stdout(io.StringIO()):
            return own.imports(self.tmp / "wl.json", n, self.tmp / "heads.txt", self.tmp / "seen.json", claim=True, now=NOW)

    def test_available_unseen_heads_claimed_on_the_main_repo(self):
        worklist(self.tmp / "wl.json", {"FN_A": 3, "FN_B": 2, "FN_C": 1, "FN_D": 1})
        fake = FakeGitHub(issues={"Kofi55533/siedler-agent": {"FN_B": f"claimed {NOW - 60} 5 Kofi55533/siedler-agent 5/FN_B\n"}},
                          prs={"Kofi55533/siedler-sim-rs": ["xc/fn-c"]})
        heads = {"xc/fn-a": "1" * 40, "xc/fn-b": "2" * 40, "xc/fn-c": "3" * 40, "xc/fn-z": "9" * 40}
        with mock.patch.dict(os.environ, {"XC_IMPORT_MAX": "2"}):
            out = self.imports(fake, heads)
            # B claimed by a worker, C has an open PR, D no result branch, fn-z no kind of the work list
            self.assertEqual(out, [{"kind": "FN_A", "sha": "1" * 40}])
            self.assertIn("claimed", fake.issues["Kofi55533/siedler-agent"]["FN_A"])
            # the same head is not tried twice; a new head of the pool is
            fake.issues["Kofi55533/siedler-agent"]["FN_A"] = ""
            self.assertEqual(self.imports(fake, heads), [])
            heads["xc/fn-a"] = "4" * 40
            self.assertEqual(self.imports(fake, heads), [{"kind": "FN_A", "sha": "4" * 40}])

    def test_running_imports_use_up_the_slots(self):
        worklist(self.tmp / "wl.json", {"FN_A": 3, "FN_B": 2, "FN_C": 1})
        fake = FakeGitHub(issues={"Kofi55533/siedler-agent": {
            "FN_X": f"claimed {NOW - 60} 5 Kofi55533/siedler-agent import-5\n",
            "FN_Y": f"claimed {NOW - 60} 6 Kofi55533/siedler-agent 6/FN_Y\n"}})
        heads = {"xc/fn-a": "1" * 40, "xc/fn-b": "2" * 40, "xc/fn-c": "3" * 40}
        with mock.patch.dict(os.environ, {"XC_IMPORT_MAX": "2"}):
            self.assertEqual([o["kind"] for o in self.imports(fake, heads)], ["FN_A"])   # one import still runs


def git(cwd, *args, env=None):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True, env=env).stdout.strip()


class ImportFlowTest(unittest.TestCase):
    """import.sh against local repos: the main project, the pool's results repo with a snapshot and branch xc/fn-a"""

    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        t = self.t
        # main project: clean/integration with two commits
        git(t, "init", "-q", "-b", "clean/integration", "main")
        m = t / "main"
        (m / "crates/s5clean/src").mkdir(parents=True)
        (m / "crates/s5clean/src/lib.rs").write_text("pub fn a() -> u32 { 1 }\n\npub fn b() -> u32 { 2 }\n")
        (m / "README").write_text("x\n")
        git(m, "add", "-A"); git(m, "commit", "-qm", "c1")
        (m / "docs").mkdir()
        (m / "docs/n.md").write_text("n\n")
        git(m, "add", "-A"); git(m, "commit", "-qm", "c2")
        # results repo: snapshot of c2's tree, then the pool's one commit on branch xc/fn-a
        git(t, "init", "-q", "--bare", "res.git")
        git(t / "res.git", "config", "uploadpack.allowFilter", "true")
        git(m, "config", "uploadpack.allowFilter", "true")
        git(t, "clone", "-q", "main", "pool")
        p = t / "pool"
        tree = git(p, "rev-parse", "HEAD^{tree}")
        snap = git(p, "commit-tree", tree, "-m", "snapshot")
        git(p, "checkout", "-q", "-B", "xc/fn-a", snap)
        (p / "crates/s5clean/src/lib.rs").write_text("pub fn a() -> u32 { 41 + 1 }\n\npub fn b() -> u32 { 2 }\n")
        (p / "crates/s5xcheck/src/functions").mkdir(parents=True)
        (p / "crates/s5xcheck/src/functions/fn_a.rs").write_text("// binding\n")
        git(p, "add", "-A"); git(p, "commit", "-qm", "xc: FN_A")
        git(p, "push", "-q", str(t / "res.git"), f"{snap}:refs/heads/clean/integration", "xc/fn-a")
        self.head = git(p, "rev-parse", "HEAD")
        # main moves on (another file) after the pool's snapshot: the change still applies
        (m / "crates/s5clean/src/lib.rs").write_text("pub fn a() -> u32 { 1 }\n\npub fn b() -> u32 { 2 + 0 }\n")
        git(m, "commit", "-qam", "c3")
        # the job's checkout: shallow, as s5-setup.sh clones
        git(t, "clone", "-q", "--depth", "1", "-b", "clean/integration", f"file://{m}", "s5/siedler-sim-rs")
        # GitHub URLs -> the local repos; gh records its calls
        gc = t / "gitconfig"
        gc.write_text(f'[url "file://{t}/main/.git"]\n\tinsteadOf = https://x-access-token:tok@github.com/Kofi55533/siedler-sim-rs.git\n'
                      f'[url "file://{t}/res.git"]\n\tinsteadOf = https://github.com/Dom/results.git\n'
                      f'[protocol "file"]\n\tallow = always\n')
        (t / "bin").mkdir()
        (t / "bin/gh").write_text(f'#!/bin/sh\necho "$@" >> {t}/gh.log\n')
        (t / "bin/gh").chmod(0o755)
        (t / "local.sh").write_text(f"S5={t}/s5\nS5_REPO={t}/s5/siedler-sim-rs\n")
        # worker.sh stand-in: what worker.sh does around its model round (checkout the base, the model in $W, verdict)
        (t / "worker.sh").write_text(f"""#!/usr/bin/env bash
cd {t}/s5/siedler-sim-rs && git checkout -q -B xc/fn-a origin/clean/integration
mkdir -p {t}/w && ln -sfn {t}/s5/siedler-sim-rs {t}/w/siedler-sim-rs
for round in $(seq 1 "$XC_ROUNDS"); do ( cd {t}/w && opencode run -m x "prompt" ) > {t}/model-$round.log 2>&1; done
( cd {t}/w && opencode run -m x --continue "resolve" ) > {t}/model-conflict.log 2>&1
git status --porcelain --untracked-files=all > {t}/status.txt
echo "rounds=$XC_ROUNDS bundle=$XC_BUNDLE" > {t}/worker.txt
""")
        (t / "worker.sh").chmod(0o755)
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": str(gc), "PATH": f"{t}/bin:{os.environ['PATH']}",
                    "XC_LOCAL": str(t / "local.sh"), "XC_OWN_RESULTS": "Dom/results", "GH_TOKEN": "tok",
                    "XC_WORKER": str(t / "worker.sh"), "XC_IMPORT_DIR": str(t), "XC_AGENT": "Kofi55533/siedler-agent"}

    def run_import(self, sha):
        return subprocess.run([str(ROOT / "scripts/xc_own/import.sh"), "FN_A", sha], env=self.env,
                              capture_output=True, text=True)

    def test_the_pools_change_reaches_worker_sh_as_its_model_round(self):
        r = self.run_import(self.head)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        repo = self.t / "s5/siedler-sim-rs"
        self.assertEqual((self.t / "worker.txt").read_text().strip(), "rounds=1 bundle=1")
        # the pool's change on the moved base: its own line changed, the base's newer line kept
        self.assertEqual((repo / "crates/s5clean/src/lib.rs").read_text(),
                         "pub fn a() -> u32 { 41 + 1 }\n\npub fn b() -> u32 { 2 + 0 }\n")
        self.assertEqual(sorted(l[3:] for l in (self.t / "status.txt").read_text().splitlines()),
                         ["crates/s5clean/src/lib.rs", "crates/s5xcheck/src/functions/fn_a.rs"])
        self.assertIn("applied the pool's change", (self.t / "model-1.log").read_text())
        self.assertIn("applied already", (self.t / "model-conflict.log").read_text())   # a second call changes nothing
        self.assertNotIn("tok", (repo / ".git/config").read_text())   # no credential in the model's checkout
        self.assertFalse((self.t / "gh.log").exists())   # no claim released: worker.sh owns it now

    def test_a_moved_branch_is_skipped_without_an_attempt(self):
        r = self.run_import("0" * 40)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("moved", r.stdout)
        self.assertFalse((self.t / "worker.txt").exists())
        self.assertIn("issues", (self.t / "gh.log").read_text())   # unclaim reads the tracking issues

    def test_a_change_that_no_longer_applies_is_skipped(self):
        m = self.t / "main"
        (m / "crates/s5clean/src/lib.rs").write_text("pub fn a() -> u32 { 7 }\n\npub fn b() -> u32 { 2 + 0 }\n")
        git(m, "commit", "-qam", "c4")
        r = self.run_import(self.head)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("does not apply", r.stdout)
        self.assertFalse((self.t / "worker.txt").exists())


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        (self.t / "gh").write_text("#!/bin/sh\necho REAL\n")
        (self.t / "gh").chmod(0o755)
        g = ROOT / "scripts/xc_own/guard"
        self.env = {**os.environ, "PATH": f"{g}:{self.t}:{os.environ['PATH']}", "XC_OWN_OWNER": "Dom"}

    def gh(self, *args):
        return subprocess.run(["gh", *args], env=self.env, capture_output=True, text=True)

    def test_reads_pass_writes_only_to_the_own_account(self):
        ok = [("api", "repos/Kofi55533/siedler-agent/issues?state=open&labels=xc-kind&per_page=100&page=1"),
              ("api", "repos/Kofi55533/siedler-sim-rs/pulls?state=open&per_page=100&page=1"),
              ("api", "repos/Dom/results/issues", "-f", "title=[xc] FN_A", "-f", "body=x"),
              ("api", "-X", "PUT", "repos/Dom/results/pulls/3/merge", "-f", "merge_method=squash"),
              ("api", "-X", "GET", "repos/Kofi55533/siedler-sim-rs/pulls", "-f", "state=open"),
              ("release", "download", "xc-worklist", "-R", "Kofi55533/siedler-data")]
        bad = [("api", "-X", "PATCH", "repos/Kofi55533/siedler-agent/issues/5", "-f", "body=x"),
               ("api", "repos/Kofi55533/siedler-agent/issues", "-f", "title=x"),
               ("api", "repos/Kofi55533/siedler-sim-rs/pulls", "--jq", ".number", "-f", "head=xc/fn-a"),
               ("api", "--method=DELETE", "/repos/Kofi55533/siedler-sim-rs/git/refs/heads/x"),
               ("api", "graphql", "-f", "query=mutation{}"),
               ("pr", "create", "-R", "Kofi55533/siedler-sim-rs"),
               ("release", "upload", "x")]
        for a in ok:
            self.assertEqual(self.gh(*a).stdout.strip(), "REAL", a)
        for a in bad:
            r = self.gh(*a)
            self.assertEqual((r.returncode, r.stdout), (1, ""), a)

    def test_push_hook_refuses_the_main_account(self):
        hook = ROOT / "scripts/xc_own/guard/hooks/pre-push"
        for url, rc in [("https://x-access-token:t@github.com/Kofi55533/siedler-sim-rs.git", 1),
                        ("https://github.com/Kofi55533/siedler-agent", 1),
                        ("https://x-access-token:t@github.com/Dom/results.git", 0)]:
            r = subprocess.run([str(hook), "origin", url], capture_output=True, text=True)
            self.assertEqual(r.returncode, rc, url)
            self.assertNotIn(":t@", r.stderr)


if __name__ == "__main__":
    unittest.main()
