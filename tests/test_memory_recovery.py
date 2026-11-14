import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import durability as D
import memory_coordination as C
import memory_recovery as R
import backup_coordination as B


class MemoryRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.model = {'model': 'colibri/glm-5.3', 'parameters_sha256': 'f' * 64}

    def receipt(self, kind='charged_limit', drained=True):
        return {'kind': kind, 'drain': {'owned_processes_drained': drained},
                'receipt_sha256': 'a' * 64, 'receipt_path': '/retained/failure.json'}

    def test_same_request_stays_held_across_time_and_restart(self):
        R.record(self.root, 'input', self.model, self.receipt(), instant=10)
        for instant in (20, 86400, 9 * 86400):
            with self.assertRaises(R.MemoryHeld):
                R.check(self.root, 'input', self.model, quiet=True, instant=instant)
        R.check(self.root, 'different-input', self.model, quiet=True, instant=20)
        R.check(self.root, 'input', dict(self.model, parameters_sha256='e' * 64), quiet=True, instant=20)

    def test_resource_admission_dependencies_cannot_enter_automatic_harness_patch(self):
        import maintenance_recovery as M
        expected={'project_action.py','commissioning.py','schedule_control.py','memory_admission.py',
                  'memory_coordination.py','memory_recovery.py','backup_coordination.py','telemetry.py','colibri_observation.py'}
        self.assertTrue(expected.issubset(M.KERNEL))

    def test_generation_only_change_does_not_renew_memory_admission(self):
        R.record(self.root, 'input', dict(self.model,promotion_generation=1), self.receipt(), instant=10)
        with self.assertRaises(R.MemoryHeld):
            R.check(self.root,'input',dict(self.model,promotion_generation=2),quiet=True,instant=90000)

    def test_native_available_memory_refusal_needs_new_quiet_interval(self):
        value=R.record(self.root,'input',self.model,self.receipt('native_startup_memory'),instant=10)
        self.assertEqual(value['retry_after'],1810)
        with self.assertRaises(R.MemoryHeld):R.check(self.root,'input',self.model,quiet=False,instant=1900)
        R.check(self.root,'input',self.model,quiet=True,instant=1900)
        R.reserve_recovery(self.root,'input',self.model,instant=1900)
        with self.assertRaises(R.MemoryHeld):R.check(self.root,'input',self.model,quiet=True,instant=999999)

    def test_pressure_requires_backoff_quiet_and_one_reservation(self):
        saved = R.record(self.root, 'input', self.model, self.receipt('host_pressure'), instant=10)
        self.assertEqual(saved['retry_after'], 1810)
        for instant, quiet in [(1809, True), (2000, False)]:
            with self.assertRaises(R.MemoryHeld):R.check(self.root, 'input', self.model, instant=instant, quiet=quiet)
        R.check(self.root, 'input', self.model, instant=2000, quiet=True)
        R.reserve_recovery(self.root, 'input', self.model, instant=2000)
        with self.assertRaises(R.MemoryHeld):R.reserve_recovery(self.root, 'input', self.model, instant=2000)
        with self.assertRaises(R.MemoryHeld):R.check(self.root, 'input', self.model, instant=3000, quiet=True)
        R.complete(self.root, 'input', self.model)
        R.check(self.root, 'input', self.model, instant=3000, quiet=True)

    def test_duplicate_receipt_does_not_increase_failure_count(self):
        first = R.record(self.root, 'input', self.model, self.receipt(), instant=10)
        second = R.record(self.root, 'input', self.model, self.receipt(), instant=20)
        self.assertEqual(first, second)

    def test_unconfirmed_cleanup_cannot_recover_from_time(self):
        value = R.record(self.root, 'input', self.model, self.receipt('host_pressure', False), instant=10)
        self.assertEqual(value['kind'], 'cleanup_unconfirmed')
        with self.assertRaises(R.MemoryHeld):R.check(self.root, 'input', self.model, instant=10000, quiet=True)

    def test_only_bound_host_failure_event_is_accepted(self):
        folder=self.root/'models/colibri/sessions'/'session';folder.mkdir(parents=True)
        receipt={'input_key':'input','model':self.model,'action_id':'action','session':'session','kind':'charged_limit',
                 'profile_sha256':self.model['parameters_sha256'],'input_sha256':'b'*64}
        intent=folder/'failure-intent.json';intent.write_text(json.dumps(receipt))
        receipt['failure_intent_sha256']=hashlib.sha256(intent.read_bytes()).hexdigest()
        path=folder/'failure.json';path.write_text(json.dumps(receipt))
        event={'type':'model.failure','receipt_path':str(path),'receipt_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        log=self.root/'events.jsonl';log.write_text(json.dumps({'type':'item.completed','item':{'text':json.dumps(event)}})+'\n'+json.dumps(event)+'\n')
        self.assertEqual(R.interrupted_failure(self.root,'input',self.model,'action')['drain'],{'owned_processes_drained':False})
        self.assertIsNone(R.interrupted_failure(self.root,'input',self.model,'other'))
        self.assertIsNotNone(R.failure_from_events(self.root,log,'input',self.model,'action'))
        self.assertIsNone(R.failure_from_events(self.root,log,'other',self.model,'action'))
        self.assertIsNone(R.failure_from_events(self.root,log,'input',self.model,'other'))
        path.write_text('{}')
        self.assertIsNone(R.failure_from_events(self.root,log,'input',self.model,'action'))

    def published_failure(self, session='crashed', kind='charged_limit', observed=10):
        folder = self.root/'models/colibri/sessions'/session
        folder.mkdir(parents=True)
        value = {'version': 1, 'session': session, 'request_id': 1,
                 'input_key': 'input', 'input_sha256': 'b'*64, 'action_id': 'old-job',
                 'model': self.model, 'profile_sha256': self.model['parameters_sha256'],
                 'kind': kind, 'observed_at': observed}
        (folder/'failure-intent.json').write_text(json.dumps(value))
        return folder, value

    def finish_failure(self, folder, value):
        unloaded = folder/'unloaded.json'
        unloaded.write_text(json.dumps({'model': self.model, 'owned_processes_drained': True}))
        final = dict(value, failure_intent_sha256=hashlib.sha256((folder/'failure-intent.json').read_bytes()).hexdigest(),
                     drain={'owned_processes_drained': True, 'unloaded_sha256': hashlib.sha256(unloaded.read_bytes()).hexdigest()})
        (folder/'failure.json').write_text(json.dumps(final))

    def test_crash_after_intent_before_backend_record_blocks_new_job_precharge(self):
        self.published_failure()
        charged = []
        with self.assertRaises(R.MemoryHeld) as held:
            R.check(self.root, 'input', dict(self.model, promotion_generation=99), quiet=True, instant=86400)
            charged.append('new-job')
        self.assertEqual(charged, [])
        self.assertEqual(held.exception.record['kind'], 'cleanup_unconfirmed')
        self.assertEqual(held.exception.record['failures'], 1)
        with self.assertRaises(R.MemoryHeld):
            R.reserve_recovery(self.root, 'input', self.model, instant=172800)

    def test_crash_after_final_receipt_before_backend_record_keeps_structural_hold(self):
        folder, value = self.published_failure()
        self.finish_failure(folder, value)
        for instant in (100, 86400):
            with self.assertRaises(R.MemoryHeld) as held:
                R.check(self.root, 'input', self.model, quiet=True, instant=instant)
            self.assertEqual(held.exception.record['kind'], 'charged_limit')
            self.assertEqual(held.exception.record['failures'], 1)

    def test_late_verified_drain_refines_original_failure_once(self):
        folder, value = self.published_failure(kind='host_pressure')
        with self.assertRaises(R.MemoryHeld):
            R.check(self.root, 'input', self.model, quiet=True, instant=10)
        self.finish_failure(folder, value)
        with self.assertRaises(R.MemoryHeld) as held:
            R.check(self.root, 'input', self.model, quiet=True, instant=20)
        self.assertEqual(held.exception.record['failures'], 1)
        self.assertEqual(held.exception.record['retry_after'], 1820)
        self.assertEqual(held.exception.record['kind'], 'host_pressure')
        R.check(self.root, 'input', self.model, quiet=True, instant=1820)
        R.reserve_recovery(self.root, 'input', self.model, instant=1820)
        with self.assertRaises(R.MemoryHeld):
            R.check(self.root, 'input', self.model, quiet=True, instant=999999)
        R.complete(self.root, 'input', self.model)
        R.check(self.root, 'input', self.model, quiet=True, instant=999999)

    def test_old_receipts_do_not_recount_or_resurrect_after_recovery(self):
        for name, at in [('first', 10), ('second', 20)]:
            folder, value = self.published_failure(session=name, kind='host_pressure', observed=at)
            self.finish_failure(folder, value)
        for instant in (30, 40):
            with self.assertRaises(R.MemoryHeld) as held:
                R.check(self.root, 'input', self.model, quiet=True, instant=instant)
            self.assertEqual(held.exception.record['failures'], 2)
        R.complete(self.root, 'input', self.model)
        R.check(self.root, 'input', self.model, quiet=True, instant=90000)

    def test_unverified_drain_or_wrong_identity_cannot_clear_crash_hold(self):
        folder, value = self.published_failure(kind='host_pressure')
        self.finish_failure(folder, value)
        (folder/'unloaded.json').write_text('{}')
        with self.assertRaises(R.MemoryHeld) as held:
            R.check(self.root, 'input', self.model, quiet=True, instant=90000)
        self.assertEqual(held.exception.record['kind'], 'cleanup_unconfirmed')
        R.check(self.root, 'other', self.model, quiet=True, instant=90000)
        R.check(self.root, 'input', dict(self.model, parameters_sha256='e'*64), quiet=True, instant=90000)

    def test_later_pressure_failure_cannot_erase_earlier_structural_failure(self):
        for name, kind, at in [('first', 'charged_limit', 10), ('second', 'host_pressure', 20)]:
            folder, value = self.published_failure(session=name, kind=kind, observed=at)
            self.finish_failure(folder, value)
        for instant in (30, 90000):
            with self.assertRaises(R.MemoryHeld) as held:
                R.check(self.root, 'input', self.model, quiet=True, instant=instant)
            self.assertEqual(held.exception.record['kind'], 'charged_limit')
            self.assertEqual(held.exception.record['failures'], 2)

    def test_older_unconfirmed_cleanup_survives_later_pressure_until_exact_drain(self):
        first, value = self.published_failure(session='first', kind='host_pressure', observed=10)
        second, newer = self.published_failure(session='second', kind='host_pressure', observed=20)
        self.finish_failure(second, newer)
        for instant in (30, 90000):
            with self.assertRaises(R.MemoryHeld) as held:
                R.check(self.root, 'input', self.model, quiet=True, instant=instant)
            self.assertEqual(held.exception.record['kind'], 'cleanup_unconfirmed')
        self.finish_failure(first, value)
        with self.assertRaises(R.MemoryHeld) as held:
            R.check(self.root, 'input', self.model, quiet=True, instant=90010)
        self.assertEqual(held.exception.record['kind'], 'host_pressure')
        self.assertEqual(held.exception.record['failures'], 2)
        self.assertEqual(held.exception.record['retry_after'], 93610)
        R.check(self.root, 'input', self.model, quiet=True, instant=93610)


class Coordination(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.runtime=self.root/'runtime'
        D.save_json(self.runtime/'memory-coordination.json',{'version':1,'enabled':True})

    def test_foreign_execution_blocks_even_without_flock_owner(self):
        with patch.object(C.P,'Actions') as store,patch.object(C.P,'live_tree',return_value=True):
            store.return_value.executions.return_value=[{'phase':'foreign','kind':'local','lease':{}}]
            self.assertTrue(C.surviving_execution(self.runtime,'ours'))
            self.assertFalse(C.surviving_execution(self.runtime,'foreign'))

    def test_dead_backup_without_completion_is_not_declared_drained(self):
        D.save_json(self.runtime/'memory-backup-owner.json',{'state':'active','owner':{'boot_id':D.boot_id()}})
        with patch.object(C.P,'Actions') as store:
            store.return_value.executions.return_value=[]
            self.assertTrue(C.surviving_execution(self.runtime))

    def test_backup_admission_never_stops_unrelated_processes(self):
        coordinator=B.Coordination(self.root)
        with patch.object(coordinator,'blocked',return_value=(True,'midnight_weekday_priority')),patch.object(B.P,'stop_tree') as stop:
            self.assertEqual(coordinator.acquire(),(False,'midnight_weekday_priority'))
            stop.assert_not_called()

    def test_priority_yield_keeps_incomplete_semantics(self):
        coordinator=B.Coordination(self.root);coordinator.record={'state':'active'}
        with patch.object(coordinator,'blocked',return_value=(True,'midnight_weekday_priority')):
            with self.assertRaisesRegex(B.BackupYield,'incomplete'):coordinator.check()

    def test_cleanup_evidence_required_before_memory_release(self):
        coordinator=B.Coordination(self.root)
        coordinator.record={'state':'active','owner':{'boot_id':D.boot_id()},'members':[{'pid':123}]}
        with patch.object(B.P,'live_tree',return_value=True):coordinator.close(True)
        self.assertEqual(D.read_json(self.runtime/'memory-backup-owner.json')['state'],'cleanup_unconfirmed')

    def test_weekday_boundary_and_weekend_backup(self):
        import datetime as dt
        from zoneinfo import ZoneInfo
        D.save_json(self.root/'projects.json',{'timezone':'Australia/Adelaide','schedule':{'start':'00:00','end':'09:00','weekdays':['mon','tue','wed','thu','fri']}})
        for instant,blocked in [('2026-11-08T23:55:00',True),('2026-11-09T08:59:59',True),('2026-11-09T09:00:00',False),('2026-11-07T01:00:00',False)]:
            self.assertEqual(C.backup_blocked(self.root,dt.datetime.fromisoformat(instant).replace(tzinfo=ZoneInfo('Australia/Adelaide')))[0],blocked)
