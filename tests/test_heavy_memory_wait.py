"""Real phase/flock admission; only memory samples and elapsed waiting are synthetic."""
import contextlib,datetime as dt,fcntl,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest import mock
SOURCE=Path(os.environ.get('MIDNIGHT_TEST_SOURCE',Path(__file__).resolve().parents[1])).resolve();sys.path.insert(0,str(SOURCE))
import action_store as A
import colibri_provider as GP
import durability as D
import memory_admission as MA
import memory_coordination as MC
import memory_runtime as MR
import model_bench as MB
import phase_runtime as P

class HeavyMemoryWait(unittest.TestCase):
 def setUp(self):
  temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name).resolve();self.runtime=self.root/'runtime';self.state=self.root/'project';self.state.mkdir();self.store=A.Actions(self.runtime)
  D.save_json(self.root/'projects.json',{'timezone':'Australia/Adelaide','schedule':{'start':'00:00','end':'09:00'},'projects':[{'id':'fixture','on_schedule':True,'state_dir':str(self.state)}]})
  D.save_json(self.runtime/'override.json',{'mode':'continuous','expires_at':None});D.save_json(self.runtime/'memory-coordination.json',{'version':1,'enabled':True})
  self.model={'model':'colibri/glm-5.3','parameters_sha256':'a'*64};self.deadline=time.time()+3600;self.job=self.store.submit('fixture',{'worker_model':self.model,'session':'fixture-session'},self.deadline);self.assertTrue(self.store.claim(self.job['id'],P.lease()))
  self.heavy=self.runtime/'resources/heavy-memory.lock';self.gpu=self.root/'gpu.lock';self.healthy=False;self.stopped=False;self.expired=False;self.gates=0;self.charged=[];self.waits=[];self.host_ran=False;self.on_wait=None;self.quiet_owned_samples=0;self.healthy_from_start=False
  self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close);self.stack.enter_context(mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':self.job['id']}))
  self.stack.enter_context(mock.patch.object(GP,'config',return_value={'version':2}));self.stack.enter_context(mock.patch.object(GP,'phase_profile',return_value={'supervisor_timeout_seconds':86400}))
  self.stack.enter_context(mock.patch.object(MB,'GPU_LOCK',self.gpu));self.stack.enter_context(mock.patch.object(MB,'api',return_value={'models':[]}))
  resource=P.resource;self.stack.enter_context(mock.patch.object(P,'resource',side_effect=lambda *a,**k:resource(*a,pressure=lambda:False,**k)))
  self.real_time=time.time;self.stack.enter_context(mock.patch.object(MR.time,'time',side_effect=lambda:self.deadline+1 if self.expired else self.real_time()))
  self.stack.enter_context(mock.patch.object(MR.time,'sleep',return_value=None));gate=MA.QuietGate
  def factory():
   self.gates+=1;generation=self.gates;clock=[0.]
   def probe():
    clock[0]+=5
    if (generation>=3 or self.healthy_from_start) and self.healthy:
     self.assertFalse(self.lock_available(self.heavy),'HOST overlapped the restarted quiet interval');self.quiet_owned_samples+=1
    return {'at':self.real_time()+clock[0],'boot_id':D.boot_id(),'pressure':'normal','total_bytes':40_000_000_000,'free_bytes':25_000_000_000 if self.healthy else 14_000_000_000,'inactive_bytes':0,'swap_out_counter':0}
   return gate(probe=probe,clock=lambda:clock[0])
  self.stack.enter_context(mock.patch.object(MA,'QuietGate',side_effect=factory))
 @staticmethod
 def lock_available(path):
  with path.open('a') as handle:
   try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:return False
   else:fcntl.flock(handle,fcntl.LOCK_UN);return True
 def charges(self):
  with self.store.read() as con:return con.execute('SELECT COUNT(*) FROM wf_charges').fetchone()[0]
 def host_phase(self):
  self.assertTrue(self.lock_available(self.heavy),'Unsuitable GLM wait retained the heavy-memory lane')
  self.assertEqual(self.charges(),0)
  host=self.store.submit('host-fixture',{'session':'host-session'},self.deadline);self.store.claim(host['id'],P.lease())
  with mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':host['id']}):
   with P.phase(self.runtime,'host-fixture','host',['fixture-check'],self.root,self.root/'host.log',self.deadline) as phase:
    self.assertFalse(self.lock_available(self.heavy));self.host_ran=True
    self.store.phase_update(phase,'succeeded',result={'code':0,'reason':'exited'})
  self.store.finish(host['id'],'succeeded',{},lease=self.store.get(host['id'])['lease'])
 def waiting(self,reason):
  self.waits.append(reason)
  if self.on_wait:self.on_wait(reason)
 def run_admission(self):
  with P.phase(self.runtime,'fixture','local',['fixture-local'],self.root,self.root/'local.log',self.deadline,stopped=lambda:self.stopped,waiting=self.waiting) as phase:
   with MR.admission(self.runtime,phase,self.model,'same-request',self.deadline,stopped=lambda:self.stopped,waiting=self.waiting) as admitted:
    self.assertEqual(self.store.get(self.job['id'])['deadline'],self.deadline)
    self.assertEqual(len([p for p in self.store.phases() if p['project']=='fixture']),1)
    self.assertFalse(self.lock_available(self.heavy));self.assertFalse(self.lock_available(self.gpu))
    def charge():self.store.charge(self.job['id'],'same-request','local_supervision');self.charged.append(phase)
    admitted.before_launch(charge)
    proof=D.read_json(self.runtime/'phases'/phase/'memory-admission-proof.json',{})
    self.assertGreaterEqual(proof['quiet']['quiet_seconds'],120);self.assertGreaterEqual(proof['quiet']['sample_count'],25)
    self.store.phase_update(phase,'succeeded',result={'code':0,'reason':'exited'})
  return phase
 def test_low_headroom_releases_host_lane_then_restarts_full_quiet_without_requeue(self):
  def waiting(reason):
   if reason=='quiet_host_memory' and not self.host_ran:
    self.assertFalse(self.lock_available(self.gpu),'Existing GPU owner changed while parked')
    self.host_phase();self.healthy=True
  self.on_wait=waiting;self.run_admission()
  self.assertTrue(self.host_ran);self.assertGreaterEqual(self.gates,3);self.assertGreaterEqual(self.quiet_owned_samples,25);self.assertEqual(self.charges(),1);self.assertEqual(len(self.charged),1)
  self.assertTrue(self.lock_available(self.heavy));self.assertTrue(self.lock_available(self.gpu))
 def test_gpu_wait_also_releases_heavy_before_the_same_owner_continues(self):
  competing=self.stack.enter_context(self.gpu.open('a'));fcntl.flock(competing,fcntl.LOCK_EX)
  def waiting(reason):
   if reason=='resource_local' and not self.host_ran:
    self.host_phase();fcntl.flock(competing,fcntl.LOCK_UN);self.healthy=True
  self.on_wait=waiting;self.run_admission();self.assertTrue(self.host_ran);self.assertEqual(self.charges(),1)
 def test_pause_while_parked_releases_all_locks_without_charge(self):
  def waiting(reason):
   if reason=='quiet_host_memory':self.assertTrue(self.lock_available(self.heavy));self.stopped=True
  self.on_wait=waiting
  with self.assertRaises(P.PhaseInterrupted):self.run_admission()
  self.assertEqual(self.charges(),0);self.assertTrue(self.lock_available(self.heavy));self.assertTrue(self.lock_available(self.gpu))
 def test_original_cutoff_while_parked_does_not_charge_or_extend_job(self):
  def waiting(reason):
   if reason=='quiet_host_memory':self.assertTrue(self.lock_available(self.heavy));self.expired=True
  self.on_wait=waiting
  with self.assertRaises(P.PhaseInterrupted):self.run_admission()
  self.assertEqual(self.charges(),0);self.assertEqual(self.store.get(self.job['id'])['deadline'],self.deadline);self.assertTrue(self.lock_available(self.heavy));self.assertTrue(self.lock_available(self.gpu))
 def test_unknown_native_cleanup_blocks_reacquisition_before_charge(self):
  def waiting(reason):
   if reason=='quiet_host_memory' and not self.healthy:
    self.assertTrue(self.lock_available(self.heavy));folder=self.runtime/'models/colibri/sessions/unknown';folder.mkdir(parents=True);D.atomic_write(folder/'native-launch-intent.json',b'{}');self.healthy=True
   elif reason=='resource_heavy_memory':self.assertTrue(self.lock_available(self.heavy));self.stopped=True
  self.on_wait=waiting
  with self.assertRaises(P.PhaseInterrupted):self.run_admission()
  self.assertEqual(self.charges(),0);self.assertIn('resource_heavy_memory',self.waits)
 def test_foreign_execution_blocks_reacquisition_even_when_flock_is_free(self):
  def waiting(reason):
   if reason=='quiet_host_memory' and not self.healthy:
    self.assertTrue(self.lock_available(self.heavy));host=self.store.submit('host-fixture',{},self.deadline);self.store.claim(host['id'],P.lease());phase=self.store.phase(host['id'],'host-fixture','host','foreign-host',self.deadline,{})
    self.store.phase_update(phase['id'],'running',lease=P.lease());self.store.execution(phase['id'],P.lease());self.healthy=True
   elif reason=='resource_heavy_memory':self.assertTrue(self.lock_available(self.heavy));self.stopped=True
  self.on_wait=waiting
  with self.assertRaises(P.PhaseInterrupted):self.run_admission()
  self.assertEqual(self.charges(),0);self.assertIn('resource_heavy_memory',self.waits)

 def test_healthy_initial_interval_keeps_host_excluded_until_charge(self):
  self.healthy=True;self.healthy_from_start=True;self.run_admission()
  self.assertEqual(self.gates,1);self.assertGreaterEqual(self.quiet_owned_samples,25);self.assertEqual(self.charges(),1);self.assertFalse(self.host_ran)

if __name__=='__main__':unittest.main()
