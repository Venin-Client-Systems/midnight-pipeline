"""An owner merge outside Midnight is reconciled only for the exact merged head."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import project_backend as B


class ReconcileExternalMergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); product = root / 'product'; product.mkdir()
        self.s = B.load_backend(product, 'fixture'); self.s.STATE = root / 'state'; self.s.STATE.mkdir()
        (product / 'a.py').write_text('x = 1\n')
        for args in (['init', '-q'], ['config', 'user.name', 'F'], ['config', 'user.email', 'f@localhost'],
                     ['add', '.'], ['-c', 'commit.gpgsign=false', 'commit', '-qm', 'base']):
            self.git(*args)
        (product / 'a.py').write_text('x = 2\n'); self.git('add', '.'); self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'merged fix')
        self.head = self.git('rev-parse', 'HEAD'); self.git('update-ref', 'refs/remotes/origin/main', self.head)
        self.task = {'id': 'Mrepair', 'files': ['a.py'], 'acceptance': ['fixed'], 'finding_ids': ['json_escape'], 'kind': 'repair'}
        recovery = mock.Mock(); recovery.tasks.return_value = [self.task]
        engine = mock.Mock(); engine.findings.return_value = []
        for target, name, kwargs in [(self.s.MR, 'ManagerRecovery', {'return_value': recovery}),
                                     (self.s, 'quality_engine', {'return_value': engine}),
                                     (self.s, 'reconcile_quality_acceptance', {}),
                                     (self.s, 'all_tasks', {'return_value': []})]:
            p = mock.patch.object(target, name, **kwargs); self.mocks = getattr(self, 'mocks', {}); self.mocks[name] = p.start(); self.addCleanup(p.stop)

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.s.ROOT, text=True, stderr=subprocess.DEVNULL).strip()

    def test_exact_merged_head_is_reconciled_through_the_pipeline_path(self):
        result = self.s.reconcile_external_merge('Mrepair', self.head)
        self.assertEqual(result['status'], 'reconciled'); self.assertEqual(result['open_material_findings'], [])
        task, state, config, directory, commit = self.mocks['reconcile_quality_acceptance'].call_args.args
        self.assertEqual(task['id'], 'Mrepair'); self.assertEqual(commit, self.head)
        self.assertTrue(directory.name.startswith('external-merge-Mrepair-'))
        events = [json.loads(l) for l in (self.s.STATE / 'events.jsonl').read_text().splitlines()]
        self.assertEqual(events[-1]['event'], 'external_merge_reconciled')

    def test_head_must_be_the_merged_commit(self):
        with self.assertRaisesRegex(ValueError, 'Check out the merged commit'):
            self.s.reconcile_external_merge('Mrepair', 'b' * 40)
        self.mocks['reconcile_quality_acceptance'].assert_not_called()

    def test_dirty_checkout_is_refused(self):
        (self.s.ROOT / 'a.py').write_text('x = 3\n')
        with self.assertRaisesRegex(ValueError, 'local changes'):self.s.reconcile_external_merge('Mrepair', self.head)

    def test_commit_must_be_on_origin_main(self):
        self.git('update-ref', 'refs/remotes/origin/main', self.git('rev-parse', 'HEAD~1'))
        with self.assertRaisesRegex(ValueError, 'not on origin/main'):self.s.reconcile_external_merge('Mrepair', self.head)

    def test_unknown_task_and_malformed_commit_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'Unknown retained task'):self.s.reconcile_external_merge('nope', self.head)
        with self.assertRaisesRegex(ValueError, 'exact 40-character'):self.s.reconcile_external_merge('Mrepair', 'HEAD')


if __name__ == '__main__':
    unittest.main()
