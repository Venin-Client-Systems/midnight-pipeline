"""Protected seed tests cannot become writable through a generated repair."""
import copy
from contextlib import ExitStack
import datetime as dt
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import project_backend as B


SEED = 'tests/test_unit_transport.py'
FINDING = {'id': 'host-check-full-regression',
           'episode_id': 'host-check-full-regression:1', 'state': 'open'}
PROPOSAL = {
    'action': 'implement',
    'title': 'Fix test assertion for exact error message in JSON transport validation',
    'description': 'Update test_invalid_json_still_raises_transport_error to use '
                   'assertEqual instead of assertRaisesRegex for the exact message.',
    'files': [SEED],
    'acceptance': ["The test asserts the exact error message 'Response was not valid JSON'",
                   'All existing tests continue to pass',
                   'The transport validation behavior remains unchanged'],
}


class ProtectedTestAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        product = self.root / 'product'
        product.mkdir()
        self.s = B.load_backend(product, 'fixture', settings={
            'source_dirs': ['src', 'tests'], 'source_globs': ['src/*.py'],
            'worker_allowed_patterns': ['src/*.py', 'tests/test_*.py'],
        })
        self.s.STATE = self.root / 'state'
        self.s.CLOUD_RUNTIME = self.root / 'runtime'
        self.s.STATE.mkdir()
        (product / 'tests').mkdir()
        (product / SEED).write_text('# immutable seed assertion\n')
        (product / 'ROADMAP.md').write_text('Preserve transport validation.\n')
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@localhost')
        self.git('add', '.')
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'seed')
        self.head = self.git('rev-parse', 'HEAD')
        self.instant = dt.datetime(2026, 11, 3, 10, tzinfo=dt.timezone.utc)
        self.cutoff = self.instant + dt.timedelta(hours=3)
        self.config = {'routine_provider': 'local', 'worker_model': {'model': 'fixture'},
                       'protected_test_files': [SEED]}
        self.state = {'done': [], 'accepted': [], 'planned': [], 'feedback': {},
                      'tries': {'Q202611030747345487': 1}, 'attempts_today': 1,
                      'local_attempts_today': 1, 'local_supervision_calls_today': 2,
                      'cloud_calls_today': 0}
        self.original = self.s.STATE / 'runs' / 'original'
        self.s.write(self.original / 'outcome.json', {
            'accepted': False, 'outcome': 'failed', 'task': 'Q202611030747345487',
            'summary': 'Changed a protected or out-of-task path: ' + SEED})
        self.original_bytes = (self.original / 'outcome.json').read_bytes()
        self.proposal = copy.deepcopy(PROPOSAL)
        self.engine = mock.Mock()
        self.engine.profile_hash = 'fixture-profile'
        self.engine.status.return_value = {'material_findings': [FINDING],
                                           'feature_allowed': False}
        self.engine.next_decision.return_value = {'action': 'wait', 'head': self.head}
        for target, name, kwargs in [
            (self.s, 'now', {'return_value': self.instant}),
            (self.s.PR, 'enabled', {'return_value': False}),
            (self.s, 'manager_context', {'return_value': {'actual_failure': SEED}}),
            (self.s, 'engineering_direction', {'return_value': 'Preserve all host gates.'}),
            (self.s, 'quality_engine', {'return_value': self.engine}),
            (self.s.PM, 'status', {'return_value': {'feature_hold': False}}),
            (self.s, 'charged_run', {'side_effect': self.supervise}),
        ]:
            patcher = mock.patch.object(target, name, **kwargs)
            patched = patcher.start()
            self.addCleanup(patcher.stop)
            if name == 'charged_run':
                self.launch = patched

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.s.ROOT,
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def supervise(self, command, directory, log, seconds, cutoff, state, charge, **kwargs):
        # Exercise the real local decision validator and charge callback without inference.
        self.assertIn('local_supervisor.py', ' '.join(command))
        charge()
        self.s.write(directory / 'result.json', self.proposal)
        self.s.write(directory / 'local-metrics.json', {'model': 'fixture'})
        log.write_text('{"type":"turn.completed"}\n')
        return {'code': 0, 'reason': 'exited', 'seconds': 1}

    def assert_history_preserved(self):
        self.assertEqual(self.state['tries'], {'Q202611030747345487': 1})
        self.assertEqual(self.state['attempts_today'], 1)
        self.assertEqual(self.state['local_attempts_today'], 1)
        self.assertEqual(self.state['cloud_calls_today'], 0)
        self.assertEqual(self.state['accepted'], [])
        self.assertEqual(self.state['done'], [])
        self.assertEqual((self.original / 'outcome.json').read_bytes(), self.original_bytes)

    def test_incident_proposal_is_held_before_persistence_and_survives_restart(self):
        self.assertIsNone(self.s.next_task(self.state, self.config, self.cutoff))
        self.assertEqual(self.state['planned'], [])
        wait = self.state['planning_wait']
        self.assertIn('protected', wait['reason'].lower())
        self.assertIn(SEED, wait['reason'])
        self.assertEqual(wait['head'], self.head)
        self.assertEqual(wait['finding_ids'], [FINDING['id']])
        self.assertEqual(dt.datetime.fromisoformat(wait['until']),
                         self.instant + dt.timedelta(hours=1))
        self.assertEqual(self.state['local_supervision_calls_today'], 3)
        self.assert_history_preserved()
        saved = self.s.read(self.s.STATE / 'state.json')
        self.assertEqual(saved['planning_wait'], wait)
        for _ in range(3):
            self.assertIsNone(self.s.next_task(saved, self.config, self.cutoff))
        self.launch.assert_called_once()
        receipt = list((self.s.STATE / 'plans').glob('*/planning-hold.json'))
        self.assertEqual(len(receipt), 1)
        self.assertEqual(self.s.read(receipt[0])['proposal'], PROPOSAL)
        # The same existing wait interval expires; a subsequent call is charged normally.
        with mock.patch.object(self.s, 'now', return_value=self.instant + dt.timedelta(hours=1)):
            self.assertIsNone(self.s.next_task(saved, self.config, self.cutoff))
        self.assertEqual(self.launch.call_count, 2)
        self.assertEqual(saved['local_supervision_calls_today'], 4)

    def test_planning_blocks_glob_overlap_and_default_committed_test_baseline(self):
        for files, config in [
            (['tests/test_*.py'], self.config),
            (['tests/test_unit_transpor?.py'], self.config),
            (['tests/test_unit_*port.py', 'src/transport.py'], self.config),
            ([SEED], {k: v for k, v in self.config.items() if k != 'protected_test_files'}),
            (['tests/test_*.py'], {k: v for k, v in self.config.items() if k != 'protected_test_files'}),
        ]:
            with self.subTest(files=files, explicit='protected_test_files' in config):
                self.proposal = dict(PROPOSAL, files=files)
                self.assertIsNone(self.s.plan_task(self.state, config, self.cutoff, findings=[FINDING]))
                self.assertEqual(self.state['planned'], [])
                self.assert_history_preserved()

    def test_rejection_backoff_survives_interruption_before_diagnostic_receipt(self):
        write = self.s.write

        def interrupted(path, value):
            if Path(path).name == 'planning-hold.json':
                raise OSError('simulated interruption publishing the diagnostic')
            return write(path, value)

        with mock.patch.object(self.s, 'write', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'simulated interruption'):
                self.s.plan_task(self.state, self.config, self.cutoff, findings=[FINDING])
        saved = self.s.read(self.s.STATE / 'state.json')
        self.assertIn('planning_wait', saved)
        self.assertEqual(saved['planning_wait']['finding_ids'], [FINDING['id']])
        self.assertEqual(saved['local_supervision_calls_today'], 3)
        for _ in range(3):
            self.assertIsNone(self.s.next_task(saved, self.config, self.cutoff))
        self.launch.assert_called_once()
        self.assertEqual(saved['local_supervision_calls_today'], 3)
        self.assertEqual(saved['tries'], {'Q202611030747345487': 1})
        self.assertFalse(list((self.s.STATE / 'plans').glob('*/planning-hold.json')))
        self.assert_history_preserved()

    def test_existing_unsafe_scope_is_rejected_before_run_or_resource_ownership(self):
        task = dict(PROPOSAL, id='old-plan', kind='repair')
        task.pop('action')
        before = copy.deepcopy(self.state)
        files = sorted(str(p.relative_to(self.s.STATE)) for p in self.s.STATE.rglob('*'))
        with mock.patch.object(self.s, 'clone_workspace', side_effect=AssertionError('Unsafe scope reached workspace creation')) as clone, \
             mock.patch.object(self.s, 'index_run') as index, \
             mock.patch.object(self.s, 'event') as event, \
             mock.patch.object(self.s.PR, 'phase') as phase:
            with self.assertRaisesRegex(self.s.ManagerHeld, 'protected'):
                self.s.attempt(task, self.state, self.config, self.cutoff)
        for operation in (clone, index, event, phase, self.launch):
            operation.assert_not_called()
        self.assertEqual(self.state, before)
        self.assertEqual(sorted(str(p.relative_to(self.s.STATE)) for p in self.s.STATE.rglob('*')), files)
        self.assertNotIn('active_attempt', self.state)
        self.assert_history_preserved()

    def test_real_launch_callback_rejection_releases_resource_without_child(self):
        runtime = self.s.CLOUD_RUNTIME
        store = self.s.PR.Actions(runtime)
        lease = self.s.PR.lease()
        job = store.submit(self.s.PROJECT_ID, {'kind': 'fixture'}, time.time() + 60)
        self.assertTrue(store.claim(job['id'], lease))
        log = self.root / 'real-phase' / 'command.log'
        log.parent.mkdir()

        def deny():
            raise self.s.ManagerHeld('Task scope includes immutable protected tests')

        resource = self.s.PR.resource

        def available_resource(*args, **kwargs):
            return resource(*args, **kwargs, pressure=lambda: False)

        with mock.patch.object(self.s.PR, 'enabled', return_value=True), \
             mock.patch.object(self.s.PR, 'resource', side_effect=available_resource), \
             mock.patch.dict(os.environ, {'MIDNIGHT_ACTION_ID': job['id']}), \
             mock.patch.object(self.s.subprocess, 'Popen', side_effect=AssertionError('A denied phase must not launch a child')) as spawn:
            with self.assertRaisesRegex(self.s.ManagerHeld, 'protected'):
                self.s.run(['/usr/bin/true'], self.s.ROOT, log, 10,
                           dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=2), self.state, on_launch=deny)
        spawn.assert_not_called()
        phases = store.phases(active=False)
        self.assertEqual(len(phases), 1)
        self.assertEqual(phases[0]['state'], 'failed')
        self.assertEqual(store.executions(), [])
        with resource(runtime, 'host', time.time() + 5, pressure=lambda: False):
            pass
        store.finish(job['id'], 'failed', {'reason': 'fixture_scope_hold'}, lease=lease)
        self.assert_history_preserved()

    def test_new_tests_remain_admissible_with_finding_lineage_and_backend_scope(self):
        for config in (self.config, {k: v for k, v in self.config.items() if k != 'protected_test_files'}):
            self.proposal = dict(PROPOSAL, files=['tests/test_transport_regression.py'])
            task = self.s.plan_task(self.state, config, self.cutoff, findings=[FINDING])
            self.assertEqual(task['files'], self.proposal['files'])
            self.assertEqual(task['finding_ids'], [FINDING['id']])
            self.assertEqual(task['issue_episode'], FINDING['episode_id'])
            self.assertEqual(task['acceptance'], PROPOSAL['acceptance'])
        self.proposal = dict(PROPOSAL, files=['elsewhere/test_transport.py'])
        with self.assertRaises(self.s.ManagerHeld):
            self.s.plan_task(self.state, self.config, self.cutoff, findings=[FINDING])
        self.assertEqual(len(self.state['planned']), 2)
        self.assert_history_preserved()

    def test_actual_late_guard_records_terminal_phase_and_clears_dispatch_across_restart(self):
        self.late_guard_restart(False)

    def test_late_guard_receipt_interruption_recovers_without_replaying_dispatch(self):
        self.late_guard_restart('receipt')

    def test_late_guard_outcome_interruption_replays_without_duplicate_history(self):
        self.late_guard_restart('outcome')

    def test_late_guard_index_interruption_replays_without_duplicate_history(self):
        self.late_guard_restart('index')

    def late_guard_restart(self, crash):
        task = dict(PROPOSAL, id='legacy', kind='repair', local_worker_files=[SEED],
                    finding_ids=[FINDING['id']], issue_episode=FINDING['episode_id'])
        task.pop('action')
        self.engine.get_finding.return_value = FINDING
        instant = dt.datetime.now(dt.timezone.utc)
        config = dict(self.config, worker_timeout_seconds=60,
                      max_attempts_per_night=None, max_cloud_calls_per_night=None,
                      max_failures_per_night=None)
        config.pop('protected_test_files')
        self.s.write(self.s.ROOT / self.s.CONFIG_FILE, config)
        self.git('add', self.s.CONFIG_FILE)
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture config')
        self.state.update(day=self.s.window(instant)[1], failures_today=0, planned=[task], issue_episodes={})
        self.s.write(self.s.STATE / 'state.json', self.state)
        runtime = self.s.CLOUD_RUNTIME
        store = self.s.PR.Actions(runtime)
        lease = self.s.PR.lease()
        job = store.submit(self.s.PROJECT_ID, {'kind': 'fixture'}, time.time() + 90)
        self.assertTrue(store.claim(job['id'], lease))
        original_resource = self.s.PR.resource
        original_popen = subprocess.Popen
        original_select = self.s.next_task
        original_write = self.s.write
        original_index = self.s.index_run
        awake = mock.Mock()

        def interrupted_write(path, value):
            result = original_write(path, value)
            if crash == 'receipt' and Path(path).parent.name == 'held-dispatches':
                raise OSError('simulated interruption after held receipt')
            if crash == 'outcome' and Path(path).name == 'outcome.json':
                raise OSError('simulated interruption after terminal outcome')
            return result

        def interrupted_index(*args):
            result = original_index(*args)
            if crash == 'index' and self.s.read(self.s.STATE/'state.json').get('held_dispatch'):
                raise OSError('simulated interruption after terminal index')
            return result

        def spawn(command, *args, **kwargs):
            if str(command[0]) == '/usr/bin/caffeinate':
                return awake
            if (str(command[0]) == str(self.s.RALPHY_ENTRY) or str(command[0]) == 'fake-worker'
                or any(str(part).endswith('/phase_guard.py') for part in command)):
                self.fail('Rejected implementation reached a child process')
            return original_popen(command, *args, **kwargs)

        def launch(command, work, log, seconds, cutoff, state, charge, **kwargs):
            return self.s.run(command, work, log, seconds, cutoff, state,
                              on_launch=charge, **kwargs)

        with ExitStack() as stack:
            patches = [
                (self.s, 'now', {'return_value': instant}),
                (self.s, 'progress_enabled', {'return_value': False}),
                (self.s, 'next_task', {'return_value': task}),
                (self.s, 'write', {'side_effect': interrupted_write}),
                (self.s, 'index_run', {'side_effect': interrupted_index}),
                (self.s, 'clone_workspace', {'side_effect': lambda work, *a: subprocess.check_call(['git', 'clone', '--quiet', str(self.s.ROOT), str(work)])}),
                (self.s, 'seed_retry', {'return_value': 'No retained candidate.'}),
                (self.s, 'implementation_choice', {'return_value': ('local', 'fixture:1')}),
                (self.s, 'issue_episode', {'return_value': 'fixture:1'}),
                (self.s, 'checkpoint_audit_guidance', {'return_value': {}}),
                (self.s, 'typed_worker_manifest', {'return_value': self.root / 'manifest.json'}),
                (self.s, 'charged_run', {'side_effect': launch}),
                (self.s.AS, 'compact', {'side_effect': lambda runtime, project, state, tasks: state}),
                (self.s.PR, 'enabled', {'return_value': True}),
                (self.s.PR, 'resource', {'side_effect': lambda *a, **k: original_resource(*a, **k, pressure=lambda: False)}),
                (self.s.subprocess, 'Popen', {'side_effect': spawn}),
            ]
            for target, name, kwargs in patches:
                stack.enter_context(mock.patch.object(target, name, **kwargs))
            stack.enter_context(mock.patch('project_action.register_backend'))
            stack.enter_context(mock.patch.dict(os.environ, {'MIDNIGHT_ACTION_ID': job['id']}))
            recovery = stack.enter_context(mock.patch.object(self.s.MR, 'ManagerRecovery'))
            recovery.return_value.get.return_value = None
            micro = stack.enter_context(mock.patch.object(self.s.MS, 'MicroRecovery'))
            micro.return_value.get.return_value = None
            journal = stack.enter_context(mock.patch.object(self.s, 'journal_charge'))
            rescue = stack.enter_context(mock.patch.object(self.s, 'rescue_store'))
            if crash:
                with self.assertRaisesRegex(OSError, 'simulated interruption'):
                    self.s.tick(once=True, managed_until=instant + dt.timedelta(minutes=2))
            else:
                self.assertEqual(self.s.tick(once=True, managed_until=instant + dt.timedelta(minutes=2)), 0)
            saved = self.s.read(self.s.STATE / 'state.json')
            self.assertNotIn('active_attempt', saved)
            self.assertNotIn('pending', saved)
            self.assertEqual(saved['failures_today'], 0)
            self.assertEqual(saved['tries'], self.state['tries'])
            self.assertEqual(saved['attempts_today'], self.state['attempts_today'])
            self.assertEqual(saved['local_attempts_today'], self.state['local_attempts_today'])
            self.assertEqual(saved['accepted'], [])
            self.assertEqual(saved['done'], [])
            held = list((self.s.STATE / 'held-dispatches').glob('*.json'))
            self.assertEqual(len(held), 1)
            receipt = self.s.read(held[0])
            self.assertEqual(receipt['state'], 'manager_hold')
            self.assertEqual(receipt['dispatch']['task']['id'], task['id'])
            self.s.write.side_effect = original_write
            self.s.index_run.side_effect = original_index
            self.s.next_task.side_effect = original_select
            self.assertEqual(self.s.tick(once=True, managed_until=instant + dt.timedelta(minutes=2)), 0)
            final = self.s.read(self.s.STATE / 'runs' / receipt['dispatch']['run'] / 'outcome.json')
            self.assertEqual(final['outcome'], 'interrupted')
            self.assertFalse(final['accepted'])
            self.assertNotIn('held_dispatch', self.s.read(self.s.STATE / 'state.json'))
            self.assertEqual(self.s.charged_run.call_count, 1)
            journal.assert_not_called()
            rescue.return_value.reserve.assert_not_called()
            recovery.return_value.reserve.assert_not_called()
        phases = store.phases(active=False)
        self.assertEqual(len(phases), 1)
        self.assertEqual(phases[0]['state'], 'failed')
        self.assertEqual(store.executions(), [])
        with self.s.HistoryStore(self.s.STATE).reader() as connection:
            rows=connection.execute('SELECT run_id,outcome,accepted_commit FROM qc_runs WHERE project_id=?',(self.s.PROJECT_ID,)).fetchall()
            self.assertEqual([(r['run_id'],r['outcome'],r['accepted_commit']) for r in rows],
                             [(receipt['dispatch']['run'],'interrupted',None)])
        with original_resource(runtime, 'local', time.time() + 5, pressure=lambda: False):
            pass
        self.assertNotIn('active_attempt', self.s.read(self.s.STATE / 'state.json'))

    def test_prelaunch_rechecks_protection_for_already_persisted_tasks(self):
        for files, config, extra in [
            ([SEED], self.config, {}),
            (['tests/test_*.py'], self.config, {}),
            ([SEED], {}, {}),
            (['tests/test_new.py'], self.config, {'local_worker_files': [SEED]}),
            # Legacy narrowings without an explicit baseline reach the actual
            # launch callback after preparing their frozen candidate.
            ([SEED], {}, {'local_worker_files': [SEED]}),
        ]:
            with self.subTest(files=files, config=config, extra=extra):
                task = dict(PROPOSAL, id='old-plan', kind='repair', **extra)
                task.pop('action')
                task['files'] = files
                self.check_launch_guard(task, config)
                self.assert_history_preserved()

    def check_launch_guard(self, task, config):
        def clone(work, *args):
            self.git('clone', '--quiet', str(self.s.ROOT), str(work))

        def launch(command, work, log, seconds, cutoff, state, charge, **kwargs):
            try:
                charge()
            except self.s.ManagerHeld:
                raise
            except Exception as exc:
                self.fail('Unguarded reservation reached unrelated fixture code: ' + type(exc).__name__)
            self.fail('An implementation launch must not get past the scope guard')

        for backend in ('local', 'cloud'):
            with self.subTest(backend=backend), ExitStack() as stack:
                for name, kwargs in [
                    ('clone_workspace', {'side_effect': clone}),
                    ('seed_retry', {'return_value': 'No retained candidate.'}),
                    ('implementation_choice', {'return_value': (backend, 'fixture:1')}),
                    ('issue_episode', {'return_value': 'fixture:1'}),
                    ('checkpoint_audit_guidance', {'return_value': {}}),
                    ('typed_worker_manifest', {'return_value': self.root / 'manifest.json'}),
                    ('charged_run', {'side_effect': launch}),
                ]:
                    stack.enter_context(mock.patch.object(self.s, name, **kwargs))
                recovery = stack.enter_context(mock.patch.object(self.s.MR, 'ManagerRecovery'))
                recovery.return_value.get.return_value = None
                micro = stack.enter_context(mock.patch.object(self.s.MS, 'MicroRecovery'))
                micro.return_value.get.return_value = None
                manager = stack.enter_context(mock.patch.object(self.s.CP, 'CloudPolicy'))
                manager.return_value.prepare_invocation.return_value = {
                    'descriptor': {}, 'env': {}, 'command': ['fake-worker']}
                journal = stack.enter_context(mock.patch.object(self.s, 'journal_charge'))
                store = stack.enter_context(mock.patch.object(self.s, 'rescue_store'))
                with self.assertRaisesRegex(self.s.ManagerHeld, 'protected'):
                    self.s.attempt(task, self.state, dict(config, worker_timeout_seconds=720), self.cutoff)
                if task.get('local_worker_files') and 'protected_test_files' not in config:
                    self.s.charged_run.assert_called_once()
                journal.assert_not_called()
                store.return_value.reserve.assert_not_called()
                recovery.return_value.reserve.assert_not_called()

    def test_persisted_forbidden_task_is_held_without_replanning_or_starving_valid_work(self):
        bad = dict(PROPOSAL, id='old-plan', kind='repair',
                   finding_ids=[FINDING['id']], issue_episode=FINDING['episode_id'])
        bad.pop('action')
        self.state['planned'] = [bad]
        self.engine.get_finding.return_value = FINDING
        for _ in range(2):
            self.assertIsNone(self.s.next_task(self.state, self.config, self.cutoff))
        self.assertEqual(self.state['planned'], [bad])
        self.assertIn('old-plan', self.state.get('protected_task_holds', {}))
        self.assertEqual(self.state['protected_task_holds']['old-plan']['task_sha256'],
                         self.s.task_fingerprint(bad))
        self.assertEqual(self.s.read(self.s.STATE / 'status.json')['phase'], 'manager_hold')
        good = dict(bad, id='independent', files=['tests/test_new.py'])
        self.state['planned'].append(good)
        rescue = mock.Mock()
        rescue.eligibility.return_value = {'status': 'local_retries_required', 'allowed': False}
        self.engine.next_decision.side_effect = lambda head, tasks, **kw: {
            'action': 'implement_locally', 'task': tasks[0]}
        with mock.patch.object(self.s, 'rescue_store', return_value=rescue), \
             mock.patch.object(self.s, 'import_episode_failures', return_value=FINDING['episode_id']), \
             mock.patch.object(self.s, 'unchecked_candidate', return_value=False):
            self.assertEqual(self.s.next_task(self.state, self.config, self.cutoff), good)
        self.launch.assert_not_called()
        self.assert_history_preserved()

    def test_completed_illegal_edit_still_fails_the_original_final_scope_gate(self):
        task = dict(PROPOSAL, id='original')
        task.pop('action')
        work = self.original / 'work'
        self.git('clone', '--quiet', str(self.s.ROOT), str(work))
        (work / SEED).write_text('# completed illegal edit\n')
        self.s.write(self.original / 'worker-completion.json', {'finished': True})
        _, _, problem = self.s.checkpoint_candidate(
            task, self.state, self.config, work, self.head, self.original, 'original')
        self.assertEqual(problem, 'Changed a protected or out-of-task path: ' + SEED)
        self.assert_history_preserved()


if __name__ == '__main__':
    unittest.main()
