"""Real temporary ledger and flock checks; process metadata and model are fake."""
import copy
import fcntl
import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import colibri_provider as GP
import durability as D
import memory_runtime as MR
import model_bench as MB
import phase_runtime as P
from action_store import Actions


class ProviderMemoryProof(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.runtime=self.root/'runtime'
        self.model={'model':GP.MODEL,'digest':'a'*64,'context':32768,'bytes':100,
                    'parameters_sha256':'b'*64,'promotion_generation':None}
        self.key='c'*64;self.deadline=time.time()+3600
        self.owner={'pid':11001,'stamp':'owner','birth_us':1,'boot_id':'fixture'}
        self.guard={'pid':11002,'stamp':'guard','birth_us':2,'boot_id':'fixture','group':True}
        self.worker={'pid':os.getpid(),'stamp':'worker','birth_us':3,'boot_id':'fixture'}
        self.store=Actions(self.runtime)
        self.job=self.store.submit('fixture',{'worker_model':self.model},self.deadline)
        self.store.claim(self.job['id'],self.owner)
        phase=self.store.phase(self.job['id'],'fixture','local',self.key,self.deadline-1,{})
        self.phase=phase['id'];self.store.phase_update(self.phase,'running',lease=self.guard)
        self.store.execution(self.phase,self.guard);self.store.execution_members(self.phase,[self.worker])
        self.folder=self.runtime/'phases'/self.phase;self.folder.mkdir(parents=True)
        D.save_json(self.runtime/'memory-coordination.json',{'version':1,'enabled':True})
        self.handles={};self.paths={}
        for name,filename in (('gpu','gpu.lock'),('heavy','heavy-memory.lock'),('local','local-0.lock')):
            path=self.runtime/'resources'/filename;handle=path.open('a')
            self.addCleanup(handle.close);fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.handles[name]=handle;self.paths[name]=path
        self.proof={'version':1,'job':self.job['id'],'phase':self.phase,'input_key':self.key,
            'model_sha256':P.fingerprint(MR.MEM.resource_model(self.model)),
            'owner_deadline':self.deadline,'admitted_at':time.time(),
            'boot_id':D.boot_id(),'quiet_observed_monotonic':time.monotonic(),
            'quiet':{'ready':True,'quiet_seconds':120,'sample_count':25,
                     'minimum_free_inactive_bytes':24_000_000_000,'swap_out_bytes':0,
                     'latest':{'at':time.time(),'boot_id':D.boot_id(),'pressure':'normal',
                               'free_inactive_bytes':24_000_000_000}},
            'sources':MR.pins(),'slot_name':'local-0.lock','locks':{}}
        for name,handle in self.handles.items():
            info=os.fstat(handle.fileno());self.proof['locks'][name]={'device':info.st_dev,'inode':info.st_ino}
        self.launch={'slot_name':'local-0.lock','memory_slot':'heavy-memory.lock',
                     'gpu_lease':True,'fresh_controls':True}
        self.env={'MIDNIGHT_ACTION_ID':self.job['id'],'MIDNIGHT_MEMORY_ADMISSION_PHASE':self.phase,
                  'MIDNIGHT_RESOURCE_INPUT_KEY':self.key}
        self.publish()
        for target,name,value in ((MB,'GPU_LOCK',self.paths['gpu']),(GP,'phase_profile',lambda model:{}),
                                  (P,'alive',lambda lease:True),(P,'descendants',lambda lease:[self.worker]),
                                  (P,'control_stopped',lambda *args:False),(MR.os,'getpgrp',lambda:self.guard['pid'])):
            mocked=patch.object(target,name,value);mocked.start();self.addCleanup(mocked.stop)

    def publish(self):
        MR.save(self.folder/'memory-admission-proof.json',self.proof)
        self.launch['memory_admission_sha256']=hashlib.sha256((self.folder/'memory-admission-proof.json').read_bytes()).hexdigest()
        MR.save(self.folder/'launch.json',self.launch)

    def validate(self,**kwargs):
        return MR.validate_provider_start(self.root,kwargs.get('model',self.model),
                                         kwargs.get('key',self.key),kwargs.get('env',self.env))

    def test_valid_guard_accepts_actual_execution_lease_with_member_enrichment(self):
        execution=self.store.executions()[0]
        self.assertIn('members',execution['lease']);self.assertIn('_execution_revision',execution['lease'])
        self.assertNotEqual(self.store.phases()[0]['lease'],execution['lease'])
        self.assertEqual(self.validate(),self.proof)

    def test_absent_unmanaged_or_linked_proof_never_admits(self):
        with self.assertRaises(ValueError):self.validate(env={})
        path=self.folder/'memory-admission-proof.json';saved=path.read_bytes();path.unlink()
        with self.assertRaises(ValueError):self.validate()
        elsewhere=self.root/'foreign.json';elsewhere.write_bytes(saved);path.symlink_to(elsewhere)
        with self.assertRaises(ValueError):self.validate()

    def test_stale_future_or_changed_original_deadline_never_admits(self):
        original=copy.deepcopy(self.proof)
        for changes in ({'admitted_at':time.time()-61},{'admitted_at':time.time()+60},
                        {'owner_deadline':self.deadline+1}):
            with self.subTest(changes=changes):
                self.proof=dict(original,**changes);self.publish()
                with self.assertRaises(ValueError):self.validate()

    def test_twenty_second_handoff_rejects_sealed_proof_and_preserves_charge(self):
        with self.store.write() as con:
            con.execute('INSERT INTO wf_charges VALUES(?,?,?,?,?,?)',
                        (self.job['id'],'original','fixture','session','local',time.time()))
        before=(self.folder/'memory-admission-proof.json').read_bytes()
        with patch.object(MR.time,'time',return_value=self.proof['admitted_at']+20), \
             patch.object(MR.time,'monotonic',return_value=self.proof['quiet_observed_monotonic']+20), \
             patch.object(GP,'phase_profile') as profile:
            with self.assertRaises(ValueError):self.validate()
        profile.assert_not_called()
        self.assertEqual((self.folder/'memory-admission-proof.json').read_bytes(),before)
        with self.store.read() as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM wf_charges').fetchone()[0],1)

    def test_fresh_written_proof_cannot_hide_old_observation_or_clock_discontinuity(self):
        original=copy.deepcopy(self.proof)
        for changed in ('old_sample','old_monotonic','future_sample','future_monotonic','different_boot','missing_latest'):
            with self.subTest(changed=changed):
                self.proof=copy.deepcopy(original)
                if changed=='old_sample':self.proof['quiet']['latest']['at']-=20
                elif changed=='old_monotonic':self.proof['quiet_observed_monotonic']-=20
                elif changed=='future_sample':self.proof['quiet']['latest']['at']+=20
                elif changed=='future_monotonic':self.proof['quiet_observed_monotonic']+=20
                elif changed=='different_boot':self.proof['boot_id']='another-boot'
                else:self.proof['quiet']['latest']=None
                self.publish()
                with self.assertRaises(ValueError):self.validate()

    def test_validator_rechecks_freshness_after_profile_verification(self):
        wall=self.proof['quiet']['latest']['at'];mono=self.proof['quiet_observed_monotonic'];clock=[wall,mono]
        def delayed_profile(_):clock[0]+=20;clock[1]+=20
        with patch.object(MR.time,'time',side_effect=lambda:clock[0]), \
             patch.object(MR.time,'monotonic',side_effect=lambda:clock[1]), \
             patch.object(GP,'phase_profile',side_effect=delayed_profile):
            with self.assertRaisesRegex(ValueError,'expired during guarded handoff'):self.validate()

    def test_final_boot_observation_delay_cannot_hide_behind_wall_clock_adjustment(self):
        wall=max(self.proof['admitted_at'],self.proof['quiet']['latest']['at'])
        monotonic=[self.proof['quiet_observed_monotonic']];calls=[0]
        def boot():
            calls[0]+=1
            if calls[0]==2:monotonic[0]+=20
            return self.proof['boot_id']
        with patch.object(MR.time,'time',return_value=wall), \
             patch.object(MR.time,'monotonic',side_effect=lambda:monotonic[0]), \
             patch.object(MR.D,'boot_id',side_effect=boot):
            with self.assertRaisesRegex(ValueError,'expired during guarded handoff'):self.validate()

    def test_changed_model_or_input_binding_never_admits(self):
        with self.assertRaises(ValueError):self.validate(model=dict(self.model,parameters_sha256='d'*64))
        with self.assertRaises(ValueError):self.validate(key='d'*64)
        with self.assertRaises(ValueError):self.validate(env=dict(self.env,MIDNIGHT_RESOURCE_INPUT_KEY='d'*64))

    def test_source_and_quiet_evidence_must_match_even_with_resealed_launch(self):
        original=copy.deepcopy(self.proof)
        for change in ('source','quiet'):
            with self.subTest(change=change):
                self.proof=copy.deepcopy(original)
                if change=='source':self.proof['sources']['memory_runtime.py']='0'*64
                else:self.proof['quiet']['ready']=False
                self.publish()
                with self.assertRaises(ValueError):self.validate()

    def test_launch_hash_and_declared_lock_contract_cannot_change(self):
        original=copy.deepcopy(self.launch)
        for changes in ({'memory_admission_sha256':'0'*64},{'slot_name':'host-0.lock'},
                        {'memory_slot':None},{'gpu_lease':False},{'fresh_controls':False}):
            with self.subTest(changes=changes):
                self.launch=dict(original,**changes);MR.save(self.folder/'launch.json',self.launch)
                with self.assertRaises(ValueError):self.validate()

    def test_dead_owner_guard_or_foreign_worker_never_admits(self):
        for dead in (self.owner['pid'],self.guard['pid']):
            with self.subTest(dead=dead),patch.object(P,'alive',side_effect=lambda lease:lease['pid']!=dead):
                with self.assertRaises(ValueError):self.validate()
        with patch.object(P,'descendants',return_value=[]):
            with self.assertRaises(ValueError):self.validate()
        with patch.object(MR.os,'getpgrp',return_value=9999):
            with self.assertRaises(ValueError):self.validate()

    def test_current_owner_control_or_disabled_coordination_stops_native_start(self):
        with patch.object(P,'control_stopped',return_value=True):
            with self.assertRaises(ValueError):self.validate()
        D.save_json(self.runtime/'memory-coordination.json',{'version':1,'enabled':False})
        with self.assertRaises(ValueError):self.validate()

    def test_terminal_phase_or_job_cannot_reuse_admission(self):
        self.store.phase_update(self.phase,'failed',result={'fixture':True})
        with self.assertRaises(ValueError):self.validate()
        self.store.finish(self.job['id'],'interrupted',{'fixture':True})
        with self.assertRaises(ValueError):self.validate()

    def test_each_released_resource_lock_prevents_start(self):
        for name,handle in self.handles.items():
            with self.subTest(name=name):
                fcntl.flock(handle,fcntl.LOCK_UN)
                with self.assertRaises(ValueError):self.validate()
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.assertEqual(self.validate(),self.proof)

    def test_replaced_lock_inode_cannot_borrow_other_held_lock(self):
        for name,path in self.paths.items():
            with self.subTest(name=name):
                old=path.with_suffix('.original');path.rename(old)
                try:
                    with path.open('a') as replacement:
                        fcntl.flock(replacement,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        with self.assertRaises(ValueError):self.validate()
                finally:path.unlink();old.rename(path)


if __name__=='__main__':unittest.main()
