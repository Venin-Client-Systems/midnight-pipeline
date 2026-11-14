"""An uncreated required file is named plainly in retry feedback.

Regression: R005F's worker never created tests/test_unit_workspace.py; the
only feedback was the runner's ModuleNotFoundError, twice.
"""
import tempfile
import unittest
from pathlib import Path

import worker_quality as Q


class ContractFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name); (self.work / 'pkg').mkdir(); (self.work / 'pkg' / 'workspace.py').write_text('x=1\n')
        self.task = {'files': ['pkg/workspace.py', 'tests/test_unit_workspace.py']}

    def test_missing_declared_test_file_is_named(self):
        text = Q.contract_feedback(self.work, self.task)
        self.assertIn('never created: tests/test_unit_workspace.py', text)
        self.assertNotIn('pkg/workspace.py', text)

    def test_complete_candidate_adds_nothing(self):
        (self.work / 'tests').mkdir(); (self.work / 'tests' / 'test_unit_workspace.py').write_text('')
        self.assertEqual(Q.contract_feedback(self.work, self.task), '')

    def test_local_worker_files_take_precedence_and_globs_are_ignored(self):
        task = {'files': ['a.py'], 'local_worker_files': ['pkg/workspace.py', 'tests/*.py']}
        self.assertEqual(Q.missing_declared_files(self.work, task), [])


if __name__ == '__main__':
    unittest.main()
