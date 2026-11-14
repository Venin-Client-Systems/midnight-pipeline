import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

import active_state as AS
import durability as D
from history_store import HistoryStore, encoded
import productive_status as P
import progress_recovery as R


class ProductiveStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.project = 'notes'
        self.instant = dt.datetime(2026, 11, 14, 1, tzinfo=dt.timezone.utc)
        self.state = {'accepted': [], 'done': []}

    def observe(self, **kwargs):
        return P.observe(self.root, self.project, self.state, instant=self.instant, **kwargs)

    def receipt(self, run='accepted', *, seconds=3600, commit='a' * 40):
        return {'id': 'T1', 'run': run, 'commit': commit, 'title': 'Verified change',
            'at': (self.instant - dt.timedelta(seconds=seconds)).isoformat()}

    def store(self):
        store = HistoryStore(self.root)
        with store.transaction() as con:
            for statement in (*AS.SCHEMA, *R.SCHEMA): con.execute(statement)
            con.execute('INSERT OR IGNORE INTO pr_scopes VALUES(?,?,?,?,?,?,?,?,?)',
                ('scope', self.project, 'episode', 'taskhash', 'task', 'a' * 40, 'profile', encoded({'task_id': 'T1'}), 1))
        return store

    def failure(self, run, *, legacy=False, kind='inference_truncated', finished=False, created=2):
        identity = {'version': 2, 'failure_class': kind, 'phase': 'decision',
            'worker_contract': 'typed-edits-chat-v3', 'model': {'model': 'fixture'},
            'inference_policy': {'version': 'fixture'}, 'requested_budget': 1024, 'components': {'worker': 'a' * 64}}
        value = {'run': run, 'kind': kind, 'observed_finished': finished,
            'fingerprint': hashlib.sha256(encoded(identity).encode()).hexdigest(),
            'failure_identity': None if legacy else identity}
        with self.store().transaction() as con:
            con.execute('INSERT INTO pr_results VALUES(?,?,?,?)', (run, 'scope', encoded(value), created))
        return value

    def test_running_without_receipts_never_establishes_acceptance_or_creates_files(self):
        before = list(self.root.iterdir())
        value = self.observe(active=True)
        self.assertTrue(value['available']); self.assertTrue(value['activity_observed'])
        self.assertFalse(value['activity_is_acceptance']); self.assertIsNone(value['last_accepted'])
        self.assertIsNone(value['seconds_since_accepted']); self.assertEqual(list(self.root.iterdir()), before)
        self.assertIn('No accepted commit receipt', P.summary(value))

    def test_latest_acceptance_comes_from_receipts_not_list_order_or_done_markers(self):
        self.state.update(accepted=[self.receipt('new', seconds=60), self.receipt('old', seconds=3600)], done=['later-unaccepted'])
        value = self.observe(); self.assertEqual(value['last_accepted']['run'], 'new')
        self.assertEqual(value['seconds_since_accepted'], 60)

    def test_archived_acceptance_can_be_newer_than_hot_history(self):
        self.state['accepted'] = [self.receipt('old')]; receipt = self.receipt('archive', seconds=30, commit='b' * 40)
        with self.store().transaction() as con:
            con.execute('INSERT INTO qc_terminal_acceptances VALUES(?,?,?,?,?)',
                (self.project, receipt['run'], receipt['id'], P.instant_value(receipt['at']), encoded(receipt)))
        value = self.observe(); self.assertEqual(value['last_accepted']['commit'], 'b' * 40)
        self.assertEqual(value['seconds_since_accepted'], 30)

    def test_malformed_unconfirmed_future_or_naive_acceptance_is_unknown(self):
        variants = [dict(self.receipt(), commit=None), dict(self.receipt(), at='2026-11-14T00:00:00'),
            self.receipt(seconds=-30), dict(self.receipt(), delivery='github', remote_merge_confirmed=False)]
        for receipt in variants:
            with self.subTest(receipt=receipt):
                self.state['accepted'] = [receipt]; value = self.observe()
                self.assertFalse(value['available']); self.assertIsNone(value['last_accepted'])
                self.assertIsNone(value['seconds_since_accepted']); self.assertTrue(value['errors'])

    def test_conflicting_hot_and_archived_acceptances_are_not_silently_selected(self):
        original = self.receipt(); self.state['accepted'] = [original]
        with self.store().transaction() as con:
            changed = dict(original, commit='b' * 40)
            con.execute('INSERT INTO qc_terminal_acceptances VALUES(?,?,?,?,?)',
                (self.project, changed['run'], changed['id'], P.instant_value(changed['at']), encoded(changed)))
        value = self.observe(); self.assertFalse(value['available']); self.assertIsNone(value['last_accepted'])

    def test_same_stable_failure_is_counted_across_distinct_runs_without_admitting_work(self):
        self.failure('run-one'); self.failure('renamed-run', created=3)
        value = self.observe(active=True); group = value['failure_groups'][0]
        self.assertTrue(group['stable_harness_identity']); self.assertEqual(group['observed_runs'], 2)
        self.assertEqual(group['runs'], ['renamed-run', 'run-one']); self.assertEqual(value['repeated_failure_groups'], 1)
        self.assertEqual(group['responsibility'], 'codex'); self.assertEqual(len(value['unfinished_handoffs']), 2)
        self.assertIsNone(value['last_accepted']); self.assertNotIn('allowed', group)

    def test_legacy_fingerprints_stay_explicitly_legacy(self):
        self.failure('legacy-one', legacy=True); self.failure('legacy-two', legacy=True)
        value = self.observe(); self.assertFalse(value['failure_groups'][0]['stable_harness_identity'])
        self.assertEqual(value['coverage']['legacy_identity_rows'], 2)

    def test_failed_publication_is_visible_even_when_saved_finish_flag_was_true(self):
        self.failure('failed-publication', kind='worker_contract', finished=True)
        value = self.observe(); self.assertEqual(len(value['unfinished_handoffs']), 1)
        self.assertTrue(value['unfinished_handoffs'][0]['reported_finished'])
        self.assertIsNone(value['last_accepted'])

    def test_current_control_and_harness_admission_responsibilities_remain_distinct(self):
        repair = {'scopes': [{'task_id': 'T1', 'scope': {'id': 'scope'},
            'admission': {'allowed': False, 'state': 'needs_harness_repair', 'reason': 'Unchanged worker failure'},
            'diagnosis': {'next_action': 'Codex must verify the harness correction'}}]}
        value = self.observe(repair=repair, control={'recovery_owner': 'owner', 'explanation': 'Owner paused'})
        self.assertEqual(value['responsibility'], 'owner'); self.assertEqual(value['admission'][0]['responsibility'], 'codex')
        self.assertFalse(value['admission'][0]['allowed']); self.assertTrue(value['admission'][0]['observational_only'])
        self.assertEqual(value['admission'][0]['reason'], 'Unchanged worker failure')

    def test_failure_history_is_bounded_and_filtered_to_this_project(self):
        for index in range(P.RESULT_LIMIT + 1): self.failure('run-' + str(index), created=index + 2)
        value = self.observe(); self.assertTrue(value['coverage']['failure_history_truncated'])
        self.assertEqual(value['failure_groups'][0]['observed_runs'], P.RESULT_LIMIT)
        self.assertEqual(value['coverage']['failure_rows'], P.RESULT_LIMIT)
        other = P.observe(self.root, 'other', {}, instant=self.instant)
        self.assertEqual(other['failure_groups'], []); self.assertTrue(other['available'])

    def test_oversized_hot_history_and_malformed_indexed_payload_are_unavailable(self):
        self.state['accepted'] = [self.receipt(str(i)) for i in range(P.ACCEPTED_LIMIT + 1)]
        value = self.observe(); self.assertFalse(value['available']); self.assertIsNone(value['last_accepted'])
        self.assertTrue(value['coverage']['accepted_hot_truncated']); self.state['accepted'] = []
        self.failure('bad')
        with self.store().transaction() as con: con.execute('UPDATE pr_results SET payload=?', ('null',))
        self.assertFalse(self.observe()['available'])

    def test_corrupt_stable_identity_cannot_be_reported_as_verified(self):
        self.failure('bad')
        with self.store().transaction() as con:
            row = json.loads(con.execute('SELECT payload FROM pr_results').fetchone()[0])
            row['fingerprint'] = 'f' * 64; con.execute('UPDATE pr_results SET payload=?', (encoded(row),))
        value = self.observe(); self.assertFalse(value['available']); self.assertEqual(value['failure_groups'], [])

    def test_read_path_never_writes_requests_models_or_changes_application_rows(self):
        self.failure('one'); store = self.store()
        with store.reader() as con: before = list(con.iterdump())
        with mock.patch.object(D, 'atomic_write', side_effect=AssertionError('No durable writes')), \
             mock.patch('subprocess.Popen', side_effect=AssertionError('No processes or providers')):
            for _ in range(3): self.assertTrue(self.observe()['available'])
        with store.reader() as con: self.assertEqual(list(con.iterdump()), before)

    def test_locked_history_returns_unavailable_within_observation_budget(self):
        store = self.store(); con = sqlite3.connect(store.path, isolation_level=None)
        self.addCleanup(con.close); con.execute('BEGIN EXCLUSIVE')
        started = time.monotonic(); value = self.observe(); elapsed = time.monotonic() - started
        con.execute('ROLLBACK')
        self.assertFalse(value['available']); self.assertLess(elapsed, 0.5)


    def test_malformed_observation_inputs_and_admission_identity_are_explicit(self):
        for kwargs in ({'control': []}, {'repair': 'bad'}, {'repair': {'scopes': [{'task_id': 'T1', 'scope': [], 'admission': {'allowed': False}}]}}):
            with self.subTest(kwargs=kwargs): self.assertFalse(self.observe(**kwargs)['available'])
        self.assertFalse(P.observe(self.root, self.project, [], instant=self.instant)['available'])
        self.assertEqual(self.observe(control={'recovery_owner': []})['responsibility'], 'unknown')

    def test_oversized_sql_payload_is_not_loaded_in_full(self):
        self.failure('large')
        with self.store().transaction() as con:
            con.execute('UPDATE pr_results SET payload=?', ('x' * (P.PAYLOAD_LIMIT * 4),))
        seen = []
        original = P.payload
        def limited(raw):
            seen.append(len(raw)); return original(raw)
        with mock.patch.object(P, 'payload', side_effect=limited): value = self.observe()
        self.assertFalse(value['available']); self.assertEqual(max(seen), P.PAYLOAD_LIMIT + 1)

    def test_backend_report_preserves_failed_worker_evidence_without_model_calls(self):
        import project_backend as B
        backend = B.load_backend(self.root, self.project)
        backend.STATE = self.root
        self.failure('failed'); self.failure('failed-again', created=3)
        with mock.patch.object(backend, 'now', return_value=self.instant), \
             mock.patch('subprocess.Popen', side_effect=AssertionError('No model or process calls')):
            backend.report(self.state, 'needs_attention', detail='Worker completion contract failed')
        view = json.loads((self.root / 'status.json').read_text())['productivity']
        self.assertEqual(view['responsibility'], 'codex'); self.assertEqual(view['repeated_failure_groups'], 1)
        self.assertIsNone(view['last_accepted'])
        report = (self.root / 'morning-report.md').read_text()
        self.assertIn('Worker completion contract failed', report); self.assertIn('inference_truncated', report)
        self.assertIn('No accepted commit receipt', report)

    def test_guardian_responsiveness_stays_separate_from_productive_evidence(self):
        import guardian as G
        from action_store import Actions
        controller = self.root / 'controller'; runtime = controller / 'runtime'; runtime.mkdir(parents=True)
        (controller / 'projects.json').write_text(json.dumps({'timezone': 'Australia/Adelaide',
            'schedule': {'start': '00:00', 'end': '09:00'},
            'projects': [{'id': self.project, 'on_schedule': True, 'state_dir': str(self.root)}]}))
        (runtime / 'PAUSED').touch()
        (runtime / 'status.json').write_text(json.dumps({'phase': 'waiting', 'updated_at': self.instant.isoformat()}))
        (self.root / 'status.json').write_text(json.dumps({'phase': 'needs_attention', 'detail': 'Harness must be repaired'}))
        (self.root / 'state.json').write_text(json.dumps(self.state)); self.failure('failed')
        restart = mock.Mock(side_effect=AssertionError('No restart')); spawn = mock.Mock(side_effect=AssertionError('No worker'))
        with mock.patch.object(G, 'check_authority', return_value={'permission_sha256': 'fixture'}), \
             mock.patch('subprocess.Popen', side_effect=AssertionError('No model or process calls')):
            value = G.audit(controller, instant=self.instant, restart=restart, spawn=spawn, notify=lambda *a: None)
        self.assertEqual(value['state'], 'paused'); self.assertEqual(value['issues'], 0)
        progress = value['productive_progress'][self.project]
        self.assertEqual(progress['responsibility'], 'codex'); self.assertEqual(len(progress['unfinished_handoffs']), 1)
        self.assertIsNone(progress['last_accepted'])
        self.assertEqual(Actions(runtime).meta('guardian')['productive_progress'], value['productive_progress'])

    def test_project_snapshot_dashboard_and_oversight_keep_productive_evidence_read_only(self):
        import midnight as M
        import dashboard as DB
        import oversight as O
        import coordinator as K
        import dashboard_details as DD
        import performance
        from contextlib import ExitStack
        runtime = self.root / 'runtime'; runtime.mkdir()
        project = {'id': self.project, 'name': 'Notes', 'on_schedule': True, 'state_dir': str(self.root),
            'path': str(self.root), 'backlog': 'backlog.json', 'config': 'config.json', 'local_model': 'fixture',
            'why': 'Fixture', 'instruction': 'Fixture', 'controller': 'backend.py'}
        registry = {'projects': [project], 'timezone': 'Australia/Adelaide', 'schedule': {'start': '00:00', 'end': '09:00'}}
        self.state['accepted'] = [self.receipt()]
        (self.root / 'state.json').write_text(json.dumps(self.state)); (self.root / 'backlog.json').write_text('[]')
        (self.root / 'status.json').write_text(json.dumps({'phase': 'needs_attention', 'detail': 'Harness must be repaired'}))
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        backend = mock.Mock(); backend.all_tasks.return_value = []
        with ExitStack() as stack:
            for patch in (mock.patch.object(M, 'STATE', runtime), mock.patch.object(M, 'registry', return_value=registry),
                    mock.patch.object(M, 'now', return_value=self.instant), mock.patch.object(M.MM, 'snapshot', return_value={}),
                    mock.patch.object(M.PR, 'lease', return_value={'pid': 123, 'boot_id': 'fixture', 'stamp': 'fixture'}),
                    mock.patch.object(M, 'improvement_snapshot', return_value={}),
                    mock.patch('subprocess.Popen', side_effect=AssertionError('No model or process calls'))): stack.enter_context(patch)
            project_view = M.project_snapshot(project)
            self.assertEqual(project_view['productivity']['last_accepted']['commit'], 'a' * 40)
            self.assertEqual(project_view['productivity']['seconds_since_accepted'], 3600)
            base = {'pipeline': 'Midnight', 'checked_at': self.instant.isoformat(), 'phase': 'waiting',
                'service': {}, 'schedule': registry['schedule'], 'timezone': registry['timezone'],
                'override': None, 'override_active': False, 'next_start': self.instant.isoformat(),
                'projects': [project_view], 'recent_sessions': []}
            with mock.patch.object(M, 'snapshot', return_value=base), \
                 mock.patch.object(M, 'doctor', return_value={'status': 'healthy', 'cold_boot_requirement': 'Login required'}), \
                 mock.patch.object(performance, 'collect', return_value={'projects': []}):
                dashboard = DB.collect()
            with mock.patch.object(K, 'settings', return_value={'mode': 'shadow'}), \
                 mock.patch.object(K, 'decisions', return_value=[]), mock.patch.object(K, 'project_backend', return_value=backend), \
                 mock.patch.object(DD, 'inspect_request', return_value={'runs': []}):
                oversight = O.collect(dashboard)
        self.assertEqual(oversight['projects'][0]['productivity'], project_view['productivity'])
        after = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)


    def test_unhashable_admission_task_returns_unavailable_without_changing_records(self):
        self.failure('one'); store = self.store()
        with store.reader() as con: before = list(con.iterdump())
        value = self.observe(repair={'scopes': [{'task_id': [], 'scope': {'id': 'scope'},
            'admission': {'allowed': False, 'state': 'needs_codex', 'reason': 'Repair required'}}]})
        self.assertFalse(value['available']); self.assertIsNone(value['last_accepted'])
        with store.reader() as con: self.assertEqual(list(con.iterdump()), before)

    def test_nonfinite_failure_identity_returns_unavailable_without_changing_record(self):
        value = self.failure('bad')
        value['failure_identity']['requested_budget'] = float('nan')
        raw = json.dumps(value)
        with self.store().transaction() as con: con.execute('UPDATE pr_results SET payload=?', (raw,))
        result = self.observe(); self.assertFalse(result['available']); self.assertEqual(result['failure_groups'], [])
        with self.store().reader() as con: self.assertEqual(con.execute('SELECT payload FROM pr_results').fetchone()[0], raw)

    def test_acceptance_index_run_and_task_must_match_payload(self):
        receipt = self.receipt()
        for run, task in [('wrong-run', receipt['id']), (receipt['run'], 'wrong-task')]:
            with self.subTest(run=run, task=task):
                with self.store().transaction() as con:
                    con.execute('DELETE FROM qc_terminal_acceptances')
                    con.execute('INSERT INTO qc_terminal_acceptances VALUES(?,?,?,?,?)',
                        (self.project, run, task, P.instant_value(receipt['at']), encoded(receipt)))
                value = self.observe(); self.assertFalse(value['available']); self.assertIsNone(value['last_accepted'])
                self.assertIsNone(value['seconds_since_accepted'])

    def test_newer_indexed_run_cannot_hide_conflicting_older_hot_receipt(self):
        older = self.receipt('older'); self.state['accepted'] = [older]
        changed = dict(older, commit='b' * 40); latest = self.receipt('latest', seconds=10, commit='c' * 40)
        with self.store().transaction() as con:
            for receipt in (changed, latest):
                con.execute('INSERT INTO qc_terminal_acceptances VALUES(?,?,?,?,?)',
                    (self.project, receipt['run'], receipt['id'], P.instant_value(receipt['at']), encoded(receipt)))
        value = self.observe(); self.assertFalse(value['available']); self.assertIsNone(value['last_accepted'])
        self.assertIsNone(value['seconds_since_accepted'])

    def test_partial_recovery_schema_is_not_verified_empty_history(self):
        store = self.store(); self.failure('retained')
        with store.transaction() as con: con.execute('DROP TABLE pr_scopes')
        value = self.observe(); self.assertFalse(value['available']); self.assertTrue(value['errors'])
        with store.reader() as con: self.assertEqual(con.execute('SELECT COUNT(*) FROM pr_results').fetchone()[0], 1)

    def test_orphan_result_cannot_disappear_from_history_coverage(self):
        self.failure('orphan')
        with self.store().transaction() as con: con.execute('DELETE FROM pr_scopes')
        value = self.observe(); self.assertFalse(value['available']); self.assertTrue(value['errors'])
        with self.store().reader() as con: self.assertEqual(con.execute('SELECT COUNT(*) FROM pr_results').fetchone()[0], 1)

    def test_unavailable_admission_snapshot_does_not_look_like_no_holds(self):
        self.state['accepted'] = [self.receipt()]
        value = self.observe(repair={'available': False, 'scopes': []})
        self.assertFalse(value['available']); self.assertIsNone(value['last_accepted'])
        self.assertTrue(value['errors'])


    def test_numeric_acceptance_time_is_projected_as_iso_for_all_consumers(self):
        receipt = self.receipt(seconds=60); expected = receipt['at']
        receipt['at'] = P.instant_value(expected); self.state['accepted'] = [receipt]
        value = self.observe(); self.assertTrue(value['available'])
        self.assertEqual(value['last_accepted']['at'], expected); self.assertEqual(value['seconds_since_accepted'], 60)
        self.assertIsInstance(receipt['at'], float)


if __name__ == '__main__': unittest.main()
