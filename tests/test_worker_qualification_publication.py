"""M20: real finish publication ordering and recovered pre-inference errors."""
import contextlib
import io
import json
import time
import unittest
from pathlib import Path
from unittest import mock

import structured_worker as S
import worker_probe_evidence as Q
import test_worker_qualification_receipts as Fixtures


class QualificationPublication(unittest.TestCase):
    setUp = Fixtures.QualificationReceipts.setUp
    write = Fixtures.QualificationReceipts.write
    representative_proof = Fixtures.QualificationReceipts.representative_proof

    def test_real_ready_marker_waits_for_terminal_tool_and_event(self):
        proof = self.representative_proof()
        directory = Path(proof['results'][-1]['directory'])
        work = directory / 'work'
        manifest_path = directory / 'worker-manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['timeout_seconds'] = 30
        self.write(manifest_path, manifest)
        manifest['_sha256'] = S.digest(manifest_path)
        worker = S.Worker(work, directory / 'worker-tools', manifest, self.model)
        value = {'action': 'write_file', 'path': 'solution.py', 'content': 'value=1\n', 'summary': 'changed'}
        with contextlib.redirect_stdout(io.StringIO()):
            worker.action(value, time.monotonic() + 5)
        log = directory / 'worker-tools/test-6/check.log'
        log.parent.mkdir();log.write_text('syntax passed')
        worker.last_test = {'signature': worker.signature(), 'sequence': 6,
            'result': {'code': 0, 'reason': 'exited', 'command': manifest['test_command'], 'log_sha256': S.digest(log)}}
        (directory / 'worker-tools/tool-result-7.json').unlink()
        (directory / 'worker-events.jsonl').write_text('')
        with mock.patch.object(S.M, 'local_descriptor', return_value=self.model), contextlib.redirect_stdout(io.StringIO()):
            result = worker.action({'action': 'finish', 'path': '', 'content': '', 'summary': 'done'}, time.monotonic() + 5)
        self.assertTrue(result['finished'])
        self.assertTrue(S.submission_complete(directory, work, manifest['task_id'], self.model))
        ready = getattr(Q, 'terminal_handoff_complete', S.submission_complete)
        self.assertFalse(ready(directory, work, manifest['task_id'], self.model), 'Host drained during final tool receipt publication')
        self.write(directory / 'worker-tools/tool-result-7.json', result)
        self.assertFalse(ready(directory, work, manifest['task_id'], self.model), 'Clean turn completion has not been emitted')
        (directory / 'worker-events.jsonl').write_text(json.dumps({'type': 'turn.completed', 'usage': {}}) + '\n')
        self.assertFalse(ready(directory, work, manifest['task_id'], self.model), 'Wrapper client completion remains pending')
        with (directory / 'worker-events.jsonl').open('a') as stream:
            stream.write(json.dumps({'type': 'worker.result_handoff', 'schema_version': 1, 'status': 'completed'}) + '\n')
            stream.write(json.dumps({'type': 'worker.client_result', 'schema_version': 1, 'exit_code': 0}) + '\n')
        self.assertTrue(ready(directory, work, manifest['task_id'], self.model))

    def insert_edit_precondition_error(self, directory):
        tools = directory / 'worker-tools'
        for prefix in ('decision', 'tool-result', 'edit'):
            paths = sorted(tools.glob(prefix + '-*.json'), key=lambda p: int(p.stem.rsplit('-', 1)[1]), reverse=True)
            for path in paths:
                number = int(path.stem.rsplit('-', 1)[1])
                path.rename(tools / (prefix + '-' + str(number + 1) + '.json'))
        original = json.loads((tools / 'decision-1.json').read_text())
        original['value'] = {'action': 'edit_file', 'path': 'solution.py', 'summary': 'attempt before read'}
        self.write(tools / 'decision-0.json', original)
        self.write(tools / 'tool-result-0.json', {'error': 'Read the current file before each edit'})

    def test_recovered_pre_inference_error_needs_no_invented_edit_receipt(self):
        proof = self.representative_proof()
        directory = Path(proof['results'][-1]['directory'])
        self.insert_edit_precondition_error(directory)
        self.assertFalse((directory / 'worker-tools/edit-0.json').exists())
        try:
            result = Q.phase_evidence(directory, self.model, True)
        except ValueError as exc:
            self.fail('A recovered pre-inference error was rejected: ' + str(exc))
        self.assertTrue(result['verified_sequence'])

    def test_failed_edit_inference_is_not_treated_as_precondition_error(self):
        proof = self.representative_proof()
        directory = Path(proof['results'][-1]['directory'])
        self.insert_edit_precondition_error(directory)
        self.write(directory / 'worker-tools/edit-0.json', {'status': 'failed', 'completion_reason': 'length'})
        with self.assertRaises(ValueError):
            Q.phase_evidence(directory, self.model, True)

    def test_successful_edit_cannot_omit_its_inference_receipt(self):
        proof = self.representative_proof()
        directory = Path(proof['results'][-1]['directory'])
        (directory / 'worker-tools/edit-5.json').unlink()
        with self.assertRaises(ValueError):
            Q.phase_evidence(directory, self.model, True)

    def test_uncertain_or_failed_terminal_events_do_not_complete(self):
        proof = self.representative_proof()
        directory = Path(proof['results'][-1]['directory'])
        path = directory / 'worker-events.jsonl'
        good = path.read_text()
        variants = [good[:-2], json.dumps({'type': 'turn.completed'}) + '\n']
        for code in (1, True, None):
            variants.append(json.dumps({'type': 'turn.completed'}) + '\n' + json.dumps(
                {'type': 'worker.client_result', 'schema_version': 1, 'exit_code': code}) + '\n')
        variants.append(good + json.dumps({'type': 'error', 'error': 'late failure'}) + '\n')
        for value in variants:
            path.write_text(value)
            with self.subTest(value=value), mock.patch.object(S, 'submission_complete', return_value=True):
                self.assertFalse(Q.terminal_handoff_complete(directory, directory / 'work', 'fixture', self.model))

    def test_explicit_handoff_must_be_unique_completed_and_ordered(self):
        # Establish a real finish/submission through the existing publication test.
        self.test_real_ready_marker_waits_for_terminal_tool_and_event()
        directory = self.root / 'summary_creation'
        task_id = json.loads((directory / 'worker-manifest.json').read_text())['task_id']
        turn = {'type': 'turn.completed'}
        handoff = {'type': 'worker.result_handoff', 'schema_version': 1, 'status': 'completed'}
        client = {'type': 'worker.client_result', 'schema_version': 1, 'exit_code': 0}
        path = directory / 'worker-events.jsonl'
        def check(events):
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            self.assertTrue(S.submission_complete(directory, directory / 'work', task_id, self.model))
            return Q.terminal_handoff_complete(directory, directory / 'work', task_id, self.model)
        self.assertTrue(check([turn, handoff, client]))
        variants = [[turn, client], [turn, handoff, handoff, client],
                    [handoff, turn, client], [turn, client, handoff],
                    [turn, handoff, dict(client, schema_version=True)]]
        for changes in ({'status': 'failed'}, {'status': None}, {'schema_version': 2},
                        {'schema_version': True}, {'unexpected': 'field'}):
            variants.append([turn, dict(handoff, **changes), client])
        variants.append([turn, {'type': 'worker.result_handoff'}, client])
        for events in variants:
            with self.subTest(events=events):
                self.assertFalse(check(events))


if __name__ == '__main__':
    unittest.main()
