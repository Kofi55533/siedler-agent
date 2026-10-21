"""Map-start releases supply their own image, never a fabricated fixture."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fixture_bundle
import gate_contract


class FixtureBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.dst=self.root/'installed/map';self.dst.mkdir(parents=True)
        self.archive=self.root/'release';self.archive.mkdir()
        self.scenario=self.dst/'scenario.json'
        self.scenario.write_text(json.dumps({'name':'01-map','image':'/workspace/siedler/artifacts/new-fixtures/images/map/start.img'}))
    def tearDown(self):self.temp.cleanup()
    def image(self,path):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'synthetic image')
        Path(str(path)+'.state.json').write_text('{"synthetic":true}')
    def resolve(self):fixture_bundle.resolve(self.dst,self.archive,self.root/'newfix',self.root/'verify')
    def test_colocated_release_image_and_state_are_used(self):
        self.image(self.dst/'start.img');self.resolve()
        self.assertEqual(Path(json.loads(self.scenario.read_text())['image']),self.dst/'start.img')
    def test_split_archive_layout_installs_matching_image_and_sidecar(self):
        self.image(self.archive/'images/map/start.img');self.resolve()
        self.assertEqual((self.dst/'start.img').read_bytes(),b'synthetic image')
        self.assertTrue((self.dst/'start.img.state.json').is_file())
    def test_missing_ambiguous_or_incomplete_image_never_passes(self):
        with self.assertRaises(ValueError):self.resolve()
        self.image(self.archive/'one/start.img');self.image(self.archive/'two/start.img')
        with self.assertRaises(ValueError):self.resolve()
        self.image(self.dst/'start.img');(self.dst/'start.img.state.json').unlink()
        with self.assertRaises(ValueError):self.resolve()
    def test_digit_prefixed_campaign_names_match_installer_contract(self):
        self.assertEqual(gate_contract.fixtures('Frontier-fixture: 01-map, 10-flood'),['01-map','10-flood'])
        with self.assertRaises(ValueError):gate_contract.fixtures('Frontier-fixture: 01-map;skip')
