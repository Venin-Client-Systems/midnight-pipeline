import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import manager_recovery as M
import worker_quality as Q


class NarrowContinuationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.store=M.ManagerRecovery(self.root/'runtime','notes')
        self.parent={'id':'old','kind':'repair','files':['src/value.py','tests/test_value.py'],
                     'finding_ids':['value'],'acceptance':['Original full contract.']}
        self.task=dict(self.parent,id='narrow',local_worker_files=['tests/test_value.py'],
            retry_of={'task_id':'old','task_sha256':Q.task_fingerprint(self.parent)})
        self.checkpoint={'task_sha256':Q.task_fingerprint(self.parent),'patch_sha256':'b'*64,'run':'prior'}
        self.work=self.root/'work';(self.work/'src').mkdir(parents=True);(self.work/'src/value.py').write_text('frozen good code')
        self.frozen={'src/value.py':hashlib.sha256((self.work/'src/value.py').read_bytes()).hexdigest()}

    def approve(self,**kw):
        args=dict(task=self.task,parent=self.parent,episode='value:1',head='a'*40,profile_hash='profile',
            checkpoint=self.checkpoint,frozen_paths=self.frozen,
            review={'decision':'accept','issues':[],'receipt_sha256':'c'*64},
            evidence={'reason':'Only remaining tests need repair.','contract_comparison':{'original':self.parent['acceptance'],'final':self.task['acceptance']}})
        args.update(kw);return self.store.approve(**args)

    def test_dispatch_is_reserved_durably_and_cannot_be_refunded_or_reset_by_rename(self):
        self.approve();self.assertTrue(self.store.eligible(self.task,'value:1','a'*40,'profile'))
        for run in ['cancelled_before_launch','failed_local']:
            self.store.reserve(self.task,'value:1','a'*40,'profile',run)
        self.assertFalse(self.store.eligible(self.task,'value:1','a'*40,'profile'))
        with self.assertRaises(M.RecoveryHeld):self.store.reserve(self.task,'value:1','a'*40,'profile','third')
        renamed=dict(self.task,id='different')
        self.assertFalse(self.store.eligible(renamed,'value:1','a'*40,'profile'))
        with self.assertRaises(M.RecoveryHeld):self.approve(task=renamed)
        # Recovery is independent of the ordinary issue/cloud budget tables.
        from cloud_rescue import RescueStore
        self.assertEqual(RescueStore(self.root/'runtime/history.sqlite3').eligibility('notes','value:1')['attempts_168h'],0)
        self.assertEqual(len(self.store.tasks()),1)

    def test_only_independently_approved_strict_scope_subset_is_admitted(self):
        with self.assertRaises(M.RecoveryHeld):self.approve(task=dict(self.task,local_worker_files=self.parent['files']))
        with self.assertRaises(M.RecoveryHeld):self.approve(review={'decision':'hold','issues':[]})
        with self.assertRaises(M.RecoveryHeld):self.approve(task=dict(self.task,files=['src/other.py']))
        with self.assertRaises(M.RecoveryHeld):self.approve(task=dict(self.task,retry_of={'task_id':'old','task_sha256':'d'*64}))

    def test_source_profile_and_frozen_code_are_checked_again(self):
        self.approve();self.store.validate_frozen(self.task,'value:1',self.work)
        self.assertFalse(self.store.eligible(self.task,'value:1','b'*40,'profile'))
        self.assertFalse(self.store.eligible(self.task,'value:1','a'*40,'changed'))
        (self.work/'src/value.py').write_text('changed outside the narrow scope')
        with self.assertRaises(M.RecoveryHeld):self.store.validate_frozen(self.task,'value:1',self.work)

    def test_reader_does_not_create_runtime(self):
        missing=self.root/'missing';self.assertEqual(M.ManagerRecovery(missing,'x').tasks(),[])
        self.assertFalse(missing.exists())

    def test_single_reviewed_host_bug_replay_retains_both_charges_and_never_renews(self):
        self.approve();runs=['failed1','failed2']
        for run in runs:
            self.store.reserve(self.task,'value:1','a'*40,'profile',run)
            directory=self.store.store.path.parent/'runs'/run;(directory/'work').mkdir(parents=True)
            (directory/'task.json').write_text(json.dumps(self.task))
            (directory/'outcome.json').write_text(json.dumps({'accepted':False,'outcome':'failed'}))
            (directory/'work/PRD.md').write_text('Old host instruction contradicts the reviewed current test-only contract.')
        args=dict(task=self.task,episode='value:1',head='a'*40,profile_hash='profile',failed_runs=runs,
            comparison={'old':'Contradictory production-edit direction','corrected':'Current reviewed test-only contract'},
            review={'decision':'accept','issues':[],'receipt_sha256':'e'*64},
            corrected_backend_sha256=hashlib.sha256(Path(M.__file__).with_name('project_backend.py').read_bytes()).hexdigest())
        with self.assertRaises(M.RecoveryHeld):self.store.approve_host_replay(**dict(args,corrected_backend_sha256='f'*64))
        self.store.approve_host_replay(**args);self.store.approve_host_replay(**args)
        self.assertTrue(self.store.eligible(self.task,'value:1','a'*40,'profile'))
        receipt=self.store.reserve(self.task,'value:1','a'*40,'profile','corrected-host-dispatch')
        self.assertEqual(receipt['max_dispatches'],3)
        self.assertFalse(self.store.eligible(self.task,'value:1','a'*40,'profile'))
        with self.assertRaises(M.RecoveryHeld):self.store.reserve(self.task,'value:1','a'*40,'profile','fourth')
        with self.assertRaises(M.RecoveryHeld):self.store.approve_host_replay(**dict(args,comparison={'different':'Renew grant'}))


if __name__=='__main__':unittest.main()
