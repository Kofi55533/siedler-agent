"""Exercise multi-package runner behavior, using real atomic state transitions."""
import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import autonomy as runtime
import autonomy_state as core
from test_autonomy import fixture, plan


class WorkerLoopTests(unittest.TestCase):
    def test_limited_rollout_counts_both_leases_and_pending_dispatches(self):
        state=fixture()
        self.assertEqual(len(core.select(state,0,worker_limit=1)),1)
        state['units']['battle']['dispatch']={'nonce':'pending'}
        self.assertEqual(core.select(state,1,worker_limit=1),[])
        state['units']['battle'].pop('dispatch')
        self.assertTrue(core.claim_unit(state,'battle','worker','run',0))
        self.assertEqual(core.select(state,1,worker_limit=1),[])
        # A gate lease holds files, not a model-worker slot.
        state['leases']['battle']['owner']='gate:1'
        self.assertEqual(len(core.select(state,1,worker_limit=1)),1)
    def setUp(self):
        self.state = fixture()
        # Three obligations let one runner emit two PRs and preserve a third WIP.
        self.state['units']['battle']['addresses'].append('400006')
        self.state['units']['battle']['dispatch'] = {'nonce': 'start'}
        self.clock = 0
        self.seen = []

    def update(self, operation, message, **kwargs):
        return operation(self.state)

    def package(self, u, deadline):
        uid = u['id']; self.seen.append(uid)
        a = core.outstanding(self.state['units'][uid])[0]
        p = plan(f'crates/s5clean/src/part{len(self.seen)}.rs', a)
        self.assertFalse(core.reserve(self.state, uid, runtime.OWNER, p, self.clock))
        self.clock += 40 * 60
        if len(self.seen) == 3:
            self.clock = 284 * 60
            return {'outcome': 'checkpoint', 'summary': 'third package still in progress',
                    'checkpoint': {'head': 'c' * 40, 'pr': 3}}
        return {'outcome': 'ready', 'summary': 'reference-checked package',
                'pr': len(self.seen), 'head': str(len(self.seen)) * 40,
                'checkpoint': {'head': str(len(self.seen)) * 40, 'pr': len(self.seen)},
                'proofs': [{'address': a, 'reference_test': 'suite::native', 'clean_entry': 'World::tick'}]}

    def run_worker(self, package=None):
        with patch.object(runtime, 'enabled'), patch.object(runtime, 'dispatch'), \
                patch.object(runtime, 'update', side_effect=self.update), \
                patch.object(runtime, 'worker_package', side_effect=package or self.package), \
                patch.object(runtime.time, 'monotonic', side_effect=lambda: self.clock), \
                patch.object(runtime.time, 'time', side_effect=lambda: self.clock), \
                patch.dict(os.environ, {'FLEET_JOB_STARTED': '0'}):
            runtime.worker('battle', 'start')

    def test_same_runner_submits_two_separate_prs_then_preserves_checkpoint(self):
        self.run_worker()
        self.assertEqual(len(self.seen), 3)
        tickets = list(self.state['tickets'].values())
        self.assertEqual([t['pr'] for t in tickets], [1, 2])
        self.assertEqual(len({t['branch'] for t in tickets}), 2)
        self.assertTrue(all(t['status'] == 'queued' for t in tickets))
        self.assertEqual(len(self.state['leases']), 2)
        saved = self.state['units'][self.seen[-1]]
        self.assertEqual(saved['wip']['pr'], 3)
        owners = [a for u in self.state['units'].values() for a in u['addresses']]
        self.assertEqual(len(owners), len(set(owners)))
        self.assertTrue(all(self.state['units'][uid]['domain'] == 'DS01' for uid in self.seen))

    def test_a_later_provider_failure_cannot_discard_the_first_pr(self):
        def failure(u, deadline):
            if self.seen:
                self.seen.append(u['id'])
                return {'outcome': 'provider_error', 'summary': 'provider unavailable'}
            return self.package(u, deadline)
        with self.assertRaisesRegex(RuntimeError, 'prior packages preserved'):
            self.run_worker(failure)
        self.assertEqual(len(self.seen), 2)
        self.assertEqual(self.state['tickets']['1']['pr'], 1)
        self.assertEqual(self.state['tickets']['1']['status'], 'queued')
        self.assertIn('provider_retry_after', self.state)

    def test_job_setup_time_is_subtracted_from_the_300_minute_budget(self):
        self.clock = 286 * 60
        self.run_worker()
        self.assertEqual(self.seen, [])
        self.assertFalse(self.state['leases'])

    def test_next_package_cannot_take_a_pending_packages_files(self):
        self.run_worker()
        u = self.state['units'][self.seen[-1]]
        self.assertTrue(core.claim_unit(self.state, u['id'], 'later', 7, 99999))
        a = core.outstanding(u)[0]
        conflicts = core.reserve(self.state, u['id'], 'later', plan('crates/s5clean/src/part1.rs', a), 99999)
        self.assertEqual(conflicts, ['crates/s5clean/src/part1.rs'])

    def test_gate_acceptance_keeps_the_next_running_package_intact(self):
        self.run_worker()
        first = self.state['tickets']['1']; first['status'] = 'verified'
        later = self.state['units'][self.seen[-1]]
        core.claim_unit(self.state, later['id'], 'later', 8, 100)
        before = copy.deepcopy(self.state['leases'][later['id']])
        core.accept(self.state, ['1'], 'd' * 40, 101)
        self.assertEqual(self.state['leases'][later['id']], before)
        self.assertEqual(self.state['tickets']['2']['status'], 'queued')
        self.assertEqual(core.dashboard(self.state)['reported_reference_verified_and_integrated'], 1)

    def test_heartbeats_coalesce_wakeups_without_sleeping_runner_jobs(self):
        with patch.object(runtime, 'update', side_effect=self.update), \
                patch.object(runtime, 'dispatch') as dispatch, \
                patch.object(runtime.time, 'time', side_effect=lambda: self.clock):
            self.clock = 1000
            runtime.wake_control(); runtime.wake_control(); runtime.wake_control()
            self.assertEqual(dispatch.call_count, 1)
            self.clock += 300
            runtime.wake_control()
            self.assertEqual(dispatch.call_count, 2)

    def test_checkpoint_context_survives_while_the_predecessor_is_still_gating(self):
        self.run_worker()
        second = self.state['units'][self.seen[1]]
        self.assertEqual(second['prior_package']['pr'], 1)
        self.assertEqual(second['parent_overview'], self.state['units']['battle']['overview'])
        self.assertEqual(second['findings'][0]['outcome'], 'ready')


if __name__ == '__main__':
    unittest.main()
