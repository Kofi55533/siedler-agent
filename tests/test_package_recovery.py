"""Real source deltas survive rejected scopes; planning uses the same reservation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import autonomy as runtime
import autonomy_state as core
from test_autonomy import fixture,plan

class Pulse:
    def __init__(self,*args): pass
    def __enter__(self): return self
    def __exit__(self,*args): pass

class RecoveryTests(unittest.TestCase):
    def test_split_children_keep_findings_and_private_recovery_input(self):
        state=fixture();u=state['units']['battle']
        u['findings']=[{'outcome':'stuck','summary':'preserve native transition finding'}]
        u['recovery_patch']='autonomy/recovery/private.patch'
        core.split(state,'battle',1)
        for child in ('battle-a','battle-b'):
            self.assertEqual(state['units'][child]['recovery_patch'],u['recovery_patch'])
            self.assertEqual(state['units'][child]['findings'],u['findings'])
            self.assertIsNot(state['units'][child]['findings'],u['findings'])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.repo=Path(self.temp.name)
        self.g('init','-q');self.g('config','user.name','test');self.g('config','user.email','test@test.invalid')
        (self.repo/'base').write_text('base');self.g('add','.');self.g('commit','-qm','base')
        self.base=self.g('rev-parse','HEAD');self.state=fixture();core.claim_unit(self.state,'battle',runtime.OWNER,'test',1)
        self.u=self.state['units']['battle'];self.calls=0;self.saved={}
        real_command=runtime.command
        def command(args,*a,**kw):
            if list(map(str,args))==['git','merge','--no-edit','origin/clean/integration']:
                return subprocess.CompletedProcess(args,0,'','')
            return real_command(args,*a,**kw)
        self.patches=[patch.object(runtime,'REPO',self.repo),patch.object(runtime,'Pulse',Pulse),
            patch.object(runtime,'command',side_effect=command),
            patch.object(runtime,'setup_worker_repo',return_value=self.base),patch.object(runtime,'setup_research'),
            patch.object(runtime,'external_resources',return_value=[]),
            patch.object(runtime,'private_record',side_effect=lambda p,v:self.saved.update({p:v})),
            patch.object(runtime,'update',side_effect=lambda op,*args,**kwargs:op(self.state)),
            patch.dict(os.environ,{'S5_NATIVE_SOURCE':str(self.repo/'native')})]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def g(self,*args):
        return subprocess.run(['git','-C',str(self.repo),*args],check=True,capture_output=True,text=True).stdout.strip()
    def assignment(self):return self.repo/'.fleet-input/domain-battle'
    def make_plan(self):
        p=plan('crates/s5clean/src/wanted.rs');(self.assignment()/'plan.json').write_text(json.dumps(p))
    def test_rejected_delta_is_durable_and_next_package_sees_clean_source(self):
        def model(*args,**kwargs):
            self.calls+=1
            if self.calls==1:self.make_plan();return
            dest=self.repo/'crates/s5clean/src';dest.mkdir(parents=True)
            (dest/'wanted.rs').write_text('pub fn wanted() {}\n')
            (dest/'unexpected.rs').write_text('pub fn unexpected() {}\n')
        with patch.object(runtime,'run_model',side_effect=model):
            result=runtime.worker_package(self.u,runtime.time.monotonic()+3600)
        self.assertEqual(result['outcome'],'checkpoint')
        recovery=self.state['units']['battle']['recovery_patch']
        self.assertIn('pub fn wanted()',self.saved[recovery])
        self.assertIn('pub fn unexpected()',self.saved[recovery])
        self.assertEqual(self.g('status','--porcelain'),'')
        self.assertFalse((self.repo/'crates/s5clean/src/wanted.rs').exists())
    def test_planning_timeout_replans_without_new_runner_or_permission_override(self):
        def model(*args,**kwargs):
            self.calls+=1
            if self.calls==1:raise runtime.ModelDeadline('phase budget')
            if self.calls==2:self.make_plan();return
            (self.assignment()/'result.json').write_text('{"outcome":"evidence","summary":"native case inspected"}')
        with patch.object(runtime,'run_model',side_effect=model):
            result=runtime.worker_package(self.u,runtime.time.monotonic()+3600)
        self.assertEqual(self.calls,3)
        self.assertEqual(result['outcome'],'evidence')
        self.assertIsNone(self.state['provider_hold'])
        self.assertEqual(self.state['leases']['battle']['run'],'test')
    def test_rejected_checkpoint_cannot_mask_a_provider_outage(self):
        def model(*args,**kwargs):
            self.calls+=1
            if self.calls==1:self.make_plan();return
            raise runtime.ProviderError('upstream unavailable')
        with patch.object(runtime,'run_model',side_effect=model), \
                patch.object(runtime,'checkpoint',side_effect=ValueError('scope remains rejected')):
            result=runtime.worker_package(self.u,runtime.time.monotonic()+3600)
        self.assertEqual(result['outcome'],'provider_error')
        self.assertEqual(self.calls,2)
    def test_denied_optional_operation_stays_denied_while_permitted_plan_is_used(self):
        def model(*args,**kwargs):
            self.calls+=1
            if self.calls==1:
                self.make_plan()
                raise runtime.ToolAccessRefusal('denied optional path remains denied')
            (self.assignment()/'result.json').write_text('{"outcome":"evidence","summary":"only permitted native input used"}')
        with patch.object(runtime,'run_model',side_effect=model):
            result=runtime.worker_package(self.u,runtime.time.monotonic()+3600)
        self.assertEqual(result['outcome'],'evidence')
        self.assertIsNone(self.state['provider_hold'])
        self.assertEqual(self.state['leases']['battle']['run'],'test')
