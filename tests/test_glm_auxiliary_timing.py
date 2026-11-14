"""Pinned GLM waits and discussions use their original owner window; no models."""
from contextlib import ExitStack, contextmanager, nullcontext
import datetime as dt
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import agent_discussion as A
import agent_workspace as C
from action_store import Actions
import colibri_provider as G
import midnight as M
import model_manager as MM
import project_backend as B


class AuxiliaryTimingTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.instant=dt.datetime.now(dt.timezone.utc)
        self.epoch=self.instant.timestamp();self.serial=0
        self.model={'model':G.MODEL,'parameters_sha256':'fixed','promotion_generation':'original'}
        self.profile={'supervisor_timeout_seconds':86400,'worker_timeout_seconds':86400,'worker_stall_seconds':1800}
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ,{},clear=False))
        os.environ.pop('MIDNIGHT_ACTION_ID',None)
        self.stack.enter_context(patch.object(G,'phase_profile',return_value=self.profile))
        self.stack.enter_context(patch.object(A.P,'alive',return_value=True))
        self.stack.enter_context(patch.object(A.PA,'source_head',return_value='a'*40))
        self.stack.enter_context(patch.object(A,'STOP',False))
        self.stack.enter_context(patch.object(A,'BACKEND',None))

    def workspace(self):
        self.serial+=1;root=self.root/str(self.serial);runtime=root/'runtime'
        workspace=C.Workspace(runtime/'agent-workspace')
        project={'id':'one','name':'One','on_schedule':True,'state_dir':str(root/'one')}
        workspace.bootstrap([project])
        workspace.operator_post('codex','project-one','Report observed progress',kind='question',mentions=['planner:one'])
        return root,runtime,workspace,project

    def scheduled(self,model,window_seconds=43200,trial_seconds=43200):
        root,runtime,workspace,project=self.workspace()
        cutoff=self.instant+dt.timedelta(seconds=window_seconds)
        with patch.object(MM,'task_selection',return_value=nullcontext(model)), \
             patch.object(G,'trial_status',return_value={'expires_at':(self.instant+dt.timedelta(seconds=trial_seconds)).isoformat()}), \
             patch.object(A.time,'time',return_value=self.epoch),patch.object(A.PA,'spawn_pending'):
            identity=A.schedule(root,{'projects':[project]},self.instant,cutoff,'session')
        return Actions(runtime).get(identity)

    def test_glm_discussion_job_can_outlast_the_old_hour(self):
        self.assertEqual(self.scheduled(self.model)['deadline'],self.epoch+43200)

    def test_discussion_job_uses_the_verified_profile_bound(self):
        with patch.object(G,'phase_profile',return_value=dict(self.profile,supervisor_timeout_seconds=7200)):
            self.assertEqual(self.scheduled(self.model)['deadline'],self.epoch+7200)

    def test_discussion_job_is_clipped_to_trial_or_window(self):
        for window,trial in [(120,43200),(43200,120)]:
            with self.subTest(window=window,trial=trial):
                self.assertEqual(self.scheduled(self.model,window,trial)['deadline'],self.epoch+120)

    def test_qwen_discussion_keeps_the_hour_job_limit(self):
        with patch.object(G,'phase_profile',side_effect=AssertionError('Qwen must not use the native profile')):
            self.assertEqual(self.scheduled({'model':'qwen'})['deadline'],self.epoch+3600)

    def execute_discussion(self,model,owner_seconds=7200,window_seconds=43200):
        root,runtime,workspace,project=self.workspace();work=workspace.pending_work()[0]
        job=Actions(runtime).submit('midnight',{'kind':'agent_discussion','work':work['id'],'source_head':'a'*40,
            'worker_model':model,'session':'session'},self.epoch+owner_seconds)
        backend=Mock()
        def decision(prompt,schema,directory,cutoff,state,config,**kwargs):
            (directory/'local-decision-receipt.json').write_text('{}')
            return {'body':'Only supplied observations were checked','needs_principal':False}
        backend.local_decision.side_effect=decision
        with patch.object(A,'ROOT',root),patch.object(A,'project_facts',return_value={}), \
             patch.object(A.P,'lease',return_value={'pid':123}),patch.object(A.PA,'drain',return_value=True), \
             patch.object(M,'registry',return_value={'projects':[project]}), \
             patch.object(M,'window',return_value={'active':True,'cutoff':self.instant+dt.timedelta(seconds=window_seconds)}), \
             patch.object(B,'load_backend',return_value=backend), \
             patch.object(G,'trial_status',side_effect=AssertionError('A later trial cannot renew the original job')):
            self.assertEqual(A.execute(runtime,job['id']),0)
        self.assertEqual(Actions(runtime).get(job['id'])['deadline'],job['deadline'])
        return backend.local_decision.call_args

    def test_glm_discussion_phase_uses_profile_and_original_owner_deadline(self):
        call=self.execute_discussion(self.model)
        self.assertEqual(call.args[5]['local_supervision_timeout_seconds'],86400)
        self.assertEqual(call.args[3],self.instant+dt.timedelta(seconds=7200))

    def test_discussion_phase_remains_clipped_to_current_window(self):
        call=self.execute_discussion(self.model,window_seconds=120)
        self.assertEqual(call.args[3],self.instant+dt.timedelta(seconds=120))

    def test_qwen_discussion_keeps_180_second_phase(self):
        call=self.execute_discussion({'model':'qwen'})
        self.assertEqual(call.args[5]['local_supervision_timeout_seconds'],180)

    def backend_action(self,model=None,owner_seconds=7200):
        runtime=self.root/'backend-runtime';state=self.root/'state';state.mkdir(exist_ok=True)
        store=Actions(runtime)
        model=self.model if model is None else model
        job=store.submit('one',{'worker_model':model,'session':'session'},self.epoch+owner_seconds)
        store.claim(job['id'],{'pid':123});store.charge(job['id'],'previous-launch','local_supervision')
        self.stack.enter_context(patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':job['id']}))
        for key,value in [('CLOUD_RUNTIME',runtime),('STATE',state),('PROJECT_ID','one'),('STOP',False),('CONTROL_STOP',lambda:False)]:
            self.stack.enter_context(patch.object(B,key,value))
        self.stack.enter_context(patch.object(B,'report'))
        return store,job

    def run_backend(self,model,*,kind='local',window_seconds=43200,wait_seconds=1000,pause=False):
        captured={};charge=Mock();process=Mock()
        @contextmanager
        def phase(runtime,project,resource,command,cwd,log,deadline,**kwargs):
            captured['deadline']=deadline
            if pause:(B.STATE/'PAUSED').touch()
            if kwargs['stopped']() or self.epoch+wait_seconds>=deadline:
                raise B.PR.PhaseInterrupted('Synthetic admission wait ended')
            yield 'synthetic-phase'
        def launch(*args,**kwargs):
            process(*args,**kwargs)
            kwargs['on_launch']()
            return {'code':0,'reason':'exited'}
        with patch.object(B.PR,'phase',side_effect=phase),patch.object(B.PR.Actions,'phase_update'), \
             patch.object(B,'_run_process',side_effect=launch),patch.object(B.time,'time',return_value=self.epoch), \
             patch.object(G,'trial_status',side_effect=AssertionError('Admission must use the saved job deadline')):
            try:
                result=B.charged_run(['synthetic-local'],self.root,self.root/'log',86400,
                    self.instant+dt.timedelta(seconds=window_seconds),{},charge,resource_kind=kind,local_model=model)
            except B.PR.PhaseInterrupted:result={'reason':'interrupted'}
        return result,captured,charge,process

    def test_glm_queue_can_wait_past_900_seconds_without_recharging_history(self):
        store,job=self.backend_action()
        result,captured,charge,process=self.run_backend(self.model)
        self.assertEqual(result['reason'],'exited')
        self.assertEqual(captured['deadline'],job['deadline'])
        self.assertEqual(process.call_args.args[4].timestamp(),job['deadline'])
        charge.assert_called_once()
        self.assertEqual(store.session_charges('session','one'),{'local_supervision':1})
        self.assertEqual(store.get(job['id'])['deadline'],job['deadline'])

    def test_glm_admission_still_stops_at_the_current_window(self):
        self.backend_action()
        result,captured,charge,process=self.run_backend(self.model,window_seconds=120)
        self.assertEqual(captured['deadline'],self.epoch+120)
        self.assertEqual(result['reason'],'interrupted');charge.assert_not_called();process.assert_not_called()

    def test_pause_during_glm_admission_never_launches_or_charges(self):
        self.backend_action()
        result,_,charge,process=self.run_backend(self.model,pause=True)
        self.assertEqual(result['reason'],'interrupted');charge.assert_not_called();process.assert_not_called()

    def test_qwen_and_host_admission_keep_the_900_second_limit(self):
        self.backend_action()
        for model,kind in [({'model':'qwen'},'local'),(self.model,'host'),(self.model,'cloud')]:
            with self.subTest(model=model,kind=kind):
                result,captured,charge,process=self.run_backend(model,kind=kind)
                self.assertEqual(captured['deadline'],self.epoch+900)
                self.assertEqual(result['reason'],'interrupted');charge.assert_not_called();process.assert_not_called()

    def test_glm_admission_rejects_a_different_pinned_model_or_owner(self):
        store,job=self.backend_action()
        actual=store.get(job['id'])
        for replacement in [None,dict(actual,project='another'),dict(actual,state='queued'),
                            dict(actual,payload={'worker_model':dict(self.model,parameters_sha256='changed')})]:
            with self.subTest(replacement=replacement),patch.object(B.PR.Actions,'get',return_value=replacement):
                with self.assertRaises(B.ManagerHeld):self.run_backend(self.model)
        with patch.object(B.PR,'alive',return_value=False),self.assertRaises(B.ManagerHeld):self.run_backend(self.model)

    def test_unverified_glm_profile_cannot_extend_admission(self):
        self.backend_action()
        with patch.object(G,'phase_profile',side_effect=ValueError('Source/profile changed')),self.assertRaises(ValueError):
            self.run_backend(self.model)

    def test_local_supervision_passes_its_exact_model_to_admission(self):
        directory=self.root/'decision'
        with patch.object(B,'PROJECT_ID','midnight'),patch.object(B,'git',return_value='a'*40), \
             patch.object(B,'charged_run',return_value={'code':1,'reason':'synthetic'}) as run:
            with self.assertRaises(B.ManagerHeld):
                B.local_decision('Fixed facts',A.REPLY_SCHEMA,directory,self.instant+dt.timedelta(hours=1),{},
                    {'worker_model':self.model,'local_supervision_timeout_seconds':86400},role='scrum')
        self.assertEqual(run.call_args.kwargs.get('local_model'),self.model)


if __name__=='__main__':unittest.main()
