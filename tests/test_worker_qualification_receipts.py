"""Qualification evidence boundaries; disposable files and no inference."""
import ast
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import structured_worker as S
import worker_recovery as W
import worker_probe_cases as C
import worker_probe_evidence as Q


class QualificationReceipts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = Path(W.__file__).resolve().parent
        self.model = {'model': 'fixture', 'digest': 'a' * 64, 'context': 32768}

    def legacy_proof(self):
        results = []
        for name in ('label', 'range'):
            directory = self.root / name
            files = {}
            for relative in ('work/solution.py', 'work/test_solution.py',
                             'work/.midnight-ready.json', 'worker-manifest.json',
                             'worker-events.jsonl', 'worker-tools/submission.json',
                             'independent-host-check/check.log'):
                path = directory / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({'task_id': 'fixture'}) if relative == 'worker-manifest.json' else 'fixture')
                files[relative] = S.digest(path)
            results.append({'case': name, 'directory': str(directory), 'passed': True,
                            'ready_handoff': True, 'scope_ok': True,
                            'host_check_code': 0, 'host_check_reason': 'exited',
                            'actual_tool_events': 2, 'files': files})
        return {'schema_version': 1, 'passed': True, 'tool_contract': S.CONTRACT,
                'product_attempts': 0, 'cloud_calls': 0, 'model': self.model,
                'worker_sources': {n: S.digest(self.source / n) for n in W.SOURCES},
                'qualification_sources': {n: S.digest(self.source / n) for n in
                    ('worker_probe.py', 'worker_probe_runtime.py', 'worker_probe_client.py', 'worker_recovery.py')},
                'results': results}

    def test_fixed_only_record_cannot_certify_representative_worker(self):
        path = self.root / 'proof.json'
        path.write_text(json.dumps(self.legacy_proof()))
        with mock.patch.object(S, 'submission_complete', return_value=True):
            with self.assertRaisesRegex(ValueError, 'representative'):
                W.qualification(path, self.model, source_root=self.source)

    def test_legacy_record_can_be_inspected_without_claiming_representative_readiness(self):
        path = self.root / 'proof.json'
        path.write_text(json.dumps(self.legacy_proof()))
        with mock.patch.object(S, 'submission_complete', return_value=True):
            result = W.qualification(path, self.model, source_root=self.source, allow_legacy=True)
        self.assertFalse(result['representative'])

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def representative_proof(self):
        tree = ast.parse((self.source / 'worker_probe.py').read_text())
        original = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == 'cases' for t in n.targets))
        self.assertEqual({name: __import__('hashlib').sha256(oracle.encode()).hexdigest()
                          for name, _, oracle, _ in original}, C.LEGACY_ORACLE_SHA256)
        results = []
        for name, _, oracle, _ in original + C.cases():
            directory = self.root / name
            work = directory / 'work'
            work.mkdir(parents=True)
            manifest = {'version': 1, 'workspace': str(work), 'allowed_files': ['solution.py'],
                        'task_id': 'typed-worker-qualification-' + name,
                        'test_command': S.syntax_command(['solution.py'], '/opt/homebrew/bin:/usr/bin:/bin')}
            self.write(directory / 'worker-manifest.json', manifest)
            paths = ['work/solution.py', 'work/.midnight-ready.json', 'worker-events.jsonl',
                     'worker-tools/submission.json', 'independent-host-check/check.log', 'work/AGENTS.md']
            if name in C.REPRESENTATIVE_CASES:
                paths += ['work/contracts.md', 'work/fixtures.json', 'work/reference.py']
            for relative in paths:
                path = directory / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture')
            (work / 'test_solution.py').write_text(oracle)
            (work / 'PRD.md').write_text('x' * C.MIN_BRIEF_BYTES)
            (directory / 'worker-events.jsonl').write_text(json.dumps({'type': 'turn.completed', 'usage': {}}) + '\n'
                + json.dumps({'type': 'worker.result_handoff', 'schema_version': 1, 'status': 'completed'}) + '\n'
                + json.dumps({'type': 'worker.client_result', 'schema_version': 1, 'exit_code': 0}) + '\n')
            phases = [('read_file', p, {'path': p, 'content': 'fixture'}) for p in C.REQUIRED_READS]
            phases += [('edit_file', 'solution.py', {'written': 'solution.py'}),
                       ('run_tests', '', {'code': 0, 'reason': 'exited'}),
                       ('finish', '', {'finished': True})]
            policy = {'version': S.INFERENCE_POLICY, 'model': self.model,
                      'thinking_capable': True, 'metadata_sha256': 'b' * 64}
            for i, (action, path, output) in enumerate(phases):
                receipt = {'schema_version': 2, 'status': 'completed', 'completion_reason': 'stop',
                           'phase': 'decision', 'requested_budget': 1024,
                           'worker_contract': S.CONTRACT, 'model': self.model, 'inference_policy': policy,
                           'metrics': {'prompt_eval_count': C.MIN_PROMPT_TOKENS, 'eval_count': 40, 'done_reason': 'stop'},
                           'value': {'action': action, 'path': path, 'summary': 'fixture'}}
                self.write(directory / f'worker-tools/decision-{i}.json', receipt)
                self.write(directory / f'worker-tools/tool-result-{i}.json', output)
                if action == 'edit_file':
                    self.write(directory / f'worker-tools/edit-{i}.json', dict(receipt, phase='edit', requested_budget=4096,
                               value={'old_text': 'a', 'new_text': 'b'}))
            names = paths + ['worker-manifest.json', 'work/test_solution.py', 'work/PRD.md']
            results.append({'case': name, 'directory': str(directory), 'passed': True,
                            'ready_handoff': True, 'scope_ok': True, 'host_check_code': 0,
                            'host_check_reason': 'exited', 'actual_tool_events': 2,
                            'protected_inputs_unchanged': True, 'unrelated_source_unchanged': True,
                            'source_existed_before': name != 'summary_creation', 'brief_bytes': C.MIN_BRIEF_BYTES,
                            'files': {n: S.digest(directory / n) for n in names},
                            'phase_evidence': Q.phase_evidence(directory, self.model, name in C.REPRESENTATIVE_CASES)})
        return {'schema_version': 2, 'passed': True, 'tool_contract': S.CONTRACT,
                'product_attempts': 0, 'cloud_calls': 0, 'model': self.model, 'results': results,
                'worker_sources': {n: S.digest(self.source / n) for n in W.SOURCES},
                'qualification_sources': {n: S.digest(self.source / n) for n in Q.QUALIFICATION_SOURCES},
                'execution_dependencies': {n: S.digest(self.source / n) for n in Q.EXECUTION_DEPENDENCIES},
                'inference_policy': S.INFERENCE_POLICY}

    def verify(self, proof):
        path = self.root / 'proof.json'
        self.write(path, proof)
        with mock.patch.object(S, 'submission_complete', return_value=True):
            return W.qualification(path, self.model, source_root=self.source)

    def test_complete_representative_receipt_verifies(self):
        self.assertTrue(self.verify(self.representative_proof())['representative'])

    def test_failed_handoff_cannot_be_hidden_by_rehashing_events(self):
        proof = self.representative_proof()
        row = proof['results'][-1]
        path = Path(row['directory']) / 'worker-events.jsonl'
        events = [{'type': 'turn.completed'},
                  {'type': 'worker.result_handoff', 'schema_version': 1, 'status': 'failed'},
                  {'type': 'worker.client_result', 'schema_version': 1, 'exit_code': 0}]
        path.write_text(''.join(json.dumps(event) + '\n' for event in events))
        row['files']['worker-events.jsonl'] = S.digest(path)
        with self.assertRaisesRegex(ValueError, 'terminal publication'):
            self.verify(proof)

    def test_stale_model_policy_sources_and_missing_case_fail(self):
        proof = self.representative_proof()
        changes = {'model': {'model': 'other'}, 'inference_policy': 'old',
                   'worker_sources': {}, 'qualification_sources': {}, 'execution_dependencies': {},
                   'results': proof['results'][:-1]}
        for key, value in changes.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify(dict(proof, **{key: value}))

    def test_changed_oracle_fails_even_with_updated_hash(self):
        proof = self.representative_proof()
        for row in proof['results']:
            path = Path(row['directory']) / 'work/test_solution.py'
            before = path.read_text()
            path.write_text('print("weakened")')
            original = row['files']['work/test_solution.py']
            row['files']['work/test_solution.py'] = S.digest(path)
            with self.subTest(case=row['case']), self.assertRaisesRegex(ValueError, 'oracle'):
                self.verify(proof)
            path.write_text(before)
            row['files']['work/test_solution.py'] = original

    def test_creation_must_have_been_absent(self):
        proof = self.representative_proof()
        proof['results'][-1]['source_existed_before'] = True
        with self.assertRaises(ValueError):
            self.verify(proof)

    def test_truncated_or_changed_phase_receipt_is_rejected(self):
        proof = self.representative_proof()
        path = Path(proof['results'][-1]['directory']) / 'worker-tools/decision-5.json'
        before = json.loads(path.read_text())
        for change in ({'completion_reason': 'length'}, {'status': 'failed'}, {'requested_budget': 2048},
                       {'inference_policy': {}}, {'metrics': {'prompt_eval_count': 14000, 'eval_count': 1025, 'done_reason': 'stop'}}):
            self.write(path, dict(before, **change))
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.verify(proof)
        self.write(path, before)

    def test_required_read_and_context_coverage_cannot_be_skipped(self):
        proof = self.representative_proof()
        directory = Path(proof['results'][-1]['directory'])
        path = directory / 'worker-tools/tool-result-0.json'
        self.write(path, {'error': 'read failed'})
        with self.assertRaisesRegex(ValueError, 'reads'):
            Q.phase_evidence(directory, self.model, True)
        self.write(path, {'path': C.REQUIRED_READS[0], 'content': 'fixture'})
        for path in (directory / 'worker-tools').glob('decision-*.json'):
            value = json.loads(path.read_text());value['metrics']['prompt_eval_count'] = 10
            self.write(path, value)
        with self.assertRaisesRegex(ValueError, 'context'):
            Q.phase_evidence(directory, self.model, True)

    def test_finish_requires_successful_check_after_edit(self):
        proof = self.representative_proof()
        directory = Path(proof['results'][-1]['directory'])
        self.write(directory / 'worker-tools/tool-result-6.json', {'code': 1, 'reason': 'exited'})
        with self.assertRaisesRegex(ValueError, 'sequence'):
            Q.phase_evidence(directory, self.model, True)


if __name__ == '__main__':
    unittest.main()
