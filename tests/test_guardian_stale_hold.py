"""A project held through a work window must reach the owner within an hour.

Regression: expected waits (manager_hold etc.) had no age limit, so the
guardian reported issues=0 through a six-day stall and a multi-hour stall.
The notice is owner-visible only: never an incident, restart or repair.
"""
import datetime as dt
import unittest
from unittest import mock

import guardian as G
import test_guardian as TG
from action_store import Actions


class StaleHoldTests(unittest.TestCase):
    setUp = TG.GuardianTests.setUp
    save = TG.GuardianTests.save

    def hold(self, detail='Current writable recovery source is unavailable'):
        self.save(self.project / 'status.json', {'phase': 'manager_hold', 'detail': detail})
        # Keep the override window open for the whole test.
        self.save(self.runtime / 'override.json', {'expires_at': (self.instant + dt.timedelta(hours=12)).isoformat()})

    def audit(self, minutes, notify):
        at = self.instant + dt.timedelta(minutes=minutes)
        self.save(self.runtime / 'status.json', {'phase': 'working', 'updated_at': at.isoformat()})
        return G.audit(self.root, instant=at, restart=self.restart, spawn=self.spawn, notify=notify)

    def setUpMocks(self):
        self.restart = mock.Mock(); self.spawn = mock.Mock()

    def test_hold_under_threshold_does_not_notify(self):
        self.setUpMocks(); self.hold(); notify = mock.Mock(return_value=True)
        self.audit(0, notify); result = self.audit(30, notify)
        notify.assert_not_called(); self.assertEqual(result['stale_holds'], [])

    def test_hold_over_threshold_notifies_once_without_incident_or_repair(self):
        self.setUpMocks(); self.hold(); notify = mock.Mock(return_value=True)
        self.audit(0, notify); result = self.audit(61, notify)
        notify.assert_called_once()
        title, message = notify.call_args.args
        self.assertEqual(title, 'Midnight stalled: notes'); self.assertIn('manager_hold for 1h01m', message)
        self.assertIn('notes', result['stale_holds'])
        self.audit(62, notify); notify.assert_called_once()
        self.restart.assert_not_called(); self.spawn.assert_not_called()
        self.assertEqual(Actions(self.runtime).incidents(), [])

    def test_a_finished_backlog_never_notifies_but_a_refused_plan_does(self):
        self.setUpMocks(); notify = mock.Mock(return_value=True)
        self.save(self.runtime / 'override.json', {'expires_at': (self.instant + dt.timedelta(hours=12)).isoformat()})
        wait = {'until': (self.instant + dt.timedelta(hours=3)).isoformat(), 'reason': 'Nothing material remains.', 'finished': True}
        self.save(self.project / 'status.json', {'phase': 'no_useful_work', 'planning': wait})
        self.audit(0, notify); result = self.audit(61, notify)
        notify.assert_not_called(); self.assertEqual(result['stale_holds'], [])
        refused = dict(wait, finished=False, reason='Planner proposed changes to immutable protected tests: tests/test_a.py')
        self.save(self.project / 'status.json', {'phase': 'no_useful_work', 'planning': refused})
        self.audit(62, notify); result = self.audit(130, notify)
        notify.assert_called_once(); self.assertIn('notes', result['stale_holds'])

    def test_renotifies_only_after_the_renotify_interval(self):
        self.setUpMocks(); self.hold(); notify = mock.Mock(return_value=True)
        self.audit(0, notify); self.audit(61, notify); self.audit(61 + 300, notify)
        self.assertEqual(notify.call_count, 1)
        self.audit(61 + 361, notify); self.assertEqual(notify.call_count, 2)

    def test_changed_hold_detail_restarts_the_clock(self):
        self.setUpMocks(); self.hold(); notify = mock.Mock(return_value=True)
        self.audit(0, notify); self.hold('A different task is now held'); self.audit(50, notify)
        self.audit(100, notify); notify.assert_not_called()
        self.audit(111, notify); notify.assert_called_once()

    def test_working_project_clears_the_hold(self):
        self.setUpMocks(); self.hold(); notify = mock.Mock(return_value=True)
        self.audit(0, notify)
        self.save(self.project / 'status.json', {'phase': 'running'}); self.audit(30, notify)
        self.hold(); self.audit(70, notify); notify.assert_not_called()

    def test_pause_never_notifies(self):
        self.setUpMocks(); self.hold(); notify = mock.Mock(return_value=True)
        (self.runtime / 'PAUSED').touch()
        self.audit(0, notify); self.audit(120, notify); notify.assert_not_called()

    def test_controller_needs_attention_is_also_tracked(self):
        self.setUpMocks(); self.save(self.project / 'status.json', {'phase': 'running'})
        self.save(self.runtime / 'override.json', {'expires_at': (self.instant + dt.timedelta(hours=12)).isoformat()})
        value = G.observation(self.root, self.instant)
        value['controller'] = {'phase': 'needs_attention', 'detail': 'Project recovery is busy or requires inspection'}
        meta = {}
        G.stale_holds(value, meta, self.instant)
        self.assertEqual(G.stale_holds(value, meta, self.instant + dt.timedelta(minutes=61)), ['midnight'])

    def test_temporary_roots_never_raise_real_notifications(self):
        self.setUpMocks(); self.hold()
        with mock.patch.object(G, 'notify_owner') as real:
            for minutes in (0, 61):
                at = self.instant + dt.timedelta(minutes=minutes)
                G.audit(self.root, instant=at, restart=self.restart, spawn=self.spawn)
        real.assert_not_called()

    def test_notification_passes_text_as_arguments_not_script(self):
        with mock.patch.object(G.subprocess, 'run') as run:
            G.notify_owner('Midnight stalled: x', 'detail with "quotes" and \\ and end run')
        command = run.call_args.args[0]
        self.assertEqual(command[-1], 'detail with "quotes" and \\ and end run')
        self.assertNotIn('quotes', ' '.join(command[:-2]))


if __name__ == '__main__':
    unittest.main()
