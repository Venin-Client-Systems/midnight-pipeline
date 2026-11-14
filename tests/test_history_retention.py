"""Temporary repository retention and crash replay; no installed runtime writes."""
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

import durability as D
import history_maintenance as H
from history_store import HistoryStore


def git(root, *args):
    return subprocess.check_output(['git', *map(str, args)], cwd=root, text=True, stderr=subprocess.PIPE).strip()


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'runtime'
        self.run_id = '20260101T000000Z-T1-abcdef'
        self.directory = self.root / 'runs' / self.run_id
        self.work = self.directory / 'work'
        self.work.mkdir(parents=True)
        self.project = {'id': 'fake', 'state_dir': str(self.root)}
        self.now = dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc)
        git(self.work, 'init', '--quiet')
        git(self.work, 'config', 'user.name', 'Test')
        git(self.work, 'config', 'user.email', 'test@localhost')
        (self.work / 'tracked.txt').write_text('baseline\n')
        git(self.work, 'add', '.')
        git(self.work, 'commit', '--quiet', '-m', 'Baseline')
        self.base = git(self.work, 'rev-parse', 'HEAD')
        (self.work / 'tracked.txt').write_text('candidate checkpoint\n')
        patch = git(self.work, 'diff', '--binary') + '\n'
        (self.directory / 'candidate.patch').write_text(patch)
        H.save(self.directory / 'candidate-checkpoint.json', {'base': self.base})
        H.save(self.directory / 'task.json', {'id': 'T1', 'title': 'Synthetic retention task'})
        H.save(self.directory / 'outcome.json', {'outcome': 'failed', 'summary': 'Saved rejected candidate'})
        (self.directory / 'worker.log').write_text('complete bounded test log\n')
        D.save_json(self.root / 'state.json', {})

    def test_archive_preserves_candidate_actual_work_and_unique_untracked_edits(self):
        # This edit occurred after the last checkpoint; it must not disappear.
        (self.work / 'tracked.txt').write_text('later unique tracked edit\n')
        (self.work / 'unfinished.txt').write_text('unique untracked draft\n')
        receipt = H.archive_clone(self.project, self.run_id, instant=self.now)
        self.assertEqual(receipt['status'], 'archived')
        self.assertFalse(self.work.exists())
        archive = self.root / 'archives' / self.run_id
        manifest = H.read(archive / 'manifest.json')
        self.assertEqual(manifest['working_tree_sha256'], hashlib.sha256((archive / 'working-tree.tar.gz').read_bytes()).hexdigest())
        with tarfile.open(archive / 'working-tree.tar.gz') as saved:
            self.assertEqual(saved.extractfile('tracked.txt').read(), b'later unique tracked edit\n')
            self.assertEqual(saved.extractfile('unfinished.txt').read(), b'unique untracked draft\n')
            self.assertFalse(any(name.startswith('.git') for name in saved.getnames()))
        self.assertEqual((archive / 'evidence' / 'candidate.patch').read_bytes(), (self.directory / 'candidate.patch').read_bytes())
        self.assertEqual(gzip.decompress((self.directory / 'worker.log.gz').read_bytes()), b'complete bounded test log\n')
        self.assertEqual(H.read(self.directory / 'archive.json')['status'], 'verified')

    def test_pending_and_retry_candidates_never_archive(self):
        for state in ({'pending': {'run': self.run_id}}, {'active_attempt': {'run': self.run_id}},
                      {'retry_candidates': {'T1': {'run': self.run_id}}}):
            with self.subTest(state=state):
                D.save_json(self.root / 'state.json', state)
                self.assertEqual(H.archive_clone(self.project, self.run_id, instant=self.now)['status'], 'pinned_or_absent')
                self.assertTrue(self.work.exists())

    def test_missing_checkpoint_and_recent_terminal_work_remain(self):
        (self.directory / 'candidate.patch').unlink()
        self.assertEqual(H.archive_clone(self.project, self.run_id, instant=self.now)['status'], 'missing_reconstruction_proof')
        self.assertTrue(self.work.exists())
        recent = dt.datetime(2026, 1, 15, tzinfo=dt.timezone.utc)
        self.assertEqual(H.archive_clone(self.project, self.run_id, instant=recent)['status'], 'retained')

    def test_crash_before_clone_deletion_replays_without_losing_saved_evidence(self):
        (self.work / 'untracked.txt').write_text('retain across interruption\n')
        original_rmtree = H.shutil.rmtree
        def stop_work_deletion(path, *args, **kwargs):
            if Path(path) == self.work:
                raise OSError('simulated power loss before clone reclaim')
            return original_rmtree(path, *args, **kwargs)
        with mock.patch.object(H.shutil, 'rmtree', side_effect=stop_work_deletion):
            with self.assertRaises(OSError):
                H.archive_clone(self.project, self.run_id, instant=self.now)
        self.assertTrue(self.work.exists())
        self.assertTrue((self.root / 'archives' / self.run_id / 'manifest.json').exists())
        self.assertEqual(H.archive_clone(self.project, self.run_id, instant=self.now)['status'], 'archived')
        with tarfile.open(self.root / 'archives' / self.run_id / 'working-tree.tar.gz') as saved:
            self.assertEqual(saved.extractfile('untracked.txt').read(), b'retain across interruption\n')

    def test_corrupt_archive_never_deletes_original_clone(self):
        archive = self.root / 'archives' / self.run_id
        archive.mkdir(parents=True)
        (archive / 'base.tar.gz').write_bytes(b'corrupted archive')
        with self.assertRaises(tarfile.ReadError):
            H.archive_clone(self.project, self.run_id, instant=self.now)
        self.assertTrue((self.work / 'tracked.txt').exists())

    def test_indexed_migration_is_replay_safe_and_separates_failed_work(self):
        H.maintain(self.project)
        H.maintain(self.project)
        store = HistoryStore(self.root)
        self.assertEqual(store.counters('fake')['total'], 1)
        self.assertEqual(store.counters('fake')['accepted'], 0)
        self.assertEqual(store.page_runs('fake')[0]['payload']['task_id'], 'T1')
        self.assertTrue(H.read(self.root / 'history-migration.json')['complete'])

    def test_closed_legacy_run_is_reconciled_from_real_host_event(self):
        (self.directory/'outcome.json').unlink()
        H.maintain(self.project)
        store=HistoryStore(self.root)
        self.assertEqual(store.page_runs('fake')[0]['outcome'],'in_progress')
        event={'event':'attempt_finished','run':self.run_id,'outcome':'failed','accepted':False,'summary':'Independent review requested a repair'}
        (self.root/'events.jsonl').write_text(json.dumps(event)+'\n')
        H.maintain(self.project);H.maintain(self.project)
        row=store.page_runs('fake')[0]
        self.assertEqual(row['outcome'],'failed');self.assertIn('Independent review',row['payload']['detail'])
        self.assertEqual(store.counters('fake')['total'],1)
        self.assertEqual(H.observe_run(self.root,self.run_id)['outcome'],'failed')
        self.assertFalse((self.directory/'outcome.json').exists())

    def test_event_replay_is_bounded_and_does_not_consume_partial_appends(self):
        (self.directory/'outcome.json').unlink()
        path=self.root/'events.jsonl'
        line=json.dumps({'event':'attempt_finished','run':self.run_id,'outcome':'interrupted','summary':'Host interrupted'})+'\n'
        path.write_text(json.dumps({'event':'other'})+'\n'+line[:-1])
        H.reconcile_host_events(self.project,max_events=1)
        cursor=H.read(self.root/'history-event-cursor.json');offset=cursor['offset']
        self.assertLess(offset,path.stat().st_size)
        H.reconcile_host_events(self.project)
        self.assertEqual(H.read(self.root/'history-event-cursor.json')['offset'],offset)
        with path.open('a') as f:f.write('\n')
        H.reconcile_host_events(self.project)
        self.assertEqual(H.observe_run(self.root,self.run_id)['outcome'],'interrupted')

    def test_active_without_closure_is_never_inferred_failed_and_pending_stays_pending(self):
        (self.directory/'outcome.json').unlink()
        H.save(self.directory/'worker-process.json',{'reason':'ready_for_review','code':130})
        self.assertEqual(H.observe_run(self.root,self.run_id)['outcome'],'in_progress')
        D.save_json(self.root/'state.json',{'pending':{'run':self.run_id}})
        H.save(self.directory/'history-outcome.json',{'event':{'outcome':'failed'}})
        self.assertEqual(H.observe_run(self.root,self.run_id)['outcome'],'awaiting_review')

    def test_accepted_event_alone_does_not_manufacture_commit_and_cloud_is_labelled(self):
        (self.directory/'outcome.json').unlink()
        H.save(self.directory/'history-outcome.json',{'event':{'outcome':'accepted','accepted':True}})
        H.save(self.directory/'implementation-backend.json',{'kind':'cloud'})
        row=H.observe_run(self.root,self.run_id)
        self.assertEqual(row['outcome'],'needs_evidence');self.assertEqual(row['worker_kind'],'cloud')
        H.save(self.directory/'accepted.json',{'commit':'a'*40})
        self.assertEqual(H.observe_run(self.root,self.run_id)['outcome'],'accepted')


if __name__ == '__main__':
    unittest.main()
