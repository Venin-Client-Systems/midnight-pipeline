import json
import copy
import fcntl
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

import performance_review as F
import performance_review_schedule as S


class Native:
    def __init__(self, specs, uid=501):
        self.uid = uid; self.values = {v['label']: copy.deepcopy(v) for v in specs.values()}; self.calls = []
        self.coalition = 42

    def __call__(self, command, **kwargs):
        self.calls.append(command); action = command[1]; label = command[-1].split('/')[-1]
        if action == 'bootout':
            self.values.pop(label, None); return subprocess.CompletedProcess(command, 0, '', '')
        if label not in self.values:
            return subprocess.CompletedProcess(command, 113, '', 'Could not find service')
        v = self.values[label]; d = v['definition']
        fields = {'path': v['path'], 'program': d['ProgramArguments'][0], 'working directory': d['WorkingDirectory'],
                  'stdout path': d['StandardOutPath'], 'stderr path': d['StandardErrorPath'], 'spawn type': 'daemon (3)'}
        text = command[-1] + ' = {\n' + ''.join('\t' + k + ' = ' + value + '\n' for k, value in fields.items())
        text += '\targuments = {\n' + ''.join('\t\t' + a + '\n' for a in d['ProgramArguments']) + '\t}\n'
        text += '\tenvironment = {\n' + ''.join('\t\t' + k + ' => ' + val + '\n' for k, val in d['EnvironmentVariables'].items()) + '\t}\n'
        text += '\tresource coalition = {\n\t\tID = ' + str(self.coalition) + '\n\t}\n}\n'
        return subprocess.CompletedProcess(command, 0, text, '')


class ScheduleTests(unittest.TestCase):
    def fixture(self, directory):
        specs = S.specifications('/root', directory / 'week', '/python', directory)
        saved = {'uid': 501, 'schedules': {k: {n: v for n, v in s.items() if n != 'raw'} for k, s in specs.items()}}
        for value in specs.values(): Path(value['path']).write_bytes(value['raw'])
        return specs, saved, Native(specs)

    def test_collection_and_review_use_separate_finite_observers(self):
        specs = S.specifications('/root', '/root/runtime/performance-reviews/glm-week', '/python', '/agents')
        self.assertEqual(set(specs), {'observe', 'review'})
        for mode, interval in [('observe', 60), ('review', 300)]:
            value = plistlib.loads(specs[mode]['raw'])
            self.assertEqual(value['StartInterval'], interval)
            self.assertEqual(value['ProgramArguments'][-1], mode)
            self.assertNotIn('KeepAlive', value)
            self.assertNotIn('Shell', value)
        self.assertNotEqual(specs['observe']['label'], specs['review']['label'])

    def test_unsafe_identifier_refused(self):
        with self.assertRaises(ValueError):
            S.specifications('/root', '/root/runtime/performance-reviews/has spaces', '/python', '/agents')

    def test_replaced_schedule_never_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            path = directory / 'foreign.plist'; path.write_bytes(b'foreign')
            saved = {'schedules': {'observe': {'path': str(path), 'sha256': F.digest(b'original')}}}
            run = Mock(); spawn = Mock()
            with self.assertRaises(ValueError):
                S.retire(directory, saved, run=run, spawn=spawn)
            self.assertEqual(path.read_bytes(), b'foreign'); run.assert_not_called(); spawn.assert_not_called()

    def test_retirement_reconciles_partial_unlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve(); specs, saved, run = self.fixture(directory)
            F.write_once(directory / 'retirement-intent.json', {'schedule_sha256': F.digest(F.encoded(saved))})
            Path(specs['observe']['path']).unlink()
            spawn = Mock()
            result = S.retire(directory, saved, run=run, spawn=spawn)
            self.assertEqual(result['state'], 'retirement_requested')
            self.assertFalse((directory / 'schedule-retired.json').exists())
            self.assertFalse(Path(specs['review']['path']).exists())
            self.assertIn(specs['review']['label'], spawn.call_args.args[0][-1])

    def test_matching_plist_does_not_authorise_foreign_loaded_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve(); specs, saved, run = self.fixture(directory)
            run.values[specs['review']['label']]['definition']['ProgramArguments'][-1] = 'foreign-command'
            spawn = Mock()
            with self.assertRaises(ValueError): S.retire(directory, saved, run=run, spawn=spawn)
            self.assertTrue(Path(specs['observe']['path']).exists())
            self.assertFalse(any(c[1] == 'bootout' for c in run.calls)); spawn.assert_not_called()

    def test_failed_self_unload_retries_same_definition_then_confirms_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve(); specs, saved, run = self.fixture(directory)
            with self.assertRaises(OSError):
                S.retire(directory, saved, run=run, spawn=Mock(side_effect=OSError('spawn failed')))
            self.assertFalse((directory / 'schedule-retired.json').exists())
            spawn = Mock(); self.assertEqual(S.retire(directory, saved, run=run, spawn=spawn)['state'], 'retirement_requested')
            spawn.assert_called_once(); run.values.clear()
            self.assertTrue(S.retire(directory, saved, run=run, spawn=spawn)['native_exit_verified'])

    def test_broken_foreign_plist_link_survives_pending_retirement(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve(); specs, saved, run = self.fixture(directory)
            F.write_once(directory / 'retirement-intent.json', {'schedule_sha256': F.digest(F.encoded(saved))})
            path = Path(specs['review']['path']); path.unlink(); path.symlink_to(directory / 'missing-foreign-target')
            with self.assertRaises(ValueError): S.retire(directory, saved, run=run, spawn=Mock())
            self.assertTrue(path.is_symlink()); self.assertFalse(any(c[1] == 'bootout' for c in run.calls))

    def test_shared_retirement_lock_prevents_duplicate_native_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve(); specs, saved, run = self.fixture(directory)
            with (directory / '.retirement.lock').open('a') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(S.retire(directory, saved, run=run, spawn=Mock())['state'], 'busy')
            self.assertEqual(run.calls, [])

    def test_observer_never_performs_native_retirement(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve(); (directory / 'retirement-intent.json').touch()
            with patch.object(S, 'ownership', return_value={}), patch.object(S, 'retire') as retire:
                self.assertEqual(S.tick(directory, directory, 'observe')['state'], 'retirement_pending')
            retire.assert_not_called()

    def test_rebooted_exact_service_generation_can_be_retired(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve(); specs, saved, run = self.fixture(directory)
            with patch.object(S.D, 'boot_id', return_value='old-boot'):
                S.retire(directory, saved, run=run, spawn=Mock())
            run.coalition = 99
            with patch.object(S.D, 'boot_id', return_value='new-boot'):
                self.assertEqual(S.retire(directory, saved, run=run, spawn=Mock())['state'], 'retirement_requested')
            self.assertEqual(len(list((directory / 'retirement-requests').glob('review-*.json'))), 2)

    def test_notification_is_local_once_and_not_visible_delivery_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve(); run = Mock(side_effect=OSError('Unavailable'))
            S.notify(directory, 1, 'completed', run=run); S.notify(directory, 1, 'completed', run=run)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(F.unseal(directory / 'notifications/day-01.json')['visible_delivery'], 'unverified')

    def test_review_wait_does_not_call_paid_reviewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            with patch.object(S, 'ownership', return_value={}), patch.object(F, 'Review') as observer, \
                    patch('performance_review_cloud.review_status', return_value={'spent_days': [], 'complete': False}), \
                    patch('performance_review_cloud.reconcile_due'), \
                    patch('performance_review_cloud.review_due') as reviewer:
                observer.return_value.status.return_value = {'complete': False}
                self.assertEqual(S.tick(directory, directory, 'review')['state'], 'waiting')
                reviewer.assert_not_called()

    def test_minute_collection_never_calls_cloud(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            with patch.object(S, 'ownership', return_value={}), patch.object(F, 'Review') as observer, \
                    patch('performance_review_cloud.review_due') as reviewer:
                observer.return_value.tick.return_value = {'complete': False}
                self.assertEqual(S.tick(directory, directory, 'observe'), {'complete': False})
                reviewer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
