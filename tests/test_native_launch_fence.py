"""Global uncertainty fences use private receipts, never model text or PID absence."""
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import action_store as A
import backup_coordination as B
import durability as D
import memory_coordination as M
import phase_runtime as P


class NativeLaunchFence(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.runtime=self.root/'runtime'
        self.folder=self.runtime/'models/colibri/sessions'/('a'*32);self.folder.mkdir(parents=True)
        self.model={'model':'colibri/glm-5.3','parameters_sha256':'b'*64}
        self.proof={'version':1,'job':'original-job','phase':'original-phase','input_key':'original-input',
                    'boot_id':D.boot_id(),'model_sha256':P.fingerprint(self.model)}
        proof_sha=self.write('admission.json',self.proof)
        self.intent={'version':1,'session':self.folder.name,'action_id':'original-job','phase':'original-phase',
                     'input_key':'original-input','input_sha256':'c'*64,'model':self.model,
                     'profile_sha256':self.model['parameters_sha256'],'boot_id':D.boot_id(),
                     'admission_sha256':proof_sha,'created_at':time.time()}
        self.intent_sha=self.write('native-launch-intent.json',self.intent)
        self.closed={'model':self.model,'session':self.folder.name,'action_id':'original-job','phase':'original-phase',
                     'native_launch_intent_sha256':self.intent_sha,'owned_processes_drained':True,'native_launch':'created'}
        D.save_json(self.runtime/'memory-coordination.json',{'version':1,'enabled':True})

    def write(self,name,value):
        raw=(json.dumps(value,sort_keys=True)+'\n').encode();(self.folder/name).write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def test_unknown_launch_blocks_other_inputs_host_work_and_backup_after_guard_is_gone(self):
        store=A.Actions(self.runtime)
        self.assertEqual(store.executions(),[])
        self.assertTrue(M.surviving_execution(self.runtime,own_phase='different-phase'))
        for kind in ('local','host'):
            with self.subTest(kind=kind):
                job=store.submit('other-'+kind,{'session':'new','input_key':'different-input','worker_model':dict(self.model,parameters_sha256='d'*64)},time.time()+60)
                store.claim(job['id'],P.lease());entered=[]
                with patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':job['id']}),patch.object(P.subprocess,'check_output',return_value='1'):
                    with self.assertRaises(P.PhaseInterrupted):
                        with P.phase(self.runtime,job['project'],kind,['fixture'],self.root,self.root/'log',time.time()+.04):entered.append('would-charge')
                self.assertEqual(entered,[]);store.finish(job['id'],'interrupted',{},lease=P.lease())
        self.assertEqual(B.Coordination(self.root).acquire(),(False,'native_cleanup_unconfirmed'))
        with store.read() as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM wf_charges').fetchone()[0],0)

    def test_disabling_coordination_cannot_bypass_existing_unknown_launch(self):
        D.save_json(self.runtime/'memory-coordination.json',{'version':1,'enabled':False})
        with self.assertRaises(P.PhaseInterrupted):
            with M.heavy(self.runtime,time.time()+1):self.fail('Unresolved launch bypassed disabled flag')
        self.assertEqual(B.Coordination(self.root).acquire(),(False,'native_cleanup_unconfirmed'))

    def test_only_exact_positive_cleanup_clears_global_fence(self):
        for state in ('created','creation_failed','not_attempted'):
            with self.subTest(state=state):
                self.write('unloaded.json',dict(self.closed,native_launch=state))
                self.assertFalse(M.unresolved_native(self.runtime))
                with M.heavy(self.runtime,time.time()+1):pass

    def test_missing_false_or_mismatched_cleanup_stays_globally_held(self):
        for change in ({'owned_processes_drained':False},{'native_launch':'unknown'},
                       {'native_launch_intent_sha256':'0'*64},{'session':'other'},
                       {'action_id':'other-job'},{'phase':'other-phase'},{'model':dict(self.model,parameters_sha256='e'*64)}):
            with self.subTest(change=change):
                self.write('unloaded.json',dict(self.closed,**change));self.assertTrue(M.unresolved_native(self.runtime))
        (self.folder/'unloaded.json').write_text('{malformed')
        self.assertTrue(M.unresolved_native(self.runtime))
        (self.folder/'unloaded.json').unlink();self.assertTrue(M.unresolved_native(self.runtime))

    def test_changed_admission_or_intent_cannot_reuse_old_cleanup(self):
        self.write('unloaded.json',self.closed)
        self.write('admission.json',dict(self.proof,input_key='other-input'))
        self.assertTrue(M.unresolved_native(self.runtime))
        self.write('admission.json',self.proof)
        for value in (None,[],{},dict(self.intent,input_sha256='invalid'),dict(self.intent,action_id='other-job')):
            with self.subTest(value=value):
                self.write('native-launch-intent.json',value);self.assertTrue(M.unresolved_native(self.runtime))

    def test_legacy_session_without_new_launch_intent_keeps_existing_rules(self):
        (self.folder/'native-launch-intent.json').unlink()
        self.write('failure-intent.json',{'kind':'old-session','drain':{'owned_processes_drained':False}})
        self.assertFalse(M.unresolved_native(self.runtime))
        self.assertFalse(M.surviving_execution(self.runtime))

    def test_completed_history_has_no_aggregate_count_or_byte_quota(self):
        original=self.folder
        self.write('unloaded.json',self.closed)
        for index in range(24):
            self.folder=original.parent/f'{index:032x}';self.folder.mkdir()
            self.write('admission.json',self.proof)
            intent=dict(self.intent,session=self.folder.name)
            sha=self.write('native-launch-intent.json',intent)
            self.write('unloaded.json',dict(self.closed,session=self.folder.name,native_launch_intent_sha256=sha))
        # Historical implementation capped both totals. Small sentinels prove
        # that completed retained history is no longer counted as a quota.
        with patch.object(M,'MAX_NATIVE_SESSIONS',2,create=True),patch.object(M,'MAX_NATIVE_SCAN_BYTES',8,create=True):
            self.assertFalse(M.unresolved_native(self.runtime))
            with M.heavy(self.runtime,time.time()+1):pass
        self.folder=original

    def test_linked_or_oversize_receipt_fails_closed(self):
        self.write('unloaded.json',self.closed)
        path=self.folder/'unloaded.json';raw=path.read_bytes();path.unlink()
        outside=self.root/'outside.json';outside.write_bytes(raw);path.symlink_to(outside)
        self.assertTrue(M.unresolved_native(self.runtime));path.unlink();path.write_bytes(raw)
        with patch.object(M,'MAX_NATIVE_RECEIPT_BYTES',8):self.assertTrue(M.unresolved_native(self.runtime))
        self.assertFalse(M.unresolved_native(self.runtime))


if __name__=='__main__':unittest.main()
