"""Actual tick exit -> PA.execute terminal outcome, with real memory admission."""
import contextlib,datetime as dt,os,sys,time,unittest
from pathlib import Path
from unittest import mock
SOURCE=Path(os.environ.get('MIDNIGHT_TEST_SOURCE',Path(__file__).resolve().parents[1])).resolve();sys.path.insert(0,str(SOURCE))
from tests import test_environmental_decision_recovery as F
import durability as D
import midnight as M
import project_action as PA

class MemoryActionOutcome(F.EnvironmentalDecisionRecovery):
 def setUp(self):
  super().setUp();self.implementation=False;self.earlier_other_charge=False
  self.config.update(routine_provider='local',max_attempts_per_night=None,max_cloud_calls_per_night=None,max_failures_per_night=None)
  (self.root/'orchestration').mkdir();D.save_json(self.root/'orchestration/config.json',self.config);D.save_json(self.root/'orchestration/backlog.json',[])
  D.save_json(self.state/'state.json',{'done':[],'accepted':[],'tries':{},'feedback':{},'planned':[]})
  self.project={'id':'fixture','on_schedule':True,'state_dir':str(self.state),'path':str(self.root),'controller':'orchestration/supervisor.py'}
  self.registry=D.read_json(self.root/'projects.json',{});self.registry['projects']=[self.project];D.save_json(self.root/'projects.json',self.registry)
  stack=contextlib.ExitStack();self.addCleanup(stack.close)
  stack.enter_context(mock.patch.object(PA,'ROOT',self.root));stack.enter_context(mock.patch.object(PA,'source_head',return_value='c'*40))
  stack.enter_context(mock.patch.object(M,'STATE',self.runtime));stack.enter_context(mock.patch.object(M,'registry',return_value=self.registry));stack.enter_context(mock.patch.object(M,'validate_backend'));stack.enter_context(mock.patch.object(M,'effective_limits',return_value={}))
  # Synchronous fake child calls the actual backend, so replace only its OS
  # parent-registration check; PA claims/finishes the action itself.
  stack.enter_context(mock.patch.object(PA,'register_backend'))
  stack.enter_context(mock.patch.object(self.backend,'recover'));stack.enter_context(mock.patch.object(self.backend.AS,'compact',side_effect=lambda _s,_p,state,_b:state))
  stack.enter_context(mock.patch.object(self.backend,'next_task',side_effect=self.next_task))
  stack.enter_context(mock.patch.object(self.backend,'window',side_effect=lambda now:(True,now.date().isoformat(),now+dt.timedelta(hours=2))))
  process=mock.Mock(wraps=self.backend.subprocess);process.Popen.return_value=mock.Mock(terminate=lambda:None,wait=lambda **k:0)
  stack.enter_context(mock.patch.object(self.backend,'subprocess',process))
 def next_task(self,state,config,cutoff):
  if self.earlier_other_charge:self.store.charge(os.environ['MIDNIGHT_ACTION_ID'],'other-completed-input','local_supervision')
  if self.implementation:
   self.directory.mkdir(parents=True,exist_ok=True)
   self.backend.charged_run(['fixture-worker'],self.directory,self.directory/'events.jsonl',86400,cutoff,state,
    lambda:self.backend.journal_charge('local','implementation-run'),resource_kind='local',local_model=self.model,resource_input_key=self.resource_key)
  else:self.backend.local_decision(self.prompt,self.schema,self.directory,cutoff,state,config,role='fixture')
  return None
 def attempt(self):
  saved=D.read_json(self.state/'state.json',{})
  if saved.get('cooldown_until'):
   saved['cooldown_until']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=1)).isoformat();D.save_json(self.state/'state.json',saved)
  cutoff=dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=2)
  identity=PA.launch(self.runtime,self.project,self.model,{},'fixture-session',cutoff,spawn=lambda *a,**k:None)
  owner=self
  class BackendProcess:
   def __init__(self):self.code=owner.backend.tick(once=True,managed_until=cutoff)
   def poll(self):return self.code
   def wait(self,timeout=None):return self.code
  with mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':identity}):
   result=PA.execute(self.runtime,identity,spawn=lambda *a,**k:BackendProcess())
  return self.store.get(identity),result
 def all_charges(self):
  with self.store.read() as con:return [dict(row) for row in con.execute('SELECT job,key,kind FROM wf_charges ORDER BY created')]
 def test_charged_environmental_failure_is_a_failed_action_and_retains_resource_wait(self):
  job,result=self.attempt();self.assertEqual(job['state'],'failed');self.assertEqual(job['result']['returncode'],75);self.assertEqual(result,1)
  self.assertEqual(len(self.all_charges()),1);state=D.read_json(self.state/'state.json',{});self.assertEqual(state['resource_wait']['kind'],'host_pressure');self.assertNotIn('local_decision_hold',state)
  self.assertGreater(state['resource_wait']['retry_after'],time.time());self.assertEqual(self.backend.report.call_args.args[1],'resource_wait')
 def test_charged_structural_memory_failure_is_a_failed_action(self):
  self.kind='charged_limit';job,_=self.attempt();self.assertEqual(job['state'],'failed');self.assertEqual(job['result']['returncode'],75);self.assertEqual(len(self.all_charges()),1)
 def test_charged_implementation_memory_failure_is_also_failed(self):
  self.implementation=True;job,_=self.attempt();self.assertEqual(job['state'],'failed');self.assertEqual(job['result']['returncode'],75)
  self.assertEqual([c['kind'] for c in self.all_charges()],['local']);self.assertEqual(D.read_json(self.state/'state.json',{})['resource_wait']['kind'],'host_pressure')
 def test_historical_hold_redispatch_stays_uncharged_and_is_not_a_new_failure(self):
  self.attempt();before=self.all_charges();second,result=self.attempt()
  self.assertNotEqual(second['state'],'failed');self.assertEqual(result,0);self.assertEqual(self.all_charges(),before);self.assertEqual(len(self.calls),1)
 def test_authorised_environmental_recovery_still_succeeds_and_preserves_first_charge(self):
  first,_=self.attempt();before=self.all_charges();self.clock_offset=1900;self.success=True;second,_=self.attempt()
  self.assertEqual(first['state'],'failed');self.assertEqual(second['state'],'succeeded');self.assertEqual(self.all_charges()[:1],before);self.assertEqual(len(self.all_charges()),2);self.assertEqual(len(self.calls),2)
 def test_current_charged_recovery_failure_is_failed_without_another_retry_permission(self):
  first,_=self.attempt();self.clock_offset=1900;second,_=self.attempt()
  self.assertEqual((first['state'],second['state']),('failed','failed'));self.assertEqual(len(self.all_charges()),2)
  self.assertEqual(D.read_json(self.state/'state.json',{})['resource_wait']['failures'],2)
 def test_other_input_charge_in_current_job_does_not_make_old_hold_a_current_failure(self):
  self.attempt();self.earlier_other_charge=True;second,result=self.attempt()
  self.assertNotEqual(second['state'],'failed');self.assertEqual(result,0);self.assertEqual(len(self.calls),1)
  current=[c for c in self.all_charges() if c['job']==second['id']];self.assertEqual([c['key'] for c in current],['other-completed-input'])

def load_tests(loader,tests,pattern):
 return unittest.TestSuite(MemoryActionOutcome(name) for name in sorted(MemoryActionOutcome.__dict__) if name.startswith('test_'))
if __name__=='__main__':unittest.main()
