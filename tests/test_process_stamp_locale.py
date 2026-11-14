"""Process identity stamps must not change with the caller's locale.

`ps -o lstart=` prints day and month in the order the locale prefers. A guard
started with a cleaned environment and a controller started from a terminal
then disagree about the same process, and a phase that succeeded is rejected.
"""
import os
import unittest
from unittest import mock

import process_observer as PO


class StampLocaleTests(unittest.TestCase):
    def setUp(self):
        env = {k: v for k, v in os.environ.items() if k != 'MIDNIGHT_CONFINED_TESTS'}
        env.update(LANG='en_AU.UTF-8', LC_TIME='en_AU.UTF-8'); env.pop('LC_ALL', None)
        patch = mock.patch.dict(os.environ, env, clear=True); patch.start(); self.addCleanup(patch.stop)

    def test_ps_stamp_matches_the_native_stamp_under_a_day_first_locale(self):
        native = PO.info(os.getpid())['stamp']
        listed = PO.run(['/bin/ps', '-p', str(os.getpid()), '-o', 'lstart='], capture_output=True, text=True).stdout
        checked = PO.check_output(['/bin/ps', '-p', str(os.getpid()), '-o', 'lstart='], text=True)
        self.assertEqual(' '.join(listed.split()), ' '.join(native.split()))
        self.assertEqual(listed, checked)

    def test_a_caller_supplied_environment_is_kept_apart_from_the_locale(self):
        with mock.patch.object(PO.subprocess, 'run') as run:
            PO.run(['/bin/ps'], env={'PATH': '/usr/bin', 'LANG': 'fr_FR.UTF-8'})
        self.assertEqual(run.call_args.kwargs['env'], {'PATH': '/usr/bin', 'LC_ALL': 'C'})


if __name__ == '__main__':
    unittest.main()
