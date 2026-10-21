import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import autonomy_state as state
from test_autonomy import fixture

POOL = "Dominickofi00/siedler-agent"


class PoolIsolationTests(unittest.TestCase):
    def refuse(self, s, uid, pool):
        self.assertTrue(state.claim_unit(s, uid, "o-" + uid, "9", 10, pool))
        state.finish(s, uid, "o-" + uid, {"outcome": "access_refusal", "summary": "refused"}, 11, pool)

    def test_refusal_on_extra_pool_holds_only_that_pool(self):
        s = fixture()
        self.refuse(s, "battle", POOL)
        self.assertTrue(state.held(s, 12, POOL))
        self.assertFalse(state.held(s, 12))
        self.assertFalse(state.claim_unit(s, "ai", "x", "8", 12, POOL))
        self.assertTrue(state.claim_unit(s, "ai", "y", "7", 12))
        self.assertIsNone(s.get("provider_hold"))

    def test_main_refusal_does_not_hold_extra_pool(self):
        s = fixture()
        self.refuse(s, "battle", None)
        self.assertTrue(state.held(s, 12))
        self.assertFalse(state.held(s, 12, POOL))
        self.assertTrue(state.claim_unit(s, "ai", "x", "8", 12, POOL))
        self.assertEqual(s["leases"]["ai"]["pool"], POOL)

    def test_extra_pool_outage_backoff_is_separate(self):
        s = fixture()
        self.assertTrue(state.claim_unit(s, "battle", "o", "9", 10, POOL))
        state.finish(s, "battle", "o", {"outcome": "provider_error", "summary": "x"}, 11, POOL)
        self.assertNotIn("provider_retry_after", s)
        self.assertGreater(s["pools"][POOL]["provider_retry_after"], 11)
        self.assertTrue(state.select(s, 0, at=12, worker_limit=16))

    def test_extra_pool_workers_do_not_use_main_worker_slots(self):
        s = fixture()
        self.assertTrue(state.claim_unit(s, "battle", "o", "9", 10, POOL))
        self.assertEqual(state.select(s, 0, at=12, worker_limit=1), ["ai"])


    def test_pool_in_flight_counts_leases_and_reservations_of_that_pool_only(self):
        s = fixture()
        self.assertTrue(state.claim_unit(s, "battle", "o", "9", 10, POOL))
        self.assertTrue(state.claim_unit(s, "ai", "p", "8", 10))
        uid = next(u for u in s["units"] if u not in s["leases"])
        s["units"][uid]["dispatch"] = {"nonce": "n", "at": 10, "pool": POOL}
        self.assertEqual(state.pool_in_flight(s, POOL), 2)
        self.assertEqual(state.pool_in_flight(s, "other/repo"), 0)


if __name__ == "__main__":
    unittest.main()
