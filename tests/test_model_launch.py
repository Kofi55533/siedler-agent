import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import autonomy as runtime

class Pulse:
    lost=threading.Event()

class LaunchTests(unittest.TestCase):
    def launch(self,text=''):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);repo=root/'private-game';repo.mkdir()
            observed={}
            def popen(args,**kw):
                observed.update(args=args,**kw)
                kw['stdout'].write(text.encode());kw['stdout'].flush()
                class Process:
                    returncode=0
                    def poll(self):return 0
                return Process()
            with patch.dict(os.environ,{'RUNNER_TEMP':td,'PWD':'/actions/checkout','GH_TOKEN':'synthetic-github-token'}),patch.object(runtime.subprocess,'Popen',side_effect=popen),patch.object(runtime,'private_record'):
                runtime.run_model('synthetic input',repo,Pulse(),1)
            return observed,repo

    def test_cli_cwd_pwd_and_explicit_directory_agree(self):
        observed,repo=self.launch()
        self.assertEqual(observed['cwd'],repo)
        self.assertEqual(observed['env']['PWD'],str(repo))
        self.assertEqual(observed['args'][2:4],['--dir',str(repo)])
        self.assertNotIn('GH_TOKEN',observed['env'])
        self.assertNotIn('--auto',observed['args'])

    def test_a_tool_refusal_is_not_a_successful_model_exit(self):
        with self.assertRaises(PermissionError):
            self.launch('permission requested: external_directory; auto-rejecting')

if __name__=='__main__':unittest.main()
