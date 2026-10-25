"""The scenarios a job plays for a kind (scripts/xc/xc.py scenarios/failing): the late bc windows only when the kind
shows in no other scenario."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "xc"))
import xc  # noqa: E402


def sc(entries, samples, failing):
    return {"entries": entries, "samples": samples, "failing": failing}


class ScenariosTest(unittest.TestCase):
    def setUp(self):
        self.wl = Path(tempfile.mkdtemp()) / "wl.json"
        self.wl.write_text(json.dumps({"kinds": {
            "FN_A": {"scenarios": {"bc-8000": sc(9, 9, 9), "x": sc(3, 3, 1), "y": sc(2, 2, 0), "bc-12000": sc(1, 1, 0)}},
            "FN_B": {"scenarios": {"bc-8000": sc(9, 9, 9), "y": sc(2, 2, 0), "bc-12000": sc(1, 1, 0)}},
            "FN_C": {"scenarios": {"bc-8000": sc(9, 9, 9), "bc-12000": sc(1, 1, 0)}},
            "FN_D": {"unbound": True, "scenarios": {"bc-8000": sc(9, 0, 0), "x": sc(1, 0, 0)}},
            "FN_E": {"unbound": True, "scenarios": {"bc-8000": sc(9, 0, 0)}}}}))

    def run_(self, kind, only_failing=False):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            xc.scenarios(self.wl, kind, only_failing)
        return out.getvalue().split()

    def test_late_windows_only_when_nothing_else_shows_the_kind(self):
        self.assertEqual(self.run_("FN_A"), ["x", "y"])
        self.assertEqual(self.run_("FN_A", True), ["x"])
        self.assertEqual(self.run_("FN_B"), ["bc-8000", "y"])
        self.assertEqual(self.run_("FN_B", True), ["bc-8000"])
        self.assertEqual(self.run_("FN_C"), ["bc-8000", "bc-12000"])
        self.assertEqual(self.run_("FN_D"), ["x"])
        self.assertEqual(self.run_("FN_E"), ["bc-8000"])


if __name__ == "__main__":
    unittest.main()
