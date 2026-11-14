"""The owner CLI refuses to burn the one-use grant outside the base schedule.

Regression: activated during a daytime override, the grant was
consumed in under a second with no attempt and then blocked all dispatch.
"""
import datetime as dt
import sys
import unittest
from unittest import mock

import midnight as M


class CommissioningWindowTests(unittest.TestCase):
    def activate(self, scheduled):
        argv = ['midnight', 'commissioning', '--activate', '--project', 'exampleapp', '--task', 'T',
                '--until', '2026-11-13T08:00', '--reason', 'owner repair']
        timing = {'scheduled_active': scheduled, 'next_start': dt.datetime(2026, 11, 13, tzinfo=dt.timezone.utc)}
        fixed = dt.datetime(2026, 11, 12, 14, tzinfo=dt.timezone.utc)  # 00:30 Adelaide, before --until
        with mock.patch.object(M, 'now', return_value=fixed), mock.patch.object(sys, 'argv', argv), mock.patch.object(M, 'registry', return_value={'timezone': 'Australia/Adelaide', 'projects': []}), \
             mock.patch.object(M, 'window', return_value=timing), \
             mock.patch.object(M.COM, 'activate', return_value={'state': 'armed'}) as activate, \
             mock.patch.object(M.PR, 'source_lock'), mock.patch.object(M.MM, 'task_selection'), \
             mock.patch.object(M.PA, 'source_head', return_value='a' * 40), mock.patch.object(M, 'kick'), \
             mock.patch('builtins.print'):
            return M.main(), activate

    def test_outside_base_schedule_is_refused_before_any_grant(self):
        with mock.patch.object(M.COM, 'activate') as activate:
            with self.assertRaisesRegex(ValueError, 'base schedule'):self.activate(False)

    def test_inside_base_schedule_activates(self):
        code, activate = self.activate(True)
        self.assertEqual(code, 0); activate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
