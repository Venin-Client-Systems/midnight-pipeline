"""One task's prelaunch/diagnosis hold must never stall the whole project.

Regression: a ManagerHeld raised by prepare_progress for one task escaped the
candidate loop, aborted selection for every task and was re-raised on every
tick (R005F held the project for hours).
"""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import project_backend as B


def decide(head, candidates, **_):
    for c in candidates:
        if not c.get('exhausted') and not c.get('needs_manager'):
            return {'action': 'implement_locally', 'reason': 'eligible_task', 'task': c, 'head': head}
    if any(c.get('needs_manager') for c in candidates):
        return {'action': 'needs_manager', 'reason': 'issue_episodes_require_manager', 'head': head}
    return {'action': 'wait', 'reason': 'no_useful_work', 'head': head}


class TaskScopedHoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); product = root / 'product'; product.mkdir()
        self.s = B.load_backend(product, 'fixture', settings={'source_dirs': ['src'], 'source_globs': ['src/*.py']})
        self.s.STATE = root / 'state'; self.s.STATE.mkdir(); self.s.CLOUD_RUNTIME = root / 'runtime'
        self.head = 'a' * 40
        self.instant = dt.datetime(2026, 11, 12, 10, tzinfo=dt.timezone.utc)
        self.tasks = [
            {'id': 'A', 'title': 'Needs a new test file', 'files': ['src/a.py', 'tests/test_a.py'], 'acceptance': ['a'], 'kind': 'feature'},
            {'id': 'B', 'title': 'Independent work', 'files': ['src/b.py', 'tests/test_b.py'], 'acceptance': ['b'], 'kind': 'feature'},
        ]
        self.state = {'done': [], 'accepted': [], 'planned': [], 'feedback': {}, 'tries': {}}
        self.engine = mock.Mock(profile_hash='profile')
        self.engine.status.return_value = {'material_findings': [], 'feature_allowed': True}
        self.engine.next_decision.side_effect = decide
        self.engine.issue_feature_ticket.return_value = {'id': 'ticket'}
        self.held = {'A'}  # tasks whose prepare_progress raises
        self.diagnose_held = set()
        rescue = mock.Mock(); rescue.eligibility.return_value = {'status': 'local_retries_required', 'allowed': False}
        monitor = mock.Mock(); monitor.snapshot.return_value = {}; monitor.due.return_value = False
        recovery = mock.Mock(); recovery.tasks.return_value = []
        patches = [
            (self.s, 'now', {'side_effect': lambda: self.instant}),
            (self.s, 'commissioning_binding', {'return_value': None}),
            (self.s, 'reconcile_saved_audits', {}),
            (self.s, 'git', {'side_effect': lambda *a, **k: self.head}),
            (self.s, 'quality_engine', {'return_value': self.engine}),
            (self.s.MR, 'ManagerRecovery', {'return_value': recovery}),
            (self.s.QM, 'QualityMonitor', {'return_value': monitor}),
            (self.s, 'all_tasks', {'side_effect': lambda state: [dict(t) for t in self.tasks]}),
            (self.s, 'current_repair_contract', {'return_value': True}),
            (self.s, 'task_valid', {'return_value': True}),
            (self.s, 'resume_micro_checkpoint', {'return_value': False}),
            (self.s, 'import_episode_failures', {'side_effect': lambda task, state: task['id'] + ':1'}),
            (self.s, 'protected_test_baseline', {'return_value': set()}),
            (self.s, 'protected_task_paths', {'return_value': []}),
            (self.s, 'rescue_store', {'return_value': rescue}),
            (self.s, 'unchecked_candidate', {'return_value': False}),
            (self.s, 'progress_enabled', {'return_value': True}),
            (self.s, 'prepare_progress', {'side_effect': self.prepare}),
            (self.s.PM, 'status', {'return_value': {'feature_hold': False}}),
            (self.s, 'report', {}),
        ]
        self.mocks = {}
        for target, name, kwargs in patches:
            p = mock.patch.object(target, name, **kwargs); self.mocks[name] = p.start(); self.addCleanup(p.stop)

    def prepare(self, task, state, config, cutoff, *, diagnose=False):
        if task['id'] in self.held or (diagnose and task['id'] in self.diagnose_held):
            raise self.s.ManagerHeld('Current writable recovery source is unavailable or outside its trusted boundary')
        return {'id': task['id']}, {'allowed': True, 'state': 'admitted', 'reason': 'ok'}

    def select(self):
        return self.s.next_task(self.state, {'routine_provider': 'local', 'worker_model': {'model': 'm'}},
                                self.instant + dt.timedelta(hours=3))

    def events(self):
        path = self.s.STATE / 'events.jsonl'
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    def test_held_task_is_parked_and_other_work_is_selected(self):
        selected = self.select()
        self.assertEqual(selected['id'], 'B')
        hold = self.state['task_holds']['A']
        self.assertEqual(hold['head'], self.head); self.assertIn('trusted boundary', hold['reason'])
        self.assertEqual([e['task'] for e in self.events() if e['event'] == 'task_held'], ['A'])

    def test_parked_task_is_skipped_without_reraising_or_duplicate_events(self):
        self.select(); calls = self.mocks['prepare_progress'].call_count
        self.assertEqual(self.select()['id'], 'B')
        self.assertEqual(self.mocks['prepare_progress'].call_count, calls + 1)  # only B re-prepared
        self.assertEqual(len([e for e in self.events() if e['event'] == 'task_held']), 1)

    def test_hold_expires_when_task_contract_changes(self):
        self.select(); self.held = {'B'}  # B now unavailable, so A must be eligible again
        self.tasks[0]['acceptance'] = ['a', 'and the new contract']
        self.assertEqual(self.select()['id'], 'A')

    def test_hold_expires_when_head_changes(self):
        self.select(); self.held = {'B'}; self.head = 'b' * 40
        self.assertEqual(self.select()['id'], 'A')

    def test_hold_expires_after_ttl_so_transient_causes_retry(self):
        self.select(); self.held = {'B'}
        self.instant += dt.timedelta(seconds=B.TASK_HOLD_SECONDS + 1)
        self.assertEqual(self.select()['id'], 'A')

    def test_all_tasks_held_reports_every_hold_instead_of_raising(self):
        self.held = {'A', 'B'}
        self.assertIsNone(self.select())
        args, kwargs = self.mocks['report'].call_args
        self.assertEqual(args[1], 'manager_hold'); self.assertEqual(set(kwargs['tasks']), {'A', 'B'})

    def test_local_decision_hold_keeps_project_level_semantics(self):
        self.mocks['prepare_progress'].side_effect = self.s.LocalDecisionHeld('k' * 64, 'spent')
        with self.assertRaises(self.s.LocalDecisionHeld):self.select()
        self.assertNotIn('task_holds', self.state)

    def test_unrelated_errors_still_raise(self):
        self.mocks['prepare_progress'].side_effect = KeyError('bug')
        with self.assertRaises(KeyError):self.select()

    def test_commissioning_binding_keeps_project_scoped_holds(self):
        self.mocks['commissioning_binding'].return_value = {'task_id': 'A', 'task_hash': self.s.task_fingerprint(self.tasks[0])}
        with self.assertRaises(self.s.ManagerHeld):self.select()
        self.assertNotIn('task_holds', self.state)

    def test_diagnosis_continues_past_a_held_candidate(self):
        # Both need a manager; diagnosing A raises, B is still diagnosed and admitted.
        self.held.clear(); self.diagnose_held = {'A'}
        real = self.prepare
        def prepare(task, state, config, cutoff, *, diagnose=False):
            if not diagnose:return {'id': task['id']}, {'allowed': False, 'state': 'needs_codex', 'reason': 'held'}
            return real(task, state, config, cutoff, diagnose=diagnose)
        self.mocks['prepare_progress'].side_effect = prepare
        self.assertEqual(self.select()['id'], 'B')
        self.assertIn('A', self.state['task_holds'])

    def test_micro_checkpoint_hold_parks_only_that_task(self):
        def resume(task, *a):
            if task['id'] == 'A':raise self.s.MS.MicroHeld('Microstep source, profile or task contract changed')
            return False
        self.mocks['resume_micro_checkpoint'].side_effect = resume; self.held.clear()
        self.assertEqual(self.select()['id'], 'B')
        self.assertEqual(self.state['task_holds']['A']['kind'], 'MicroHeld')


if __name__ == '__main__':
    unittest.main()
