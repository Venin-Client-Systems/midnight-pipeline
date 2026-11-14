"""A completed failed review parks evidence and never reissues its charged call."""
from contextlib import ExitStack
import hashlib
from pathlib import Path
import time
import unittest
from unittest import mock
import core_repair as R
import maintenance_recovery as M
import test_recovery_maintenance_progress as F


class TerminalReviewTests(unittest.TestCase):
    def setUp(self):
        self.f=F.MaintenanceProgressTests();self.f.setUp();self.addCleanup(self.f.doCleanups);f=self.f
        self.identity,self.directory,self.binding,self.patch=f.prepared_checked_session(review=False)
        self.original=f.actions.repairs(active=False);self.invocation={'command':['fixed-qualified-final-review'],'invocation_id':'b'*32,
            'descriptor':f.model,'receipt':{'cwd':str(self.directory/'work')}}
        M.save(self.directory/'review-invocation.json',{'patch_sha256':self.binding['patch_sha256'],'base':f.base,'invocation':self.invocation})
        stack=ExitStack();self.addCleanup(stack.close)
        def git(root,*args):
            if args[0] in {'status','add'}:return ''
            if args==('write-tree',):return self.binding['tree']
            return f.base
        for target,name,kwargs in ((R,'permitted',{'return_value':True}),(R,'maintenance_current',{'return_value':True}),
            (R,'check_authority',{}),(R.G,'git',{'side_effect':git}),(R,'allowed_patch',{'return_value':(self.binding['files'],self.patch)})):
            stack.enter_context(mock.patch.object(target,name,**kwargs))
        self.cloud=stack.enter_context(mock.patch.object(R.CP,'CloudPolicy'));self.cloud.return_value.descriptor.return_value=f.model
        self.review=stack.enter_context(mock.patch.object(R,'independent_review',side_effect=self.failed_phase))
        self.install=stack.enter_context(mock.patch.object(R,'install',side_effect=AssertionError('Failed review cannot install')))
        self.save_process=False

    def failed_phase(self,root,job,work,directory,*args,**kwargs):
        f=self.f;log=directory/'review-events.jsonl';raw=b'{"type":"error","message":"known terminal review failure"}\n';log.write_bytes(raw)
        f.actions.charge(job['id'],self.invocation['invocation_id'],'maintenance_review')
        phase=f.actions.phase(job['id'],'midnight','cloud','original-final-review-failure',time.time()+60,{'log':str(log)})
        M.save(f.root/'runtime/phases'/phase['id']/'launch.json',{'command':self.invocation['command'],'cwd':str(work),'output_file':str(log)})
        result={'code':1,'reason':'command_failed','log_sha256':hashlib.sha256(raw).hexdigest()};f.actions.phase_update(phase['id'],'failed',result=result)
        if self.save_process:M.save(directory/'review-process.json',result)
        raise OSError('failed review phase persisted before execution could park')

    def interrupt(self):
        self.assertEqual(M.execute(self.f.root,self.identity),1)
        state=self.f.manager.session(self.identity);self.assertEqual(state['state'],'paused')
        dead={'pid':99999999,'stamp':'original review executor exited','boot_id':R.D.boot_id(),'birth_us':1}
        self.f.manager.update(self.identity,'paused',lease=dead,expected=state['lease'],result={**state['result'],'next_retry_at':0})

    def reconcile(self,process=False):
        f=self.f;self.save_process=process;self.interrupt();self.assertEqual(M.execute(f.root,self.identity),1)
        session=f.manager.session(self.identity);self.assertEqual(session['state'],'waiting');self.assertEqual(session['result']['error'],'ReviewProcessFailed')
        self.assertEqual(self.review.call_count,1);self.cloud.return_value.prepare_invocation.assert_not_called()
        self.cloud.return_value.record_result.assert_called_once();self.assertEqual(self.cloud.return_value.record_result.call_args.args[0],self.invocation['invocation_id'])
        self.assertEqual(f.actions.repairs(active=False),self.original);self.install.assert_not_called()
        with f.actions.read() as con:self.assertEqual(con.execute("SELECT count(*) FROM wf_charges WHERE kind='maintenance_review'").fetchone()[0],1)
        receipt=M.read(self.directory/'failed-review-receipt.json');self.assertEqual(receipt['invocation_id'],self.invocation['invocation_id']);self.assertFalse(receipt['review_relaunch_allowed'])
        with mock.patch.object(M,'enabled',return_value=True):self.assertFalse(M.running(f.root,spawn=mock.Mock()))
        f.incident=f.actions.incident('midnight','backend_error','another-reviewed-repair','new-evidence',{'detail':'A distinct current repair is needed'})
        f.manager=M.MaintenanceRecovery(f.root,f.incident);scope,_,capsule=f.observe();f.offer(scope);spawn=mock.Mock()
        with mock.patch.object(M,'enabled',return_value=True):identity=M.launch(f.root,f.actions,f.incident,spawn=spawn)
        self.assertIsNotNone(identity);self.assertEqual(f.manager.session(identity)['payload']['action'],'implementation');self.assertEqual(spawn.call_count,1)
        self.assertEqual(self.review.call_count,1)

    def test_failed_phase_before_process_mirror_parks_without_reinvocation_and_frees_lane(self):self.reconcile()
    def test_failed_process_mirror_before_parking_reconciles_original_charge(self):self.reconcile(process=True)

    def test_changed_failed_review_log_remains_uncertain_without_reinvocation(self):
        self.interrupt();(self.directory/'review-events.jsonl').write_text('unbound changed bytes\n')
        self.assertEqual(M.execute(self.f.root,self.identity),1);self.assertEqual(self.f.manager.session(self.identity)['state'],'paused')
        self.assertEqual(self.review.call_count,1);self.cloud.return_value.prepare_invocation.assert_not_called();self.install.assert_not_called()


if __name__=='__main__':unittest.main()
