"""Conversation semantics, identity, crash reconciliation and calendar invariants."""
import contextlib,datetime as dt,hashlib,io,json,os,tempfile,time,unittest
from pathlib import Path
from unittest import mock
import agent_workspace as C
import agent_discussion as A
import agent_workspace_cli as CLI
import workspace_view as V
import project_backend as B
import structured_worker as S
import core_repair as CR
import midnight as M
from operation_status import describe
from action_store import Actions,fingerprint

class WorkspaceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.instant=dt.datetime(2026,11,1,2,tzinfo=dt.timezone.utc);self.clock=[self.instant.timestamp()]
  self.w=C.Workspace(self.root/'agent-workspace',clock=lambda:self.clock[0])
  self.projects=[{'id':p,'name':p.title(),'on_schedule':True,'state_dir':str(self.root/p)} for p in ('one','two')]
  self.w.bootstrap(self.projects)
  self.alive=mock.patch.object(C.P,'alive',return_value=True);self.alive.start();self.addCleanup(self.alive.stop)
 def cap(self,actor='worker:one'):
  return self.w.issue_session(actor,{'task':'retained-task','model':{'model':'fixture'}},{'fixture':True},self.clock[0]+100)['capability']
 def post(self,body='Observed result',room='project-one',**kw):return self.w.operator_post('codex',room,body,**kw)
 def test_read_paths_do_not_create_runtime_or_schedule_work(self):
  empty=C.Workspace(self.root/'absent')
  for path in ('/api/chat/overview','/api/chat/rooms','/api/chat/inbox','/api/chat/messages?room=general'):
   V.inspect(path,empty)
  self.assertFalse(empty.runtime.exists())
 def test_private_permissions_and_persistent_history(self):
  m=self.post();fresh=C.Workspace(self.w.runtime)
  self.assertEqual(fresh.messages('project-one')['messages'][0]['id'],m['id'])
  self.assertEqual(self.w.runtime.stat().st_mode&0o777,0o700)
  self.assertEqual(self.w.store.path.stat().st_mode&0o777,0o600)
 def test_direct_and_group_membership_are_stable(self):
  dm=self.w.conversation('worker:one',['reviewer:one'])
  self.assertEqual(dm,self.w.conversation('reviewer:one',['worker:one']))
  group=self.w.conversation('worker:one',['reviewer:one','planner:two'],name='Troubleshoot')
  self.assertNotEqual(dm,group)
  self.w.agent_post(self.cap(),dm,'Can you check this?','question',event_key='1')
  with self.assertRaises(ValueError):self.w.agent_read(self.cap('worker:two'),dm)
  self.assertEqual(self.w.pending_work()[0]['actor'],'reviewer:one')
 def test_capability_cannot_impersonate_codex_or_host(self):
  for actor in ('codex','midnight'):
   with self.assertRaises(ValueError):self.cap(actor)
  token=self.cap()
  for kind in ('decision','system'):
   with self.assertRaises(ValueError):self.w.agent_post(token,'general','Fake approval',kind,event_key=kind)
  with mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':'local'}):
   with self.assertRaises(ValueError):self.w.operator_post('codex','general','Fake Codex')
   with self.assertRaises(ValueError):self.w.decide('all','Fake release','guidance')
   with self.assertRaises(ValueError):self.w.register_agent('pretend','Pretend')
 def test_expiry_lost_owner_and_unregistered_sender_are_rejected(self):
  token=self.cap();self.clock[0]+=101
  with self.assertRaises(ValueError):self.w.agent_read(token,'general')
  token=self.cap()
  with mock.patch.object(C.P,'alive',return_value=False),self.assertRaises(ValueError):self.w.agent_read(token,'general')
  with self.assertRaises(ValueError):self.cap('unknown')
 def test_replay_is_exact_and_session_budget_is_durable(self):
  token=self.cap();first=self.w.agent_post(token,'general','One','message',event_key='0')
  self.assertEqual(first['id'],self.w.agent_post(token,'general','One','message',event_key='0')['id'])
  with self.assertRaises(ValueError):self.w.agent_post(token,'general','Changed','message',event_key='0')
  for i in range(1,8):self.w.agent_post(token,'general',str(i),'message',event_key=str(i))
  with self.assertRaises(ValueError):self.w.agent_post(token,'general','Extra','message',event_key='8')
  self.assertEqual(self.w.agent_post(token,'general','One','message',event_key='0')['id'],first['id'])
 def test_reply_must_be_same_room_and_closes_only_recipient_inbox(self):
  question=self.post('Architect advice please',kind='question',mentions=['planner:one','reviewer:one'])
  with self.assertRaises(ValueError):self.w.agent_post(self.cap('planner:one'),'general','Answer','reply',parent=question['id'],event_key='bad')
  self.w.agent_post(self.cap('planner:one'),'project-one','Answer','reply',parent=question['id'],event_key='ok')
  self.assertEqual(self.w.inbox('planner:one'),[]);self.assertEqual(len(self.w.inbox('reviewer:one')),1)
 def test_principal_question_does_not_enter_local_inference_queue(self):
  self.w.agent_post(self.cap(),'architecture','Need a decision','question',mentions=['codex'],event_key='q')
  self.assertEqual(len(self.w.inbox('codex')),1);self.assertEqual(self.w.pending_work(),[])
 def test_history_cursor_search_and_escaped_text(self):
  for i in range(7):self.post('needle <script> '+str(i))
  page=self.w.messages('project-one',limit=3,query='needle')
  self.assertEqual([m['sequence'] for m in page['messages']],[5,6,7])
  second=self.w.messages('project-one',limit=3,before=page['next_cursor'])
  self.assertEqual([m['sequence'] for m in second['messages']],[2,3,4])
  self.assertIn('<script>',page['messages'][0]['body'])
  for before in (0,True,-1):
   with self.assertRaises(ValueError):self.w.messages('project-one',before=before)
 def test_principal_hold_is_scoped_and_exact_release_is_required(self):
  hold=self.w.decide('one','Resolve actual regression first','hold_delivery')
  self.assertEqual(len(self.w.delivery_holds('one')),1);self.assertEqual(self.w.delivery_holds('two'),[])
  self.assertFalse(any(d['scope']=='one' for d in self.w.context('worker:two')['principal_decisions']))
  with self.assertRaises(ValueError):self.w.decide('two','Release another project','release',releases=hold['id'])
  self.w.decide('one','Regression checked; original gates remain','release',releases=hold['id'])
  self.assertEqual(self.w.delivery_holds('one'),[])
 def test_backend_gate_does_not_change_ordinary_acceptance_or_other_projects(self):
  hold=self.w.decide('one','Check regression','hold_delivery');b=B.load_backend(self.root,'one',settings={})
  b.CLOUD_RUNTIME=self.root
  with mock.patch.object(b.PR,'enabled',return_value=True),self.assertRaises(b.ManagerHeld):b.principal_delivery_gate()
  self.w.decide('one','Checked','release',releases=hold['id'])
  with mock.patch.object(b.PR,'enabled',return_value=True):b.principal_delivery_gate()
 def test_scrum_starts_next_midnight_once_and_uses_adelaide_dst(self):
  self.assertIsNone(self.w.ensure_scrum(self.projects,self.instant))
  next_at=dt.datetime.fromisoformat(self.w.overview()['next_scrum_at'])
  self.assertEqual(next_at.hour,0);self.assertEqual(next_at.utcoffset(),dt.timedelta(hours=10,minutes=30))
  self.clock[0]=next_at.timestamp()+90
  meeting=self.w.ensure_scrum(self.projects,dt.datetime.fromtimestamp(self.clock[0],dt.timezone.utc))
  self.assertEqual(meeting['opened']-meeting['scheduled'],90)
  self.assertEqual(self.w.ensure_scrum(self.projects)['room'],meeting['room'])
  self.assertEqual(len(self.w.pending_work()),4)
  self.assertEqual(self.w.messages(meeting['room'])['messages'][0]['actor'],'midnight')
 def test_scrum_late_boot_catches_up_today_without_fabricating_past_attendance(self):
  self.clock[0]+=3*86400+3600
  meeting=self.w.ensure_scrum(self.projects)
  self.assertEqual(len(self.w.overview()['recent_meetings']),1)
  self.assertGreater(meeting['opened'],meeting['scheduled'])
  self.assertEqual({w['actor'] for w in self.w.pending_work()},{r+':'+p for p in ('one','two') for r in ('planner','reviewer')})
 def test_unanswered_old_scrum_remains_missed_history(self):
  self.clock[0]+=86400;old=self.w.ensure_scrum(self.projects)
  self.clock[0]+=86400;self.w.ensure_scrum(self.projects)
  with self.w.store.reader() as con:
   self.assertEqual(con.execute('SELECT state FROM aw_meetings WHERE day=?',(old['day'],)).fetchone()[0],'partial')
   self.assertEqual(con.execute("SELECT COUNT(*) FROM aw_work WHERE day=? AND state='missed'",(old['day'],)).fetchone()[0],4)
 def test_failed_claim_is_not_repeated_and_stays_visible(self):
  self.post('Check it',kind='question',mentions=['planner:one']);work=self.w.pending_work()[0]
  self.assertTrue(self.w.claim_work(work['id'],{'fixture':True}))
  with mock.patch.object(C.P,'alive',return_value=False),mock.patch.object(A.PA,'source_head',return_value='a'*40):A.recover_claims(self.w)
  self.assertEqual(self.w.pending_work(),[]);self.assertEqual(self.w.overview()['failed_local_replies'],1)
  self.assertEqual(self.w.inbox('planner:one')[0]['state'],'failed')
 def test_completed_crash_recovers_exact_receipt_once_without_inference(self):
  self.post('Check it',kind='question',mentions=['planner:one']);work=self.w.pending_work()[0];self.w.claim_work(work['id'],{'fixture':True})
  d=self.w.runtime/'turns'/work['id'];d.mkdir(parents=True)
  model={'model':'fixture'};head='a'*40;prompt='Actual frozen facts';answer={'body':'Checked the supplied facts','needs_principal':False}
  values={'result.json':answer,'events.jsonl':{},'process.json':{'code':0,'reason':'exited'},'supervision-model.json':model,
   'local-metrics.json':{},'input.txt':prompt,'schema.json':A.REPLY_SCHEMA,'local-request.json':{'model':model,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()}}
  for name,value in values.items():(d/name).write_text(value if isinstance(value,str) else json.dumps(value))
  (d/'turn-binding.json').write_text(json.dumps({'model':model,'source_head':head,'invocation':'job','work':work['id']}))
  receipt={'input_hash':fingerprint({'prompt':prompt,'schema':A.REPLY_SCHEMA,'model':model,'head':head}),
   'files':{n:hashlib.sha256((d/n).read_bytes()).hexdigest() for n in values}}
  (d/'local-decision-receipt.json').write_text(json.dumps(receipt))
  with mock.patch.object(C.P,'alive',return_value=False),mock.patch.object(A.PA,'source_head',return_value=head),mock.patch.object(B,'local_decision',side_effect=AssertionError('Recovery must not infer')):
   A.recover_claims(self.w);A.recover_claims(self.w)
  replies=[m for m in self.w.messages('project-one')['messages'] if m['kind']=='reply']
  self.assertEqual(len(replies),1);self.assertEqual(replies[0]['actor'],'planner:one')
  (d/'result.json').write_text('{}');self.assertIsNone(A.completed_reply(d,head))
 def test_removal_deactivates_actor_and_registration_does_not_admit_work(self):
  self.w.register_agent('specialist','Specialist','one');self.w.bootstrap(self.projects[:1])
  self.assertIn('specialist',{a['id'] for a in self.w.actors()})
  with self.assertRaises(ValueError):self.cap('worker:two')
  self.post('Advice',kind='question',mentions=['specialist'],room='general')
  self.assertEqual(self.w.pending_work(),[])
 def test_read_api_rejects_ambiguous_and_write_requests(self):
  for path in ('/api/chat/messages?room=general&room=architecture','/api/chat/messages?room=general&limit=-1','/api/chat/post?body=change','/api/chat/rooms?extra=1'):
   with self.assertRaises(ValueError):V.inspect(path,self.w)
 def test_body_controls_and_size_are_validated(self):
  for body in ('', 'x'*4001,'bad\u202e','bad\x00'):
   with self.assertRaises(ValueError):self.post(body)
 def test_registry_reconciliation_keeps_decisions_and_prior_history(self):
  message=self.post();self.w.decide('one','Direction','guidance')
  self.projects[0]['name']='Renamed';self.w.bootstrap(self.projects)
  self.assertEqual(self.w.messages('project-one')['messages'][0]['id'],message['id'])
  self.assertTrue(self.w.context('planner:one')['principal_decisions'])
 def test_coding_worker_can_chat_but_chat_alone_is_not_a_submission(self):
  work=self.root/'work';work.mkdir();(work/'value.py').write_text('value=0\n')
  manifest={'task_id':'retained','allowed_files':['value.py'],'chat':{'capability':self.cap()},'_sha256':'fixture'}
  tools=self.root/'tools';tools.mkdir();worker=S.Worker(work,tools,manifest,{'model':'fixture'})
  payload={'body':'What is the exact assertion?','kind':'question','reply_to':'','mentions':[],'participants':[]}
  with mock.patch.object(S.C,'Workspace',return_value=self.w):
   result=worker.chat({'action':'post_chat','path':'@reviewer:one'},payload)
   self.assertEqual(worker.chat({'action':'read_chat','path':result['conversation']})['messages'][0]['body'],payload['body'])
   for i in range(3):worker.chat({'action':'post_chat','path':'project-one'},dict(payload,kind='message',body=str(i)))
   with self.assertRaises(ValueError):worker.chat({'action':'post_chat','path':'project-one'},payload)
  with contextlib.redirect_stdout(io.StringIO()):result=worker.action({'action':'finish','path':'','content':'','summary':'Only talked'},time.monotonic()+1)
  self.assertIn('error',result)
  self.assertFalse((work/'.midnight-ready.json').exists());self.assertEqual((work/'value.py').read_text(),'value=0\n')
 def test_controller_pause_never_bootstraps_or_launches_chat(self):
  (self.root/'runtime').mkdir();(self.root/'runtime/PAUSED').touch()
  with mock.patch.object(A.C.Workspace,'bootstrap',side_effect=AssertionError('Paused writes')):
   self.assertIsNone(A.schedule(self.root,{'projects':self.projects},self.instant,self.instant+dt.timedelta(hours=1),'session'))
 def test_paused_or_unregistered_project_cannot_get_chat_inference(self):
  work={'actor':'planner:one'};self.assertTrue(A.eligible_work(work,{'projects':self.projects}))
  (self.root/'one').mkdir();(self.root/'one/PAUSED').touch()
  self.assertFalse(A.eligible_work(work,{'projects':self.projects}))
  for actor in ('codex','midnight','specialist','planner:absent'):
   self.assertFalse(A.eligible_work({'actor':actor},{'projects':self.projects}))
 def test_pending_old_question_is_in_context_despite_unrelated_activity(self):
  q=self.post('Exact old question',kind='question',mentions=['planner:one']);work=self.w.pending_work()[0]
  for i in range(40):self.post('Recent unrelated activity '+str(i),room='general')
  context=self.w.discussion_context(work)
  self.assertEqual(context['question']['id'],q['id']);self.assertEqual(context['question']['body'],'Exact old question')
 def test_recipient_control_is_checked_inside_resource_wait_before_charging(self):
  b=B.load_backend(self.root,'one',settings={});b.STATE=self.root/'state';b.CLOUD_RUNTIME=self.root/'runtime'
  b.CONTROL_STOP=lambda:(self.root/'one/PAUSED').exists()
  @contextlib.contextmanager
  def waiting(*args,**kw):
   (self.root/'one').mkdir();(self.root/'one/PAUSED').touch()
   if kw['stopped']():raise b.PR.PhaseInterrupted('paused in queue')
   yield 'never'
  charge=mock.Mock()
  with mock.patch.object(b.PR,'enabled',return_value=True),mock.patch.object(b.PR,'phase',side_effect=waiting),mock.patch.object(b.subprocess,'Popen') as spawn:
   with self.assertRaises(b.PR.PhaseInterrupted):b.charged_run(['python','never.py'],self.root,self.root/'log',30,dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=1),{},charge,resource_kind='local')
   charge.assert_not_called();spawn.assert_not_called()
 def test_recipient_pause_immediately_before_spawn_is_uncharged(self):
  b=B.load_backend(self.root,'one',settings={});b.CONTROL_STOP=mock.Mock(side_effect=[False,True]);charge=mock.Mock()
  with mock.patch.object(b.PR,'enabled',return_value=False),mock.patch.object(b.subprocess,'Popen') as spawn:
   result=b._run_process(['python','never.py'],self.root,self.root/'log',30,dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=1),{},on_launch=charge)
   self.assertEqual(result['reason'],'stopped_before_start');charge.assert_not_called();spawn.assert_not_called()
 def test_maintenance_and_discussions_have_atomic_exclusive_admission(self):
  store=Actions(self.root/'runtime',clock=lambda:1000)
  incident=store.incident('midnight','fault','retained','proof',{})
  job=store.submit('midnight',{'kind':'agent_discussion'},2000)
  for running in (False,True):
   if running:store.claim(job['id'],{'pid':1})
   self.assertIsNone(store.reserve_repair(incident,{}));self.assertEqual(store.get_incident(incident)['attempts'],0)
  store.finish(job['id'],'succeeded',{})
  repair=store.reserve_repair(incident,{})
  self.assertIsNotNone(repair);self.assertIsNone(store.submit('midnight',{'kind':'agent_discussion'},2000))
  self.assertEqual(store.get_incident(incident)['attempts'],1)
 def test_maintenance_cannot_claim_an_existing_different_core_job(self):
  store=mock.Mock();store.repair.return_value={'state':'reserved','incident':'incident','payload':{'source':'base'}}
  store.submit.return_value={'id':'chat-job','payload':{'kind':'agent_discussion'}}
  with mock.patch.object(CR,'Actions',return_value=store),mock.patch.object(CR.G,'git',return_value='a'*40),mock.patch.object(CR.P,'lease',return_value={'fixture':True}):
   with self.assertRaises(ValueError):CR.execute(self.root,'repair')
  store.claim.assert_not_called()
 def test_architect_hold_blocks_uncommitted_crash_promotion_but_keeps_receipts(self):
  b=B.load_backend(self.root,'one',settings={});b.CLOUD_RUNTIME=self.root
  intent={'run':'retained-run','prepared':True,'base':'a'*40}
  self.w.decide('one','Hold after earlier review','hold_delivery')
  def git(*args):return '' if args[0]=='log' else 'a'*40
  with mock.patch.object(b.PR,'enabled',return_value=True),mock.patch.object(b,'git',side_effect=git) as calls:
   with self.assertRaises(b.ManagerHeld):b.resume_promotion(intent,allow_promotion=True)
   self.assertFalse(any(c.args[0] in {'apply','commit'} for c in calls.call_args_list))
  def committed(*args):return 'b'*40 if args[0]=='log' else b.RUN_TRAILER+': retained-run'
  with mock.patch.object(b,'git',side_effect=committed):self.assertEqual(b.resume_promotion(intent,allow_promotion=True),'b'*40)
 def test_dense_chat_cannot_evict_principal_direction(self):
  decision=self.w.decide('one','Preserve exact validation and owner authority.','guidance')
  for i in range(8):self.post('長'*3000+str(i))
  context=self.w.context('planner:one')
  self.assertEqual(context['principal_decisions'][0]['id'],decision['id'])
  self.assertLessEqual(len(C.encoded(context).encode()),8000)
 def test_long_room_history_and_long_decisions_have_a_final_context_bound(self):
  for i in range(80):
   actor='specialist'+str(i);self.w.register_agent(actor,'Specialist '+str(i))
   self.w.conversation('planner:one',['codex',actor],name='界'*110)
  for i in range(8):self.w.decide('one','指示'*1900+str(i),'guidance')
  context=self.w.context('planner:one')
  self.assertTrue(context['principal_decisions']);self.assertTrue(context['principal_decisions'][0]['excerpted'])
  self.assertLessEqual(len(C.encoded(context).encode()),8000);self.assertLessEqual(len(context['rooms']),8)
 def test_paused_question_backlog_cannot_starve_an_eligible_project(self):
  for i in range(21):self.post('Older paused question '+str(i),kind='question',mentions=['planner:one'])
  question=self.post('Eligible later question',room='project-two',kind='question',mentions=['planner:two'])
  pending=self.w.pending_work(['planner:two','reviewer:two','worker:two'])
  self.assertEqual(len(pending),1);self.assertEqual(pending[0]['parent'],question['id'])
  self.assertEqual(len(self.w.inbox('planner:one')),21)
 def test_model_facing_history_and_exact_question_are_bounded_with_hashes(self):
  for i in range(8):self.post('😀'*3999+str(i),kind='question',mentions=['planner:one'])
  full=self.w.messages('project-one')['messages']
  page=self.w.agent_read(self.cap('planner:one'),'project-one',limit=8)
  self.assertLessEqual(len(C.encoded(page).encode()),12000)
  self.assertTrue(page['messages'][-1]['excerpted'])
  self.assertEqual(page['messages'][-1]['body_sha256'],hashlib.sha256(full[-1]['body'].encode()).hexdigest())
  context=self.w.discussion_context(self.w.pending_work()[0])
  self.assertTrue(context['question']['excerpted']);self.assertLessEqual(len(C.encoded(context).encode()),20000)
 def test_final_inference_history_bound_covers_four_large_messages(self):
  original='😀'*3999
  messages=[{'role':'system','content':'Base instructions'},{'role':'user','content':'Task'},
   {'role':'assistant','content':'Read history'},{'role':'user','content':json.dumps({'host_tool_result':[original]*8})}]
  S.bound_history(messages)
  self.assertLessEqual(len(json.dumps(messages).encode()),86000)
  self.assertIn('original_sha256',messages[-1]['content'])
 def test_conversation_never_hides_actual_product_coding_or_stale_controller(self):
  view={'phase':'working','service':{'loaded':True,'running':True},'checked_at':'2026-11-01T00:00:00+10:30',
    'controller':{'updated_at':'2026-10-31T13:29:59+00:00'},'next_start':'2026-11-02T00:00:00+10:30',
    'projects':[{'name':'One','active_task':{'title':'Actual product repair'},'activity':'local_worker'}],
    'agent_workspace':{'active_discussions':[{'actor':'planner:two','kind':'reply','phase_state':'waiting'}]}}
  baseline=describe({k:v for k,v in view.items() if k!='agent_workspace'});result=describe(view)
  self.assertEqual(result['local_worker_active'],baseline['local_worker_active']);self.assertIn('Actual product repair',result['happening'])
  view['controller']['updated_at']='2026-10-30T13:00:00+00:00';view['agent_workspace']['active_discussions'][0]['phase_state']='running'
  result=describe(view);self.assertEqual(result['state'],'attention');self.assertFalse(result['running']);self.assertFalse(result['work_active'])
 def test_exact_thread_loads_parent_and_replies_even_outside_current_page(self):
  parent=self.post('Old root');self.w.agent_post(self.cap('reviewer:one'),'project-one','Actual reply','reply',parent=parent['id'],event_key='reply')
  for i in range(50):self.post('Later message '+str(i))
  thread=V.inspect('/api/chat/thread?room=project-one&message='+parent['id'],self.w)
  self.assertEqual([m['body'] for m in thread['messages']],['Old root','Actual reply'])
  with self.assertRaises(ValueError):self.w.thread('project-two',parent['id'])
 def test_scrum_achievement_interval_uses_prior_adelaide_day_and_saved_facts(self):
  project=self.projects[0];runtime=Path(project['state_dir']);runtime.mkdir()
  accepted=[{'id':'a','run':'old','title':'Outside prior day','commit':'a'*40,'at':'2026-10-30T10:00:00Z'},
    {'id':'b','run':'yesterday','title':'Actual prior day','commit':'b'*40,'at':'2026-10-31T10:00:00Z'},
    {'id':'c','run':'today','title':'Today','commit':'c'*40,'at':'2026-11-01T01:00:00Z'}]
  (runtime/'state.json').write_text(json.dumps({'accepted':accepted}))
  facts=A.project_facts(project,self.instant)
  self.assertEqual(facts['previous_day']['local_date'],'2026-10-31')
  self.assertEqual([a['run'] for a in facts['previous_day']['accepted_receipts']],['yesterday'])
  self.clock[0]+=86400;meeting=self.w.ensure_scrum(self.projects)
  saved=self.w.scrum_facts(meeting['day'],'one',facts)
  replay=self.w.scrum_facts(meeting['day'],'one',{'different':'later observation'})
  self.assertEqual(saved['id'],replay['id'])
  message=self.w.messages(meeting['room'])['messages'][-1]
  self.assertTrue(message['context']['host_observation']);self.assertEqual(message['context']['facts'],facts)

if __name__=='__main__':unittest.main()
