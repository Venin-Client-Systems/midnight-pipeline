"""The actual representative PRD must dispatch through the frozen Ralphy loop."""
import ast
import importlib.util
import json
from pathlib import Path
import unittest

import worker_probe_cases as C

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('qualification_adapter_fixture',
    SOURCE / 'ralphy/test_adapter.py')
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)


def representative_brief(task):
    # worker_probe.py is an executable native entry. Evaluate only its actual
    # pure PRD construction, avoiding imports/entry effects and a test-side copy.
    tree = ast.parse((SOURCE / 'worker_probe.py').read_text())
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Subscript)
                           and isinstance(target.value, ast.Name) and target.value.id == 'inputs'
                           and isinstance(target.slice, ast.Constant) and target.slice.value == 'PRD.md'
                           for target in node.targets)]
    if len(assignments) != 1:
        raise AssertionError('Expected one explicit representative PRD construction')
    context = C.context_files(task, '')['host_context']
    return eval(compile(ast.Expression(assignments[0].value), '<qualification-brief>', 'eval'),
                {'C': C, 'task': task, 'context': context}), context


class QualificationDispatchTests(unittest.TestCase):
    def test_each_production_sized_case_invokes_exactly_one_real_wrapper_client(self):
        for name, _, _, task in C.cases():
            with self.subTest(case=name):
                fixture = A.AdapterTests('test_help_does_not_invoke_model')
                fixture.setUp()
                try:
                    brief, context = representative_brief(task)
                    self.assertGreaterEqual(len(brief.encode()), C.MIN_BRIEF_BYTES)
                    self.assertIn(context, brief)
                    self.assertIn(task, brief)
                    for path in C.REQUIRED_READS:
                        self.assertIn(path, brief)
                    (fixture.repo / 'PRD.md').write_text(brief)
                    fixture.install_fake()
                    result = fixture.invoke('--prd', 'PRD.md')
                    self.assertTrue(fixture.events.exists(), result.stdout + result.stderr)
                    records = [json.loads(line) for line in fixture.events.read_text().splitlines()]
                    self.assertEqual(len([r for r in records if r['type'] == 'adapter_test']), 1)
                    self.assertEqual([r for r in records if r['type'] == 'worker.client_result'],
                        [{'type': 'worker.client_result', 'schema_version': 1, 'exit_code': 0}])
                    self.assertEqual(len([line for line in brief.splitlines() if line.startswith('- [ ] ')]), 1)
                    self.assertIn('- [ ] ' + task, brief)
                finally:
                    fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
