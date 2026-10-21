"""Use real Git to pin the shallow Actions checkout and workspace input boundary."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import autonomy as runtime

class SetupTests(unittest.TestCase):
    def g(self,repo,*args):
        return subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,check=True).stdout.strip()

    def test_adopted_wip_has_common_history_after_shallow_actions_setup(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);remote=root/'remote.git';source=root/'source';repo=root/'repo'
            subprocess.run(['git','init','--bare','-q',remote],check=True)
            source.mkdir();self.g(source,'init','-q');self.g(source,'config','user.name','test');self.g(source,'config','user.email','test@test.invalid')
            (source/'a').write_text('base');self.g(source,'add','.');self.g(source,'commit','-qm','base');base=self.g(source,'rev-parse','HEAD')
            (source/'a').write_text('integration');self.g(source,'commit','-qam','integration');self.g(source,'remote','add','origin',str(remote));self.g(source,'push','-q','origin','HEAD:refs/heads/clean/integration')
            self.g(source,'checkout','--detach',base);(source/'b').write_text('wip');self.g(source,'add','.');self.g(source,'commit','-qm','wip');self.g(source,'push','-q','origin','HEAD:refs/heads/agent/subsystem-example')
            subprocess.run(['git','clone','-q','--depth','1','--no-single-branch','--branch','clean/integration',remote.as_uri(),repo],check=True)
            self.assertEqual(self.g(repo,'rev-parse','--is-shallow-repository'),'true')
            real=runtime.command
            def command(args,*a,**kw):
                if list(map(str,args[:3]))==['git','remote','set-url']:
                    return subprocess.CompletedProcess(args,0,'','')
                return real(args,*a,**kw)
            with patch.object(runtime,'REPO',repo),patch.object(runtime,'command',side_effect=command),patch.object(runtime,'author_environment',return_value=os.environ.copy()):
                tip=runtime.setup_worker_repo({'branch':'agent/subsystem-example'})
                self.assertEqual(self.g(repo,'merge-base',tip,'HEAD'),base)
                self.assertEqual(self.g(repo,'rev-parse','--is-shallow-repository'),'false')

    def test_same_runner_can_set_up_an_unpushed_unit_branch_again(self):
        # Runs 39635976569 and 39626274937: a stuck package left its local
        # branch behind; the next claim of that unit failed with git exit 128.
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);remote=root/'remote.git';source=root/'source';repo=root/'repo'
            subprocess.run(['git','init','--bare','-q',remote],check=True)
            source.mkdir();self.g(source,'init','-q');self.g(source,'config','user.name','test');self.g(source,'config','user.email','test@test.invalid')
            (source/'a').write_text('base');self.g(source,'add','.');self.g(source,'commit','-qm','base')
            self.g(source,'remote','add','origin',str(remote));self.g(source,'push','-q','origin','HEAD:refs/heads/clean/integration')
            subprocess.run(['git','clone','-q','--depth','1','--no-single-branch','--branch','clean/integration',remote.as_uri(),repo],check=True)
            real=runtime.command
            def command(args,*a,**kw):
                if list(map(str,args[:3]))==['git','remote','set-url']:
                    return subprocess.CompletedProcess(args,0,'','')
                return real(args,*a,**kw)
            with patch.object(runtime,'REPO',repo),patch.object(runtime,'command',side_effect=command),patch.object(runtime,'author_environment',return_value=os.environ.copy()):
                unit={'branch':'agent/unit-battle-e01'}
                first=runtime.setup_worker_repo(unit)
                self.assertEqual(runtime.setup_worker_repo(unit),first)
                self.assertEqual(self.g(repo,'rev-parse','--abbrev-ref','HEAD'),'agent/unit-battle-e01')
                self.assertEqual(self.g(repo,'rev-parse','HEAD'),first)

    def test_inputs_are_in_the_existing_workspace_and_cannot_enter_git_changes(self):
        with tempfile.TemporaryDirectory() as td:
            repo=Path(td);self.g(repo,'init','-q')
            with patch.object(runtime,'REPO',repo):
                inputs=runtime.worker_inputs();(inputs/'plan.json').write_text('{}')
                self.assertTrue(inputs.is_relative_to(repo))
                self.assertEqual(self.g(repo,'status','--porcelain'),'')
                runtime.worker_inputs()
                self.assertEqual((repo/'.git/info/exclude').read_text().count('/.fleet-input/'),1)

if __name__=='__main__': unittest.main()

class ModelLaunchTests(unittest.TestCase):
    def test_cli_and_environment_use_the_actual_game_checkout_without_permission_override(self):
        import threading
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);directory=root/'game';directory.mkdir()
            process=type('Process',(),{'returncode':0,'poll':lambda self:0})()
            pulse=type('Pulse',(),{'lost':threading.Event()})()
            with patch.dict(os.environ,{'RUNNER_TEMP':td,'PWD':'/different/actions/checkout','GH_TOKEN':'synthetic'}), \
                    patch.object(runtime.subprocess,'Popen',return_value=process) as popen, \
                    patch.object(runtime,'private_record'):
                runtime.run_model('synthetic',directory,pulse,1)
            args=popen.call_args
            self.assertEqual(args.args[0][2:4],['--dir',str(directory)])
            self.assertEqual(args.kwargs['env']['PWD'],str(directory))
            self.assertNotIn('GH_TOKEN',args.kwargs['env'])
            self.assertNotIn('--auto',args.args[0])
            self.assertNotIn('OPENCODE_PERMISSION',args.kwargs['env'])

    def test_tool_refusal_cannot_be_reported_as_a_successful_model_call(self):
        import threading
        with tempfile.TemporaryDirectory() as td:
            process=type('Process',(),{'returncode':0,'poll':lambda self:0})()
            pulse=type('Pulse',(),{'lost':threading.Event()})()
            def denied(*args,**kwargs):
                kwargs['stdout'].write(b'permission requested: external_directory; auto-rejecting')
                kwargs['stdout'].flush()
                return process
            with patch.dict(os.environ,{'RUNNER_TEMP':td}), \
                    patch.object(runtime.subprocess,'Popen',side_effect=denied), \
                    patch.object(runtime,'private_record'),self.assertRaises(PermissionError):
                runtime.run_model('synthetic',Path(td),pulse,1)
