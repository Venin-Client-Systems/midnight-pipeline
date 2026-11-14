"""Real decision/action/phase/recovery ledger; synthetic host samples and child output."""
import contextlib,datetime as dt,hashlib,json,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest import mock
SOURCE=Path(os.environ.get('MIDNIGHT_TEST_SOURCE',Path(__file__).resolve().parents[1])).resolve();sys.path.insert(0,str(SOURCE))
import action_store as A
import durability as D
import memory_admission as MA
import memory_recovery as MEM
import memory_runtime as MRT
import model_bench as MB
import phase_runtime as P
import project_backend as B

class EnvironmentalDecisionRecovery(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve();self.runtime=self.root/'runtime';self.state=self.root/'state';self.state.mkdir()
  self.backend=B.load_backend(self.root,'fixture',settings={});self.backend.STATE=self.state;self.backend.CLOUD_RUNTIME=self.runtime
  self.model={'model':'colibri/glm-5.3','digest':'a'*64,'parameters_sha256':'b'*64,'context':32768,'bytes':1,'promotion_generation':None}
  self.config={'worker_model':self.model,'local_supervision_timeout_seconds':86400};self.directory=self.state/'decision';self.prompt='Evaluate this identical retained input.';self.schema={'type':'object','properties':{'answer':{'type':'boolean'}},'required':['answer'],'additionalProperties':False}
  self.key=A.fingerprint({'prompt':self.prompt,'schema':self.schema,'model':self.model,'head':'c'*40});self.resource_key=A.fingerprint({'prompt':self.prompt,'schema':self.schema,'model':MEM.resource_model(self.model),'head':'c'*40})
  D.save_json(self.root/'projects.json',{'timezone':'Australia/Adelaide','schedule':{'start':'00:00','end':'09:00'},'projects':[{'id':'fixture','on_schedule':True,'state_dir':str(self.state)}]})
  D.save_json(self.runtime/'override.json',{'mode':'continuous','expires_at':None});D.save_json(self.runtime/'memory-coordination.json',{'version':1,'enabled':True})
  self.store=A.Actions(self.runtime);self.calls=[];self.quiet_proofs=[];self.kind='host_pressure';self.drained=True;self.clock_offset=0;self.success=False;self.quiet_ok=True;self.stopped=False
  stack=contextlib.ExitStack();self.addCleanup(stack.close)
  stack.enter_context(mock.patch.object(self.backend,'git',return_value='c'*40));stack.enter_context(mock.patch.object(self.backend,'report'))
  stack.enter_context(mock.patch.object(self.backend.GP,'config',return_value={'version':2}));stack.enter_context(mock.patch.object(self.backend.GP,'phase_profile',return_value={'supervisor_timeout_seconds':86400}))
  stack.enter_context(mock.patch.object(MB,'GPU_LOCK',self.root/'gpu.lock'));stack.enter_context(mock.patch.object(MB,'api',return_value={'models':[]}))
  # Native pressure observation only is synthetic; phase, locks, action ownership and quiet-window algorithm are real.
  stack.enter_context(mock.patch.object(P.subprocess,'check_output',wraps=P.subprocess.check_output))
  self.backend.CONTROL_STOP=lambda:self.stopped
  original_resource=P.resource
  def resource(*args,**kwargs):return original_resource(*args,pressure=lambda:False,**kwargs)
  stack.enter_context(mock.patch.object(P,'resource',side_effect=resource))
  original_gate=MA.QuietGate
  def gate():
   clock=[0.0]
   def probe():
    clock[0]+=5
    if not self.quiet_ok:self.stopped=True
    return {'at':time.time()+clock[0],'boot_id':D.boot_id(),'pressure':'normal' if self.quiet_ok else 'critical','total_bytes':40_000_000_000,'free_bytes':25_000_000_000,'inactive_bytes':1_000_000_000,'swap_out_counter':0}
   return original_gate(probe=probe,clock=lambda:clock[0])
  stack.enter_context(mock.patch.object(MA,'QuietGate',side_effect=gate));stack.enter_context(mock.patch.object(MRT.time,'sleep',return_value=None))
  original_check,original_reserve=MEM.check,MEM.reserve_recovery
  stack.enter_context(mock.patch.object(MEM,'check',side_effect=lambda *a,**k:original_check(*a,**dict(k,instant=time.time()+self.clock_offset))))
  stack.enter_context(mock.patch.object(MEM,'reserve_recovery',side_effect=lambda *a,**k:original_reserve(*a,**dict(k,instant=time.time()+self.clock_offset))))
  stack.enter_context(mock.patch.object(self.backend,'_run_process',side_effect=self.child))
 def failure(self,job):
  folder=self.runtime/'models/colibri/sessions'/('session-'+str(len(self.calls)));folder.mkdir(parents=True)
  def save(name,value):
   path=folder/name;D.atomic_write(path,json.dumps(value,sort_keys=True).encode());return hashlib.sha256(path.read_bytes()).hexdigest()
  value={'version':1,'input_key':self.resource_key,'input_sha256':'d'*64,'model':MEM.resource_model(self.model),'action_id':job,'session':folder.name,'kind':self.kind,'profile_sha256':self.model['parameters_sha256'],'observed_at':time.time()}
  intent=save('failure-intent.json',value)
  phase=self.store.phases()[0]
  proof=D.read_json(self.runtime/'phases'/phase['id']/'memory-admission-proof.json',{})
  admission_sha=save('admission.json',proof)
  native={'version':1,'session':folder.name,'action_id':job,'phase':phase['id'],'input_key':self.resource_key,
          'input_sha256':value['input_sha256'],'model':MEM.resource_model(self.model),'profile_sha256':self.model['parameters_sha256'],
          'boot_id':D.boot_id(),'admission_sha256':admission_sha,'created_at':time.time()}
  launch_sha=save('native-launch-intent.json',native)
  drain={'owned_processes_drained':False}
  if self.drained:drain={'owned_processes_drained':True,'unloaded_sha256':save('unloaded.json',{'model':MEM.resource_model(self.model),'owned_processes_drained':True,'session':folder.name,'action_id':job,'phase':phase['id'],'native_launch':'created','native_launch_intent_sha256':launch_sha})}
  receipt=save('failure.json',dict(value,failure_intent_sha256=intent,drain=drain))
  D.atomic_write(self.directory/'events.jsonl',(json.dumps({'type':'model.failure','receipt_path':str(folder/'failure.json'),'receipt_sha256':receipt})+'\n').encode())
 def child(self,*args,**kwargs):
  kwargs['on_launch']();job=os.environ['MIDNIGHT_ACTION_ID'];self.calls.append(job)
  phase=self.store.phases()[0];proof=D.read_json(self.runtime/'phases'/phase['id']/'memory-admission-proof.json',{});self.quiet_proofs.append(proof)
  self.assertTrue(proof['quiet']['ready']);self.assertGreaterEqual(proof['quiet']['quiet_seconds'],120);self.assertEqual(set(proof['locks']),{'gpu','heavy','local'})
  if self.success:
   D.save_json(self.directory/'result.json',{'answer':True});D.save_json(self.directory/'local-metrics.json',{'model':self.model['model']});D.atomic_write(self.directory/'events.jsonl',b'{"type":"turn.completed"}\n')
   return {'code':0,'reason':'exited'}
  if self.kind:self.failure(job)
  else:D.atomic_write(self.directory/'events.jsonl',b'{"type":"turn.started"}\n')
  return {'code':1,'reason':'exited'}
 def attempt(self,*,historical_managed_call=False):
  deadline=time.time()+7200;job=self.store.submit('fixture',{'worker_model':self.model,'source_head':'c'*40,'session':'fixture-session'},deadline);self.assertTrue(self.store.claim(job['id'],P.lease()))
  error=None;value=None
  with mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':job['id']}):
   try:
    cutoff=dt.datetime.fromtimestamp(deadline,dt.timezone.utc)
    if historical_managed_call:
     # Establish a prior-release charged history through the actual managed
     # run/admission path; no local-decision replay policy is mocked or changed.
     value=self.backend.charged_run(['historical-fixture'],self.directory,self.directory/'events.jsonl',86400,cutoff,{},
       lambda:self.backend.journal_charge('local_supervision',self.key),resource_kind='local',local_model=self.model,resource_input_key=self.resource_key)
    else:value=self.backend.local_decision(self.prompt,self.schema,self.directory,cutoff,{},self.config,role='fixture')
   except (MEM.MemoryHeld,self.backend.LocalDecisionHeld,P.PhaseInterrupted) as exc:error=exc
  self.store.finish(job['id'],'failed' if error else 'succeeded',{'reason':type(error).__name__ if error else 'complete'},lease=self.store.get(job['id'])['lease'])
  return error,value
 def charges(self):
  with self.store.read() as c:return [dict(row) for row in c.execute("SELECT job,key,kind FROM wf_charges WHERE kind='local_supervision' ORDER BY created")]
 def test_verified_environmental_failure_recovers_after_backoff_and_fresh_quiet(self):
  error,_=self.attempt();self.assertIsInstance(error,MEM.MemoryHeld);first=self.charges();self.assertEqual(len(first),1);self.assertFalse((self.directory/'process.json').exists())
  self.clock_offset=1900;self.success=True;error,value=self.attempt()
  self.assertIsNone(error,'Existing verified environmental recovery was blocked: '+str(error));self.assertEqual(value,{'answer':True});self.assertEqual(len(self.calls),2);self.assertEqual(self.charges()[:1],first);self.assertEqual(len(self.charges()),2);self.assertEqual(len(self.quiet_proofs),2)
 def test_native_startup_memory_uses_same_authorised_recovery(self):
  self.kind='native_startup_memory';self.test_verified_environmental_failure_recovers_after_backoff_and_fresh_quiet()
 def test_environmental_backoff_still_prevents_second_charge(self):
  self.attempt();self.success=True;error,_=self.attempt();self.assertIsNotNone(error);self.assertEqual(len(self.calls),1);self.assertEqual(len(self.charges()),1)
 def test_structural_memory_failure_never_receives_identical_retry(self):
  self.kind='charged_limit';self.attempt();self.clock_offset=19000;self.success=True;error,_=self.attempt();self.assertIsNotNone(error);self.assertEqual(len(self.calls),1);self.assertEqual(len(self.charges()),1)
 def test_unconfirmed_cleanup_never_receives_identical_retry(self):
  self.drained=False;self.attempt();self.clock_offset=19000;self.success=True;error,_=self.attempt();self.assertIsNotNone(error);self.assertEqual(len(self.calls),1);self.assertEqual(len(self.charges()),1)
 def test_unknown_spent_failure_has_no_memory_recovery_permission(self):
  self.kind=None;self.attempt();self.clock_offset=19000;self.success=True;error,_=self.attempt();self.assertIsNotNone(error);self.assertEqual(len(self.calls),1);self.assertEqual(len(self.charges()),1)

 def test_mutable_saved_memory_hold_cannot_authorise_an_unknown_charge(self):
  self.kind=None;self.attempt()
  MEM.record(self.runtime,self.resource_key,self.model,{'kind':'host_pressure','drain':{'owned_processes_drained':True},'receipt_sha256':'e'*64,'receipt_path':'unverified/failure.json'},instant=0)
  self.clock_offset=19000;self.success=True;error,_=self.attempt();self.assertIsNotNone(error);self.assertEqual(len(self.calls),1);self.assertEqual(len(self.charges()),1)
 def test_tampered_cleanup_cannot_borrow_the_previous_verified_hold(self):
  self.attempt();folder=next((self.runtime/'models/colibri/sessions').iterdir());D.atomic_write(folder/'unloaded.json',b'{}')
  self.clock_offset=19000;self.success=True;error,_=self.attempt();self.assertIsNotNone(error);self.assertEqual(len(self.calls),1);self.assertEqual(len(self.charges()),1)
 def test_failed_reserved_retry_cannot_borrow_the_first_failure_receipt(self):
  self.attempt();self.clock_offset=1900;self.kind=None;self.attempt()
  self.assertEqual(len(self.calls),2);self.assertEqual(len(self.charges()),2)
  self.clock_offset=19000;self.success=True;error,_=self.attempt();self.assertIsNotNone(error);self.assertEqual(len(self.calls),2);self.assertEqual(len(self.charges()),2)
 def test_busy_quiet_gate_stays_uncharged_then_rechecks_a_new_full_interval(self):
  self.attempt();self.clock_offset=1900;self.success=True;self.quiet_ok=False;error,_=self.attempt()
  self.assertIsInstance(error,P.PhaseInterrupted);self.assertEqual(len(self.calls),1);self.assertEqual(len(self.charges()),1)
  self.stopped=False;self.quiet_ok=True;error,value=self.attempt();self.assertIsNone(error);self.assertEqual(value,{'answer':True});self.assertEqual(len(self.calls),2);self.assertEqual(len(self.charges()),2)

 def test_older_unknown_charge_cannot_borrow_a_newer_environmental_receipt(self):
  self.kind=None;self.attempt();self.kind='host_pressure';error,_=self.attempt(historical_managed_call=True)
  self.assertIsInstance(error,MEM.MemoryHeld);self.assertEqual(len(self.charges()),2)
  self.clock_offset=1900;self.success=True;error,_=self.attempt()
  self.assertIsNotNone(error);self.assertEqual(len(self.calls),2);self.assertEqual(len(self.charges()),2)

if __name__=='__main__':unittest.main()
