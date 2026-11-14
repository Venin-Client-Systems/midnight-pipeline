from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from history_store import HistoryStore


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = HistoryStore(self.tmp.name)

    def test_reads_are_empty_without_creating_database(self):
        self.assertEqual(self.store.query('a'), [])
        self.assertEqual(self.store.page_runs('a'), [])
        self.assertEqual(self.store.counters('a')['total'], 0)
        self.assertFalse(self.store.path.exists())

    def test_transaction_rollback_and_event_replay(self):
        with self.assertRaises(RuntimeError):
            with self.store.transaction() as con:
                self.store.append('attempt', 'a', {'value': 1}, event_id='rollback', connection=con)
                raise RuntimeError('simulated interruption')
        self.assertEqual(self.store.query('a'), [])
        self.store.append('attempt', 'a', {'value': 2}, event_id='once')
        self.store.append('attempt', 'a', {'value': 2}, event_id='once')
        with self.assertRaises(ValueError):
            self.store.append('attempt', 'a', {'value': 3}, event_id='once')
        with self.assertRaises(ValueError):
            self.store.append('attempt', 'a', {'value': 2}, event_id='once', episode_id='changed')
        self.assertEqual(len(self.store.query('a')), 1)

    def test_concurrent_unique_receipts_and_project_isolation(self):
        self.store.append('start', 'a', {})
        def write(index):
            self.store.record_run('a', str(index), {'outcome': 'failed', 'worker_kind': 'local'})
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, range(20)))
        self.store.record_run('b', '1', {'outcome': 'accepted', 'accepted_commit': 'b1', 'worker_kind': 'cloud'})
        self.assertEqual(self.store.counters('a')['total'], 20)
        self.assertEqual(self.store.counters('b')['accepted'], 1)
        self.assertEqual(len(self.store.page_runs('a', 5)), 5)

    def test_terminal_accepted_receipts_are_protected(self):
        with self.assertRaises(ValueError):
            self.store.record_run('a', '1', {'outcome': 'accepted'})
        self.store.record_run('a', '1', {'outcome': 'pending'})
        self.store.record_run('a', '1', {'outcome': 'accepted', 'accepted_commit': 'abc'})
        with self.assertRaises(ValueError):
            self.store.record_run('a', '1', {'outcome': 'failed'})
        self.assertEqual(self.store.counters('a')['accepted'], 1)

    def test_bounded_queries_and_git_audit(self):
        self.store.log_git_event('a', 'promote', {'head': 'a', 'outcome': 'accepted'})
        for limit in (0, -1, 501, True):
            with self.assertRaises(ValueError):
                self.store.query('a', limit=limit)
        self.assertEqual(self.store.query('a', kind='git')[0]['payload']['operation'], 'promote')

    def test_100000_runs_over_36_months_use_indexed_bounded_queries(self):
        """Durable history scale rehearsal, not a claim of live run throughput."""
        start = 1609459200
        interval = 36 * 30 * 86400 / 100000
        begun = time.monotonic()
        with self.store.transaction() as con:
            for index in range(100000):
                accepted = index % 4 == 0
                payload = {'outcome': 'accepted' if accepted else 'failed',
                           'worker_kind': 'cloud' if index % 10 == 0 else 'local',
                           'duration_seconds': 30, 'accepted_commit': f'commit-{index}' if accepted else None}
                self.store.record_run('scale', f'run-{index:06d}', payload,
                                      created_at=start + index * interval, connection=con)
        self.assertEqual(self.store.counters('scale')['total'], 100000)
        self.assertEqual(self.store.counters('scale')['accepted'], 25000)
        page = self.store.page_runs('scale', 50)
        self.assertEqual(len(page), 50)
        self.assertEqual(page[0]['run_id'], 'run-099999')
        next_page = self.store.page_runs('scale', 50, before=(page[-1]['created_at'], page[-1]['run_id']))
        self.assertEqual(next_page[0]['run_id'], 'run-099949')
        cutoff = start + (36 * 30 - 7) * 86400
        self.assertLess(self.store.counters('scale', since=cutoff)['total'], 700)
        with self.store.reader() as con:
            plan = con.execute('EXPLAIN QUERY PLAN SELECT * FROM qc_runs WHERE project_id=? ORDER BY created_at DESC,run_id DESC LIMIT 50', ('scale',)).fetchall()
            self.assertTrue(any('qc_runs_project_time' in row['detail'] for row in plan), plan)
        # Generous guard catches accidental history rescans per insertion.
        self.assertLess(time.monotonic() - begun, 45)


if __name__ == '__main__':
    unittest.main()
