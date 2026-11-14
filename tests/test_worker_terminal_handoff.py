"""M23: production completion and restart boundaries, without model calls."""
import contextlib
import copy
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import project_backend as B
import structured_worker as S
import test_git_delivery_recovery as DeliveryFixtures
import test_recovery_manager_dispatch as ManagerFixtures


class WorkerTerminalHandoff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.repo = self.root / 'source'; self.repo.mkdir()
        self.git('init', '-q'); self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@localhost')
        (self.repo / 'src').mkdir(); (self.repo / 'src/value.py').write_text('value = 1\n')
        self.git('add', '.'); self.git('commit', '-qm', 'Fixture baseline')
        self.head = self.git('rev-parse', 'HEAD')
        self.b = self.backend()
        self.task = {'id': 'repair', 'kind': 'repair', 'title': 'Repair value',
            'description': 'Set the existing value to two.', 'files': ['src/value.py'],
            'acceptance': ['The value is two and unrelated source is unchanged.']}
        self.model = {'model': 'fixture', 'digest': 'a' * 64, 'context': 32768}
        self.config = {'worker_timeout_seconds': 30, 'worker_model': self.model,
            'typed_worker_required': True, 'protected_test_files': []}
        self.state = {'tries': {}, 'attempts_today': 0, 'local_attempts_today': 0,
            'cloud_calls_today': 0, 'done': [], 'accepted': [], 'feedback': {}, 'retry_candidates': {}}
        self.events = [{'type': 'turn.completed'},
            {'type': 'worker.result_handoff', 'schema_version': 1, 'status': 'completed'},
            {'type': 'worker.client_result', 'schema_version': 1, 'exit_code': 0}]

    def backend(self):
        b = B.load_backend(self.repo, 'fixture', settings={'source_dirs': ['src'], 'product_dirs': ['src']})
        b.STATE = self.root / 'state'; b.STATE.mkdir(exist_ok=True)
        b.CLOUD_RUNTIME = self.root / 'coordinator'; b.CLOUD_RUNTIME.mkdir(exist_ok=True)
        return b

    def git(self, *args, cwd=None):
        return subprocess.check_output(['git', '-c', 'commit.gpgsign=false', *args],
            cwd=cwd or self.repo, text=True, stderr=subprocess.DEVNULL).strip()

    def finish(self, work, directory, events):
        """Use real edit/syntax/finish APIs; only model identity is supplied locally."""
        path = directory / 'worker-manifest.json'; manifest = json.loads(path.read_text())
        manifest['_sha256'] = S.digest(path)
        tools = directory / 'worker-tools'; tools.mkdir(exist_ok=True)
        worker = S.Worker(work, tools, manifest, self.model)
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(S.M, 'local_descriptor', return_value=self.model):
            worker.action({'action': 'read_file', 'path': 'src/value.py', 'content': '', 'summary': ''}, time.monotonic() + 10)
            worker.edit('src/value.py', {'old_text': 'value = 1', 'new_text': 'value = 2'}, 'Repair value', time.monotonic() + 10)
            result = worker.action({'action': 'run_tests', 'path': '', 'content': '', 'summary': ''}, time.monotonic() + 10)
            self.assertEqual(result['code'], 0, result)
            result = worker.action({'action': 'finish', 'path': '', 'content': '', 'summary': 'Value repaired'}, time.monotonic() + 10)
        self.assertTrue(result['finished'])
        self.b.write(tools / 'decision-0.json', {'status': 'completed', 'value': {'action': 'finish'}})
        self.b.write(tools / 'tool-result-0.json', result)
        (directory / 'worker-events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
        self.assertTrue(S.submission_complete(directory, work, self.task['id'], self.model))

    def attempt(self, events=None, process=None, *, typed=True):
        b = self.b; captured = {}; before = self.state['local_attempts_today']
        def clone(work, *_):
            self.git('clone', '-q', str(self.repo), str(work))
            (work / '.git/info/exclude').write_text('PRD.md\n.midnight-ready.json\n')
        def worker(*args, **kwargs):
            captured.update(kwargs); args[6]()
            if typed:self.finish(args[1], args[2].parent, self.events if events is None else events)
            else:(args[1] / 'src/value.py').write_text('value = 2\n')
            return process or {'code': 0, 'reason': 'exited', 'seconds': 1}
        def checks(work, directory, *_):
            b.write(directory / 'checks.json', [{'code': 1, 'reason': 'exited', 'command': ['fixture-check']}])
            return False
        with contextlib.ExitStack() as stack:
            for name in ('report', 'event', 'index_run', 'feature_gate', 'prepare_workspace'):
                stack.enter_context(mock.patch.object(b, name))
            stack.enter_context(mock.patch.object(b, 'clone_workspace', side_effect=clone))
            stack.enter_context(mock.patch.object(b, 'seed_retry', return_value=''))
            stack.enter_context(mock.patch.object(b, 'repair_plan', return_value={'complete_review': {'summary': 'Fixture'}, 'items': []}))
            stack.enter_context(mock.patch.object(b, 'implementation_choice', return_value=('local', 'repair:1')))
            stack.enter_context(mock.patch.object(b, 'charged_run', side_effect=worker))
            verify = stack.enter_context(mock.patch.object(b, 'verify', side_effect=checks))
            stack.enter_context(mock.patch.object(b, 'cloud', side_effect=AssertionError('No provider call')))
            stack.enter_context(mock.patch.object(b.PR, 'enabled', return_value=False))
            result = b.attempt(self.task, self.state, dict(self.config, typed_worker_required=typed), b.now() + dt.timedelta(minutes=2))
        self.assertEqual(self.state['local_attempts_today'], before + 1)
        directory = b.STATE / 'runs' / result[1]
        self.assertTrue((directory / 'candidate.patch').is_file(), result)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.head)
        self.assertEqual(self.state['accepted'], []); self.assertFalse((directory / 'accepted.json').exists())
        return directory, result, captured, verify

    def test_typed_launch_waits_for_complete_client_exit_instead_of_ready_marker(self):
        directory, _, captured, verify = self.attempt()
        self.assertIsNone(captured.get('ready'), 'Typed worker was armed for premature marker termination')
        self.assertTrue(self.b.read(directory / 'worker-completion.json')['finished'])
        verify.assert_called_once()

    def test_explicit_standalone_compatibility_keeps_legacy_marker_route(self):
        directory, _, captured, verify = self.attempt(typed=False, process={'code': -15, 'reason': 'ready_for_review'})
        self.assertIsNotNone(captured.get('ready'))
        self.assertTrue(self.b.read(directory / 'worker-completion.json')['finished'])
        self.assertEqual(self.b.read(directory / 'worker-manifest.json')['check_kind'], 'standalone_compatibility')
        verify.assert_called_once()

    def test_saved_candidate_and_cloud_routes_do_not_invent_typed_completion(self):
        for kind in ('saved_candidate', 'cloud'):
            with self.subTest(kind=kind):
                directory, _, _, _ = self.attempt(self.events[:1])
                (directory / 'worker-manifest.json').unlink()
                (directory / 'worker-completion.json').unlink()
                self.b.write(directory / 'implementation-backend.json', {'kind': kind})
                original = (directory / 'candidate.patch').read_bytes()
                self.b.write(directory / 'checks.json', [{'code': 0, 'reason': 'exited'}])
                self.state['pending'] = {'task': self.task, 'run': directory.name, 'base': self.head}
                with mock.patch.object(self.b, 'reconcile_saved_audits'), \
                     mock.patch.object(self.b, 'require_checked_candidate'), \
                     mock.patch.object(self.b, 'cloud', return_value={'decision': 'revise', 'issues': ['Fixture rejection'], 'summary': 'Not accepted'}) as review:
                    result = self.b.review_and_promote(self.task, self.state, self.config,
                        self.b.now() + dt.timedelta(minutes=1), directory, directory / 'work', directory.name,
                        self.head, original.decode(), ['src/value.py'])
                review.assert_called_once(); self.assertFalse(result[0])
                self.assertFalse((directory / 'worker-completion.json').exists())
                self.assertEqual(self.state['accepted'], [])

    def test_incomplete_terminal_evidence_cannot_reach_host_checks(self):
        variants = [[], self.events[:1], self.events[1:], self.events + [self.events[-1]],
            [self.events[0], dict(self.events[1], status='failed'), self.events[2]],
            [self.events[0], self.events[1], dict(self.events[2], exit_code=1)],
            [self.events[0], self.events[2], self.events[1]],
            [{'type': 'turn.failed'}, *self.events]]
        for events in variants:
            with self.subTest(events=events):
                directory, result, _, verify = self.attempt(events)
                self.assertFalse(verify.called, 'Incomplete worker reached host verification')
                self.assertFalse(self.b.read(directory / 'worker-completion.json')['finished'])
                self.assertIn('unfinished', result[2].lower())

    def test_failed_or_interrupted_process_cannot_claim_completed_worker(self):
        for process in ({'code': 1, 'reason': 'exited'}, {'code': -15, 'reason': 'paused_or_shutdown'},
                        {'code': -15, 'reason': '09:00_cutoff'}, {'code': -15, 'reason': 'task_timeout'}):
            with self.subTest(process=process):
                directory, _, _, verify = self.attempt(process=process)
                self.assertFalse(verify.called)
                self.assertFalse(self.b.read(directory / 'worker-completion.json')['finished'])

    def test_pending_review_rechecks_terminal_receipts_after_restart(self):
        directory, _, _, _ = self.attempt(self.events[:1])
        self.b.write(directory / 'worker-completion.json', {'contract': S.CONTRACT, 'finished': True})
        before = copy.deepcopy(self.state); original = (directory / 'candidate.patch').read_bytes()
        reopened = self.backend(); pending = {'task': self.task, 'run': directory.name, 'base': self.head}
        self.state['pending'] = pending
        with mock.patch.object(reopened, 'reconcile_saved_audits'), \
             mock.patch.object(reopened, 'require_checked_candidate'), \
             mock.patch.object(reopened, 'cloud', return_value={'decision': 'revise', 'issues': ['Fixture rejection'], 'summary': 'Not accepted'}) as provider:
            with self.assertRaisesRegex(reopened.ManagerHeld, 'terminal'):
                reopened.review_and_promote(self.task, self.state, self.config,
                    reopened.now() + dt.timedelta(minutes=1), directory, directory / 'work', directory.name,
                    self.head, original.decode(), ['src/value.py'])
        self.assertFalse(provider.called, 'Restart trusted saved finished flag without terminal receipts')
        self.assertEqual(self.state['pending'], pending)
        self.assertEqual(self.state['local_attempts_today'], before['local_attempts_today'])
        self.assertEqual((directory / 'candidate.patch').read_bytes(), original)
        self.assertEqual(self.state['accepted'], [])

    def promotion_fixture(self):
        directory, _, _, _ = self.attempt(self.events[:1])
        patch = (directory / 'candidate.patch').read_bytes(); digest = hashlib.sha256(patch).hexdigest()
        review = {'decision': 'accept', 'issues': [], 'summary': 'Fixture approved'}
        checks = [{'code': 0, 'reason': 'exited', 'command': ['fixture-check']}]
        self.b.write(directory / 'review-receipt.json', {'review': review, 'patch_sha256': digest})
        self.b.write(directory / 'checks.json', checks)
        return directory, digest, review, checks

    def test_pending_remote_delivery_gates_new_effects_after_reconciliation(self):
        directory, _, review, checks = self.promotion_fixture()
        counts = (self.state['attempts_today'], self.state['local_attempts_today'])
        with mock.patch.object(self.b, 'reconcile_saved_audits'), mock.patch.object(self.b, 'report'), \
             mock.patch.object(self.b.GD, 'GitDelivery') as delivery:
            def reconcile_then_prepare(*args, **kwargs):
                kwargs.get('pre_prepare', lambda: None)()
                return {'status': 'pending', 'reason': 'Fixture'}
            delivery.return_value.deliver.side_effect = reconcile_then_prepare
            with self.assertRaisesRegex(self.b.ManagerHeld, 'terminal'):
                self.b.deliver_candidate(self.task, self.state, self.config, directory, directory / 'work',
                    directory.name, self.head, review, checks, cutoff=self.b.now() + dt.timedelta(minutes=1))
        delivery.return_value.deliver.assert_called_once()
        self.assertEqual(counts, (self.state['attempts_today'], self.state['local_attempts_today']))
        self.assertTrue((directory / 'candidate.patch').exists()); self.assertEqual(self.state['accepted'], [])

    def test_uncommitted_promotion_intent_holds_but_committed_history_recovers(self):
        directory, digest, _, _ = self.promotion_fixture()
        intent = {'run': directory.name, 'task': self.task, 'prepared': True,
            'base': self.head, 'patch_sha256': digest}
        with mock.patch.object(self.b, 'principal_delivery_gate'), mock.patch.object(self.b, 'reconcile_saved_audits'):
            with self.assertRaisesRegex(self.b.ManagerHeld, 'terminal'):
                self.b.resume_promotion(intent, allow_promotion=True)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.head)
        # Once an actual accepted commit exists, recovering that historical fact
        # must not depend on upgrading its older worker-publication evidence.
        self.git('commit', '--allow-empty', '-qm', 'Existing acceptance\n\n' + self.b.RUN_TRAILER + ': ' + directory.name)
        accepted = self.git('rev-parse', 'HEAD')
        self.assertEqual(self.b.resume_promotion(intent, allow_promotion=False), accepted)

    def test_host_publication_failure_survives_successful_client_and_wrapper(self):
        events = [self.events[0], dict(self.events[1], status='failed'), self.events[2]]
        directory, _, _, _ = self.attempt(events)
        failure = self.b.Q.worker_failure(directory)
        self.assertIsNotNone(failure, 'Successful client exit hid a failed host handoff')
        self.assertEqual(failure['phase'], 'result_handoff')
        self.assertEqual(failure['completion_reason'], 'failed')
        self.assertEqual(failure['client_result']['exit_code'], 0)
        self.assertEqual(failure['wrapper_result']['code'], 0)
        self.assertIsNone(failure['requested_budget'])

    def test_owned_process_can_publish_after_ready_and_still_obeys_pause(self):
        ready = self.root / 'ready.json'; self.b.write(ready, {'task_id': 'fixture', 'ready_for_review': True})
        final = self.root / 'terminal.txt'
        command = [sys.executable, '-c', 'import pathlib,time,sys;time.sleep(.15);pathlib.Path(sys.argv[1]).write_text("published")', str(final)]
        sleep = time.sleep
        with mock.patch.object(self.b, 'report'), mock.patch.object(self.b.PR, 'enabled', return_value=False), \
             mock.patch.object(self.b.time, 'sleep', side_effect=lambda _: sleep(.01)):
            result = self.b._run_process(command, self.root, self.root / 'process.log', 5,
                self.b.now() + dt.timedelta(seconds=10), {}, ready=None)
            self.assertEqual(result['code'], 0); self.assertEqual(result['reason'], 'exited')
            self.assertEqual(final.read_text(), 'published')
            final.unlink()
            result = self.b._run_process(command, self.root, self.root / 'legacy.log', 5,
                self.b.now() + dt.timedelta(seconds=10), {}, ready=(ready, 'fixture'))
            self.assertEqual(result['reason'], 'ready_for_review'); self.assertFalse(final.exists())
            self.b.CONTROL_STOP = lambda: True
            result = self.b._run_process(command, self.root, self.root / 'paused.log', 5,
                self.b.now() + dt.timedelta(seconds=10), {}, ready=None)
            self.assertEqual(result['reason'], 'stopped_before_start'); self.assertFalse(final.exists())
        self.assertEqual(self.b.read(self.b.STATE / 'active-child.json'), {})

    def test_pause_cutoff_and_timeout_still_stop_a_running_typed_process(self):
        sleep = time.sleep
        for boundary in ('pause', 'cutoff', 'timeout'):
            with self.subTest(boundary=boundary):
                final = self.root / (boundary + '.txt')
                command = [sys.executable, '-c', 'import pathlib,time,sys;time.sleep(1);pathlib.Path(sys.argv[1]).write_text("too late")', str(final)]
                calls = []
                def stop():
                    calls.append(1); return boundary == 'pause' and len(calls) > 2
                self.b.CONTROL_STOP = stop
                with mock.patch.object(self.b, 'report'), mock.patch.object(self.b.PR, 'enabled', return_value=False), \
                     mock.patch.object(self.b.time, 'sleep', side_effect=lambda _: sleep(.01)):
                    result = self.b._run_process(command, self.root, self.root / (boundary + '.log'),
                        .03 if boundary == 'timeout' else 5,
                        self.b.now() + dt.timedelta(seconds=.03 if boundary == 'cutoff' else 5), {}, ready=None)
                self.assertEqual(result['reason'], {'pause': 'paused_or_shutdown', 'cutoff': '09:00_cutoff', 'timeout': 'task_timeout'}[boundary])
                self.assertNotEqual(result['code'], 0); self.assertFalse(final.exists())
                self.assertEqual(self.b.read(self.b.STATE / 'active-child.json'), {})

    def test_saved_micro_review_does_not_upgrade_an_incomplete_typed_handoff(self):
        fixture = ManagerFixtures.ManagerDispatchTests('test_intermediate_attempt_runs_real_focused_tests_then_only_checkpoints')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        task, run = fixture.interrupted_micro_review(); directory = fixture.s.STATE / 'runs' / run
        fixture.s.write(directory / 'worker-completion.json', {'finished': True, 'contract': S.CONTRACT})
        counts = (fixture.state['local_attempts_today'], fixture.state['attempts_today'])
        original = (directory / 'candidate.patch').read_bytes()
        with mock.patch.object(fixture.s, 'cloud', side_effect=fixture.cloud) as provider, \
             mock.patch.object(fixture.s, 'charged_run', side_effect=AssertionError('No coding replay')):
            self.assertFalse(fixture.s.resume_micro_checkpoint(task, fixture.state, {},
                fixture.s.now() + dt.timedelta(minutes=1), fixture.head))
        provider.assert_not_called(); self.assertEqual((directory / 'candidate.patch').read_bytes(), original)
        self.assertEqual(counts, (fixture.state['local_attempts_today'], fixture.state['attempts_today']))
        self.assertFalse((directory / 'accepted.json').exists())

    def test_corrupt_or_unsafe_typed_metadata_cannot_become_legacy_completion(self):
        directory, _, _, _ = self.attempt()
        path = directory / 'worker-manifest.json'; original = path.read_bytes()
        (directory / 'worker-completion.json').unlink()
        self.b.write(directory / 'implementation-backend.json', {'kind': 'cloud'})
        for data in (b'{broken', b'null', b'{}', b'{"check_kind": []}'):
            with self.subTest(data=data):
                path.write_bytes(data)
                with self.assertRaisesRegex(self.b.ManagerHeld, 'terminal'):
                    self.b.require_worker_terminal(self.task, {}, directory, directory / 'work')
        path.unlink(); outside = self.root / 'outside-manifest.json'; outside.write_bytes(original)
        path.symlink_to(outside)
        with self.assertRaisesRegex(self.b.ManagerHeld, 'terminal'):
            self.b.require_worker_terminal(self.task, {}, directory, directory / 'work')
        self.assertEqual(outside.read_bytes(), original)

    def test_malformed_process_and_model_receipts_hold_without_changing_evidence(self):
        directory, _, _, _ = self.attempt()
        candidate = (directory / 'candidate.patch').read_bytes(); counts = copy.deepcopy(self.state)
        for name in ('worker-process.json', 'worker-model.json'):
            path = directory / name; original = path.read_bytes()
            for data in (b'{broken', b'\xff', b'null', b'[]', b'"' + b'x' * 96000 + b'"'):
                with self.subTest(file=name, data=data[:16]):
                    try:
                        path.write_bytes(data)
                        try:
                            self.b.require_worker_terminal(self.task, self.config, directory, directory / 'work')
                            outcome = None
                        except Exception as exc:outcome = type(exc)
                        self.assertEqual(path.read_bytes(), data)
                        self.assertEqual((directory / 'candidate.patch').read_bytes(), candidate)
                        self.assertEqual(self.state, counts)
                        self.assertIs(outcome, self.b.ManagerHeld, 'Malformed evidence escaped the held-candidate boundary')
                    finally:path.write_bytes(original)
            path.unlink()
            try:
                with self.assertRaises(self.b.ManagerHeld):
                    self.b.require_worker_terminal(self.task, self.config, directory, directory / 'work')
                outside = self.root / ('outside-' + name); outside.write_bytes(original); path.symlink_to(outside)
                with self.assertRaises(self.b.ManagerHeld):
                    self.b.require_worker_terminal(self.task, self.config, directory, directory / 'work')
                self.assertEqual(outside.read_bytes(), original)
            finally:
                if path.is_symlink():path.unlink()
                path.write_bytes(original)
        self.assertEqual(self.state, counts); self.assertEqual((directory / 'candidate.patch').read_bytes(), candidate)

    def test_malformed_micro_completion_is_retained_without_provider_or_coding_replay(self):
        fixture = ManagerFixtures.ManagerDispatchTests('test_intermediate_attempt_runs_real_focused_tests_then_only_checkpoints')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        task, run = fixture.interrupted_micro_review(); directory = fixture.s.STATE / 'runs' / run
        path = directory / 'worker-completion.json'; original = path.read_bytes()
        counts = copy.deepcopy(fixture.state); candidate = (directory / 'candidate.patch').read_bytes()
        for data in (b'{broken', b'null', b'\xff'):
            with self.subTest(data=data):
                try:
                    path.write_bytes(data)
                    with mock.patch.object(fixture.s, 'cloud') as provider, mock.patch.object(fixture.s, 'charged_run') as worker:
                        try:outcome = fixture.s.resume_micro_checkpoint(task, fixture.state, {},
                            fixture.s.now() + dt.timedelta(minutes=1), fixture.head)
                        except Exception as exc:outcome = type(exc).__name__
                    provider.assert_not_called(); worker.assert_not_called()
                    self.assertEqual(path.read_bytes(), data)
                    self.assertEqual((directory / 'candidate.patch').read_bytes(), candidate)
                    self.assertEqual(fixture.state, counts)
                    self.assertIs(outcome, False, 'Malformed completion escaped the safe recovery refusal')
                finally:path.write_bytes(original)

    def test_receipt_metadata_access_failure_holds_without_changing_candidate(self):
        directory, _, _, _ = self.attempt()
        candidate = (directory / 'candidate.patch').read_bytes(); counts = copy.deepcopy(self.state)
        with mock.patch.object(self.b.EC, 'safe_file', return_value=True), \
             mock.patch.object(Path, 'stat', side_effect=OSError('fixture receipt became unavailable')):
            try:
                self.b.require_worker_terminal(self.task, self.config, directory, directory / 'work')
                outcome = None
            except Exception as exc:outcome = type(exc)
        self.assertEqual((directory / 'candidate.patch').read_bytes(), candidate)
        self.assertEqual(self.state, counts); self.assertIs(outcome, self.b.ManagerHeld)


class DeliveryTerminalBoundary(unittest.TestCase):
    setUp = DeliveryFixtures.DeliveryRecoveryTests.setUp
    deliver = DeliveryFixtures.DeliveryRecoveryTests.deliver

    def test_terminal_hold_prevents_new_delivery_effects(self):
        gate = mock.Mock(side_effect=RuntimeError('terminal publication incomplete'))
        result = self.deliver(pre_prepare=gate)
        self.assertEqual(result['status'], 'pending'); gate.assert_called_once()
        self.assertEqual(self.api.posts, 0); self.assertEqual(self.api.merges, 0)
        self.assertTrue(all(method == 'GET' for _, method, _ in self.api.calls))
        self.assertFalse((self.directory / 'delivery-work').exists())
        self.assertTrue((self.directory / 'candidate.patch').exists())

    def test_completed_remote_merge_reconciles_without_new_completion_authority(self):
        self.api.uncertain_merge = True; gate = mock.Mock()
        self.assertEqual(self.deliver(pre_prepare=gate)['status'], 'pending')
        gate.assert_called_once(); gate.side_effect = RuntimeError('terminal evidence now unavailable')
        start = len(self.api.calls)
        result = self.deliver(pre_prepare=gate)
        self.assertEqual(result['status'], 'merged'); gate.assert_called_once()
        self.assertEqual(self.api.posts, 1); self.assertEqual(self.api.merges, 1)
        self.assertTrue(all(method == 'GET' for _, method, _ in self.api.calls[start:]))
        self.assertEqual(DeliveryFixtures.git(self.canonical, 'rev-parse', 'HEAD'), result['commit'])

    def test_uncertain_pr_creation_is_retained_when_completion_gate_later_holds(self):
        self.api.uncertain_create = True; gate = mock.Mock()
        self.assertEqual(self.deliver(pre_prepare=gate)['status'], 'pending')
        start = len(self.api.calls); gate.side_effect = RuntimeError('terminal publication incomplete')
        self.assertEqual(self.deliver(pre_prepare=gate)['status'], 'pending')
        self.assertEqual(self.api.posts, 1); self.assertEqual(self.api.merges, 0)
        self.assertTrue(all(method == 'GET' for _, method, _ in self.api.calls[start:]))
        self.assertTrue((self.directory / 'candidate.patch').exists())


if __name__ == '__main__':
    unittest.main()
