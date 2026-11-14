from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

import performance_review_cloud as R


class FakeCloud:
    def __init__(self, model):
        self.model = model; self.calls = []; self.results = []; self.failure = None

    def descriptor(self): return copy.deepcopy(self.model)

    def prepare_invocation(self, cwd, purpose, output_path, schema_path, descriptor):
        self.calls.append({'purpose': purpose, 'descriptor': descriptor, 'output': output_path})
        if self.failure: raise self.failure
        return {'command': ['fake-codex', 'exec', '-'], 'env': {}, 'invocation_id': 'a' * 32,
                'receipt': {'invocation_id': 'a' * 32, 'requested': descriptor}}

    def record_result(self, invocation_id, events, code):
        self.results.append((invocation_id, events, code))
        return {'invocation_id': invocation_id, 'requested': self.model, 'profile_mismatch': False, 'returncode': code}


class CloudPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.directory = self.root / 'runtime/performance-reviews/one-week'
        self.now = time.time(); self.start = self.now - R.F.DAY
        self.pins = {'commit': 'a' * 40, 'files': {'fixed': 'b' * 64}, 'cloud_config_sha256': 'c' * 64}
        self.model = R.CP.config_descriptor(); self.cloud = FakeCloud(self.model)
        self.provider = self.root / 'runtime/models/colibri'
        self.profile = {'version': 1, 'metadata_fixture': 'x' * 25000}
        profile_sha = R.hashlib.sha256(R.GP.canonical(self.profile)).hexdigest()
        self.local_model = {'model': R.GP.MODEL, 'context': 32768, 'digest': 'e' * 64, 'parameters_sha256': profile_sha}
        self.selection = {'enabled': True, 'mode': 'owner_default', 'expires_at': None, 'parameters_sha256': profile_sha, 'started_at': 'original'}
        self.override = {'mode': 'continuous', 'expires_at': None, 'created_at': 'original', 'limits': {'max_cloud_calls_per_night': None}}
        R.D.save_json(self.provider / 'config.json', self.profile); R.D.save_json(self.provider / 'trial.json', self.selection)
        R.D.save_json(self.root / 'runtime/override.json', self.override)
        for name, value in (('ROOT', self.root), ('DIRECTORY', self.provider)):
            active = patch.object(R.GP, name, value); active.start(); self.addCleanup(active.stop)
        active = patch.object(R.GP, 'active', return_value=self.local_model); active.start(); self.addCleanup(active.stop)
        self.baseline = {'version': 1, 'started_at': self.start, 'ends_at': self.start + 7 * R.F.DAY,
            'config': {'expected_source': self.pins['commit'], 'expected_model': self.local_model}, 'collector_sha256': R.sha(R.F.__file__)}
        R.F.write_once(self.directory / 'baseline.json', self.baseline)
        for name, value in ((R, 'source'), (R.A, 'check'), (R.CP, 'CloudPolicy')):
            replacement = self.pins if name is R else {'permission_sha256': 'd' * 64} if name is R.A else self.cloud
            active = patch.object(name, value, return_value=replacement); active.start(); self.addCleanup(active.stop)
        R.F.write_once(self.directory / 'cloud-review-plan.json', R.review_plan(self.root, self.directory))
        self.publish(1)
        self.guard = patch.object(R, 'guarded', side_effect=self.complete)
        self.guard_mock = self.guard.start(); self.addCleanup(self.guard.stop)
        pressure = patch.object(R.P, 'memory_critical', return_value=False); pressure.start(); self.addCleanup(pressure.stop)

    def publish(self, day):
        report = {'version': 1, 'day': day, 'start': self.start + (day - 1) * R.F.DAY,
            'end': self.start + day * R.F.DAY, 'baseline_sha256': R.sealed(self.baseline),
            'cloud_summary': {'day': day, 'interval_seconds': R.F.DAY,
                'projects': {'secondapp': {'accepted_commits': 0}, 'exampleapp': {'accepted_commits': 1}},
                'resources': {'telemetry_covered_seconds': 30, 'peak_sampled_charged_bytes': None}},
            'evidence_ids': {'accepted-001': {'receipt_sha256': 'e' * 64, 'identity': 'private-commit'}}}
        R.F.write_once(self.directory / 'reports' / f'day-{day:02}.json', report)
        return report

    def complete(self, root, folder, store, job, phase, invocation, slot, deadline):
        report = R.F.unseal(self.directory / 'reports' / f'{folder.name}.json'); day = report['day']
        value = {'day': day, 'report_sha256': R.sealed(report), 'weekly': day == 7, 'assessment': 'insufficient_evidence',
            'summary': 'Limited accepted receipts and sparse observation coverage.',
            'findings': [{'dimension': 'coverage', 'assessment': 'insufficient_evidence',
                         'evidence_ids': [f'day-{day:02}-summary'], 'explanation': 'Most of the interval is unobserved.'}],
            'recommended_next_steps': ['Collect the remaining original observation intervals.']}
        R.D.save_json(folder / 'answer.json', value)
        R.D.atomic_write(folder / 'events.jsonl', b'{"type":"turn.started"}\n{"type":"turn.completed"}\n')
        return 0

    def run_day(self, day=1, now=None):
        return R.review_due(self.root, self.directory, self.directory / 'reports' / f'day-{day:02}.json',
                            clock=lambda: self.now if now is None else now)

    def replace_report(self, update, day=1):
        path = self.directory / 'reports' / f'day-{day:02}.json'; value = R.F.unseal(path)
        update(value); R.D.atomic_write(path, R.F.encoded(R.F.seal(value)))

    def test_original_day_must_close_before_any_paid_reservation(self):
        with self.assertRaises(ValueError): self.run_day(now=self.now - 1)
        self.assertEqual(self.cloud.calls, [])
        self.assertFalse((self.directory / 'cloud').exists())

    def test_one_qualified_review_deduplicates_and_keeps_host_evidence_private(self):
        result = self.run_day(); self.assertEqual(result['state'], 'completed')
        self.assertEqual(self.run_day()['state'], 'spent'); self.assertEqual(len(self.cloud.calls), 1)
        self.assertEqual(self.cloud.calls[0]['purpose'], 'review'); self.assertEqual(self.cloud.calls[0]['descriptor'], self.model)
        prompt = (self.directory / 'cloud/day-01/prompt.txt').read_text()
        self.assertNotIn('private-commit', prompt); self.assertIn('day-01-accepted-001', prompt)
        status = R.review_status(self.root, self.directory)
        self.assertEqual(status['spent_days'], [1]); self.assertEqual(status['completed_days'], [1]); self.assertFalse(status['all_spent'])
        self.assertEqual(R.Actions(self.root / 'runtime').jobs(), [])

    def test_failed_prepare_and_unknown_parent_loss_never_retry_same_day(self):
        self.cloud.failure = RuntimeError('Unknown provider preparation')
        self.assertEqual(self.run_day()['state'], 'failed')
        folder = self.directory / 'cloud/day-01'; (folder / 'result.json').unlink()
        self.assertEqual(R.review_status(self.root, self.directory)['days']['1']['state'], 'unknown')
        self.assertEqual(self.run_day()['state'], 'spent'); self.assertEqual(len(self.cloud.calls), 1)

    def test_changed_source_or_policy_holds_before_paid_call(self):
        self.pins['files']['fixed'] = 'f' * 64
        with self.assertRaises(ValueError): self.run_day()
        self.assertEqual(self.cloud.calls, [])

    def test_runtime_selection_profile_and_continuous_controls_are_exactly_pinned(self):
        mutations = [(self.provider / 'trial.json', dict(self.selection, enabled=False)),
                     (self.provider / 'trial.json', dict(self.selection, started_at='renewed-default')),
                     (self.provider / 'config.json', dict(self.profile, metadata_fixture='x' * 24999 + 'y')),
                     (self.root / 'runtime/override.json', dict(self.override, mode='scheduled')),
                     (self.root / 'runtime/override.json', dict(self.override, created_at='new-continuous-override'))]
        originals = {self.provider / 'trial.json': self.selection, self.provider / 'config.json': self.profile,
                     self.root / 'runtime/override.json': self.override}
        for path, changed in mutations:
            with self.subTest(path=path.name, changed=next(k for k in changed if changed[k] != originals[path].get(k))):
                R.D.save_json(path, changed)
                with self.assertRaises(ValueError): self.run_day()
                self.assertFalse((self.directory / 'cloud/day-01/attempt.json').exists()); self.assertEqual(self.cloud.calls, [])
                R.D.save_json(path, originals[path])
        with patch.object(R.GP, 'active', return_value=dict(self.local_model, model='qwen-retained')):
            with self.assertRaises(ValueError): self.run_day()
        self.assertEqual(self.cloud.calls, [])

    def test_complete_large_profile_is_hashed_and_controls_observation_is_read_only(self):
        before = {path: path.read_bytes() for path in (self.provider / 'config.json', self.provider / 'trial.json', self.root / 'runtime/override.json')}
        self.assertGreater(len(before[self.provider / 'config.json']), 21000)
        with patch.object(R.D, 'save_json', side_effect=AssertionError('Read-only controls')):
            controls = R.runtime_controls(self.root, self.baseline)
        self.assertEqual(controls['provider_config_sha256'], R.F.digest(before[self.provider / 'config.json']))
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        R.GP.active.assert_called_with(verify=True)

    def test_runtime_change_immediately_before_reservation_holds_without_attempt(self):
        calls = 0
        def pressure():
            nonlocal calls
            calls += 1
            if calls == 2: R.D.save_json(self.root / 'runtime/override.json', dict(self.override, created_at='changed-at-final-gate'))
            return False
        with patch.object(R.P, 'memory_critical', side_effect=pressure):
            self.assertEqual(self.run_day()['state'], 'held')
        self.assertFalse((self.directory / 'cloud/day-01/attempt.json').exists()); self.assertEqual(self.cloud.calls, [])

    def test_pause_resume_is_dynamic_and_does_not_reseal_runtime_controls(self):
        plan = (self.directory / 'cloud-review-plan.json').read_bytes(); marker = self.root / 'runtime/PAUSED'; marker.touch()
        self.assertEqual(self.run_day()['state'], 'held'); marker.unlink()
        self.assertEqual(self.run_day()['state'], 'completed')
        self.assertEqual((self.directory / 'cloud-review-plan.json').read_bytes(), plan)

    def test_report_tampering_or_raw_text_summary_is_rejected(self):
        self.replace_report(lambda r: r['cloud_summary'].update(raw_log='secret-like prompt content'))
        with self.assertRaises(ValueError): self.run_day()
        self.assertEqual(self.cloud.calls, [])

    def test_late_evidence_references_are_cited_without_counting_new_output(self):
        self.replace_report(lambda r: r['evidence_ids'].update({'late-accepted-001': {'receipt_sha256': 'f' * 64, 'identity': 'old-commit'}}))
        self.assertEqual(self.run_day()['state'], 'completed')
        prompt = (self.directory / 'cloud/day-01/prompt.txt').read_text()
        self.assertIn('day-01-late-accepted-001', prompt)
        self.assertIn('not new output on the correction day', prompt)
        self.assertNotIn('old-commit', prompt)

    def test_global_pause_and_install_fence_do_not_spend_attempts(self):
        for marker in ('PAUSED', 'INSTALLING'):
            path = self.root / 'runtime' / marker; path.touch()
            self.assertEqual(self.run_day()['state'], 'held'); path.unlink()
        self.assertEqual(self.cloud.calls, []); self.assertEqual(R.review_status(self.root, self.directory)['spent_days'], [])

    def test_midnight_discussion_coexists_without_being_claimed_or_replaced(self):
        store = R.Actions(self.root / 'runtime'); job = store.submit('midnight', {'kind': 'agent_discussion'}, self.now + 100)
        self.assertEqual(self.run_day()['state'], 'completed')
        self.assertEqual(store.get(job['id'])['state'], 'queued'); self.assertEqual(len(self.cloud.calls), 1)

    def test_invalid_evidence_citation_is_failed_and_spent(self):
        original = self.complete
        def bad(*args):
            code = original(*args); path = args[1] / 'answer.json'; value = R.F.document(path)
            value['findings'][0]['evidence_ids'] = ['invented']; R.D.save_json(path, value); return code
        self.guard_mock.side_effect = bad
        self.assertEqual(self.run_day()['state'], 'failed'); self.assertEqual(self.run_day()['state'], 'spent')
        self.assertEqual(len(self.cloud.calls), 1)

    def test_semantic_quality_cannot_be_claimed_from_counts(self):
        original = self.complete
        def bad(*args):
            code = original(*args); path = args[1] / 'answer.json'; value = R.F.document(path)
            value['findings'][0].update(dimension='quality', assessment='strong'); R.D.save_json(path, value); return code
        self.guard_mock.side_effect = bad
        self.assertEqual(self.run_day()['state'], 'failed')

    def test_action_tool_event_is_rejected_without_retry(self):
        original = self.complete
        def bad(*args):
            code = original(*args)
            R.D.atomic_write(args[1] / 'events.jsonl', b'{"type":"item.completed","item":{"type":"command_execution","command":"wrong"}}\n')
            return code
        self.guard_mock.side_effect = bad
        self.assertEqual(self.run_day()['state'], 'failed'); self.assertEqual(self.run_day()['state'], 'spent')

    def test_missed_windows_catch_up_once_and_day_seven_contains_whole_week(self):
        for day in range(2, 8): self.publish(day)
        later = self.start + 8 * R.F.DAY
        for day in range(1, 8): self.assertEqual(self.run_day(day, later)['state'], 'completed')
        self.assertEqual(len(self.cloud.calls), 7); self.assertTrue(R.review_status(self.root, self.directory)['complete'])
        for day in range(1, 8): self.assertEqual(self.run_day(day, later)['state'], 'spent')
        prompt = (self.directory / 'cloud/day-07/prompt.txt').read_text()
        for day in range(1, 8): self.assertIn(f'day-{day:02}-summary', prompt)
        self.assertEqual(len(self.cloud.calls), 7)

    def test_resource_wait_reserves_no_cloud_attempt(self):
        with patch.object(R.P, 'resource', side_effect=R.P.PhaseInterrupted('occupied')):
            self.assertEqual(self.run_day()['state'], 'resource_wait')
        self.assertEqual(self.cloud.calls, []); self.assertEqual(R.review_status(self.root, self.directory)['spent_days'], [])

    def test_critical_memory_before_admission_or_reservation_does_not_spend(self):
        for samples in ([True], [False, True]):
            with patch.object(R.P, 'memory_critical', side_effect=samples):
                self.assertEqual(self.run_day()['state'], 'held')
            self.assertEqual(self.cloud.calls, [])
            self.assertFalse((self.directory / 'cloud/day-01/attempt.json').exists())
        self.assertEqual(self.run_day()['state'], 'completed')

    def test_weekly_synthesis_rejects_duplicate_original_intervals(self):
        for day in range(2, 8): self.publish(day)
        first = R.F.unseal(self.directory / 'reports/day-01.json')
        R.D.atomic_write(self.directory / 'reports/day-02.json', R.F.encoded(R.F.seal(first)))
        with self.assertRaises(ValueError): self.run_day(7, self.start + 8 * R.F.DAY)
        self.assertEqual(self.cloud.calls, [])

    def test_atomic_owned_job_is_invisible_until_committed_running(self):
        store = R.Actions(self.root / 'runtime'); owner = R.P.lease(); observations = []
        with store.write(): pass
        event = store.event
        def observed(con, project, kind, identity, payload):
            observations.append(R.Actions(self.root / 'runtime').jobs())
            row = con.execute('SELECT state,lease FROM wf_jobs WHERE id=?', (identity,)).fetchone()
            self.assertEqual(row['state'], 'running'); self.assertEqual(json.loads(row['lease']), owner)
            event(con, project, kind, identity, payload)
        with patch.object(store, 'event', side_effect=observed):
            job = R.owned_review_job(store, 'one-week', 1, self.now + 100, owner)
        self.assertEqual(observations, [[], []]); self.assertEqual(store.jobs(), [job])
        self.assertEqual(job['state'], 'running'); self.assertEqual(job['project'], R.PROJECT)
        self.assertIsNone(R.owned_review_job(store, 'another-week', 2, self.now + 100, owner))

    def test_atomic_owned_job_preserves_four_active_job_capacity(self):
        store = R.Actions(self.root / 'runtime')
        for number in range(4): store.submit('existing-' + str(number), {}, self.now + 100)
        self.assertEqual(self.run_day()['state'], 'busy')
        self.assertEqual(len(store.jobs()), 4); self.assertEqual(self.cloud.calls, [])
        self.assertFalse((self.directory / 'cloud/day-01/attempt.json').exists())

    def test_atomic_owned_job_event_failure_rolls_back_job_and_counters(self):
        store = R.Actions(self.root / 'runtime'); owner = R.P.lease(); event = store.event
        with store.write(): pass
        def fail(con, project, kind, identity, payload):
            event(con, project, kind, identity, payload)
            if kind == 'action_started': raise RuntimeError('Injected transaction failure')
        with patch.object(store, 'event', side_effect=fail):
            with self.assertRaises(RuntimeError): R.owned_review_job(store, 'one-week', 1, self.now + 100, owner)
        self.assertEqual(store.jobs(active=False), [])
        with store.read() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM wf_events').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM wf_totals').fetchone()[0], 0)

    def test_retained_live_execution_prevents_overlap_and_retirement(self):
        self.assertEqual(self.run_day()['state'], 'completed'); self.publish(2)
        attempt = R.F.unseal(self.directory / 'cloud/day-01/attempt.json')
        retained = {'phase': attempt['phase'], 'lease': {'pid': 8765, 'birth_us': 12345}}
        with patch.object(R.Actions, 'executions', return_value=[retained]), patch.object(R.P, 'live_tree', return_value=True):
            status = R.review_status(self.root, self.directory)
            self.assertTrue(status['days']['1']['live']); self.assertFalse(status['complete'])
            self.assertEqual(self.run_day(2, self.now + R.F.DAY)['state'], 'busy')
        self.assertEqual(len(self.cloud.calls), 1)

    def test_status_never_clears_or_finishes_owned_work(self):
        self.assertEqual(self.run_day()['state'], 'completed')
        with patch.object(R.Actions, 'clear_execution', side_effect=AssertionError('Status must not clear')) as clear, \
             patch.object(R.Actions, 'finish', side_effect=AssertionError('Status must not finish')) as finish:
            self.assertEqual(R.review_status(self.root, self.directory)['completed_days'], [1])
        clear.assert_not_called(); finish.assert_not_called()

    def test_guard_owns_slot_until_birth_matched_cleanup(self):
        self.guard.stop()
        folder = self.directory / 'guard-fixture'; folder.mkdir(); (folder / 'prompt.txt').write_text('bounded prompt')
        store = Mock(); store.executions.return_value = [{'phase': 'phase-1', 'lease': {'pid': 123, 'birth_us': 456}}]
        proc = Mock(pid=123); proc.poll.return_value = 0; proc.wait.return_value = 0
        observed = []; child = {'pid': 123, 'birth_us': 456, 'stamp': 'original', 'boot_id': 'boot'}
        invocation = {'command': ['fake-codex', 'exec', '-'], 'env': {}}
        def save_guard(path, value):
            if path.name == 'launch.json':
                R.D.save_json(self.root / 'runtime/phases/phase-1/guard-result.json',
                    {'phase': 'phase-1', 'launch_sha256': R.P.fingerprint(value), 'code': 0,
                     'reason': 'exited', 'guard': dict(child, group=True)})
            original_save(path, value)
        original_save = R.D.save_json
        with patch.object(R.subprocess, 'Popen', return_value=proc) as popen, patch.object(R.P, 'lease', return_value=child), \
             patch.object(R.P, 'stop_tree', side_effect=lambda value, **kw: observed.append(value) or True), \
             patch.dict(R.P.SLOT_NAMES, {31: 'cloud-0.lock'}), patch.object(R.D, 'save_json', side_effect=save_guard):
            self.assertEqual(R.guarded(self.root, folder, store, 'job-1', 'phase-1', invocation, 31, self.now + 10), 0)
        self.assertEqual(popen.call_args.kwargs['pass_fds'], (31,))
        self.assertIn('phase_guard.py', ' '.join(popen.call_args.args[0]))
        self.assertEqual(observed, [{'pid': 123, 'birth_us': 456}]); store.clear_execution.assert_called_once()
        self.assertTrue(R.F.document(folder / 'process.json')['drained'])

    def test_zero_exit_code_with_interrupted_guard_reason_is_rejected(self):
        self.guard.stop()
        folder = self.directory / 'interrupted-guard'; folder.mkdir(); (folder / 'prompt.txt').write_text('bounded prompt')
        store = Mock(); store.executions.return_value = []
        proc = Mock(pid=123); proc.poll.return_value = 0; proc.wait.return_value = 0
        child = {'pid': 123, 'birth_us': 456, 'stamp': 'original', 'boot_id': 'boot'}
        original_save = R.D.save_json
        def save_guard(path, value):
            if path.name == 'launch.json':
                original_save(self.root / 'runtime/phases/phase-1/guard-result.json',
                    {'phase': 'phase-1', 'launch_sha256': R.P.fingerprint(value), 'code': 0,
                     'reason': 'task_timeout', 'guard': dict(child, group=True)})
            original_save(path, value)
        with patch.object(R.subprocess, 'Popen', return_value=proc), patch.object(R.P, 'lease', return_value=child), \
             patch.object(R.P, 'stop_tree', return_value=True), patch.dict(R.P.SLOT_NAMES, {31: 'cloud-0.lock'}), \
             patch.object(R.D, 'save_json', side_effect=save_guard):
            with self.assertRaises(ValueError):
                R.guarded(self.root, folder, store, 'job-1', 'phase-1', {'command': ['fake'], 'env': {}}, 31, self.now + 10)


if __name__ == '__main__': unittest.main()
