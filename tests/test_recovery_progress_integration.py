"""Real selection/reservation boundaries; no live controls or model calls."""
import contextlib,datetime as dt,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import test_micro_recovery as MF
import progress_recovery as P
import project_backend as B
import worker_quality as Q
import micro_recovery as M
from cloud_rescue import RescueStore

class IntegrationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  self.b=B.load_backend(self.root,'notes',settings={});self.b.STATE=self.root/'state';self.b.CLOUD_RUNTIME=self.root/'coordinator';self.b.STATE.mkdir()
  self.model={'model':'fixture','digest':'a'*64};self.task={'id':'held','kind':'repair','title':'Repair exact behavior','description':'One bounded behavior','files':['tests/test_value.py'],'acceptance':['Exact error text','Captured output'],'finding_ids':['f1']}
  self.config={'routine_provider':'local','worker_model':self.model,'progress_recovery_enabled':True}
  self.state={'tries':{'held':1000},'done':[],'accepted':[],'feedback':{},'planned':[]}
  self.s=P.scope('notes','behavior:1',self.task,'a'*40,'profile')
 def fake_run(self,run,kind,bind_profile='profile'):
  d=self.b.STATE/'runs'/run;d.mkdir(parents=True);self.b.write(d/'task.json',self.task)
  self.b.write(d/'worker-process.json',{'code':0,'reason':'exited'});self.b.write(d/'worker-completion.json',{'finished':kind=='semantic','contract':'fixture'})
  self.b.write(d/'worker-model.json',self.model)
  self.b.write(d/'finding-bindings.json',{'head':'a'*40,'profile_hash':bind_profile,'task_sha256':Q.task_fingerprint(self.task)})
  if kind=='semantic':self.b.write(d/'checks.json',[{'code':1,'reason':'exited','command':['fixed','tests']}])
  return d
 def base_patches(self):
  stack=contextlib.ExitStack();stack.enter_context(mock.patch.object(self.b,'quality_engine',return_value=SimpleNamespace(profile_hash='profile')))
  stack.enter_context(mock.patch.object(self.b,'git',return_value='a'*40));stack.enter_context(mock.patch.object(self.b,'issue_episode',return_value='behavior:1'))
  return stack
 def test_latest_failure_is_separate_from_latest_reusable_candidate(self):
  self.fake_run('r1','semantic');self.fake_run('r2','unfinished')
  self.state['retry_candidates']={'held':{'run':'r1','task_sha256':Q.task_fingerprint(self.task)}}
  self.state['feedback_history']={'held':[{'run':'r2','summary':'Newer worker failed scope validation'}]}
  self.b.write(self.b.STATE/'state.json',self.state)
  with self.base_patches():identity,decision=self.b.prepare_progress(self.task,self.state,self.config,self.b.now()+dt.timedelta(minutes=1))
  self.assertEqual(decision['diagnosis']['run'],'r2');self.assertEqual(decision['diagnosis']['kind'],'unfinished_handoff')
 def test_underlying_candidate_pointer_is_revalidated_at_reservation(self):
  r=P.Recovery(self.b.STATE,'notes');self.b.write(self.b.STATE/'state.json',{'retry_candidates':{'held':{'run':'r1','task_sha256':Q.task_fingerprint(self.task)}}})
  r.enroll(self.s,self.task,self.model,legacy_runs=['r1']);review={'decision':'accept','issues':[]};p=self.b.STATE/'review.json';self.b.write(p,review)
  r.offer_narrowing(self.s,self.model,obligation=0,instruction='One concrete obligation',refs=[P.ref(p)],review=review)
  self.assertTrue(r.admission(self.s,self.model)['allowed'])
  self.b.write(self.b.STATE/'state.json',{'retry_candidates':{'held':{'run':'r2','task_sha256':Q.task_fingerprint(self.task)}}})
  with self.assertRaises(P.RecoveryHeld):r.reserve(self.s,'new',self.model)
 def test_old_manager_run_cannot_become_progress_micro_dispatch(self):
  fixture=MF.MicroTests(next(n for n in dir(MF.MicroTests) if n.startswith('test_')));fixture.setUp();self.addCleanup(fixture.doCleanups);fixture.approve()
  s=P.scope('notes','value:1',fixture.task,'a'*40,'profile','micro-0');r=P.Recovery(fixture.root,'notes')
  r.enroll(s,fixture.task,self.model,legacy_runs=['failed1','failed2'],legacy_tries=2)
  p=fixture.root/'review.json';review={'decision':'accept','issues':[]};p.write_text(json.dumps(review));r.offer_narrowing(s,self.model,obligation=0,instruction='One small repair',refs=[P.ref(p)],review=review)
  with self.assertRaises(M.MicroHeld):fixture.store.reserve(fixture.task,'value:1','a'*40,'profile','failed1',self.model)
  self.assertTrue(r.admission(s,self.model)['allowed'])
  first=fixture.store.reserve(fixture.task,'value:1','a'*40,'profile','new-progress',self.model)
  self.assertEqual(first,fixture.store.reserve(fixture.task,'value:1','a'*40,'profile','new-progress',self.model))
  self.assertEqual(fixture.store.current(fixture.task,'value:1','a'*40,'profile')['used'],1)
 def test_ready_repair_is_selected_before_a_diagnostic_model_call(self):
  ready=dict(self.task,id='ready');tasks=[self.task,ready]
  status={'material_findings':[{'id':'f1'}],'feature_allowed':False,'reasons':[]}
  engine=mock.Mock(profile_hash='profile');engine.status.return_value=status
  def choose(head,candidates,**kw):
   available=next((c for c in candidates if not c['exhausted']),None)
   return {'action':'implement_locally','task':available} if available else {'action':'needs_manager'}
  engine.next_decision.side_effect=choose;rescue=mock.Mock();rescue.eligibility.return_value={'allowed':False,'status':'manager_required'}
  def admission(task,state,config,cutoff,**kw):
   if kw.get('diagnose'):raise AssertionError('Ready repair was starved by diagnosis')
   return {},{'allowed':task['id']=='ready','state':'ready' if task['id']=='ready' else 'diagnosing','reason':'fixture'}
  with contextlib.ExitStack() as stack:
   for name,value in [('reconcile_saved_audits',None),('all_tasks',tasks),('current_repair_contract',True),('task_valid',True),('resume_micro_checkpoint',False),('unchecked_candidate',False),('import_episode_failures','behavior:1'),('issue_episode','behavior:1'),('git','a'*40),('quality_engine',engine),('rescue_store',rescue)]:stack.enter_context(mock.patch.object(self.b,name,return_value=value))
   stack.enter_context(mock.patch.object(self.b.PM,'status',return_value={'feature_hold':False}));stack.enter_context(mock.patch.object(self.b,'prepare_progress',side_effect=admission))
   selected=self.b.next_task(self.state,self.config,self.b.now()+dt.timedelta(minutes=1))
  self.assertEqual(selected['id'],'ready')
 def test_new_committed_guidance_enables_guided_work_without_renaming_task(self):
  r=P.Recovery(self.b.STATE,'notes');cr=RescueStore(r.store.path)
  for run in ('initial1','initial2'):cr.record_local_failure('notes','behavior:1','held',run,'acceptance',{'test':'fixed'},host_verified=True)
  r.enroll(self.s,self.task,self.model,legacy_runs=['initial1','initial2'],legacy_tries=2)
  guidance={'summary':'Actual Codex guidance','steps':['Fix the exact condition','Run the original check']};cr.record_guidance('notes','behavior:1','g1',guidance)
  gp=self.b.STATE/'guidance.json';gp.write_text(json.dumps(guidance));r.offer_guidance(self.s,self.model,[P.ref(gp)]);r.reserve(self.s,'guided1',self.model)
  cr.record_local_failure('notes','behavior:1','held','guided1','acceptance',{'test':'fixed'},host_verified=True,guidance_id='g1')
  review={'decision':'accept','issues':[]};rp=self.b.STATE/'review.json';rp.write_text(json.dumps(review))
  r.offer_narrowing(self.s,self.model,obligation=0,instruction='One smaller guided edit',refs=[P.ref(rp)],review=review,guidance_id='g1');r.reserve(self.s,'guided2',self.model)
  cr.record_local_failure('notes','behavior:1','held','guided2','acceptance',{'test':'fixed'},host_verified=True,guidance_id='g1')
  self.assertTrue(cr.eligibility('notes','behavior:1')['allowed']);self.assertEqual(cr.eligibility('notes','behavior:1')['attempts_168h'],0)
  with cr._connect() as con:
   self.assertEqual({row[0] for row in con.execute('SELECT DISTINCT task_id FROM cr_events WHERE substantive=1')},{'held'})
  self.assertEqual(r.snapshot()['scopes'][0]['scope']['task_hash'],Q.task_fingerprint(self.task))
 def test_old_guidance_cannot_be_repackaged_as_a_new_admission(self):
  r=P.Recovery(self.b.STATE,'notes');cr=RescueStore(r.store.path)
  for run in ('old1','old2'):cr.record_local_failure('notes','behavior:1','held',run,'acceptance',{'test':'fixed'},host_verified=True)
  guidance={'summary':'Earlier advice','steps':['Original guidance']};cr.record_guidance('notes','behavior:1','old-g',guidance)
  r.enroll(self.s,self.task,self.model,legacy_tries=4)
  path=self.b.STATE/'guidance.json';path.write_text(json.dumps(guidance))
  self.assertIsNone(r.offer_guidance(self.s,self.model,[P.ref(path)]));self.assertFalse(r.admission(self.s,self.model)['allowed'])
 def test_exhausted_cloud_daily_allowance_does_not_hide_local_work(self):
  import midnight as midnight
  p={'id':'notes','on_schedule':True,'state_dir':str(self.b.STATE),'path':str(self.root),'config':'config.json'}
  state={'day':self.b.now().astimezone(midnight.ZoneInfo('Australia/Adelaide')).date().isoformat(),'cloud_calls_today':100,'attempts_today':100,'failures_today':100}
  self.b.write(self.b.STATE/'state.json',state);self.b.write(self.root/'config.json',{'routine_provider':'local','max_attempts_per_night':1,'max_cloud_calls_per_night':1,'max_failures_per_night':1})
  with mock.patch.object(midnight,'STATE',self.b.CLOUD_RUNTIME),mock.patch.object(midnight,'effective_limits',return_value={}):self.assertTrue(midnight.eligible(p,self.b.now()))
 def test_new_verification_outcome_wins_over_old_local_progress_launch(self):
  r=P.Recovery(self.b.STATE,'notes');r.enroll(self.s,self.task,self.model);r.reserve(self.s,'r1',self.model)
  self.fake_run('r1','unfinished');self.fake_run('r2','semantic')
  self.state.update(last_outcome={'task':'held','run':'r2'},feedback_history={'held':[{'run':'r2'}]})
  with mock.patch.object(self.b,'progress_identity',return_value=self.s):_,decision=self.b.prepare_progress(self.task,self.state,self.config,self.b.now()+dt.timedelta(minutes=1))
  self.assertEqual(decision['diagnosis']['run'],'r2');self.assertEqual(decision['diagnosis']['kind'],'semantic')
 def test_full_recovery_context_is_bounded_and_secrets_are_redacted(self):
  raw={'review_issues':['x'*16000 for _ in range(8)],'checks':[{'output':'TOKEN=sk-test-retained-secret\nACCESS_TOKEN=secret-value'}]}
  context=self.b.recovery_context(raw)
  self.assertLessEqual(len(context.encode()),60000);self.assertNotIn('sk-test-retained-secret',context);self.assertNotIn('secret-value',context)
  self.assertIn('truncated',context);json.loads(context)
 def harness_fixture(self):
  from test_quality_cycle import profile
  import quality_cycle as QC
  controller=self.root/'controller';controller.mkdir();(controller/'runtime').mkdir()
  self.b.write(controller/'projects.json',{'projects':[{'id':'notes','on_schedule':True,'path':str(self.root),'config':'config.json','state_dir':str(self.b.STATE)}]})
  self.b.write(self.root/'config.json',{'quality_profile':profile()})
  s=P.scope('notes','behavior:1',self.task,'a'*40,QC.profile_hash(profile()));r=P.Recovery(self.b.STATE,'notes')
  r.enroll(s,self.task,self.model,legacy_tries=4);self.fake_run('r1','unfinished',s['profile'])
  self.b.write(self.b.STATE/'runs/r1/worker-completion.json',{'finished':False,'contract':'fixture','recovery_components':P.components()})
  f=r.observe(s,'r1',self.b.STATE/'runs/r1');r.wait(s,'needs_harness_repair','Actual worker handoff needs Codex')
  self.b.write(self.b.STATE/'state.json',{'last_outcome':{'task':'held','run':'r1'}})
  request={'id':'fixed-request','scope':s,'failure':f,'source_head':'b'*40}
  return controller,r,s,request
 def qualified_harness_intervention(self,r,s,request):
  path=self.b.STATE/'review.json';path.write_text(json.dumps({'decision':'accept','issues':[]}))
  certificate={'id':'e'*64,'kind':'verified_harness_fix','project':'notes','model':self.model,
      'worker_sources':{n:P.components()[n] for n in ('local_worker.py','structured_worker.py','ralphy/run.sh')},
      'from_components':request['failure']['components'],'failure_kinds':['unfinished_handoff'],'refs':[P.ref(path)],'instruction':'Use the independently qualified harness repair.'}
  patch=mock.patch.object(P,'validate_qualification');patch.start();self.addCleanup(patch.stop)
  r.offer_certificate(s,self.model,certificate)
 def test_completed_or_superseded_harness_requests_cannot_charge_cloud_repair(self):
  import guardian as G
  from action_store import Actions
  controller,r,s,request=self.harness_fixture()
  with mock.patch('git_delivery.git',side_effect=lambda root,*_: 'b'*40 if Path(root)==controller else 'a'*40):
   self.assertTrue(P.harness_request_current(controller,request))
   self.b.write(self.b.STATE/'state.json',{'done':['held']})
   self.assertFalse(P.harness_request_current(controller,request))
   actions=Actions(controller/'runtime');incident=actions.incident('midnight','worker_contract','same-failure','evidence',{'progress_request':request})
   self.assertIsNone(G.launch_repair(controller,actions,incident,spawn=lambda *_a,**_k:self.fail('Obsolete cloud repair launched')))
   self.assertEqual(actions.repairs(active=False),[])
   self.b.write(self.b.STATE/'state.json',{'last_outcome':{'task':'held','run':'r2'}})
   self.assertFalse(P.harness_request_current(controller,request))
 def test_ready_intervention_retires_old_harness_request(self):
  controller,r,s,request=self.harness_fixture();self.qualified_harness_intervention(r,s,request)
  with mock.patch('git_delivery.git',side_effect=lambda root,*_: 'b'*40 if Path(root)==controller else 'a'*40):self.assertFalse(P.harness_request_current(controller,request))
 def test_new_inflight_reservation_invalidates_old_harness_request(self):
  controller,r,s,request=self.harness_fixture();self.qualified_harness_intervention(r,s,request);r.reserve(s,'new-active-run',self.model)
  self.b.write(self.b.STATE/'state.json',{'last_outcome':{'task':'held','run':'r1'},'active_attempt':{'task':self.task,'run':'new-active-run'}})
  with mock.patch('git_delivery.git',side_effect=lambda root,*_: 'b'*40 if Path(root)==controller else 'a'*40):self.assertFalse(P.harness_request_current(controller,request))
 def test_completed_predecessor_does_not_complete_new_microstep(self):
  fixture=MF.MicroTests(next(n for n in dir(MF.MicroTests) if n.startswith('test_')));fixture.setUp();self.addCleanup(fixture.doCleanups);fixture.approve();fixture.advance('predecessor')
  b=B.load_backend(fixture.root,'notes',settings={});b.STATE=fixture.root;b.CLOUD_RUNTIME=fixture.root/'coordinator'
  directory=fixture.root/'runs/predecessor';b.write(directory/'task.json',fixture.task);b.write(directory/'outcome.json',{'outcome':'checkpoint'})
  state={'tries':{'narrow':10},'last_outcome':{'task':'narrow','run':'predecessor'}}
  with mock.patch.object(b,'git',return_value='a'*40),mock.patch.object(b,'issue_episode',return_value='value:1'),mock.patch.object(b,'quality_engine',return_value=SimpleNamespace(profile_hash='profile')):
   identity,decision=b.prepare_progress(fixture.task,state,self.config,b.now()+dt.timedelta(minutes=1))
  self.assertEqual(identity['stage'],'micro-1');self.assertTrue(decision['allowed']);self.assertEqual(decision['reason'],'initial');self.assertIsNone(decision.get('diagnosis'))
 def test_new_occurrence_preserves_request_retirement_and_incident_budget(self):
  import os,phase_runtime as PR
  from action_store import Actions
  controller,r,s,request=self.harness_fixture();actions=Actions(controller/'runtime');job=actions.submit('notes',{'source_head':'b'*40},self.b.now().timestamp()+60);actions.claim(job['id'],PR.lease())
  with mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':job['id']}):
   first=actions.request_harness(s,request['failure']);actions.retire_harness(first)
   self.fake_run('r2','unfinished',s['profile']);self.b.write(self.b.STATE/'runs/r2/worker-completion.json',{'finished':False,'contract':'fixture','recovery_components':P.components()})
   failure=r.observe(s,'r2',self.b.STATE/'runs/r2');r.wait(s,'needs_harness_repair','New exact occurrence')
   second=actions.request_harness(s,failure)
  self.assertEqual(first,second);self.assertEqual(actions.harness_requests(),[])
  with actions.read() as con:
   saved=con.execute('SELECT state,payload FROM wf_harness_requests WHERE id=?',(first,)).fetchone()
   self.assertEqual(saved['state'],'superseded');latest=json.loads(saved['payload']);self.assertEqual(latest['failure']['run'],'r2')
  incident=actions.incident('midnight','worker_contract','progress-'+failure['fingerprint'],'r1',{'progress_request':request})
  charged=actions.reserve_repair(incident,{'source':'b'*40});actions.repair(charged,state='failed')
  same=actions.incident('midnight','worker_contract','progress-'+failure['fingerprint'],'r2',{'progress_request':latest})
  self.assertEqual(same,incident);actions.reserve_repair(same,{'source':'b'*40});self.assertEqual(len(actions.repairs(active=False)),2)
 def test_ordinary_missing_source_profile_evidence_is_not_assumed_current(self):
  r=P.Recovery(self.b.STATE,'notes');self.fake_run('unknown-source','unfinished');(self.b.STATE/'runs/unknown-source/finding-bindings.json').unlink()
  self.state['last_outcome']={'task':'held','run':'unknown-source'}
  with mock.patch.object(self.b,'progress_identity',return_value=self.s):_,decision=self.b.prepare_progress(self.task,self.state,self.config,self.b.now()+dt.timedelta(minutes=1),diagnose=True)
  self.assertFalse(decision['allowed']);self.assertEqual(decision['state'],'needs_codex');self.assertIsNone(decision['diagnosis'].get('run'))
 def test_unverified_micro_seed_does_not_bypass_new_progress_admission(self):
  task=dict(self.task,local_worker_files=['tests/test_value.py']);decision={'allowed':True,'state':'ready','reason':'reviewed_narrowing'}
  with mock.patch.object(self.b,'import_episode_failures',return_value='behavior:1'),mock.patch.object(self.b,'unchecked_candidate',return_value=True),mock.patch.object(self.b,'prepare_progress',return_value=(self.s,decision)) as prepare:
   self.assertEqual(self.b.implementation_choice(task,self.state,self.config,self.b.now()+dt.timedelta(minutes=1)),('local','behavior:1'));prepare.assert_called_once()

if __name__=='__main__':unittest.main()
