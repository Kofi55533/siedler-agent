"""Provider refusals count only on OpenCode's own error lines (2026-10-21 16:41 false hold)."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import autonomy as runtime
import autonomy_state as core
from test_autonomy import fixture

DATA = Path(__file__).resolve().parent / 'data'
GENUINE = ("\x1b[91m\x1b[1mError: \x1b[0mOpenCode's free models aren't available from your current network "
           "because we've detected abusive traffic from this IP address. This isn't related to your account.")


class RefusalScopeTests(unittest.TestCase):
    def test_grep_of_wrapper_source_in_tool_output_is_not_a_refusal(self):
        # Run 39605003708 (lua-api-next1): the model grepped scripts/autonomy.py,
        # whose refusal phrases then appeared in its tool output.
        text = (DATA / 'opencode_false_refusal_20261021.txt').read_text()
        self.assertIn('abusive traffic from this ip', text.lower())
        self.assertIsNone(runtime.classify_model_output(text, 0))

    def test_quoted_phrases_inside_file_contents_are_ignored(self):
        for text in ("461-   if 'abusive traffic from this ip' in lowered:",
                     "  Error: OpenCode's free models aren't available from your current network",
                     "notes: rate limit exceeded / invalid api key / upstream request failed: endpoint is unavailable",
                     "doc says: permission requested: x; auto-rejecting"):
            with self.subTest(text=text):
                self.assertIsNone(runtime.classify_model_output(text, 0))

    def test_genuine_opencode_refusal_line_still_triggers_the_hold(self):
        for text in (GENUINE, "> build · deepseek-v4.1-flash-free\nError: OpenCode's free models aren't available "
                              "from your current network because we've detected abusive traffic from this IP address."):
            with self.subTest(text=text[:20]):
                failure = runtime.classify_model_output(text, 1)
                self.assertIsInstance(failure, runtime.ProviderAccessRefusal)
        # and it is still detected after a long session full of tool output
        noisy = (DATA / 'opencode_false_refusal_20261021.txt').read_text() + '\n' + GENUINE + '\n'
        self.assertIsInstance(runtime.classify_model_output(noisy, 1), runtime.ProviderAccessRefusal)

    def test_genuine_error_lines_keep_their_classes(self):
        cases = [("\x1b[91m\x1b[1mError: \x1b[0mRate limit exceeded", runtime.ProviderError),
                 ("\x1b[91m\x1b[1mError: \x1b[0mUpstream request failed: Endpoint is unavailable.", runtime.UpstreamUnavailable),
                 ("\x1b[93m\x1b[1m! \x1b[0mpermission requested: external_directory (/tmp/*); auto-rejecting",
                  runtime.ToolAccessRefusal)]
        for text, kind in cases:
            with self.subTest(kind=kind.__name__):
                self.assertIsInstance(runtime.classify_model_output(text, 1), kind)

    def test_local_file_permission_error_is_not_a_provider_refusal(self):
        self.assertFalse(issubclass(PermissionError, runtime.ProviderAccessRefusal))


class RefusalHoldExpiryTests(unittest.TestCase):
    def refuse(self, state, at):
        self.assertTrue(core.claim_unit(state, 'battle', 'w', 'r', at))
        core.finish(state, 'battle', 'w', {'outcome': 'access_refusal', 'summary': 'refused'}, at)

    def test_genuine_refusal_holds_then_expires_with_exponential_backoff(self):
        state = fixture()
        self.refuse(state, 0)
        self.assertEqual(state['provider_hold']['until'], 1800)
        self.assertTrue(core.held(state, 1799))
        self.assertFalse(core.claim_unit(state, 'battle', 'w', 'r', 1799))
        self.assertEqual(core.select(state, 0, at=1799), [])
        self.assertFalse(core.held(state, 1800))
        self.refuse(state, 2000)
        self.assertEqual(state['provider_hold']['until'], 2000 + 3600)
        self.refuse(state, 6000)
        self.assertEqual(state['provider_hold']['until'], 6000 + 7200)
        for n in range(10):
            self.refuse(state, 10**6 * (n + 1))
        self.assertEqual(state['provider_hold']['until'] - state['provider_hold']['at'], core.REFUSAL_BACKOFF_CAP)

    def test_clean_step_after_expiry_resets_backoff_and_clears_hold(self):
        state = fixture()
        self.refuse(state, 0)
        self.refuse(state, 2000)
        self.assertEqual(state['provider_refusals'], 2)
        self.assertTrue(core.claim_unit(state, 'battle', 'w', 'r', 9000))
        core.finish(state, 'battle', 'w', {'outcome': 'checkpoint', 'summary': 'x'}, 9100)
        self.assertEqual(state['provider_refusals'], 0)
        self.assertIsNone(state['provider_hold'])
        self.refuse(state, 9200)
        self.assertEqual(state['provider_hold']['until'], 9200 + 1800)

    def test_session_finishing_during_hold_does_not_reset_backoff(self):
        state = fixture()
        state['leases']['battle'] = {'owner': 'w', 'run': 'r', 'heartbeat': 0, 'resources': []}
        state['provider_hold'] = {'at': 0, 'until': 1800}
        state['provider_refusals'] = 1
        core.finish(state, 'battle', 'w', {'outcome': 'checkpoint', 'summary': 'x'}, 100)
        self.assertEqual(state['provider_refusals'], 1)
        self.assertTrue(core.held(state, 100))

    def test_hold_without_expiry_stays_manual(self):
        state = fixture()
        state['provider_hold'] = {'at': 0, 'reason': 'manual'}
        self.assertTrue(core.held(state, 10**9))
        self.assertFalse(runtime.controller_needs_wakeup(state, {}))

    def test_timed_hold_rearms_the_idle_controller_so_it_can_expire(self):
        self.assertTrue(runtime.controller_needs_wakeup({'provider_hold': {'at': 0, 'until': 1800}}, {}))


if __name__ == '__main__':
    unittest.main()
