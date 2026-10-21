"""The fleet recovers from a fully idle provider backoff without external help."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import autonomy as runtime


class ControllerWakeupTests(unittest.TestCase):
    def test_empty_fleet_during_provider_backoff_needs_an_independent_wakeup(self):
        self.assertTrue(runtime.controller_needs_wakeup({'provider_hold':None,'provider_retry_after':99999},{}))
    def test_live_and_queued_producers_already_supply_completion_or_heartbeat(self):
        for name in ('fleet-worker','fleet-verify','fleet-integrate','opencode'):
            with self.subTest(name=name):
                self.assertFalse(runtime.controller_needs_wakeup({'provider_hold':None},
                    {'1':{'path':'.github/workflows/'+name+'.yml'}}))
    def test_unrelated_infrastructure_does_not_supply_a_completion_trigger(self):
        self.assertTrue(runtime.controller_needs_wakeup({'provider_hold':None},
            {'1':{'path':'.github/workflows/scoreboard.yml'},'2':{'path':'.github/workflows/fleet-control.yml'}}))
    def test_hard_provider_hold_is_not_rearmed(self):
        self.assertFalse(runtime.controller_needs_wakeup({'provider_hold':{'reason':'access refused'}},{}))
