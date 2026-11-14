"""M11 fault matrix. All state, repositories, providers and models are disposable.

Each method retains JSON-compatible before/after observations in ``evidence``.
The reviewed operator rehearsal can save these observations before cleanup;
ordinary test execution writes only inside the fixtures' temporary directories.
"""
import contextlib
import copy
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest import mock

import progress_recovery as P
import structured_worker as S
import test_progress_recovery as ProofFixtures
import test_worker_terminal_handoff as WorkerFixtures
import test_git_delivery_recovery as DeliveryFixtures
import test_midnight as SessionFixtures


class ProductiveFailureBoundaries(unittest.TestCase):
    def setUp(self):
        self.evidence = {'case': self._testMethodName, 'observations': []}

    def record(self, stage, value):
        self.evidence['observations'].append({'stage': stage, 'records': copy.deepcopy(value)})

    def proof_fixture(self):
        f = ProofFixtures.RecoveryTests('test_crash_after_reservation_keeps_proof_charged')
        f.setUp(); self.addCleanup(f.doCleanups); f.enroll(True); f.narrow()
        return f

    def proof_rows(self, recovery):
        with recovery.store.reader() as con:
            return {name: [dict(row) for row in con.execute('SELECT * FROM ' + name + ' ORDER BY rowid')]
                for name in ('pr_scopes', 'pr_proofs', 'pr_launches', 'qc_events')}

    def worker_fixture(self):
        f = WorkerFixtures.WorkerTerminalHandoff('test_typed_launch_waits_for_complete_client_exit_instead_of_ready_marker')
        f.setUp(); self.addCleanup(f.doCleanups)
        return f

    def candidate_records(self, fixture, directory):
        names = ('task.json', 'worker-process.json', 'worker-completion.json', 'candidate-checkpoint.json',
                 'candidate.patch', 'checks.json', 'review/result.json', 'review-receipt.json',
                 'recovery.json', 'outcome.json', 'accepted.json')
        files = {}
        for name in names:
            path = directory / name
            if path.is_file():
                data = path.read_bytes()
                files[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'text': data.decode()}
        return {'state': copy.deepcopy(fixture.state), 'persisted_state': fixture.b.read(fixture.b.STATE / 'state.json'),
                'canonical_head': fixture.git('rev-parse', 'HEAD'), 'files': files}

    def recover_twice(self, fixture, directory):
        b = fixture.b
        with mock.patch.object(b, 'cloud', side_effect=AssertionError('Recovery cannot request a provider')), \
             mock.patch.object(b, 'charged_run', side_effect=AssertionError('Recovery cannot relaunch coding')), \
             mock.patch.object(b, 'verify', side_effect=AssertionError('This recovery only retains evidence')):
            b.recover(fixture.state)
            first = self.candidate_records(fixture, directory)
            b.recover(fixture.state)
            second = self.candidate_records(fixture, directory)
        self.assertEqual(first, second)
        self.record('first_recovery', first); self.record('second_recovery', second)
        return second

    def test_crash_before_proof_commit_rolls_back_both_launch_and_event(self):
        f = self.proof_fixture(); before = self.proof_rows(f.r); self.record('before', before)
        append = f.r.store.append
        def crash(*args, **kwargs):
            append(*args, **kwargs)
            raise InterruptedError('Fault after launch/event inserts but before transaction commit')
        with mock.patch.object(f.r.store, 'append', side_effect=crash), self.assertRaises(InterruptedError):
            f.r.reserve(f.scope, 'uncommitted', f.model)
        reopened = P.Recovery(f.root, 'notes'); after = self.proof_rows(reopened)
        self.record('after_restart', after); self.assertEqual(after, before)
        self.assertTrue(reopened.admission(f.scope, f.model)['allowed'])
        reservation = reopened.reserve(f.scope, 'committed', f.model)
        self.assertTrue(reservation['charged']); self.assertEqual(len(self.proof_rows(reopened)['pr_launches']), 1)
        self.record('one_valid_consumption', self.proof_rows(reopened))

    def test_crash_after_proof_commit_never_refunds_or_launches_twice(self):
        f = self.proof_fixture(); self.record('before', self.proof_rows(f.r))
        reservation = f.r.reserve(f.scope, 'charged_before_spawn', f.model)
        committed = self.proof_rows(f.r); self.record('after_commit_before_spawn', committed)
        reopened = P.Recovery(f.root, 'notes')
        self.assertEqual(reopened.reserve(f.scope, 'charged_before_spawn', f.model), reservation)
        with self.assertRaises(P.RecoveryHeld):reopened.reserve(f.scope, 'renamed_retry', f.model)
        self.assertFalse(reopened.admission(f.scope, f.model)['allowed'])
        self.assertTrue(reservation['charged']); self.assertEqual(self.proof_rows(reopened), committed)
        self.assertEqual(reopened.snapshot()['scopes'][0]['legacy_launches'], 4)
        self.record('after_restart_and_duplicate_refusal', self.proof_rows(reopened))

    def test_truncated_inference_retains_partial_candidate_without_acceptance_or_replay(self):
        f = self.worker_fixture()
        def interrupted(work, directory, _events):
            tools = directory / 'worker-tools'; tools.mkdir()
            manifest = json.loads((directory / 'worker-manifest.json').read_text())
            worker = S.Worker(work, tools, manifest, f.model); output = io.StringIO()
            response = {'model': 'fixture', 'done': True, 'done_reason': 'length',
                'prompt_eval_count': 16000, 'eval_count': 1024,
                'message': {'role': 'assistant', 'thinking': 'unfinished reasoning', 'content': '{"action":'}}
            def open_(request, **_):
                value = {'capabilities': ['thinking']} if request.full_url.endswith('/api/show') else response
                return io.BytesIO((json.dumps(value) + '\n').encode())
            opener = mock.Mock(); opener.open.side_effect = open_
            with contextlib.redirect_stdout(output):
                worker.action({'action': 'read_file', 'path': 'src/value.py', 'content': '', 'summary': ''}, time.monotonic() + 10)
                worker.edit('src/value.py', {'old_text': 'value = 1', 'new_text': 'value = 2'}, 'Retained partial candidate', time.monotonic() + 10)
                with mock.patch.object(S.urllib.request, 'build_opener', return_value=opener), S.inference_session(f.model), self.assertRaises(S.IncompleteInference):
                    S.recorded_infer(tools, 0, 'decision', f.model, [], time.monotonic() + 10,
                        schema=S.ACTION_SCHEMA, predict=1024)
            (directory / 'worker-events.jsonl').write_text(output.getvalue() + json.dumps(
                {'type': 'worker.client_result', 'schema_version': 1, 'exit_code': 17}) + '\n')
        with mock.patch.object(f, 'finish', side_effect=interrupted):
            directory, _, _, verify = f.attempt()
        verify.assert_not_called(); diagnosis = P.classify(directory)
        self.assertEqual(diagnosis['kind'], 'inference_truncated')
        receipt = f.b.read(directory / 'worker-tools/decision-0.json')
        self.assertFalse(receipt['partial_text_applied']); self.assertEqual(receipt['requested_budget'], 1024)
        before = self.candidate_records(f, directory); self.record('failed_worker', before)
        after = self.recover_twice(f, directory)
        self.assertEqual(after['files']['candidate.patch'], before['files']['candidate.patch'])
        self.assertEqual(after['state']['local_attempts_today'], 1); self.assertEqual(after['state']['accepted'], [])
        self.assertNotIn('accepted.json', after['files'])

    def test_host_check_failure_retains_candidate_and_spent_attempt_across_restart(self):
        f = self.worker_fixture(); directory, _, _, verify = f.attempt(); verify.assert_called_once()
        before = self.candidate_records(f, directory); self.record('failed_host_check', before)
        self.assertEqual(f.b.read(directory / 'checks.json')[0]['code'], 1)
        after = self.recover_twice(f, directory)
        self.assertEqual(after['files']['candidate.patch'], before['files']['candidate.patch'])
        self.assertEqual(after['state']['local_attempts_today'], 1); self.assertEqual(after['state']['accepted'], [])
        self.assertNotIn('accepted.json', after['files'])

    def passed_fixture_check(self, fixture, directory):
        command = [sys.executable, '-I', '-B', '-c', 'exec(open("src/value.py").read()); assert value == 2']
        result = subprocess.run(command, cwd=directory / 'work', capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        fixture.b.write(directory / 'checks.json', [{'code': result.returncode, 'reason': 'exited', 'command': command}])
        (directory / 'check-0.log').write_text(result.stdout + result.stderr)

    def test_reviewer_rejection_retains_evidence_without_second_provider_request(self):
        f = self.worker_fixture(); directory, _, _, _ = f.attempt(); self.passed_fixture_check(f, directory)
        f.state['pending'] = {'task': f.task, 'run': directory.name, 'base': f.head}
        self.record('before_review', self.candidate_records(f, directory))
        def reject(_prompt, _schema, target, *_args, **_kwargs):
            value = {'decision': 'revise', 'issues': ['Fixture independent rejection'], 'summary': 'Retain candidate'}
            f.b.write(target / 'result.json', value); return value
        with mock.patch.object(f.b, 'cloud', side_effect=reject) as provider:
            result = f.b.review_and_promote(f.task, f.state, f.config, f.b.now() + dt.timedelta(minutes=1),
                directory, directory / 'work', directory.name, f.head,
                (directory / 'candidate.patch').read_text(), ['src/value.py'])
        provider.assert_called_once(); self.assertFalse(result[0])
        rejected = self.candidate_records(f, directory); self.record('after_rejection', rejected)
        after = self.recover_twice(f, directory)
        self.assertEqual(after['files']['candidate.patch'], rejected['files']['candidate.patch'])
        self.assertEqual(after['files']['review/result.json'], rejected['files']['review/result.json'])
        self.assertEqual(after['state']['accepted'], []); self.assertEqual(after['state']['local_attempts_today'], 1)

    def test_pending_delivery_recovers_uncertain_creation_and_merge_exactly_once(self):
        f = DeliveryFixtures.DeliveryRecoveryTests('test_uncertain_remote_merge_reconciles_without_reusing_promotion_authority')
        f.setUp(); self.addCleanup(f.doCleanups)
        def snapshot():
            path = f.directory / 'github-delivery.json'
            return {'journal': json.loads(path.read_text()) if path.exists() else None,
                'candidate_patch': (f.directory / 'candidate.patch').read_text(),
                'pr_creations': f.api.posts, 'remote_merges': f.api.merges,
                'canonical_head': DeliveryFixtures.git(f.canonical, 'rev-parse', 'HEAD'),
                'remote_pr': copy.deepcopy(f.api.pr)}
        self.record('before', snapshot()); f.api.uncertain_create = True
        self.assertEqual(f.deliver()['status'], 'pending'); self.record('after_uncertain_pr_creation', snapshot())
        f.api.uncertain_merge = True
        self.assertEqual(f.deliver()['status'], 'pending'); self.record('after_uncertain_merge', snapshot())
        self.assertEqual(f.deliver()['status'], 'merged'); accepted = snapshot(); self.record('after_reconciliation', accepted)
        self.assertEqual(f.deliver()['status'], 'merged'); replayed = snapshot(); self.record('after_second_reconciliation', replayed)
        # A fresh remote observation updates observed_at. Preserve both full
        # observations above while requiring every acceptance/effect fact stable.
        stable = lambda row: dict(row, journal={k: v for k, v in row['journal'].items() if k != 'observed_at'})
        self.assertEqual(stable(replayed), stable(accepted))
        self.assertEqual(f.api.posts, 1); self.assertEqual(f.api.merges, 1)

    def test_commit_before_acceptance_receipt_recovers_once_without_provider_replay(self):
        f = self.worker_fixture(); directory, _, _, _ = f.attempt(); self.passed_fixture_check(f, directory)
        f.state['pending'] = {'task': f.task, 'run': directory.name, 'base': f.head}
        self.record('before_review', self.candidate_records(f, directory))
        def accept(_prompt, _schema, target, *_args, **_kwargs):
            value = {'decision': 'accept', 'issues': [], 'summary': 'Fixture reviewed change'}
            f.b.write(target / 'result.json', value); return value
        with mock.patch.object(f.b, 'cloud', side_effect=accept) as provider, \
             mock.patch.object(f.b, 'accept_candidate', side_effect=InterruptedError('Crash after commit before accepted receipt')):
            with self.assertRaises(InterruptedError):
                f.b.review_and_promote(f.task, f.state, f.config, f.b.now() + dt.timedelta(minutes=1),
                    directory, directory / 'work', directory.name, f.head,
                    (directory / 'candidate.patch').read_text(), ['src/value.py'])
        provider.assert_called_once(); committed = f.git('rev-parse', 'HEAD'); self.assertNotEqual(committed, f.head)
        self.assertFalse((directory / 'accepted.json').exists()); self.record('committed_before_receipt', self.candidate_records(f, directory))
        # Finding/procedure housekeeping is outside this receipt crash boundary;
        # existing profile/recovery tests exercise that separately.
        with mock.patch.object(f.b, 'reconcile_quality_acceptance'):
            after = self.recover_twice(f, directory)
        self.assertEqual(len(after['state']['accepted']), 1)
        self.assertEqual(after['state']['accepted'][0]['commit'], committed)
        self.assertEqual(after['state']['done'].count(f.task['id']), 1)
        self.assertEqual(after['state']['local_attempts_today'], 1)

    def session_fixture(self):
        f = SessionFixtures.IsolatedTests('test_failed_attempt_is_not_reported_as_an_achievement')
        f.setUp(); self.addCleanup(f.tearDown); return f

    def test_failed_and_retained_work_produces_no_session_achievement(self):
        f = self.session_fixture(); m = SessionFixtures.M
        state = {'tries': {'T1': 2}, 'accepted': [], 'retry_candidates': {'T1': {'run': 'retained'}},
            'pending': {'task': {'id': 'T1'}, 'run': 'retained'}}
        m.write(Path(f.project['state_dir']) / 'state.json', state)
        self.record('before_finish', {'project': state, 'rows': m.ledger_rows()})
        m.finish(f.session(), 'controller_error', f.data); first = m.ledger_rows()
        m.finish(f.session(), 'controller_error', f.data)
        self.assertEqual(m.ledger_rows(), first); self.assertEqual(len(first), 1)
        self.assertEqual(first[0]['accepted_count'], '0'); self.assertIn('No new changes passed', first[0]['summary'])
        self.record('after_finish_and_replay', {'project': m.read(Path(f.project['state_dir']) / 'state.json'), 'rows': first})

    def test_session_ledger_commit_survives_checkpoint_crash_without_new_achievements(self):
        f = self.session_fixture(); m = SessionFixtures.M; path = Path(f.project['state_dir']) / 'state.json'
        state = {'tries': {'T1': 3}, 'accepted': [{'run': 'new-run', 'title': 'Verified fixture change'}],
            'pending': {'task': {'id': 'T2'}, 'run': 'unfinished'}}
        m.write(path, state); m.write(f.state / 'session.json', f.session())
        self.record('before', {'project': state, 'session': m.read(f.state / 'session.json'), 'rows': m.ledger_rows()})
        write = m.write
        def crash(target, value):
            if Path(target) == f.state / 'sessions/test-session.json':raise OSError('Crash after session ledger commit')
            return write(target, value)
        with mock.patch.object(m, 'write', side_effect=crash), self.assertRaises(OSError):
            m.finish(f.session(), 'controller_error', f.data)
        rows = m.ledger_rows(); self.assertEqual(len(rows), 1); self.assertEqual(rows[0]['accepted_count'], '1')
        self.record('after_ledger_before_checkpoint', {'session': m.read(f.state / 'session.json'), 'rows': rows})
        m.write(path, {'tries': {'T1': 90}, 'accepted': [{'run': 'later-run', 'title': 'Later fixture work'}]})
        m.finish(m.read(f.state / 'session.json'), 'window_ended', f.data)
        self.assertEqual(m.ledger_rows(), rows)
        self.assertTrue(m.read(f.state / 'session.json')['finished'])
        self.assertNotIn('Later fixture work', rows[0]['achieved'])
        self.record('after_restart', {'session': m.read(f.state / 'session.json'), 'rows': m.ledger_rows()})


if __name__ == '__main__':
    unittest.main()
