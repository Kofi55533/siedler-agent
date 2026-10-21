"""Fresh complete reservations and immutable state caching under API pressure."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fleet_state as fs


def page(number=1, more=False, many_files=False):
    return {"data": {"repository": {"pullRequests": {
        "nodes": [{"number": number, "files": {"nodes": [{"path": f"file{number}.rs"}],
                  "pageInfo": {"hasNextPage": many_files}}}],
        "pageInfo": {"hasNextPage": more, "endCursor": str(number)}}}}}


class ReservationReadTests(unittest.TestCase):
    def test_active_capacity_includes_every_live_status_and_deduplicates_runs(self):
        with patch.object(fs,'paged',return_value=[{'id':123}]) as paged:
            self.assertEqual(fs.active_runs('synthetic/repo'),{'123':{'id':123}})
        self.assertEqual(paged.call_count,5)
        self.assertTrue(any('status=pending' in c.args[0] for c in paged.call_args_list))
    def test_normal_fleet_needs_one_graphql_call_and_no_per_pr_reads(self):
        with patch.object(fs, "api", return_value=page()) as api, patch.object(fs, "paged") as paged:
            self.assertEqual(fs.open_pr_resources(), [{"number": 1, "files": ["file1.rs"]}])
        self.assertEqual(api.call_count, 1)
        self.assertEqual(api.call_args.args[:2], ("graphql", "POST"))
        paged.assert_not_called()

    def test_both_pr_and_file_connections_must_be_complete(self):
        with patch.object(fs, "api", side_effect=[page(more=True), page(2,many_files=True)]) as api, \
                patch.object(fs, "paged", return_value=[{"filename": "big.rs"}]) as paged:
            self.assertEqual(fs.open_pr_resources(), [
                {"number": 1, "files": ["file1.rs"]}, {"number": 2, "files": ["big.rs"]}])
        self.assertEqual(api.call_args.args[2]["variables"]["after"], "1")
        self.assertIn("/pulls/2/files", paged.call_args.args[0])

    def test_incomplete_or_failed_reads_cannot_admit_an_empty_scope(self):
        for response in ({"errors": [{"message": "rate limited"}]}, {"data": {"repository": None}}):
            with self.subTest(response=response), patch.object(fs,"api",return_value=response), \
                    self.assertRaises(RuntimeError):
                fs.open_pr_resources()
        response=page(more=True);response['data']['repository']['pullRequests']['pageInfo']['endCursor']=None
        with patch.object(fs,"api",return_value=response),self.assertRaises(RuntimeError):
            fs.open_pr_resources()

    def test_state_cache_always_checks_ref_and_returns_independent_copies(self):
        store=fs.Store();heads=iter(['a','a','b']);calls=[]
        def api(path):
            calls.append(path)
            if '/git/ref/' in path:return {'object':{'sha':next(heads)}}
            return {'tree':{'sha':'tree'}}
        with patch.object(fs,'api',side_effect=api),patch.object(fs,'file_text',return_value='{"events":[]}') as file:
            state,_,_=store.read();state['events'].append('local mutation')
            self.assertEqual(store.read()[0],{'events':[]})
            store.read()
        self.assertEqual(sum('/git/ref/' in c for c in calls),3)
        self.assertEqual(file.call_count,2)
