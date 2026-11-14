"""Private fake-provider admission and real temporary GPU-lock handoff checks."""
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import action_store as A
import durability as D
import memory_admission as MA
import memory_recovery as MR
import model_bench as MB
import phase_runtime as P
import project_backend as B


class MemoryAdmissionIntegration(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.b=B.load_backend(self.root,'fixture',settings={});self.b.STATE=self.root/'project';self.b.STATE.mkdir()
        self.runtime=self.root/'runtime';self.runtime.mkdir();self.b.CLOUD_RUNTIME=self.runtime
        self.model={'model':'colibri/glm-5.3','parameters_sha256':'a'*64,'promotion_generation':'one'}
        self.job={'id':'job-fixture','project':'fixture','state':'running','lease':{'fixture':True},
                  'deadline':time.time()+300,'payload':{'worker_model':self.model}}
        self.events=[];self.stopped=False
        stack=contextlib.ExitStack();self.addCleanup(stack.close)
        stack.enter_context(mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':self.job['id']}))
        stack.enter_context(mock.patch.object(self.b.PR,'phase',return_value=contextlib.nullcontext('phase-fixture')))
        # The shared phase now owns these descriptors before backend admission.
        local=stack.enter_context((self.root/'local.lock').open('a'));heavy=stack.enter_context((self.root/'heavy.lock').open('a'))
        stack.enter_context(mock.patch.object(P,'SLOT_FD',local.fileno()))
        stack.enter_context(mock.patch.object(P,'MEMORY_FD',heavy.fileno()))
        stack.enter_context(mock.patch.dict(P.SLOT_NAMES,{local.fileno():'local-0.lock'}))
        self.actions=stack.enter_context(mock.patch.object(self.b.PR,'Actions'));self.actions.return_value.get.return_value=self.job
        self.actions.return_value.phases.return_value=[{'id':'phase-fixture','job':self.job['id'],'state':'running','deadline':self.job['deadline']}]
        stack.enter_context(mock.patch.object(self.b.PR,'alive',return_value=True))
        stack.enter_context(mock.patch.object(self.b.PR,'control_stopped',side_effect=lambda *a,**k:self.stopped))
        stack.enter_context(mock.patch.object(self.b.GP,'phase_profile',return_value={}))
        stack.enter_context(mock.patch.object(self.b.GP,'config',return_value={'version':2}))
        stack.enter_context(mock.patch.object(self.b.GP,'matches',return_value=True))
        stack.enter_context(mock.patch.object(self.b.MC,'heavy',return_value=contextlib.nullcontext(None)))
        stack.enter_context(mock.patch.object(self.b.MC,'enabled',return_value=True))
        stack.enter_context(mock.patch.object(self.b,'report'))
        stack.enter_context(mock.patch.object(self.b.time,'sleep',side_effect=lambda _:self.events.append('wait')))
        self.api=stack.enter_context(mock.patch.object(MB,'api',return_value={'models':[]}))
        self.gpu=self.root/'gpu.lock';stack.enter_context(mock.patch.object(MB,'GPU_LOCK',self.gpu))
        self.gate=stack.enter_context(mock.patch.object(MA,'QuietGate')).return_value
        self.process=stack.enter_context(mock.patch.object(self.b,'_run_process'))
        self.log=self.root/'events.jsonl'

    def run_phase(self):
        return self.b.run(['fixture-command'],self.root,self.log,60,
                          dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=5),{},
                          on_launch=lambda:self.events.append('charge'),resource_kind='local',
                          local_model=self.model,resource_input_key='same-input')

    def observe(self,ready):
        self.events.append('quiet' if ready else 'busy');return {'ready':ready}

    def test_quiet_observation_precedes_charge_and_gpu_remains_owned_at_launch(self):
        self.gate.observe.side_effect=lambda:self.observe(self.events.count('busy')>0)
        def process(*a,**kw):
            kw['on_launch']();self.events.append('child')
            with self.gpu.open('a') as other:
                with self.assertRaises(BlockingIOError):fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.log.write_text('');return {'code':0,'reason':'exited'}
        self.process.side_effect=process
        self.assertEqual(self.run_phase()['code'],0)
        self.assertEqual(self.events,['busy','wait','quiet','quiet','quiet','charge','child'])
        self.assertEqual(MA.QuietGate.call_count,3)  # Initial, parked observation, then a fresh owned interval.
        self.assertIsNone(P.GPU_FD)
        with self.gpu.open('a') as other:fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_pause_during_quiet_wait_never_charges_or_launches(self):
        def observe():self.stopped=True;return self.observe(False)
        self.gate.observe.side_effect=observe
        with self.assertRaises(P.PhaseInterrupted):self.run_phase()
        self.assertNotIn('charge',self.events);self.process.assert_not_called()

    def test_datetime_rounding_cannot_extend_or_reject_exact_owner_epoch(self):
        original=float(int(time.time())+30)+.1234567
        self.assertGreater(dt.datetime.fromtimestamp(original,dt.timezone.utc).timestamp(),original)
        self.job['deadline']=original
        self.actions.return_value.phases.return_value[0]['deadline']=original
        self.gate.observe.return_value={'ready':True}
        def process(*args,**kwargs):
            kwargs['on_launch']();self.log.write_text('')
            return {'code':0,'reason':'exited'}
        self.process.side_effect=process
        try:result=self.run_phase()
        except P.PhaseInterrupted as exc:self.fail('Datetime rounding rejected the exact owned deadline: '+str(exc))
        self.assertEqual(result['code'],0)
        self.assertEqual(self.b.PR.phase.call_args.args[6],original)
        deadlines=[call.kwargs['deadline'] for call in self.actions.return_value.phase_update.call_args_list if 'deadline' in call.kwargs]
        self.assertEqual(deadlines,[original])
        self.assertEqual(self.job['deadline'],original)
        self.assertEqual(self.events,['charge'])

    def test_quiet_proof_lost_at_charge_boundary_never_launches(self):
        self.gate.observe.side_effect=[{'ready':True},{'ready':False}]
        self.process.side_effect=lambda *a,**kw:kw['on_launch']()
        with self.assertRaises(P.PhaseInterrupted):self.run_phase()
        self.assertNotIn('charge',self.events)

    def test_bound_provider_failure_enters_hold_before_next_identical_inference(self):
        self.gate.observe.return_value={'ready':True}
        def process(*a,**kw):
            kw['on_launch']()
            folder=self.runtime/'models/colibri/sessions'/'fixture';folder.mkdir(parents=True)
            model=MR.resource_model(self.model)
            intent={'input_key':'same-input','model':model,'action_id':self.job['id'],'session':'fixture',
                    'kind':'charged_limit','profile_sha256':model['parameters_sha256'],'input_sha256':'b'*64}
            def save(name,value):
                path=folder/name;path.write_text(json.dumps(value));return hashlib.sha256(path.read_bytes()).hexdigest()
            intent_hash=save('failure-intent.json',intent)
            unload_hash=save('unloaded.json',{'owned_processes_drained':True,'model':model})
            receipt=dict(intent,failure_intent_sha256=intent_hash,drain={'owned_processes_drained':True,'unloaded_sha256':unload_hash})
            receipt_hash=save('failure.json',receipt)
            self.log.write_text(json.dumps({'type':'model.failure','receipt_path':str(folder/'failure.json'),'receipt_sha256':receipt_hash})+'\n')
            return {'code':1,'reason':'exited'}
        self.process.side_effect=process
        with self.assertRaises(MR.MemoryHeld) as failure:self.run_phase()
        self.assertEqual(failure.exception.record['kind'],'charged_limit')
        with self.assertRaises(MR.MemoryHeld):self.run_phase()
        self.assertEqual(self.process.call_count,1);self.assertEqual(self.events,['charge'])


class ResourceIdentity(unittest.TestCase):
    def test_supervision_generation_change_keeps_the_same_resource_hold(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);backend=B.load_backend(root,'fixture',settings={});backend.STATE=root/'state'
            first={'model':'colibri/glm-5.3','parameters_sha256':'a'*64,'promotion_generation':'one'}
            second=dict(first,promotion_generation='two');keys=[]
            def stopped(*a,**kw):keys.append(kw['resource_input_key']);raise backend.ManagerHeld('No inference in fixture')
            with mock.patch.object(backend.PR,'enabled',return_value=False),mock.patch.object(backend,'git',return_value='b'*40), \
                 mock.patch.object(backend,'charged_run',side_effect=stopped):
                for index,model in enumerate((first,second)):
                    with self.assertRaises(backend.ManagerHeld):
                        backend.local_decision('Exact same prompt',{'type':'object'},root/str(index),
                            dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=1),{}, {'worker_model':model})
            self.assertEqual(keys[0],keys[1])
            MR.record(root/'runtime',keys[0],first,{'kind':'charged_limit','drain':{'owned_processes_drained':True},
                'receipt_sha256':'c'*64,'receipt_path':'private/failure.json'},instant=0)
            with self.assertRaises(MR.MemoryHeld):MR.check(root/'runtime',keys[1],second,instant=86400,quiet=True)
            MR.check(root/'runtime',keys[1],dict(second,parameters_sha256='d'*64),instant=86400,quiet=True)


class FreshControlIntegration(unittest.TestCase):
    def test_cached_running_job_cannot_survive_terminalization(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);runtime=root/'runtime';runtime.mkdir();state=root/'project';state.mkdir()
            D.save_json(root/'projects.json',{'timezone':'Australia/Adelaide','schedule':{'start':'00:00','end':'09:00'},
                'projects':[{'id':'fixture','on_schedule':True,'state_dir':str(state)}]})
            D.save_json(runtime/'override.json',{'mode':'continuous','expires_at':None})
            actions=A.Actions(runtime);job=actions.submit('fixture',{},time.time()+30);actions.claim(job['id'],P.lease())
            cached=actions.get(job['id']);self.assertFalse(P.control_stopped(runtime,cached))
            actions.finish(job['id'],'interrupted',{},lease=cached['lease'])
            self.assertTrue(P.control_stopped(runtime,cached))

    def test_metadata_window_exemption_never_exempts_pause_or_activation_hold(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime=Path(temporary)/'runtime';runtime.mkdir()
            actions=A.Actions(runtime);job=actions.submit('midnight-performance-review',{'kind':'performance_review'},time.time()+30)
            actions.claim(job['id'],P.lease());cached=actions.get(job['id'])
            self.assertFalse(P.control_stopped(runtime,cached))
            for name in ('PAUSED','INSTALLING','WEEKDAY_ACTIVATION_PENDING'):
                marker=runtime/name;marker.touch();self.assertTrue(P.control_stopped(runtime,cached));marker.unlink()
            actions.finish(job['id'],'failed',{},lease=cached['lease'])
            self.assertTrue(P.control_stopped(runtime,cached))


class NativeGpuHandoff(unittest.TestCase):
    def test_guard_retains_gpu_lease_after_worker_closes_its_inherited_copy(self):
        source=Path(B.__file__).parent
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);runtime=root/'runtime';runtime.mkdir();state=root/'project';state.mkdir()
            D.save_json(root/'projects.json',{'timezone':'Australia/Adelaide','schedule':{'start':'00:00','end':'09:00'},
                'projects':[{'id':'fixture','on_schedule':True,'state_dir':str(state)}]})
            D.save_json(runtime/'override.json',{'mode':'continuous','expires_at':None})
            actions=A.Actions(runtime);job=actions.submit('fixture',{},time.time()+30);actions.claim(job['id'],P.lease())
            phase=actions.phase(job['id'],'fixture','local','native-gpu',time.time()+25,{})
            actions.phase_update(phase['id'],'running',lease=P.lease())
            folder=runtime/'phases'/phase['id'];folder.mkdir(parents=True)
            resources=runtime/'resources';resources.mkdir();gpu=root/'gpu.lock';ready=root/'ready';done=root/'done'
            worker="import sys,os,time,json;sys.path.insert(0,sys.argv[1]);from pathlib import Path;import phase_runtime as P\nfd=int(os.environ['MIDNIGHT_GPU_LEASE_FD'])\nwith P.gpu_lock(Path(sys.argv[2])):\n try:os.fstat(fd);closed=False\n except OSError:closed=True\n Path(sys.argv[3]).write_text(json.dumps({'closed':closed,'token_removed':'MIDNIGHT_GPU_LEASE_FD' not in os.environ}))\n while not Path(sys.argv[4]).exists():time.sleep(.02)\n"
            # Exercise the trusted shell hop used by the coding launcher too.
            command=['/bin/sh','-c','exec "$@"','fixture-shell',sys.executable,'-B','-c',worker,str(source),str(gpu),str(ready),str(done)]
            launch={'command':command,'cwd':str(root),'slot_name':'local-0.lock','gpu_lease':True,'fresh_controls':True}
            D.atomic_write(folder/'launch.json',json.dumps(launch).encode())
            local=(resources/'local-0.lock').open('a');lease=gpu.open('a')
            fcntl.flock(local,fcntl.LOCK_EX);fcntl.flock(lease,fcntl.LOCK_EX)
            wrapper="import sys;sys.path.insert(0,sys.argv.pop(1));from pathlib import Path;import model_bench as B;B.GPU_LOCK=Path(sys.argv.pop(1));import phase_guard as G;sys.exit(G.main())"
            env=dict(os.environ,MIDNIGHT_ACTION_ID=job['id'],MIDNIGHT_SHARED_ROOT=str(root))
            proc=subprocess.Popen([sys.executable,'-B','-c',wrapper,str(source),str(gpu),'--runtime',str(runtime),
                '--phase',phase['id'],'--launch',str(folder/'launch.json'),'--slot',str(local.fileno()),'--gpu-slot',str(lease.fileno())],
                pass_fds=(local.fileno(),lease.fileno()),env=env,start_new_session=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            local.close();lease.close()
            try:
                limit=time.monotonic()+10
                while not ready.exists() and proc.poll() is None and time.monotonic()<limit:time.sleep(.03)
                self.assertTrue(ready.exists(),proc.communicate(timeout=1) if proc.poll() is not None else 'worker did not enter')
                self.assertEqual(json.loads(ready.read_text()),{'closed':True,'token_removed':True})
                with gpu.open('a') as competing:
                    with self.assertRaises(BlockingIOError):fcntl.flock(competing,fcntl.LOCK_EX|fcntl.LOCK_NB)
                done.touch();out,error=proc.communicate(timeout=10)
                self.assertEqual(proc.returncode,0,error)
                with gpu.open('a') as competing:fcntl.flock(competing,fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally:
                done.touch()
                if proc.poll() is None:
                    proc.terminate()
                    try:proc.communicate(timeout=5)
                    except subprocess.TimeoutExpired:proc.kill();proc.communicate(timeout=5)


if __name__=='__main__':unittest.main()
