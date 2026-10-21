"""Map-start releases supply their own image, never a fabricated fixture."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
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

    def test_actual_installer_combines_pr_alias_metadata_with_original_bundle(self):
        scripts=self.root/'scripts';shutil.copytree(Path(fixture_bundle.__file__).parent,scripts)
        repo=self.root/'repo';repo.mkdir()
        def git(*args):subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True)
        git('init','-q');git('config','user.name','test');git('config','user.email','test@test.invalid')
        dest=repo/'fixtures-gen/campaign-01-map';dest.mkdir(parents=True)
        (dest/'scenario.json').write_text(json.dumps({'name':'campaign-01-map','image':'/workspace/siedler/artifacts/new-fixtures/images/01-map/start.img'}))
        (dest/'commands.jsonl').write_text('')
        git('add','.');git('commit','-qm','fixture metadata');git('remote','add','origin',str(repo))
        bundle=self.archive/'01-map';bundle.mkdir();self.image(bundle/'start.img')
        (bundle/'scenario.json').write_text(json.dumps({'name':'01-map'}))
        archive=self.root/'bundle.tar.gz'
        with tarfile.open(archive,'w:gz') as f:f.add(bundle,arcname='01-map')
        nf=self.root/'nf';nf.mkdir()
        (scripts/'s5-env.sh').write_text(f'export S5_REPO="{repo}" S5_NF="{nf}" S5_V="{self.root / "verify"}"\n')
        bin=self.root/'bin';bin.mkdir();gh=bin/'gh'
        gh.write_text('''#!/usr/bin/env python3
import json,os,shutil,sys
from pathlib import Path
a=sys.argv[1:]
if a[0]=='api': print('[]')
elif a[:2]==['release','view']: print('bundle.tar.gz')
elif a[:2]==['release','download']:
 d=Path(a[a.index('-D')+1]);d.mkdir(exist_ok=True);shutil.copy2(os.environ['SYNTHETIC_BUNDLE'],d/'bundle.tar.gz')
else: sys.exit(1)
''');gh.chmod(0o755)
        body=self.root/'body';body.write_text('fixtures-gen-2026-10-21-01-map')
        r=subprocess.run(['bash',scripts/'s5-fixture-install.sh','campaign-01-map',body,'HEAD'],
            env={**os.environ,'PATH':str(bin)+':'+os.environ['PATH'],'SYNTHETIC_BUNDLE':str(archive)},
            capture_output=True,text=True,timeout=20)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        installed=nf/'fixtures/campaign-01-map/scenario.json'
        value=json.loads(installed.read_text())
        self.assertEqual(value['name'],'campaign-01-map')
        self.assertEqual(Path(value['image']).read_bytes(),b'synthetic image')
