"""Exercise the actual probe lifecycle with synthetic input and an explicit bridge boundary."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class DiscoveryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        bundle = self.base / 'bundle'
        (bundle / 'data').mkdir(parents=True)
        for name, value in {'catalog': dict(starts=[], lo=[], hi=[]), 'logic': [], 'union3': [],
                            'plan': {'fixtures': []}}.items():
            (bundle / 'data' / (name + '.json')).write_text(json.dumps(value))
        state = SimpleNamespace(poke_row=lambda *a: {}, gen=lambda *a: [])
        state.add_pokes = lambda path, writes: path.write_bytes(path.read_bytes() + writes)
        verify = SimpleNamespace(remap_for=lambda *a: 'synthetic')
        spec = importlib.util.spec_from_file_location('test_worker', Path(__file__).with_name('novelty_shard.py'))
        self.worker = importlib.util.module_from_spec(spec)
        args = ['worker', '--bundle', str(bundle), '--imgdir', str(self.base), '--wave', '1',
                '--shard', '0', '--nshards', '1', '--tmp', str(self.base / 'scratch')]
        old_path = sys.path[:]
        try:
            with patch.object(sys, 'argv', args), patch.dict(sys.modules, state_cmds=state, verify=verify):
                spec.loader.exec_module(self.worker)
        finally:
            sys.path[:] = old_path
        self.image = self.base / 'fork.img'
        self.image.write_bytes(b'synthetic image')
        self.calls = []
        self.sealed = self.base / 'sealed.bin'

    def invoke(self, verdict='GAPS', preserved=True, encode=True, native_error=False):
        def capture(image, commands, turns, case):
            self.calls.append('bridge')
            self.assertEqual(commands.read_bytes(), b'commands:pokes')
            self.assertEqual(image, self.image)
            self.assertEqual(turns, 50)
            self.sealed.write_bytes(commands.read_bytes())
            return dict(verdict=verdict, preserved=preserved, case_id='synthetic')

        def ingest(_source, destination):
            destination.write_bytes(b'commands:')
            return encode

        def cover(*args, **kwargs):
            self.calls.append('cover')
            self.assertTrue(self.sealed.exists())
            if native_error:
                raise OSError('native execution failed')
            return SimpleNamespace(returncode=124, stderr=''), True

        self.worker.BRIDGE = SimpleNamespace(capture=capture)
        with patch.object(self.worker, 'ingest', side_effect=ingest), \
                patch.object(self.worker, 'run_bin', side_effect=cover):
            self.worker.probe({'name': 'fixture'}, 700, self.image,
                              {'turn': 700, 'pokes': b'pokes'}, 'none', 0)

    def test_gaps_survive_native_timeout_and_scratch_cleanup(self):
        self.invoke()
        self.assertEqual(self.calls, ['bridge', 'cover'])
        self.assertEqual(self.sealed.read_bytes(), b'commands:pokes')
        self.assertEqual(list(self.worker.TMP.iterdir()), [])
        self.assertEqual(self.worker.res['probes'][0]['xcheck']['verdict'], 'GAPS')
        self.assertEqual(self.worker.res['probes'][0]['rc'], 124)

    def test_unsealed_failure_preserves_scratch_and_protects_fork(self):
        self.invoke(verdict='FAILED', preserved=False)
        self.assertIn(self.image, self.worker.preserve_images)
        commands = list(self.worker.TMP.glob('*/c.bin'))
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].read_bytes(), b'commands:pokes')

    def test_encoding_failure_is_retained_without_claiming_comparison(self):
        self.invoke(encode=False)
        self.assertEqual(self.calls, [])
        self.assertIn(self.image, self.worker.preserve_images)
        self.assertEqual(self.worker.res['probes'][0]['xcheck']['verdict'], 'NOT_ENCODED')

    def test_sealed_case_survives_native_exception(self):
        with self.assertRaisesRegex(OSError, 'native execution failed'):
            self.invoke(native_error=True)
        self.assertEqual(self.sealed.read_bytes(), b'commands:pokes')
        self.assertEqual(list(self.worker.TMP.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
