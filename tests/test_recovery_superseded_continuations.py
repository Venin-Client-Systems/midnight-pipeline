"""Obsolete charged work retires without allowing pauses to create fresh grants."""
import copy
import json
from pathlib import Path
import time
import subprocess
import sys
import unittest
from unittest import mock
import core_repair as R
import maintenance_recovery as M
import progress_recovery as RP
import project_action as PA
import test_recovery_maintenance_progress as F
import test_recovery_progress_integration as I


class SupersededContinuationTests(unittest.TestCase):
    def setUp(self):
        self.f=F.MaintenanceProgressTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.product=I.IntegrationTests();self.product.setUp();self.addCleanup(self.product.doCleanups)
        controller,_,_,self.request=self.product.harness_fixture();f=self.f
        (f.root/'projects.json').write_bytes((controller/'projects.json').read_bytes());f.base='b'*40
        f.incident=f.actions.incident('midnight','worker_contract','old-request','actual-receipts',{'progress_request':self.request})
        f.manager=M.MaintenanceRecovery(f.root,f.incident);self.original=f.legacy();f.source.write_text('def repair(value):\n    return value + 1\n\ndef distinct(value):\n    return value * 3\n');self.expected_rows=4;self.paid=False;scope,_,capsule=f.observe()
        self.identity=f.manager.create('diagnosis',scope,f.model,capsule)
        def git(path,*args):
            if args==('status','--porcelain'):return ''
            return 'b'*40 if Path(path)==f.root else 'a'*40
        self.git=git;patch=mock.patch.object(R.G,'git',side_effect=git);patch.start();self.addCleanup(patch.stop)
        self.assertEqual(RP.harness_request_status(f.root,self.request),'current')

    def supersede(self):self.product.b.write(self.product.b.STATE/'state.json',{'done':['held']})

    def new_admitted_repair(self):
        f=self.f;old=f.manager;old_incident=f.incident
        f.incident=f.actions.incident('midnight','backend_error','new-eligible-source','new-evidence',{'detail':'actual unmet source unit'})
        f.manager=M.MaintenanceRecovery(f.root,f.incident);scope,_,capsule=f.observe()
        if self.paid:
            review={'decision':'accept','issues':[],'unmet':True,'smaller':True,'summary':'A distinct original source unit remains unmet.'}
            path=f.root/'distinct-reviewed-unit.json';M.save(path,review)
            proposal={'action':'narrow','instruction':'Repair only this distinct demonstrated source unit','path':'source.py','symbol':'distinct','evidence_quote':'    return value * 3\n','reason':'A separate actual source obligation.'}
            f.manager.offer_reviewed(scope,f.model,proposal,review,[RP.ref(path)],f.root)
        else:f.offer(scope)
        self.assertTrue(f.manager.admission(scope,f.model)['allowed'])
        model=mock.Mock();model.descriptor.return_value=f.model;spawn=mock.Mock()
        with mock.patch.object(M,'enabled',return_value=True),mock.patch.object(R,'permitted',return_value=True),mock.patch.object(R,'check_authority'),mock.patch.object(R.CP,'CloudPolicy',return_value=model):
            identity=M.launch(f.root,f.actions,f.incident,spawn=spawn)
        self.assertIsNotNone(identity);self.assertNotEqual(identity,self.identity);self.assertEqual(f.manager.session(identity)['payload']['action'],'implementation')
        self.assertEqual(spawn.call_count,1);self.assertEqual(old.session(self.identity)['state'],'retired')
        rows={r['id']:r for r in f.actions.repairs(active=False)};self.assertEqual([rows[r['id']] for r in self.original],self.original);self.assertEqual(len(rows),self.expected_rows)

    def retirement(self,paused):
        f=self.f
        if paused:
            dead={'pid':99999999,'stamp':'dead original owner','boot_id':R.D.boot_id(),'birth_us':1}
            f.manager.update(self.identity,'paused',lease=dead)
            job=f.actions.submit('midnight',{'continuation':self.identity},time.time()+300);self.assertTrue(f.actions.claim(job['id'],dead));self.job=job
        self.supersede();spawn=mock.Mock()
        with mock.patch.object(M,'enabled',return_value=True):self.assertFalse(M.running(f.root,spawn=spawn))
        spawn.assert_not_called();self.assertEqual(f.manager.session(self.identity)['state'],'retired')
        if paused:self.assertEqual(f.actions.get(self.job['id'])['state'],'interrupted')
        self.new_admitted_repair()

    def test_queued_superseded_continuation_retires_before_queue_grace_and_allows_next_repair(self):self.retirement(False)
    def test_paused_superseded_continuation_drains_original_job_and_allows_next_repair(self):self.retirement(True)

    def test_current_request_with_recipient_pause_is_not_retired(self):
        f=self.f;(self.product.b.STATE/'PAUSED').touch();f.manager.update(self.identity,'paused');spawn=mock.Mock()
        self.assertEqual(M.continuation_currency(f.root,f.manager.session(self.identity)),'current')
        with mock.patch.object(M,'enabled',return_value=False):self.assertTrue(M.running(f.root,spawn=spawn))
        spawn.assert_not_called();self.assertEqual(f.manager.session(self.identity)['state'],'paused');self.assertEqual(f.actions.repairs(active=False),self.original)

    def test_unavailable_scope_evidence_does_not_retire_or_grant(self):
        f=self.f;(f.root/'projects.json').unlink();f.manager.update(self.identity,'paused');spawn=mock.Mock()
        self.assertEqual(M.continuation_currency(f.root,f.manager.session(self.identity)),'unavailable')
        with mock.patch.object(M,'enabled',return_value=False):self.assertTrue(M.running(f.root,spawn=spawn))
        spawn.assert_not_called();self.assertEqual(f.manager.session(self.identity)['state'],'paused');self.assertEqual(f.actions.repairs(active=False),self.original)

    def test_superseded_request_waits_for_proven_owned_drainage(self):
        f=self.f;dead={'pid':99999999,'stamp':'dead owner','boot_id':R.D.boot_id(),'birth_us':1};f.manager.update(self.identity,'paused',lease=dead)
        job=f.actions.submit('midnight',{'continuation':self.identity},time.time()+300);f.actions.claim(job['id'],dead);self.supersede()
        with mock.patch.object(M,'enabled',return_value=True),mock.patch.object(PA,'drain',return_value=False):self.assertTrue(M.running(f.root,spawn=mock.Mock()))
        self.assertEqual(f.manager.session(self.identity)['state'],'paused');self.assertEqual(f.actions.get(job['id'])['state'],'running')
        with mock.patch.object(M,'enabled',return_value=True):self.assertFalse(M.running(f.root,spawn=mock.Mock()))
        self.assertEqual(f.manager.session(self.identity)['state'],'retired');self.assertEqual(f.actions.get(job['id'])['state'],'interrupted')

    def test_paid_unknown_launch_retires_without_refund_and_admits_a_distinct_unit(self):
        f=self.f;scope,_,capsule=f.observe();f.offer(scope)
        child=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(30)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        def cleanup():
            if child.poll() is None:child.kill()
            child.wait(timeout=3)
        self.addCleanup(cleanup)
        lease=R.P.lease(child.pid);self.assertTrue(R.P.alive(lease));job=f.actions.submit('midnight',{'continuation':self.identity},time.time()+300)
        self.assertTrue(f.actions.claim(job['id'],lease));f.manager.update(self.identity,'running',lease=lease)
        repair=f.reserve(self.identity,scope);child.terminate();child.wait(timeout=3)
        f.actions.repair(repair,state='unknown',expected_lease=lease);self.supersede();f.actions.meta('maintenance_recovery',{'version':2},write=True)
        with mock.patch.object(M,'enabled',return_value=True):self.assertFalse(R.GU.repair_running(f.actions,spawn=mock.Mock()))
        self.assertEqual(f.manager.session(self.identity)['state'],'retired');self.assertEqual(f.actions.repair(repair)['state'],'failed');self.assertEqual(f.actions.repair(repair)['payload']['retired_from_state'],'unknown')
        self.assertEqual(f.actions.get_incident(f.incident)['attempts'],5);self.assertEqual(f.actions.session_charges('maintenance:'+self.identity,'midnight'),{'maintenance_implementation':1})
        self.assertFalse(f.manager.admission(scope,f.model)['allowed'])
        with self.assertRaisesRegex(ValueError,'immutable'):f.manager.update(self.identity,'queued')
        self.expected_rows=5;self.paid=True;self.new_admitted_repair()

    def test_execute_retires_supersession_instead_of_pausing_forever(self):
        f=self.f;self.supersede()
        with mock.patch.object(R,'permitted',return_value=True),mock.patch.object(R,'check_authority'),mock.patch.object(M,'diagnose',side_effect=AssertionError('Obsolete diagnosis must not run')):
            self.assertEqual(M.execute(f.root,self.identity),0)
        self.assertEqual(f.manager.session(self.identity)['state'],'retired');self.assertEqual(f.actions.repairs(active=False),self.original)


if __name__=='__main__':unittest.main()
