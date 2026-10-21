"""Transient upstream outages, refusals and generated lockfiles (2026-10-21 v2 fix)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import autonomy as runtime
import autonomy_state as core
from test_autonomy import fixture

UNAVAILABLE = '> build · deepseek-v4.1-flash-free\nError: Upstream request failed: Endpoint is unavailable.\n'


class Pulse:
    def __init__(self):
        self.lost = threading.Event()


class UpstreamRetryTests(unittest.TestCase):
    def setUp(self):
        runtime.new_package_session()

    def run_sequence(self, outputs, minutes=60, delays=(0, 0, 0)):
        calls = []
        with tempfile.TemporaryDirectory() as td:
            def popen(args, **kw):
                text, code = outputs[min(len(calls), len(outputs) - 1)]
                calls.append(args[-1]); self.argv = getattr(self, 'argv', []) + [args]
                kw['stdout'].write(text.encode()); kw['stdout'].flush()
                class Process:
                    returncode = code
                    pid = 0
                    def poll(self): return 0
                return Process()
            with patch.dict(os.environ, {'RUNNER_TEMP': td}), \
                 patch.object(runtime.subprocess, 'Popen', side_effect=popen), \
                 patch.object(runtime, 'private_record'), \
                 patch.object(runtime, 'UPSTREAM_RETRY_DELAYS', delays):
                try:
                    runtime.run_model('task', Path(td), Pulse(), minutes)
                    return calls, None
                except Exception as error:  # noqa: BLE001 - asserted by callers
                    return calls, error

    def test_transient_outage_is_retried_on_the_same_runner_and_then_succeeds(self):
        calls, error = self.run_sequence([(UNAVAILABLE, 1), ('done', 0)])
        self.assertIsNone(error)
        self.assertEqual(len(calls), 2)
        self.assertIn('transient provider outage', calls[1])

    def test_persistent_outage_becomes_fleet_backoff_after_bounded_retries(self):
        calls, error = self.run_sequence([(UNAVAILABLE, 1)])
        self.assertIsInstance(error, runtime.ProviderError)
        self.assertNotIsInstance(error, runtime.UpstreamUnavailable)
        self.assertEqual(len(calls), 1 + len((0, 0, 0)))

    def test_no_retry_without_budget_for_the_pause(self):
        calls, error = self.run_sequence([(UNAVAILABLE, 1)], minutes=1, delays=(60, 60, 60))
        self.assertIsInstance(error, runtime.ProviderError)
        self.assertEqual(len(calls), 1)

    def test_refusals_rate_limits_and_tool_denials_are_never_retried(self):
        for text, kind in [("Error: OpenCode's free models aren't available from your current network", PermissionError),
                           ('Error: ... we have detected abusive traffic from this IP address.', PermissionError),
                           ('Error: Rate limit exceeded', runtime.ProviderError),
                           ('! permission requested: external_directory (/tmp/*); auto-rejecting', runtime.ToolAccessRefusal),
                           # a refusal wins over a simultaneous outage line
                           ("Error: OpenCode's free models aren't available from your current network\n" + UNAVAILABLE, PermissionError)]:
            with self.subTest(text=text):
                calls, error = self.run_sequence([(text, 1), ('done', 0)])
                self.assertIsInstance(error, kind)
                self.assertNotIsInstance(error, runtime.UpstreamUnavailable)
                self.assertEqual(len(calls), 1)

    def test_lost_reservation_stops_the_pause(self):
        pulse = Pulse(); pulse.lost.set()
        with tempfile.TemporaryDirectory() as td:
            def popen(args, **kw):
                kw['stdout'].write(UNAVAILABLE.encode()); kw['stdout'].flush()
                class Process:
                    returncode = 1
                    def poll(self): return 0
                return Process()
            with patch.dict(os.environ, {'RUNNER_TEMP': td}), patch.object(runtime.subprocess, 'Popen', side_effect=popen), \
                 patch.object(runtime, 'private_record'), patch.object(runtime, 'UPSTREAM_RETRY_DELAYS', (0, 0, 0)):
                # pulse.lost is checked first inside the process loop only while running;
                # here the pause itself must notice the lost reservation.
                with self.assertRaisesRegex(RuntimeError, 'reservation lost'):
                    runtime.run_model('task', Path(td), pulse, 60)


class ProviderBackoffResetTests(unittest.TestCase):
    def test_completed_model_step_resets_escalation(self):
        state = fixture()
        state['provider_errors'] = 5
        self.assertTrue(core.claim_unit(state, 'battle', 'w', 'r', 0))
        core.finish(state, 'battle', 'w', {'outcome': 'checkpoint', 'summary': 'x'}, 10)
        self.assertEqual(state['provider_errors'], 0)
        self.assertTrue(core.claim_unit(state, 'battle', 'w', 'r', 20))
        core.finish(state, 'battle', 'w', {'outcome': 'provider_error', 'summary': 'x'}, 30)
        self.assertEqual(state['provider_errors'], 1)
        self.assertEqual(state['provider_retry_after'], 30 + 900)


class GeneratedLockfileTests(unittest.TestCase):
    def test_cargo_lockfiles_never_fail_the_package_scope(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            g = lambda *a: subprocess.run(['git', *a], cwd=repo, check=True, capture_output=True, text=True).stdout
            g('init', '-q'); g('config', 'user.email', 't@t'); g('config', 'user.name', 't')
            for name in ('crates/s5clean/src/a.rs', 'crates/s5clean/Cargo.lock', 'crates/s5xcheck/Cargo.toml'):
                (repo / name).parent.mkdir(parents=True, exist_ok=True); (repo / name).write_text('old\n')
            g('add', '.'); g('commit', '-qm', 'base')
            base = g('rev-parse', 'HEAD').strip()
            (repo / 'crates/s5clean/src/a.rs').write_text('new\n')
            (repo / 'crates/s5xcheck/Cargo.lock').write_text('generated\n')   # untracked, as cargo test creates it
            (repo / 'crates/s5clean/Cargo.lock').write_text('rewritten\n')    # tracked, rewritten by cargo
            with patch.object(runtime, 'REPO', repo):
                files = runtime.scope_files(base, ['crates/s5clean/src/a.rs'])
            self.assertEqual(files, ['crates/s5clean/src/a.rs'])
            self.assertEqual((repo / 'crates/s5clean/Cargo.lock').read_text(), 'old\n')
            with patch.object(runtime, 'REPO', repo), self.assertRaises(ValueError):
                (repo / 'crates/s5xcheck/Cargo.toml').write_text('new dependency\n')
                runtime.scope_files(base, ['crates/s5clean/src/a.rs'])


class MalformedPlanTests(unittest.TestCase):
    def test_object_resources_are_plan_feedback_not_a_crash(self):
        # Run 39586506205 (2026-10-21 13:13) died with "TypeError: unhashable type: 'dict'".
        state = fixture(); u = state['units']['battle']
        good = {'goal': 'g', 'native_family': 'f', 'acceptance': 'a', 'next_step': 'n', 'minutes': 60,
                'addresses': [core.outstanding(u)[0]], 'resources': ['crates/s5clean/src/a.rs']}
        core.validate_plan(good, u)
        for bad in ([{'path': 'crates/s5clean/src/a.rs'}], 'crates/s5clean/src/a.rs', [1]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                core.validate_plan(dict(good, resources=bad), u)
        with self.assertRaises(ValueError):
            core.validate_plan(dict(good, addresses={'a': 1}), u)


class LegacyInvocationTests(unittest.TestCase):
    def test_one_session_per_package_and_legacy_permissions(self):
        runtime.new_package_session()
        seen = []
        with tempfile.TemporaryDirectory() as td:
            def popen(args, **kw):
                seen.append(list(args))
                kw['stdout'].write(b'done'); kw['stdout'].flush()
                class Process:
                    returncode = 0
                    def poll(self): return 0
                return Process()
            with patch.dict(os.environ, {'RUNNER_TEMP': td}), patch.object(runtime.subprocess, 'Popen', side_effect=popen), \
                 patch.object(runtime, 'private_record'):
                for _ in range(3):
                    runtime.run_model('step', Path(td), Pulse(), 10)
                runtime.new_package_session()
                runtime.run_model('next package', Path(td), Pulse(), 10)
            config = json.loads((Path(td) / 'opencode.json').read_text())
        self.assertNotIn('--continue', seen[0])
        self.assertTrue(all('--continue' in a for a in seen[1:3]))
        self.assertNotIn('--continue', seen[3])
        self.assertTrue(all(a[2:4] == ['--dir', td] and a[4:6] == ['-m', 'opencode/deepseek-v4.1-flash-free'] for a in seen))
        self.assertTrue(all('--auto' not in a for a in seen))
        self.assertEqual(config['permission'], {'edit': 'allow', 'bash': 'allow', 'webfetch': 'allow',
                                                'external_directory': 'allow'})

    def test_outage_retry_continues_the_same_session(self):
        runtime.new_package_session()
        t = UpstreamRetryTests('test_transient_outage_is_retried_on_the_same_runner_and_then_succeeds')
        t.setUp()
        calls, error = t.run_sequence([(UNAVAILABLE, 1), ('done', 0)])
        self.assertIsNone(error)
        self.assertNotIn('--continue', t.argv[0])
        self.assertIn('--continue', t.argv[1])

    def test_config_is_excluded_from_package_scope(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            g = lambda *a: subprocess.run(['git', *a], cwd=repo, check=True, capture_output=True, text=True).stdout
            g('init', '-q'); g('config', 'user.email', 't@t'); g('config', 'user.name', 't')
            (repo / 'a.txt').write_text('x'); g('add', '.'); g('commit', '-qm', 'b')
            with patch.object(runtime, 'REPO', repo):
                runtime.worker_inputs()
                runtime.model_config(repo)
                self.assertEqual(runtime.git('status', '--porcelain'), '')


if __name__ == '__main__':
    unittest.main()
