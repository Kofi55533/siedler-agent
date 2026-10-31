"""A bundle of small function ports (scripts/xc/xc.py bundle): one job, one claim token, one verdict for all kinds."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "xc"))
import xc  # noqa: E402


def groups(path, g):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"groups": g}))


class BundleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "wl.json").write_text(json.dumps({"sha": "x", "kinds": {}}))

    def check(self, kinds):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = xc.check(self.tmp / "base", self.tmp / "new", kinds, self.tmp / "wl.json", ["s1"])
        return rc, out.getvalue()

    def test_check_needs_every_kind(self):
        groups(self.tmp / "base/full/s1.json", {"FN_A": {"entries": 3, "samples": 3, "failing": 1},
                                               "FN_B": {"entries": 2, "samples": 0, "failing": 0, "unbound": True}})
        groups(self.tmp / "new/full/s1.json", {"FN_A": {"entries": 3, "samples": 3, "failing": 0, "observed": 2}})
        groups(self.tmp / "new/only/s2.json", {"FN_B": {"entries": 2, "samples": 2, "failing": 0, "observed": 1}})
        rc, out = self.check("FN_A,FN_B")
        self.assertEqual(rc, 0, out)
        self.assertIn("FN_B: 2/2 sampled calls identical", out)
        groups(self.tmp / "new/only/s2.json", {"FN_B": {"entries": 2, "samples": 2, "failing": 1, "observed": 1}})
        rc, out = self.check("FN_A,FN_B")
        self.assertEqual(rc, 1)
        self.assertIn("FN_B still differs in s2", out)

    def test_bundled_kind_is_no_regression(self):
        groups(self.tmp / "base/full/s1.json", {"FN_A": {"entries": 3, "samples": 3, "failing": 1},
                                               "FN_B": {"entries": 2, "samples": 2, "failing": 0}})
        groups(self.tmp / "new/full/s1.json", {"FN_A": {"entries": 3, "samples": 3, "failing": 0, "observed": 2},
                                              "FN_B": {"entries": 2, "samples": 2, "failing": 1, "observed": 1}})
        self.assertIn("regression: FN_B", self.check("FN_A")[1])
        self.assertNotIn("regression", self.check("FN_A,FN_B")[1])

    def test_stub_merges_the_bundle(self):
        wl = {"sha": "x", "kinds": {
            "FN_A": {"failing": 0, "scenarios": {"s1": {"entries": 4, "samples": 0, "failing": 0}}},
            "FN_B": {"failing": 0, "scenarios": {"s1": {"entries": 2, "samples": 0, "failing": 0},
                                                 "s2": {"entries": 0, "samples": 0, "failing": 0}}}}}
        (self.tmp / "wl2.json").write_text(json.dumps(wl))
        xc.stub(self.tmp / "wl2.json", "FN_A,FN_B", self.tmp / "stub", ["s1", "s2"])
        self.assertEqual(sorted(json.loads((self.tmp / "stub/s1.json").read_text())["groups"]), ["FN_A", "FN_B"])
        self.assertFalse((self.tmp / "stub/s2.json").exists())

    def test_jobs_count_tokens(self):
        old = xc.ended
        xc.ended = lambda run, pool: False
        try:
            issues = {"a": (1, "claimed 900 r1 P tok1"), "b": (2, "claimed 900 r1 P tok1"), "c": (3, "claimed 900 r2 P"),
                      "d": (4, "claimed 900 r3 P"), "e": (5, "claimed 900 r4 Q tok9"), "f": (6, "")}
            self.assertEqual(xc.jobs(issues, 1000, "P"), 3)
            self.assertEqual(xc.jobs(issues, 1000), 4)
        finally:
            xc.ended = old


    def test_timed_out_full_run_is_no_crash(self):
        g = {"entries": 3, "samples": 3, "failing": 0, "observed": 3}
        (self.tmp / "base/full").mkdir(parents=True)
        (self.tmp / "new/full").mkdir(parents=True)
        (self.tmp / "base/full/s1.json").write_text(json.dumps({"turns": 1200, "groups": {"FN_A": g, "FN_B": g}}))
        (self.tmp / "new/full/s1.json").write_text(json.dumps({"turns": 120, "groups": {"FN_A": g}}))
        rc, out = self.check("FN_A")
        self.assertEqual(rc, 1, out)
        self.assertIn("without result after the change (crash?)", out)
        crash = self.tmp / "new/full/s1.crash"
        crash.write_text("thread 'x' panicked at\nno result in 1200 turns (exit 124): again with 120\n")
        rc, out = self.check("FN_A")
        self.assertEqual(rc, 0, out)
        self.assertIn("timed out, played 120 of 1200 turns, 1 later kinds not compared", out)
        crash.write_text("no result in 1200 turns (exit 101): again with 120\n")
        rc, out = self.check("FN_A")
        self.assertEqual(rc, 1, out)


    def test_callee_of_a_new_binding_is_no_crash(self):
        g = {"entries": 3, "samples": 3, "failing": 0, "observed": 3}
        groups(self.tmp / "base/full/s1.json", {
            "FN_A": {"entries": 1, "samples": 0, "failing": 0, "unbound": True, "callers": {"ROOT": 1}},
            "FN_M": {"entries": 2, "samples": 0, "failing": 0, "unbound": True, "callers": {"FN_A": 2}},
            "FN_N": dict(g, callers={"FN_M": 3}),
            "FN_O": dict(g, callers={"FN_A": 1, "FN_X": 2})})
        groups(self.tmp / "new/full/s1.json", {"FN_A": {"entries": 1, "samples": 1, "failing": 0, "observed": 1,
                                                       "callers": {"ROOT": 1}}})
        rc, out = self.check("FN_A")
        self.assertEqual(rc, 1, out)
        self.assertIn("1 kinds without result after the change (crash?), e.g. FN_O", out)
        groups(self.tmp / "base/full/s1.json", {
            "FN_A": {"entries": 1, "samples": 0, "failing": 0, "unbound": True, "callers": {"ROOT": 1}},
            "FN_M": {"entries": 2, "samples": 0, "failing": 0, "unbound": True, "callers": {"FN_A": 2}},
            "FN_N": dict(g, callers={"FN_M": 3})})
        rc, out = self.check("FN_A")
        self.assertEqual(rc, 0, out)

    def test_callee_of_an_unnamed_new_binding_is_no_crash(self):
        g = {"entries": 3, "samples": 3, "failing": 0, "observed": 3}
        groups(self.tmp / "base/full/s1.json", {
            "FN_A": {"entries": 1, "samples": 0, "failing": 0, "unbound": True, "callers": {"ROOT": 1}},
            "FN_B": dict(g, callers={"FN_A": 3}),
            "FN_C": dict(g, callers={"ROOT": 3})})
        groups(self.tmp / "new/full/s1.json", {
            "FN_A": {"entries": 1, "samples": 1, "failing": 0, "observed": 1, "callers": {"ROOT": 1}},
            "FN_C": dict(g, callers={"ROOT": 3})})
        rc, out = self.check("FN_C")
        self.assertEqual(rc, 0, out)
        groups(self.tmp / "new/full/s1.json", {"FN_A": {"entries": 1, "samples": 0, "failing": 0, "unbound": True,
                                                       "callers": {"ROOT": 1}}, "FN_C": dict(g, callers={"ROOT": 3})})
        rc, out = self.check("FN_C")
        self.assertEqual(rc, 1, out)


class CoveredTest(unittest.TestCase):
    def test_recursion_runs_inside_the_outer_call(self):
        fns = {"FN_V": {"samples": 2, "failing": 0, "observed": 1},
               "FN_R": {"unbound": True, "callers": {"FN_V": 3, "FN_R": 9}},  # self recursion
               "FN_A": {"unbound": True, "callers": {"FN_V": 1, "FN_B": 2}},  # A <-> B
               "FN_B": {"unbound": True, "callers": {"FN_A": 2}},
               "FN_C": {"unbound": True, "callers": {"FN_R": 1}},
               "FN_O": {"unbound": True, "callers": {"FN_O": 1, "ROOT": 1}},  # called from the turn itself
               "FN_P": {"unbound": True, "callers": {"FN_P": 4}}}  # no call from outside
        xc.covered(fns)
        self.assertEqual({k for k, v in fns.items() if v.get("covered")}, {"FN_R", "FN_A", "FN_B", "FN_C"})
        self.assertEqual(xc.fn_state(fns["FN_O"]), "port")


if __name__ == "__main__":
    unittest.main()
