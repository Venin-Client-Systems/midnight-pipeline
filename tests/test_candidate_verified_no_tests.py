"""A check that ran but found no tests is a failing verdict, not unavailable verification."""
import json
import tempfile
import unittest
from pathlib import Path

import project_backend as B


class CandidateVerifiedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.dir = Path(self.tmp.name)

    def checks(self, code, log='Ran 0 tests in 0.000s\n\nNO TESTS RAN\n', reason='exited'):
        (self.dir / 'checks.json').write_text(json.dumps([{'code': code, 'reason': reason, 'command': ['python3', '-m', 'unittest']}]))
        (self.dir / 'check-0.log').write_text(log)

    def test_no_tests_ran_is_a_completed_check(self):
        self.checks(5); self.assertTrue(B.candidate_verified(self.dir))

    def test_ordinary_failures_and_passes_remain_completed(self):
        for code in (0, 1, 2):
            with self.subTest(code=code):self.checks(code, log='ok\n'); self.assertTrue(B.candidate_verified(self.dir))

    def test_sandbox_or_environment_failures_stay_unavailable(self):
        self.checks(1, log='sandbox-exec: execvp() of python3 failed\n'); self.assertFalse(B.candidate_verified(self.dir))
        self.checks(5, reason='timeout'); self.assertFalse(B.candidate_verified(self.dir))
        self.checks(127, log='not found\n'); self.assertFalse(B.candidate_verified(self.dir))


if __name__ == '__main__':
    unittest.main()
