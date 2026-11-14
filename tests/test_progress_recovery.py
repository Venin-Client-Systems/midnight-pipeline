"""Deterministic admission, migration and non-repetition, using private temp state."""
import concurrent.futures,json,tempfile,threading,unittest
from pathlib import Path
from unittest import mock
import progress_recovery as P

class RecoveryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.task={'id':'repair','acceptance':['Exact error text','Capture both outputs'],'files':['tests/test_value.py']}
  self.model={'model':'fixture','digest':'a'*64};self.scope=P.scope('notes','behavior:1',self.task,'a'*40,'profile','micro-1')
  self.r=P.Recovery(self.root,'notes');self.evidence=self.root/'review.json';self.review={'decision':'accept','issues':[]}
 def enroll(self,old=False):return self.r.enroll(self.scope,self.task,self.model,legacy_runs=['old-run'] if old else [],legacy_tries=4 if old else 0)
 def narrow(self,index=0,text='Repair only this original obligation'):
  self.evidence.write_text(json.dumps(self.review));return self.r.offer_narrowing(self.scope,self.model,obligation=index,instruction=text,refs=[P.ref(self.evidence)],review=self.review)
 def test_read_only_missing_store_creates_nothing(self):
  missing=P.Recovery(self.root/'missing','notes');self.assertEqual(missing.snapshot()['scopes'],[])
  self.assertFalse(missing.admission(self.scope)['allowed']);self.assertFalse((self.root/'missing').exists())
 def test_new_work_receives_initial_admission_once(self):
  self.enroll();self.assertTrue(self.r.admission(self.scope,self.model)['allowed'])
  first=self.r.reserve(self.scope,'run1',self.model);self.assertTrue(first['charged'])
  self.assertEqual(first,self.r.reserve(self.scope,'run1',self.model));self.assertFalse(self.r.admission(self.scope,self.model)['allowed'])
  with self.assertRaises(P.RecoveryHeld):self.r.reserve(self.scope,'run2',self.model)
 def test_source_upgrade_requalifies_only_an_uncharged_initial_admission(self):
  self.enroll();first=self.r.admission(self.scope,self.model)['proof']
  with mock.patch.object(P,'components',return_value={'new':'generation'}):
   self.enroll();self.assertNotEqual(self.r.admission(self.scope,self.model)['proof'],first);self.r.reserve(self.scope,'actual-run',self.model)
  self.enroll();self.assertFalse(self.r.admission(self.scope,self.model)['allowed'])
 def test_changed_product_head_cannot_erase_an_unknown_charged_launch(self):
  self.enroll();self.r.reserve(self.scope,'unknown-after-crash',self.model)
  newer=P.scope('notes','behavior:1',self.task,'b'*40,'profile','micro-1');saved=self.r.enroll(newer,self.task,self.model)
  self.assertIn('unknown-after-crash',saved['legacy_runs']);self.assertFalse(self.r.admission(newer,self.model)['allowed'])
 def test_new_qualified_model_can_prepare_uncharged_work_without_refunding_launch(self):
  self.enroll();selected=dict(self.model,model='new-qualified')
  self.r.enroll(self.scope,self.task,selected);self.assertTrue(self.r.admission(self.scope,selected)['allowed']);self.r.reserve(self.scope,'new-model-run',selected)
  self.enroll();self.assertFalse(self.r.admission(self.scope,self.model)['allowed'])
 def test_preissued_other_head_proof_cannot_duplicate_a_later_launch(self):
  self.enroll();newer=P.scope('notes','behavior:1',self.task,'b'*40,'profile','micro-1');self.r.enroll(newer,self.task,self.model)
  self.r.reserve(self.scope,'old-head-raced',self.model)
  with self.assertRaises(P.RecoveryHeld):self.r.reserve(newer,'duplicate',self.model)
 def test_migrating_exhausted_work_preserves_history_and_does_not_reset_permission(self):
  payload=self.enroll(True);self.assertEqual(payload['legacy_runs'],['old-run']);self.assertEqual(payload['legacy_tries'],4)
  self.assertFalse(self.r.admission(self.scope,self.model)['allowed']);self.enroll(False)
  self.assertFalse(self.r.admission(self.scope,self.model)['allowed']);snapshot=self.r.snapshot()['scopes'][0]
  self.assertEqual(snapshot['historical_launches'],0);self.assertEqual(snapshot['legacy_launches'],4)
 def test_reviewed_narrowing_admits_one_bound_run(self):
  self.enroll(True);proof=self.narrow();decision=self.r.admission(self.scope,self.model)
  self.assertTrue(decision['allowed']);self.assertEqual(decision['proof'],proof)
  self.r.reserve(self.scope,'new-run',self.model);self.assertFalse(self.r.admission(self.scope,self.model)['allowed'])
 def test_rewording_does_not_renew_a_consumed_intervention(self):
  self.enroll(True);proof=self.narrow();self.r.reserve(self.scope,'one',self.model)
  self.assertEqual(proof,self.narrow(text='Completely different prose and confidence'))
  self.assertFalse(self.r.admission(self.scope,self.model)['allowed'])
 def test_distinct_original_obligation_can_justify_different_work(self):
  self.enroll(True);self.narrow(0);self.r.reserve(self.scope,'one',self.model);second=self.narrow(1)
  self.assertEqual(self.r.admission(self.scope,self.model)['proof'],second)
 def test_invented_obligation_or_rejected_review_cannot_mint_permission(self):
  self.enroll(True)
  with self.assertRaises(P.RecoveryHeld):self.narrow(20)
  self.review={'decision':'revise','issues':['scope changed']}
  with self.assertRaises(P.RecoveryHeld):self.narrow()
 def test_changed_model_components_or_evidence_cannot_launch(self):
  self.enroll(True);self.narrow()
  self.assertFalse(self.r.admission(self.scope,dict(self.model,digest='b'*64))['allowed'])
  with mock.patch.object(P,'components',return_value={'different':'source'}):self.assertFalse(self.r.admission(self.scope,self.model)['allowed'])
  self.evidence.write_text('tampered');self.assertFalse(self.r.admission(self.scope,self.model)['allowed'])
 def test_renamed_task_cannot_acquire_fresh_permission_for_same_issue(self):
  self.enroll(True);task=dict(self.task,id='renamed');scope=P.scope('notes','behavior:1',task,'a'*40,'profile')
  with self.assertRaises(P.RecoveryHeld):self.r.enroll(scope,task,self.model)
 def test_stale_scope_and_cross_project_evidence_are_rejected(self):
  self.enroll(True);other=dict(self.scope,head='b'*40)
  with self.assertRaises(P.RecoveryHeld):self.r.enroll(other,self.task,self.model)
  outside=self.root.parent/'outside-progress-proof';outside.write_text('{}');self.addCleanup(outside.unlink)
  with self.assertRaises(P.RecoveryHeld):self.r.offer_narrowing(self.scope,self.model,obligation=0,instruction='One edit',refs=[P.ref(outside)],review=self.review)
 def test_competing_reservations_cannot_consume_one_proof_twice(self):
  self.enroll(True);self.narrow();another=P.Recovery(self.root,'notes')
  self.r.reserve(self.scope,'one',self.model)
  with self.assertRaises(P.RecoveryHeld):another.reserve(self.scope,'two',self.model)
  self.assertEqual(another.snapshot()['scopes'][0]['historical_launches'],1)
 def test_crash_after_reservation_keeps_proof_charged(self):
  self.enroll(True);self.narrow();self.r.reserve(self.scope,'lost-launch',self.model)
  recovered=P.Recovery(self.root,'notes');self.assertFalse(recovered.admission(self.scope,self.model)['allowed'])
  self.assertEqual(recovered.snapshot()['scopes'][0]['historical_launches'],1)
 def test_simultaneous_controllers_consume_exactly_one_proof(self):
  self.enroll(True);self.narrow();barrier=threading.Barrier(2)
  def launch(name):
   contender=P.Recovery(self.root,'notes');barrier.wait()
   try:return contender.reserve(self.scope,name,self.model)['charged']
   except P.RecoveryHeld:return False
  with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(launch,['controller-a','controller-b']))
  self.assertEqual(sorted(results),[False,True]);self.assertEqual(self.r.snapshot()['scopes'][0]['historical_launches'],1)
 def test_candidate_source_change_invalidates_proof_before_consumption(self):
  self.enroll(True);self.evidence.write_text(json.dumps(self.review));candidate=self.root/'candidate.py';candidate.write_text('before\n')
  self.r.offer_narrowing(self.scope,self.model,obligation=0,instruction='One exact assertion',refs=[P.ref(self.evidence),P.ref(candidate)],review=self.review)
  candidate.write_text('after\n')
  with self.assertRaises(P.RecoveryHeld):self.r.reserve(self.scope,'stale-source',self.model)
  self.assertEqual(self.r.snapshot()['scopes'][0]['historical_launches'],0)
 def test_corrupt_receipts_remain_unknown(self):
  d=self.root/'runs/corrupt';d.mkdir(parents=True)
  for name,value in {'worker-process.json':[],'worker-completion.json':None,'checks.json':{},'review/result.json':{'issues':42}}.items():
   path=d/name;path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(value))
  self.assertEqual(P.classify(d)['kind'],'unknown')
 def test_new_or_changed_classification_inputs_invalidate_admission(self):
  self.enroll(True);d=self.root/'runs/old-run';d.mkdir(parents=True)
  (d/'worker-process.json').write_text(json.dumps({'code':0,'reason':'exited'}));(d/'worker-completion.json').write_text(json.dumps({'finished':True}))
  q=d/'quality-checks';q.mkdir();checks=q/'checks.json';checks.write_text(json.dumps([{'code':1,'reason':'exited','command':['old']}]))
  self.r.observe(self.scope,'old-run',d);self.narrow();checks.write_text(json.dumps([{'code':0,'reason':'exited','command':['new']}]))
  with self.assertRaises(P.RecoveryHeld):self.r.reserve(self.scope,'changed-checks',self.model)
  checks.write_text(json.dumps([{'code':1,'reason':'exited','command':['old']}]))
  self.assertTrue(self.r.admission(self.scope,self.model)['allowed'])
  (d/'review').mkdir();(d/'review/result.json').write_text(json.dumps({'decision':'revise','issues':['New real finding']}))
  self.assertFalse(self.r.admission(self.scope,self.model)['allowed'])
 def test_plain_exit_zero_is_classified_as_unfinished_not_product_failure(self):
  self.enroll(True);d=self.root/'runs/failed';d.mkdir(parents=True)
  (d/'worker-process.json').write_text(json.dumps({'code':0,'reason':'exited'}));(d/'worker-completion.json').write_text(json.dumps({'finished':False}))
  value=self.r.observe(self.scope,'old-run',d)
  self.assertEqual(value['kind'],'unfinished_handoff');self.assertFalse(self.r.admission(self.scope,self.model)['allowed'])
  self.assertEqual(self.r.snapshot()['scopes'][0]['admission']['state'],'needs_harness_repair')
 def test_receipts_distinguish_interruption_semantic_failure_and_missing_evidence(self):
  d=self.root/'runs/fixture';d.mkdir(parents=True)
  self.assertEqual(P.classify(d)['kind'],'unknown')
  (d/'worker-process.json').write_text(json.dumps({'code':-1,'reason':'paused_or_shutdown'}));self.assertEqual(P.classify(d)['kind'],'interrupted')
  (d/'worker-process.json').write_text(json.dumps({'code':0,'reason':'exited'}));(d/'checks.json').write_text(json.dumps([{'code':1,'reason':'exited','command':['fixed','test']}]))
  self.assertEqual(P.classify(d)['kind'],'semantic')
 def test_error_wording_cannot_change_failure_identity(self):
  d=self.root/'runs/fixture';d.mkdir(parents=True);(d/'worker-process.json').write_text(json.dumps({'code':0,'reason':'exited'}))
  (d/'worker-completion.json').write_text(json.dumps({'finished':False,'summary':'First prose'}));a=P.classify(d)
  (d/'worker-completion.json').write_text(json.dumps({'finished':False,'summary':'Changed text, line 777'}));self.assertEqual(a['fingerprint'],P.classify(d)['fingerprint'])
 def test_unknown_legacy_failure_is_not_guessed_as_truncation(self):
  d=self.root/'runs/fixture';d.mkdir(parents=True);(d/'worker-process.json').write_text(json.dumps({'code':0,'reason':'exited'}))
  (d/'worker-completion.json').write_text(json.dumps({'finished':False}));self.assertEqual(P.classify(d)['kind'],'unfinished_handoff')
  (d/'worker-tools').mkdir();(d/'worker-tools/decision-1.json').write_text(json.dumps({'status':'failed','completion_reason':'length'}));self.assertEqual(P.classify(d)['kind'],'inference_truncated')
 def test_product_action_cannot_register_harness_certificates(self):
  with mock.patch.dict(P.os.environ,{'MIDNIGHT_ACTION_ID':'product-action'}),self.assertRaises(P.RecoveryHeld):P.register_certificate(self.root,{})

if __name__=='__main__':unittest.main()
