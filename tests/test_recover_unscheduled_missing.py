"""An archived, unscheduled project must not block controller recovery.

Regression: SecondApp was archived (directory moved) while still
registered with on_schedule=false. Startup recovery raised FileNotFoundError
for it and left the controller in needs_attention, blocking exampleapp.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import midnight as M


class RecoverUnscheduledMissingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        jobs = mock.Mock(); jobs.jobs.return_value = []
        p = mock.patch.object(M.AW, 'Actions', return_value=jobs); p.start(); self.addCleanup(p.stop)

    def project(self, ident, path, on_schedule):
        return {'id': ident, 'path': str(path), 'controller': 'project_backend.py', 'on_schedule': on_schedule}

    def test_absent_unscheduled_project_is_skipped_without_launching_recovery(self):
        data = {'projects': [self.project('secondapp', self.root / 'moved-away', False)]}
        with mock.patch.object(M, 'validate_backend', side_effect=AssertionError('No backend for archived project')), \
             mock.patch.object(M.subprocess, 'run', side_effect=AssertionError('No recovery process')):
            result = M.recover_projects(data)
        self.assertEqual(result[0]['returncode'], 0)
        self.assertIn('recovery skipped', result[0]['detail'])

    def test_absent_scheduled_project_still_needs_attention(self):
        data = {'projects': [self.project('exampleapp', self.root / 'missing', True)]}
        with mock.patch.object(M, 'validate_backend', side_effect=FileNotFoundError('missing')):
            self.assertEqual(M.recover_projects(data)[0]['returncode'], 1)

    def test_present_unscheduled_project_is_still_recovered(self):
        present = self.root / 'present'; present.mkdir()
        data = {'projects': [self.project('notes', present, False)]}
        done = mock.Mock(returncode=0, stdout='{"status": "recovered"}', stderr='')
        with mock.patch.object(M, 'validate_backend'), mock.patch.object(M.subprocess, 'run', return_value=done) as run:
            self.assertEqual(M.recover_projects(data)[0]['returncode'], 0)
        run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
