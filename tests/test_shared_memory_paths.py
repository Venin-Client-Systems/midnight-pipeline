"""Reachable maintenance/discussion admission contracts; no inference or accounts."""
import contextlib
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import action_store as A
import agent_discussion as AD
import agent_workspace as AW
import colibri_provider as GP
import core_repair as CR
import durability as D
import memory_admission as MA
import memory_coordination as MC
import memory_runtime as MR
import model_bench as MB
import phase_runtime as P
import project_action as PA
import project_backend as B
import maintenance_recovery as M


class SharedPaths(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.runtime=self.root/'runtime';self.runtime.mkdir()
        self.state=self.root/'project';self.state.mkdir();self.store=A.Actions(self.runtime)
        self.model={'model':'colibri/glm-5.3','parameters_sha256':'a'*64}
        self.registry={'timezone':'Australia/Adelaide','schedule':{'start':'00:00','end':'09:00'},
                       'projects':[{'id':'one','name':'One','on_schedule':True,'state_dir':str(self.state)}]}
        D.save_json(self.root/'projects.json',self.registry)
        D.save_json(self.runtime/'override.json',{'mode':'continuous','expires_at':None})
        D.save_json(self.runtime/'memory-coordination.json',{'version':1,'enabled':True})
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(PA,'source_head',return_value='a'*40))

    def job(self,project='midnight',payload=None):
        job=self.store.submit(project,payload or {'maintenance':'fixture','source_head':'a'*40,'worker_model':self.model},time.time()+300)
        self.store.claim(job['id'],P.lease());job=self.store.get(job['id'])
        self.stack.enter_context(mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':job['id']}))
        return job

    def core_fakes(self):
        self.stack.enter_context(mock.patch.object(CR,'permitted',return_value=True))
        self.stack.enter_context(mock.patch.object(CR,'maintenance_current',return_value=True))
        self.stack.enter_context(mock.patch.object(P,'control_stopped',return_value=False))
        self.stack.enter_context(mock.patch.object(P.subprocess,'check_output',return_value='1'))
        self.stack.enter_context(mock.patch.object(GP,'phase_profile',return_value={'supervisor_timeout_seconds':32400}))
        self.stack.enter_context(mock.patch.object(GP,'config',return_value={'version':2}))
        self.stack.enter_context(mock.patch.object(MB,'GPU_LOCK',self.root/'gpu.lock'))
        self.stack.enter_context(mock.patch.object(MB,'api',return_value={'models':[]}))
        self.gate=self.stack.enter_context(mock.patch.object(MA,'QuietGate')).return_value
        self.gate.observe.return_value={'ready':True}

    def test_core_local_command_owns_all_leases_and_proof_before_charge_and_child(self):
        job=self.job();self.core_fakes();events=[]
        def charge():
            events.append('charge')
            phase=self.store.phases()[0]
            proof=D.read_json(self.runtime/'phases'/phase['id']/'memory-admission-proof.json',{})
            self.assertEqual(proof['input_key'],'retained-maintenance-input')
            self.assertEqual(set(proof['locks']),{'gpu','heavy','local'})
            for path in (self.root/'gpu.lock',self.runtime/'resources/heavy-memory.lock',self.runtime/'resources/local-0.lock'):
                with path.open('a') as other:
                    with self.assertRaises(BlockingIOError):fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
        def child(command,**kwargs):
            events.append('child');self.assertEqual(len(kwargs['pass_fds']),3)
            self.assertIn('--memory-slot',command);self.assertIn('--gpu-slot',command)
            self.assertEqual(kwargs['env']['MIDNIGHT_RESOURCE_INPUT_KEY'],'retained-maintenance-input')
            launch=D.read_json(Path(command[command.index('--launch')+1]),{})
            self.assertTrue(launch['memory_admission_sha256'])
            return mock.Mock(pid=999999,poll=lambda:0,wait=lambda **k:0)
        with mock.patch.object(CR.subprocess,'Popen',side_effect=child):
            result=CR.command(self.root,job,'local',['fixture-local'],self.root,self.root/'events.jsonl',60,job['deadline'],
                              local_model=self.model,resource_input_key='retained-maintenance-input',on_launch=charge)
        self.assertEqual(result['code'],0);self.assertEqual(events,['charge','child'])
        self.assertIsNone(P.MEMORY_FD);self.assertIsNone(P.GPU_FD)

    def test_core_host_command_shares_heavy_lease_without_gpu_or_local_model(self):
        job=self.job();self.core_fakes()
        def child(command,**kwargs):
            self.assertIn('--memory-slot',command);self.assertNotIn('--gpu-slot',command)
            with (self.runtime/'resources/heavy-memory.lock').open('a') as other:
                with self.assertRaises(BlockingIOError):fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return mock.Mock(pid=999999,poll=lambda:0,wait=lambda **k:0)
        with mock.patch.object(CR.subprocess,'Popen',side_effect=child):
            self.assertEqual(CR.command(self.root,job,'host',['fixture-check'],self.root,self.root/'checks.log',60,job['deadline'])['code'],0)

    def test_unmanaged_version_two_rejected_before_either_direct_or_charged_callback(self):
        backend=B.load_backend(self.root,'one',settings={});charge=mock.Mock()
        with mock.patch.object(P,'enabled',return_value=False),mock.patch.object(MR,'version_two',return_value=True),mock.patch.object(backend,'_run_process') as process:
            for fn in (lambda:backend.run(['fixture'],self.root,self.root/'x',60,dt.datetime.now(dt.timezone.utc),{},local_model=self.model,on_launch=charge),
                       lambda:backend.charged_run(['fixture'],self.root,self.root/'x',60,dt.datetime.now(dt.timezone.utc),{},charge,local_model=self.model)):
                with self.assertRaises(P.PhaseInterrupted):fn()
        charge.assert_not_called();process.assert_not_called()

    def test_qualification_v2_fails_before_command_or_any_charge(self):
        job=self.job();source=Path(CR.__file__).parent
        with mock.patch.object(MR,'version_two',return_value=True),mock.patch.object(CR,'command') as command:
            with self.assertRaisesRegex(P.PhaseInterrupted,'qualification broker'):
                CR.qualify_worker(self.root,job,source,self.root,job['deadline'])
        command.assert_not_called()
        with self.store.read() as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM wf_charges').fetchone()[0],0)

    def discussion(self):
        workspace=AW.Workspace(self.runtime/'agent-workspace');workspace.bootstrap(self.registry['projects'])
        workspace.operator_post('codex','project-one','Please preserve this question',kind='question',mentions=['worker:one'])
        work=workspace.pending_work()[0]
        job=self.job(payload={'kind':'agent_discussion','work':work['id'],'actor':work['actor'],
            'discussion_kind':work['kind'],'source_head':'a'*40,'worker_model':self.model,'session':'fixture'})
        workspace.claim_work(work['id'],job['lease'])
        return workspace,work,job

    def test_exact_discussion_is_admitted_but_untyped_or_changed_recipient_is_not(self):
        workspace,work,job=self.discussion();self.assertFalse(P.control_stopped(self.runtime,job))
        with self.store.write() as con:
            changed=dict(job['payload'],actor='worker:other');con.execute('UPDATE wf_jobs SET payload=? WHERE id=?',(json.dumps(changed),job['id']))
        self.assertTrue(P.control_stopped(self.runtime,job))
        with self.store.write() as con:con.execute('UPDATE wf_jobs SET payload=? WHERE id=?',(json.dumps({'worker_model':self.model,'source_head':'a'*40}),job['id']))
        self.assertTrue(P.control_stopped(self.runtime,job))

    def test_discussion_keeps_global_project_and_commission_controls(self):
        workspace,work,job=self.discussion()
        for marker in (self.runtime/'PAUSED',self.runtime/'INSTALLING',self.runtime/'WEEKDAY_ACTIVATION_PENDING',self.state/'PAUSED'):
            marker.touch();self.assertTrue(P.control_stopped(self.runtime,job));marker.unlink()
        import commissioning
        with mock.patch.object(commissioning,'snapshot',return_value={'blocks_dispatch':True}):
            self.assertTrue(P.control_stopped(self.runtime,job))

    def test_unspent_question_is_preserved_but_a_charged_question_is_not_requeued(self):
        workspace,work,job=self.discussion()
        self.assertTrue(AD.release_unspent(workspace,work,job));self.assertEqual(workspace.pending_work()[0]['id'],work['id'])
        workspace.claim_work(work['id'],job['lease']);self.store.charge(job['id'],'original-call','local_supervision')
        self.assertFalse(AD.release_unspent(workspace,work,job))
        with workspace.store.reader() as con:self.assertEqual(con.execute('SELECT state FROM aw_work WHERE id=?',(work['id'],)).fetchone()[0],'claimed')

    def test_retained_maintenance_model_is_pinned_once_with_exact_session_owner(self):
        identity='continuation-fixture';job=self.job(payload={'maintenance':identity,'continuation':identity,'source_head':'a'*40,
            'incident_binding':{'code':'fixture'}})
        saved={'capsule':{'base':'a'*40,'fault':{'code':'fixture'}},'local_model':self.model}
        with self.store.write() as con:
            for sql in M.SCHEMA:con.execute(sql)
            con.execute('INSERT INTO mr_sessions VALUES(?,?,?,?,?,?,?,?)',(identity,'incident','running',time.time(),time.time(),json.dumps(job['lease']),json.dumps(saved),None))
        with mock.patch.object(CR,'maintenance_current',return_value=True):
            self.assertFalse(P.control_stopped(self.runtime,job))
            bound=MR.pin_maintenance_model(self.runtime,job['id'],self.model)
            self.assertEqual(bound['payload']['worker_model'],self.model)
            with self.assertRaises(P.PhaseInterrupted):MR.pin_maintenance_model(self.runtime,job['id'],dict(self.model,parameters_sha256='b'*64))
            self.assertEqual(self.store.get(job['id'])['payload']['worker_model'],self.model)
            with self.store.write() as con:con.execute("UPDATE mr_sessions SET state='paused' WHERE id=?",(identity,))
            self.assertTrue(P.control_stopped(self.runtime,job))

    def test_discussion_execute_preserves_precharge_memory_hold(self):
        workspace,work,job=self.discussion()
        self.assertTrue(AD.release_unspent(workspace,work,job))
        with self.store.write() as con:con.execute("UPDATE wf_jobs SET state='queued',lease=NULL WHERE id=?",(job['id'],))
        import midnight
        import memory_recovery as MEM
        backend=mock.Mock();backend.local_decision.side_effect=MEM.MemoryHeld({'kind':'quiet_wait','reason':'quiet_wait'})
        with mock.patch.object(AD,'ROOT',self.root),mock.patch.object(midnight,'registry',return_value=self.registry), \
             mock.patch.object(AD,'project_facts',return_value={}),mock.patch.object(B,'load_backend',return_value=backend), \
             mock.patch.object(GP,'phase_profile',return_value={'supervisor_timeout_seconds':32400}), \
             self.assertRaises(MEM.MemoryHeld):
            AD.execute(self.runtime,job['id'])
        self.assertEqual(workspace.pending_work()[0]['id'],work['id'])
        self.assertEqual(self.store.get(job['id'])['state'],'interrupted')
        self.assertTrue(self.store.get(job['id'])['result']['work_preserved'])

    def test_current_phase_expiring_during_final_quiet_observation_never_charges(self):
        job=self.job();self.core_fakes();count=[0]
        def observe():
            count[0]+=1
            if count[0]==2:
                phase=self.store.phases()[0]
                self.store.phase_update(phase['id'],'running',deadline=time.time()-1)
            return {'ready':True}
        self.gate.observe.side_effect=observe
        charge=mock.Mock()
        with mock.patch.object(CR.subprocess,'Popen') as child,self.assertRaises(P.PhaseInterrupted):
            CR.command(self.root,job,'local',['fixture'],self.root,self.root/'x.log',60,job['deadline'],
                       local_model=self.model,resource_input_key='same-input',on_launch=charge)
        charge.assert_not_called();child.assert_not_called()


if __name__=='__main__':unittest.main()
