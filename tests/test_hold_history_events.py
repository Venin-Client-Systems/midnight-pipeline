"""Holds leave durable history: one event when entered or changed, one when released."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import project_backend as B


class HoldHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); (root / 'product').mkdir()
        self.s = B.load_backend(root / 'product', 'fixture')
        self.s.STATE = root / 'state'; self.s.STATE.mkdir()
        self.instant = dt.datetime(2026, 11, 12, 9, tzinfo=dt.timezone.utc)
        p = mock.patch.object(self.s, 'now', side_effect=lambda: self.instant); p.start(); self.addCleanup(p.stop)
        self.state = {'accepted': []}

    def events(self):
        path = self.s.STATE / 'events.jsonl'
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    def at(self, minutes):
        self.instant = dt.datetime(2026, 11, 12, 9, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=minutes)

    def test_repeated_identical_hold_is_one_event(self):
        for minutes in (0, 1, 2):
            self.at(minutes); self.s.report(self.state, 'manager_hold', detail='Task A held')
        holds = [e for e in self.events() if e['event'] == 'project_hold']
        self.assertEqual(len(holds), 1); self.assertEqual(holds[0]['detail'], 'Task A held')

    def test_changed_detail_is_a_new_hold_event(self):
        self.s.report(self.state, 'manager_hold', detail='Task A held')
        self.s.report(self.state, 'manager_hold', detail='Task B held')
        self.assertEqual([e['detail'] for e in self.events() if e['event'] == 'project_hold'], ['Task A held', 'Task B held'])

    def test_release_records_duration_once(self):
        self.s.report(self.state, 'manager_hold', detail='Task A held')
        self.at(95); self.s.report(self.state, 'running')
        self.at(96); self.s.report(self.state, 'running')
        cleared = [e for e in self.events() if e['event'] == 'project_hold_cleared']
        self.assertEqual(len(cleared), 1); self.assertEqual(cleared[0]['held_seconds'], 95 * 60)
        self.assertEqual(cleared[0]['next_phase'], 'running')

    def test_all_held_report_lists_tasks(self):
        self.s.report(self.state, 'manager_hold', detail='first', tasks={'B': {}, 'A': {}})
        self.assertEqual(self.events()[0]['tasks'], ['A', 'B'])

    def test_morning_report_shows_the_hold(self):
        self.s.report(self.state, 'manager_hold', detail='Task A held')
        text = (self.s.STATE / 'morning-report.md').read_text()
        self.assertIn('HELD since 2026-11-12T09:00:00+00:00: manager_hold: Task A held', text)

    def test_working_phases_emit_nothing(self):
        for phase in ('running', 'retry_or_next_task', 'accepted'):
            self.s.report(self.state, phase)
        self.assertEqual(self.events(), [])

    def test_corrupt_tracker_never_breaks_reporting(self):
        (self.s.STATE / 'hold-tracker.json').write_text('{not json')
        self.s.report(self.state, 'manager_hold', detail='Task A held')
        self.assertEqual(json.loads((self.s.STATE / 'status.json').read_text())['phase'], 'manager_hold')


if __name__ == '__main__':
    unittest.main()
