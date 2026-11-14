"""Host admission, not advice or run identifiers, fences unchanged harness failures."""
import json, tempfile, unittest
from pathlib import Path
from unittest import mock
import coordinator as K
import progress_recovery as R
import structured_worker as S


class HarnessAdmissionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  self.task={'id':'repair','files':['case.py'],'acceptance':['Correct the original behaviour']}
  self.model={'model':'fixture','context':32768,'digest':'a'*64,'parameters_sha256':'b'*64}
  self.scope=R.scope('fixture','original:1',self.task,'c'*40,'profile')
  self.store=R.Recovery(self.root,'fixture');self.store.enroll(self.scope,self.task,self.model,legacy_tries=1,legacy_runs=['original'])
  self.components=R.components();self.run=self.failure('original');self.store.observe(self.scope,'original',self.run)
  self.advice=self.root/'coordinator/decision.json';self.advice.parent.mkdir();self.advice.write_text('{"guidance":"bounded"}')

 def failure(self,run,*,model=None,policy=None,components=None):
  directory=self.root/'runs'/run;tools=directory/'worker-tools';tools.mkdir(parents=True)
  for name,value in {'task.json':self.task,'worker-model.json':model or self.model,'worker-process.json':{'code':0,'reason':'exited'},
                     'worker-completion.json':{'finished':False,'contract':S.CONTRACT,'recovery_components':components or self.components},
                     'outcome.json':{'outcome':'failed','summary':'Run '+run}}.items():(directory/name).write_text(json.dumps(value))
  (tools/'decision-4.json').write_text(json.dumps({'schema_version':2,'status':'failed','phase':'decision','completion_reason':'length','requested_budget':1024,
      'worker_contract':S.CONTRACT,'model':model or self.model,'inference_policy':{'version':policy or S.INFERENCE_POLICY,'thinking_capable':True},'metrics':{'eval_count':1024}}))
  return directory

 def legacy_proof(self,kind='coordinator_guidance'):
  with self.store.store.transaction() as con:
   return self.store._offer(con,self.scope,kind,'old-proof-'+kind,self.model,{'refs':[R.ref(self.advice)],'instruction':'Prior unreviewed advice'})

 def test_coordinator_advice_never_mints_a_proof_and_cannot_release_harness_hold(self):
  result=self.store.offer_coordinator(self.scope,self.model,'d'*32,'Repair the exact permitted source only.',[R.ref(self.advice)])
  decision=self.store.admission(self.scope,self.model)
  self.assertFalse(decision['allowed']);self.assertEqual(decision['state'],'needs_harness_repair')
  with self.store.store.reader() as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM pr_proofs').fetchone()[0],0)
  self.assertEqual(result['admission'],'independent_review_required')
  saved=self.store.coordinator_advice(self.scope,self.model);self.assertEqual(saved['instruction'],'Repair the exact permitted source only.')
  self.store.observe(self.scope,'original',self.run)
  self.assertEqual(self.store.coordinator_advice(self.scope,self.model),saved)

 def test_legacy_coordinator_narrowing_or_guidance_cannot_admit_harness_retry(self):
  for kind in ('coordinator_guidance','codex_guidance','reviewed_narrowing','initial'):
   with self.subTest(kind=kind):
    self.legacy_proof(kind)
    self.assertFalse(self.store.admission(self.scope,self.model)['allowed'])
    with self.assertRaises(R.RecoveryHeld):self.store.reserve(self.scope,'repeat-'+kind,self.model)
  with self.store.store.reader() as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM pr_launches').fetchone()[0],0)

 def test_run_prose_time_and_redeployment_do_not_renew_permission(self):
  first=R.classify(self.run);second=self.failure('different-run');(second/'outcome.json').write_text(json.dumps({'outcome':'failed','summary':'Rewritten advice','at':'later'}))
  self.assertEqual(first['fingerprint'],R.classify(second)['fingerprint'])
  self.store.observe(self.scope,'different-run',second)
  for index in range(2):self.store.offer_coordinator(self.scope,self.model,str(index)*32,'Changed wording without a harness repair.',[R.ref(self.advice)])
  with mock.patch.object(R,'components',return_value=dict(self.components,unrelated='new-deployment')):
   self.store.enroll(self.scope,self.task,self.model)
   self.legacy_proof('codex_guidance')
   self.assertFalse(self.store.admission(self.scope,self.model)['allowed'])

 def test_failure_identity_binds_recorded_contract_model_policy_and_sources(self):
  first=R.classify(self.run)['fingerprint']
  for run,kwargs in [('model',{'model':dict(self.model,digest='f'*64)}),('policy',{'policy':'different-policy'}),('component',{'components':dict(self.components,**{'structured_worker.py':'e'*64})})]:
   with self.subTest(run=run):self.assertNotEqual(first,R.classify(self.failure(run,**kwargs))['fingerprint'])

 def test_only_a_matching_verified_harness_certificate_can_admit_once(self):
  certificate={'id':'e'*64,'kind':'verified_harness_fix','project':'fixture','model':self.model,
      'worker_sources':{n:R.components()[n] for n in ('local_worker.py','structured_worker.py','ralphy/run.sh')},
      'from_components':self.components,'failure_kinds':['inference_truncated'],'refs':[R.ref(self.advice)],'instruction':'Use the independently qualified harness repair.'}
  with mock.patch.object(R,'validate_qualification'):
   wrong=dict(certificate,id='f'*64,from_components={'unrelated':'source'})
   with self.assertRaises(R.RecoveryHeld):self.store.offer_certificate(self.scope,self.model,wrong)
   proof=self.store.offer_certificate(self.scope,self.model,certificate)
   self.assertEqual(self.store.admission(self.scope,self.model)['proof'],proof)
   self.store.reserve(self.scope,'qualified-once',self.model)
   self.assertFalse(self.store.admission(self.scope,self.model)['allowed'])

 def test_terminal_inference_failure_has_priority_over_stale_failed_check(self):
  (self.run/'checks.json').write_text(json.dumps([{'command':['old-check'],'code':1,'reason':'exited'}]))
  self.assertEqual(R.classify(self.run)['kind'],'inference_truncated')

 def test_coordinator_escalation_identity_excludes_run_and_guidance_prose(self):
  item={'scope':self.scope,'task_id':'repair','admission':{'allowed':False,'state':'needs_harness_repair'},
        'diagnosis':dict(R.classify(self.run),run='first',next_action='Original words'),'historical_launches':1}
  backend=mock.Mock();backend.progress_store.return_value.snapshot.return_value={'scopes':[item]}
  backend.git.return_value=self.scope['head'];backend.coordinator_retired.return_value={};backend.all_tasks.return_value=[self.task]
  project={'id':'fixture','state_dir':str(self.root)};first=K.escalations(project,backend)[0]['id']
  item['diagnosis'].update(run='second',next_action='Rewritten words')
  self.assertEqual(first,K.escalations(project,backend)[0]['id'])

 def semantic_advice(self):
  (self.run/'worker-completion.json').write_text(json.dumps({'finished':True,'contract':S.CONTRACT}))
  (self.run/'checks.json').write_text(json.dumps([{'code':1,'reason':'exited','command':['fixed-check']}]))
  self.store.observe(self.scope,'original',self.run)
  self.store.offer_coordinator(self.scope,self.model,'d'*32,'Repair the exact original source unit.',[R.ref(self.advice)])
  work=self.root/'work';work.mkdir();source=work/'case.py';source.write_text('def behavior():\n    return False\n')
  anchor=R.source_anchor(work,self.task,{'path':'case.py','symbol':'behavior','evidence_quote':source.read_text()})
  review={'decision':'accept','issues':[]};path=self.root/'review.json';path.write_text(json.dumps(review))
  return {'obligation':0,'instruction':'Correct only the reviewed original behaviour.','refs':[R.ref(path),anchor['source']],
          'review':review,'anchor':anchor,'coordinator_advice_id':'d'*32}

 def test_overrule_between_review_and_reservation_blocks_advice_bound_proof(self):
  kwargs=self.semantic_advice();proof=self.store.offer_narrowing(self.scope,self.model,**kwargs)
  self.assertTrue(self.store.admission(self.scope,self.model)['allowed'])
  self.assertEqual(self.store.revoke_coordinator('d'*32),1)
  self.assertFalse(self.store.admission(self.scope,self.model)['allowed'])
  with self.assertRaises(R.RecoveryHeld):self.store.reserve(self.scope,'withdrawn',self.model)
  with self.store.store.reader() as con:
   self.assertIsNotNone(con.execute('SELECT id FROM pr_proofs WHERE id=?',(proof,)).fetchone())
   self.assertEqual(con.execute('SELECT COUNT(*) FROM pr_launches').fetchone()[0],0)
  with self.assertRaises(R.RecoveryHeld):self.store.offer_narrowing(self.scope,self.model,**kwargs)

 def test_rewritten_advice_cannot_renew_a_consumed_reviewed_source_unit(self):
  kwargs=self.semantic_advice();proof=self.store.offer_narrowing(self.scope,self.model,**kwargs);self.store.reserve(self.scope,'one',self.model)
  self.store.offer_coordinator(self.scope,self.model,'e'*32,'Different wording for the same original source unit.',[R.ref(self.advice)])
  kwargs['coordinator_advice_id']='e'*32
  self.assertEqual(self.store.offer_narrowing(self.scope,self.model,**kwargs),proof)
  self.assertFalse(self.store.admission(self.scope,self.model)['allowed'])
  with self.assertRaises(R.RecoveryHeld):self.store.reserve(self.scope,'two',self.model)

 def test_cutoff_or_pause_cannot_hide_a_recorded_terminal_truncation(self):
  for reason in ('09:00_cutoff','paused_or_shutdown','resource_unavailable'):
   with self.subTest(reason=reason):
    (self.run/'worker-process.json').write_text(json.dumps({'reason':reason,'code':-15}))
    (self.run/'outcome.json').write_text(json.dumps({'outcome':'interrupted'}))
    value=self.store.observe(self.scope,'original',self.run)
    self.assertEqual(value['kind'],'inference_truncated')
    self.legacy_proof('reviewed_narrowing')
    self.assertFalse(self.store.admission(self.scope,self.model)['allowed'])
    with self.assertRaises(R.RecoveryHeld):self.store.reserve(self.scope,'cutoff-retry',self.model)

 def test_clean_control_interruption_stays_a_control_wait(self):
  (self.run/'worker-process.json').write_text(json.dumps({'reason':'09:00_cutoff','code':-15}))
  (self.run/'outcome.json').write_text(json.dumps({'outcome':'interrupted'}))
  (self.run/'worker-tools/decision-4.json').write_text(json.dumps({'status':'failed','phase':'decision','error_class':'InterruptedError','completion_reason':'stream_error','metrics':{}}))
  self.assertEqual(self.store.observe(self.scope,'original',self.run)['kind'],'interrupted')
  self.assertEqual(self.store.admission(self.scope,self.model)['state'],'waiting_for_controls')

 def test_late_advice_preserves_completed_verifying_and_control_states(self):
  (self.run/'worker-tools/decision-4.json').unlink()
  for index,(outcome,finished,reason,expected) in enumerate((('accepted',True,'exited','complete'),('checkpoint',True,'exited','complete'),('failed',True,'exited','verifying'),('pending_delivery',True,'exited','verifying'),('interrupted',False,'09:00_cutoff','waiting_for_controls'))):
   with self.subTest(outcome=outcome):
    (self.run/'outcome.json').write_text(json.dumps({'outcome':outcome}))
    (self.run/'worker-completion.json').write_text(json.dumps({'finished':finished,'contract':S.CONTRACT}))
    (self.run/'worker-process.json').write_text(json.dumps({'reason':reason,'code':0}))
    self.store.observe(self.scope,'original',self.run)
    self.store.offer_coordinator(self.scope,self.model,str(index)*32,'Late advice cannot reopen completed or controlled work.',[R.ref(self.advice)])
    self.assertEqual(self.store.admission(self.scope,self.model)['state'],expected)

 def test_malformed_legacy_certificate_remains_held_in_readonly_snapshot(self):
  for index,certificate in enumerate((None,[],{'kind':'verified_harness_fix','failure_kinds':None},{'kind':'verified_harness_fix','failure_kinds':'inference_truncated'})):
   with self.subTest(certificate=certificate):
    with self.store.store.transaction() as con:
     self.store._offer(con,self.scope,'verified_harness_fix','malformed-'+str(index),self.model,{'certificate':certificate,'refs':[R.ref(self.advice)]})
    self.assertFalse(self.store.snapshot()['scopes'][0]['admission']['allowed'])

 def test_cached_launch_provenance_is_revalidated_against_consumed_proof(self):
  from test_launch_provenance import LaunchProvenanceTests
  for problem in ('model','proof','missing_launch','cached_record'):
   with self.subTest(problem=problem),tempfile.TemporaryDirectory() as name:
    recovery,identity,model,old,reservation,run=LaunchProvenanceTests().fixture(Path(name))
    recovery.observe(identity,'actual-run',run)
    if problem=='model':(run/'worker-model.json').write_text(json.dumps({'model':'different'}))
    elif problem=='cached_record':
     path=run/'reservation-provenance.json';value=json.loads(path.read_text());value['components']['structured_worker.py']='c'*64;path.write_text(json.dumps(value))
    else:
     with recovery.store.transaction() as con:
      if problem=='missing_launch':con.execute('DELETE FROM pr_launches WHERE run=?',('actual-run',))
      else:
       row=con.execute('SELECT payload FROM pr_proofs WHERE id=?',(reservation['proof'],)).fetchone();value=json.loads(row[0]);value['components']['structured_worker.py']='c'*64
       con.execute('UPDATE pr_proofs SET payload=? WHERE id=?',(R.encoded(value),reservation['proof']))
    with self.assertRaises(R.RecoveryHeld):recovery.observe(identity,'actual-run',run)



class OwnerHarnessReleaseTests(unittest.TestCase):
 REASON='Owner decision: harness fixed by a reviewed pull request'
 setUp=HarnessAdmissionTests.setUp;failure=HarnessAdmissionTests.failure

 def changed(self,value='e'):return mock.patch.object(R,'components',return_value=dict(self.components,**{'project_backend.py':value*64}))

 def test_release_needs_changed_sources_a_reason_and_a_harness_hold(self):
  with self.assertRaises(R.RecoveryHeld):self.store.offer_owner_release(self.scope,self.REASON)
  with self.changed():
   with self.assertRaises(R.RecoveryHeld):self.store.offer_owner_release(self.scope,'too short')
   other=R.scope('fixture','other:1',dict(self.task,id='other'),'c'*40,'profile');self.store.enroll(other,dict(self.task,id='other'),self.model)
   with self.assertRaises(R.RecoveryHeld):self.store.offer_owner_release(other,self.REASON)
  self.assertFalse(self.store.admission(self.scope,self.model)['allowed'])

 def test_release_admits_one_launch_and_is_recorded_as_unqualified(self):
  with self.changed():
   proof=self.store.offer_owner_release(self.scope,self.REASON)
   self.assertEqual(self.store.offer_owner_release(self.scope,self.REASON),proof)
   decision=self.store.admission(self.scope,self.model)
   self.assertTrue(decision['allowed']);self.assertEqual(decision['reason'],'owner_harness_release')
   self.assertIs(decision['intervention']['owner_release']['qualified'],False)
   self.assertFalse(self.store.admission(self.scope,dict(self.model,digest='f'*64))['allowed'])
   self.store.reserve(self.scope,'released-run',self.model)
   self.assertFalse(self.store.admission(self.scope,self.model)['allowed'])
   with self.assertRaises(R.RecoveryHeld):self.store.reserve(self.scope,'second-run',self.model)

 def test_release_does_not_cover_a_later_failure_or_later_sources(self):
  with self.changed() as current:
   self.store.offer_owner_release(self.scope,self.REASON)
   again=self.failure('after-release',components=current.return_value);self.store.observe(self.scope,'after-release',again)
   self.assertFalse(self.store.admission(self.scope,self.model)['allowed'])
   with self.assertRaises(R.RecoveryHeld):self.store.offer_owner_release(self.scope,self.REASON)

 def test_release_is_bound_to_the_sources_it_was_given_on(self):
  with self.changed():first=self.store.offer_owner_release(self.scope,self.REASON)
  with self.changed('9'):
   self.assertFalse(self.store.admission(self.scope,self.model)['allowed'])
   second=self.store.offer_owner_release(self.scope,self.REASON);self.assertNotEqual(first,second)
   self.assertEqual(self.store.admission(self.scope,self.model)['proof'],second)

 def unanswered(self,run):
  directory=self.failure(run)
  (directory/'worker-tools/decision-4.json').write_text(json.dumps({'schema_version':2,'status':'failed','phase':'decision','error_class':'TimeoutError',
      'completion_reason':'stream_error','requested_budget':1024,'worker_contract':S.CONTRACT,'model':self.model,
      'inference_policy':{'version':S.INFERENCE_POLICY,'thinking_capable':True},
      'metrics':{'prompt_eval_count':None,'eval_count':None,'thinking_bytes':0,'final_text_bytes':0,'done_reason':None}}))
  self.store.observe(self.scope,run,directory);return directory

 def test_a_model_that_never_answered_can_be_released_once_without_a_source_change(self):
  self.unanswered('silent')
  self.assertEqual(self.store.admission(self.scope,self.model)['state'],'needs_harness_repair')
  proof=self.store.offer_owner_release(self.scope,self.REASON)
  decision=self.store.admission(self.scope,self.model)
  self.assertTrue(decision['allowed']);self.assertEqual(decision['proof'],proof)
  self.assertIs(decision['intervention']['owner_release']['model_never_answered'],True)
  self.store.reserve(self.scope,'after-silence',self.model)
  self.assertFalse(self.store.admission(self.scope,self.model)['allowed'])
  with self.assertRaises(R.RecoveryHeld):self.store.reserve(self.scope,'again',self.model)

 def test_each_silent_run_needs_its_own_release_and_a_real_defect_still_needs_changed_sources(self):
  self.unanswered('silent-one');first=self.store.offer_owner_release(self.scope,self.REASON)
  self.store.reserve(self.scope,'retry-one',self.model);self.unanswered('retry-one')
  self.assertFalse(self.store.admission(self.scope,self.model)['allowed'])
  second=self.store.offer_owner_release(self.scope,self.REASON);self.assertNotEqual(first,second)
  self.assertEqual(self.store.admission(self.scope,self.model)['proof'],second)
  self.store.reserve(self.scope,'retry-two',self.model)
  truncated=self.failure('retry-two');self.store.observe(self.scope,'retry-two',truncated)
  with self.assertRaises(R.RecoveryHeld):self.store.offer_owner_release(self.scope,self.REASON)

 def test_command_requires_a_paused_project_and_reports_real_admission(self):
  root=self.root/'midnight';(root/'runtime').mkdir(parents=True)
  (root/'projects.json').write_text(json.dumps({'projects':[{'id':'fixture','path':str(self.root),'state_dir':str(self.root)}]}))
  with self.changed():
   with self.assertRaises(R.RecoveryHeld):R.owner_release(root,'fixture','repair',self.REASON)
   (self.root/'PAUSED').write_text('')
   with self.assertRaises(R.RecoveryHeld):R.owner_release(root,'fixture','absent',self.REASON)
   result=R.owner_release(root,'fixture','repair',self.REASON)
   self.assertTrue(result['admission']['allowed']);self.assertIs(result['qualified'],False)
   self.store.reserve(self.scope,'released-run',self.model)
   with self.assertRaises(R.RecoveryHeld):R.owner_release(root,'fixture','repair',self.REASON)

if __name__=='__main__':unittest.main()
