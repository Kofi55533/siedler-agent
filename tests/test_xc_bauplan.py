"""The Bauplan (scripts/xc/bauplan.py): atlas bytes become ranges per class, and bytes under an object reached through
pointer words are filed under that object's vtable class too."""
import contextlib
import gzip
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "xc"))
import bauplan  # noqa: E402


class BauplanTest(unittest.TestCase):
    def build(self, atlas, vtables):
        tmp = Path(tempfile.mkdtemp())
        with gzip.open(tmp / "atlas-x.json.gz", "wt") as f:
            json.dump(atlas, f)
        (tmp / "vt.json").write_text(json.dumps(vtables))
        with contextlib.redirect_stdout(io.StringIO()):
            bauplan.main(str(tmp / "b.json.gz"), str(tmp / "vt.json"), str(tmp / "atlas-x.json.gz"))
        return json.load(gzip.open(tmp / "b.json.gz"))

    def test_behaviour_bytes_get_their_own_class(self):
        walk = "[[+0x78]+0x0]"
        atlas = {"GGL::CSettler": {"entities": 2, "objects": {walk: {"0x700000": 2}, "[+0x90]": {"0x123": 1}}, "bytes": {
            "+0x14": {"pos.x": 2},
            f"{walk}+0x10": {"behaviors.[0].[0].speed": 2},
            f"{walk}+0x11": {"behaviors.[0].[0].speed": 2},
            f"[{walk}+0x20]+0x4": {"behaviors.[0].[0].path.len": 1},
            "[+0x90]+0x4": {"other": 1},
        }}}
        vtables = {"0x700000": {"class": ".?AVCWalk@GGL@@", "offset": 0, "slots": ["0x401000"]},
                   "0x710000": {"class": ".?AVCSettler@GGL@@", "offset": 0, "slots": ["0x402000"]}}
        doc = self.build(atlas, vtables)
        walk_ranges = {r["at"]: (r["size"], r["fields"]) for r in doc["classes"]["GGL::CWalk"]}
        self.assertEqual(walk_ranges, {"+0x10": (2, ["behaviors.[0].[0].speed"]),
                                       "[+0x20]+0x4": (1, ["behaviors.[0].[0].path.len"])})
        # the entity keeps its own view; a first word that is no vtable files nothing
        settler = {r["at"] for r in doc["classes"]["GGL::CSettler"]}
        self.assertIn(f"{walk}+0x10", settler)
        self.assertIn("[+0x90]+0x4", settler)
        self.assertEqual(sorted(doc["classes"]), ["GGL::CSettler", "GGL::CWalk"])
        self.assertEqual(doc["methods"]["0x401000"], ["GGL::CWalk"])

    def test_secondary_vtable_shifts_to_the_object_start(self):
        self.assertEqual(bauplan.owners("[+0x40]+0x4", {"[+0x40]": ("X::Sub", 8)}), [("X::Sub", "+0xc")])
        self.assertEqual(bauplan.owners("+0x4", {"[+0x40]": ("X::Sub", 8)}), [])


if __name__ == "__main__":
    unittest.main()
