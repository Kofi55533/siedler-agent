"""Transient upstream outages, refusals and generated lockfiles (2026-10-21 v2 fix)."""
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
    def run_sequence(self, outputs, minutes=60, delays=(0, 0, 0)):
        calls = []
        with tempfile.TemporaryDirectory() as td:
            def popen(args, **kw):
                text, code = outputs[min(len(calls), len(outputs) - 1)]
                calls.append(args[-1])
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
        for text, kind in [("Free models aren't available from your current network", PermissionError),
                           ('abusive traffic from this IP', PermissionError),
                           ('Rate limit exceeded', runtime.ProviderError),
                           ('permission requested: external_directory (/tmp/*); auto-rejecting', runtime.ToolAccessRefusal),
                           # a refusal wins over a simultaneous outage line
                           ("Free models aren't available from your current network\n" + UNAVAILABLE, PermissionError)]:
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


if __name__ == '__main__':
    unittest.main()
