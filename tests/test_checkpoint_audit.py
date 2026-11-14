"""A real private checkpoint can receive one budgeted operator quality audit."""
import tempfile,unittest
from pathlib import Path
import manager_recovery as R
import micro_recovery as M
import quality_monitor as Q
import worker_quality as W


class CheckpointAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.parent={'id':'old','kind':'repair','files':['src/value.py','tests/test_value.py'],'finding_ids':['value'],'acceptance':['Keep full final coverage.']}
        self.task=dict(self.parent,id='narrow',local_worker_files=['tests/test_value.py'],retry_of={'task_id':'old','task_sha256':W.task_fingerprint(self.parent)})
        self.recovery=R.ManagerRecovery(self.root,'notes');self.micro=M.MicroRecovery(self.root,'notes');self.q=Q.QualityMonitor(self.root,'notes',clock=lambda:2_000_000)
        self.review={'decision':'accept','issues':[],'receipt_sha256':'c'*64};self.head='a'*40;self.patch='f'*64
        self.recovery.approve(self.task,self.parent,'value:1',self.head,'profile',{'task_sha256':W.task_fingerprint(self.parent),'patch_sha256':'b'*64},{'src/value.py':'d'*64},self.review,{'reason':'Only regression tests remain.','contract_comparison':'Same final coverage.'})
        for run in ('prior1','prior2'):self.recovery.reserve(self.task,'value:1',self.head,'profile',run)
        proposal={'decision':'decompose','reason':'Two small steps.','steps':[{'title':'First tests','description':'Add baseline tests.','acceptance':['Real parser coverage.']},{'title':'Full coverage','description':'Finish original integration cases.','acceptance':['Complete original final criteria.']}]}
        self.micro.approve(self.task,'value:1',self.head,'profile',proposal,{'task_sha256':W.task_fingerprint(self.task),'base':self.head,'patch_sha256':'e'*64},self.review)
        self.args=(self.task,'value:1',self.head,'profile','step1',self.patch)

    def complete(self):
        self.micro.reserve(self.task,'value:1',self.head,'profile','step1')
        self.micro.complete_step(self.task,'value:1',self.head,'profile',0,'step1',self.patch,[{'code':0,'reason':'exited'}],self.review,{'receipt_sha256':'1'*64})

    def result(self):return {'decision':'regression','summary':'Local review missed an existing criterion.','issues':['tests/test_value.py mocks a parser exception despite the real-parser criterion.']}
    def evidence(self):return {'source_head':self.head,'checkpoint_run':'step1','candidate_patch_sha256':self.patch,'provenance':'interactive_codex_operator_audit','receipt_sha256':'2'*64}

    def hold(self):
        self.complete();token=self.q.reserve_checkpoint(*self.args);self.q.finish(token,self.result(),self.evidence());return token

    def test_requires_genuine_host_completed_local_checkpoint_without_product_acceptance(self):
        with self.assertRaises(ValueError):self.q.reserve_checkpoint(*self.args)
        self.complete();token=self.q.reserve_checkpoint(*self.args);snapshot=self.q.snapshot()
        self.assertEqual(snapshot['accepted_local_reviews'],0);self.assertEqual(snapshot['local_reviews'],[])
        self.assertEqual(snapshot['cloud_audit_reservations_7d'],{'routine':1,'corrective':0})
        self.assertEqual(snapshot['audits'][0]['state'],'reserved_unknown');self.assertEqual(snapshot['audits'][0]['id'],token)
        self.assertEqual(self.micro.current(self.task,'value:1',self.head,'profile')['index'],1)

    def test_wrong_task_head_profile_run_and_patch_never_reserve(self):
        self.complete()
        for index,value in ((0,dict(self.task,acceptance=['weaker'])),(1,'value:2'),(2,'b'*40),(3,'changed'),(4,'other'),(5,'e'*64)):
            args=list(self.args);args[index]=value
            with self.subTest(index=index),self.assertRaises(ValueError):self.q.reserve_checkpoint(*args)
        self.assertEqual(self.q.snapshot().get('cloud_audit_reservations_7d',{'routine':0})['routine'],0)

    def test_audit_completion_and_hold_bind_the_same_checkpoint(self):
        self.complete();token=self.q.reserve_checkpoint(*self.args)
        for changes in ({'source_head':'b'*40},{'checkpoint_run':'other'},{'candidate_patch_sha256':'d'*64}):
            with self.assertRaises(ValueError):self.q.finish(token,self.result(),dict(self.evidence(),**changes))
        self.q.finish(token,self.result(),self.evidence());self.q.finish(token,self.result(),self.evidence())
        hold=self.q.snapshot()['hold'];self.assertEqual(hold['operator_checkpoint']['task_sha256'],W.task_fingerprint(self.task))
        self.assertEqual(hold['operator_checkpoint']['episode'],'value:1');self.assertFalse(self.q.delivery_allowed('e'*64,self.head))

    def test_checkpoint_and_accepted_source_share_one_routine_budget(self):
        self.complete();self.q.reserve_checkpoint(*self.args)
        from tests.test_local_first import REVIEW,CHECKS
        self.q.record_review('accepted',self.head,'b'*64,{'model':'fixture'},REVIEW,CHECKS);self.q.accepted('accepted','b'*40)
        with self.assertRaises(ValueError):self.q.reserve('b'*40)
        with self.assertRaises(ValueError):self.q.reserve_checkpoint(*self.args)
        self.assertFalse(self.q.due('b'*40));self.assertEqual(self.q.snapshot()['cloud_audit_reservations_7d']['routine'],1)

    def test_unknown_reservation_recovers_by_exact_identity_even_after_budget_window(self):
        self.complete();self.assertIsNone(self.q.checkpoint_audit(*self.args));token=self.q.reserve_checkpoint(*self.args)
        restarted=Q.QualityMonitor(self.root,'notes',clock=lambda:2_000_000+Q.WINDOW+1)
        self.assertEqual(restarted.checkpoint_audit(*self.args)['id'],token)
        with self.assertRaises(ValueError):restarted.reserve_checkpoint(*self.args)
        changed=list(self.args);changed[-1]='d'*64
        with self.assertRaises(ValueError):restarted.checkpoint_audit(*changed)
        restarted.finish(token,self.result(),self.evidence())
        self.assertEqual(restarted.checkpoint_audit(*self.args)['state'],'regression')
        self.assertEqual(len(restarted.snapshot()['audits']),1)

    def test_corrective_audit_still_requires_exact_patch_base_and_acceptance(self):
        self.hold();token=self.q.reserve('e'*64,'corrective',head=self.head)
        self.q.finish(token,{'decision':'healthy','summary':'Concrete supplied repair resolves the finding.','issues':[]},{'source_head':self.head,'candidate_patch_sha256':'e'*64,'receipt':'real-saved-receipt'},approved_patch='e'*64)
        self.assertTrue(self.q.delivery_allowed('e'*64,self.head));self.assertFalse(self.q.delivery_allowed('e'*64,'b'*40))
        self.assertIsNotNone(self.q.snapshot()['hold'])
        from tests.test_local_first import REVIEW,CHECKS
        self.q.record_review('fixed',self.head,'e'*64,{'model':'fixture'},REVIEW,CHECKS);self.q.accepted('fixed','c'*40)
        self.assertIsNone(self.q.snapshot()['hold']);self.assertEqual(self.q.snapshot()['cloud_audit_reservations_7d'],{'routine':1,'corrective':1})


if __name__=='__main__':unittest.main()
