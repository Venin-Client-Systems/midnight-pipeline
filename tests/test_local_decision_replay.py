"""Real action/backend/charge boundaries; only model process effects are fake."""
import contextlib
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import action_store as A
import durability as D
import midnight as M
import phase_runtime as P
import project_action as PA
import project_backend as B


class LocalDecisionReplay(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.runtime=self.root/'runtime'
        self.state=self.runtime/'projects/fixture';self.state.mkdir(parents=True)
        self.project_root=self.root/'product';(self.project_root/'orchestration').mkdir(parents=True)
        self.model={'model':'fixture','digest':'b'*64,'parameters_sha256':'c'*64,
                    'bytes':1,'context':32768,'promotion_generation':None}
        self.config={'worker_model':self.model,'routine_provider':'local','max_attempts_per_night':None,
                     'max_cloud_calls_per_night':None,'max_failures_per_night':None}
        D.save_json(self.project_root/'orchestration/config.json',self.config)
        D.save_json(self.project_root/'orchestration/backlog.json',[])
        D.save_json(self.state/'state.json',{'done':[],'accepted':[],'tries':{},'feedback':{},'planned':[]})
        self.project={'id':'fixture','on_schedule':True,'state_dir':str(self.state),
                      'path':str(self.project_root),'controller':'orchestration/supervisor.py'}
        self.registry={'timezone':'Australia/Adelaide','schedule':{'start':'00:00','end':'09:00'},'projects':[self.project]}
        D.save_json(self.root/'projects.json',self.registry)
        D.save_json(self.runtime/'override.json',{'mode':'continuous','expires_at':None})
        self.store=A.Actions(self.runtime);self.backend=B.load_backend(self.project_root,'fixture')
        self.directory=self.state/'decision';self.prompt='Evaluate the exact retained evidence.'
        self.schema={'type':'object','additionalProperties':False,'properties':{'answer':{'type':'boolean'}},'required':['answer']}
        self.calls=[];self.outcome='failure';self.host_accept=True
        stack=contextlib.ExitStack();self.addCleanup(stack.close)
        for obj,name,value in [(PA,'ROOT',self.root),(M,'STATE',self.runtime),(self.backend,'STATE',self.state),
                (self.backend,'CLOUD_RUNTIME',self.runtime),(self.backend,'ROOT',self.project_root)]:
            stack.enter_context(mock.patch.object(obj,name,value))
        stack.enter_context(mock.patch.object(PA,'source_head',return_value='a'*40))
        stack.enter_context(mock.patch.object(M,'registry',return_value=self.registry))
        stack.enter_context(mock.patch.object(M,'validate_backend'))
        stack.enter_context(mock.patch.object(M,'effective_limits',return_value={}))
        # The outer action is real; this fake child invokes the real backend
        # synchronously, so only its OS parent-registration check is replaced.
        stack.enter_context(mock.patch.object(PA,'register_backend'))
        stack.enter_context(mock.patch.object(self.backend,'recover'))
        stack.enter_context(mock.patch.object(self.backend.AS,'compact',side_effect=lambda _s,_p,state,_b:state))
        stack.enter_context(mock.patch.object(self.backend,'git',return_value='d'*40))
        stack.enter_context(mock.patch.object(self.backend,'run',side_effect=self.model_process))
        stack.enter_context(mock.patch.object(self.backend,'next_task',side_effect=self.decision))
        processes=mock.Mock(wraps=self.backend.subprocess);processes.Popen.return_value=self.NoopProcess()
        stack.enter_context(mock.patch.object(self.backend,'subprocess',processes))
        stack.enter_context(mock.patch.object(self.backend,'window',side_effect=lambda now:(True,now.date().isoformat(),now+dt.timedelta(minutes=5))))

    class NoopProcess:
        def terminate(self):pass
        def wait(self,timeout=None):return 0

    def model_process(self,*args,on_launch=None,**kwargs):
        if self.outcome=='unspent':raise P.PhaseInterrupted('Resource admission interrupted before charge')
        if self.outcome=='unspent_return':return {'code':-1,'reason':'stopped_before_start'}
        if on_launch:on_launch()
        self.calls.append(os.environ['MIDNIGHT_ACTION_ID'])
        if self.outcome=='unknown':raise P.PhaseInterrupted('Owned child result unavailable')
        if self.outcome in {'paused','cutoff'}:return {'code':75,'reason':'paused_or_shutdown' if self.outcome=='paused' else '09:00_cutoff','seconds':0.1}
        if self.outcome in {'success','invalid'}:
            D.save_json(self.directory/'result.json',{'answer':'not a boolean'} if self.outcome=='invalid' else {'answer':True})
            D.save_json(self.directory/'local-metrics.json',{'model':'fixture'})
        D.atomic_write(self.directory/'events.jsonl',b'{"type":"turn.completed"}\n')
        return {'code':0 if self.outcome in {'success','invalid'} else 1,'reason':'exited','seconds':0.1}

    def decision(self,state,config,cutoff):
        self.backend.local_decision(self.prompt,self.schema,self.directory,cutoff,state,config,
                                    host_validate=lambda _v:self.host_accept,role='fixture')
        return None

    def attempt(self):
        # Exercise a later observation/action, beyond the saved wait. Elapsed
        # time may allow observation, never another charge for identical input.
        saved=D.read_json(self.state/'state.json',{})
        if saved.get('cooldown_until'):
            saved['cooldown_until']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=1)).isoformat()
            D.save_json(self.state/'state.json',saved)
        cutoff=dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=5)
        identity=PA.launch(self.runtime,self.project,self.model,{},'fixture-session',cutoff,spawn=lambda *a,**k:None)
        owner=self
        class BackendProcess:
            def __init__(self):self.code=owner.backend.tick(once=True,managed_until=cutoff)
            def poll(self):return self.code
            def wait(self,timeout=None):return self.code
        with mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':identity}):
            PA.execute(self.runtime,identity,spawn=lambda *a,**k:BackendProcess())
        return self.store.get(identity)

    def charges(self):
        with self.store.read() as con:
            return con.execute("SELECT COUNT(*) FROM wf_charges WHERE kind='local_supervision'").fetchone()[0]

    def test_failed_local_decision_is_not_reported_as_successful_action(self):
        job=self.attempt()
        self.assertEqual(job['state'],'failed')
        self.assertEqual(self.charges(),1)

    def test_unchanged_failed_decision_does_not_relaunch_or_charge_on_new_action(self):
        self.attempt();self.attempt()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.charges(),1)

    def test_unknown_charged_completion_is_held_on_new_action(self):
        self.outcome='unknown';first=self.attempt();self.attempt()
        self.assertEqual(first['state'],'failed')
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.charges(),1)

    def test_decision_stopped_by_owner_pause_or_cutoff_is_asked_again_and_charged_again(self):
        for stop in ('paused','cutoff'):
            with self.subTest(stop=stop):
                self.calls.clear();self.directory=self.state/('decision-'+stop);self.prompt='Evaluate the exact retained evidence after '+stop+'.'
                self.outcome=stop;first=self.attempt();before=self.charges()-1
                self.assertEqual(first['state'],'failed');self.assertEqual(len(self.calls),1)
                self.outcome='success';job=self.attempt()
                self.assertEqual(job['state'],'succeeded');self.assertEqual(len(self.calls),2)
                self.assertEqual(self.charges(),before+2)
                self.attempt();self.assertEqual(len(self.calls),2)

    def test_control_stop_allows_one_reask_and_an_unknown_rerun_stays_spent(self):
        self.outcome='paused';self.attempt()
        self.outcome='unknown';self.attempt();self.attempt();self.attempt()
        self.assertEqual(len(self.calls),2);self.assertEqual(self.charges(),2)
        self.outcome='success';self.assertEqual(self.attempt()['state'],'failed');self.assertEqual(len(self.calls),2)

    def test_uncharged_stop_while_still_paused_keeps_the_control_stop_reask(self):
        self.outcome='paused';self.attempt()
        self.outcome='unspent_return';self.attempt();self.outcome='unspent';self.attempt()
        self.assertEqual(len(self.calls),1);self.assertEqual(self.charges(),1)
        self.outcome='success';self.assertEqual(self.attempt()['state'],'succeeded')
        self.assertEqual(len(self.calls),2);self.assertEqual(self.charges(),2)

    def test_host_rejected_complete_decision_is_rechecked_without_another_model_call(self):
        self.outcome='success';self.host_accept=False;self.attempt()
        self.host_accept=True;job=self.attempt()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.charges(),1)
        self.assertEqual(job['state'],'succeeded')

    def test_valid_complete_decision_control_is_reused(self):
        self.outcome='success';self.attempt();job=self.attempt()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.charges(),1)
        self.assertEqual(job['state'],'succeeded')

    def test_complete_process_recovers_missing_final_receipt_without_relaunch(self):
        self.outcome='success';self.attempt()
        (self.directory/'local-decision-receipt.json').unlink()
        self.attempt()
        self.assertEqual(len(self.calls),1)
        self.assertTrue((self.directory/'local-decision-receipt.json').is_file())

    def test_current_complete_process_recovers_over_previous_input_receipt(self):
        self.outcome='success';self.attempt()
        old_receipt=(self.directory/'local-decision-receipt.json').read_bytes()
        self.prompt+=' New host evidence.';self.attempt()
        # Crash after the new process receipt but before final receipt replaces A.
        D.atomic_write(self.directory/'local-decision-receipt.json',old_receipt)
        job=self.attempt()
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.charges(),2)
        self.assertEqual(job['state'],'succeeded')
        self.assertNotEqual((self.directory/'local-decision-receipt.json').read_bytes(),old_receipt)

    def test_tampered_complete_result_is_held_without_relaunch(self):
        self.outcome='success';self.attempt()
        D.save_json(self.directory/'result.json',{'answer':False})
        job=self.attempt()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.charges(),1)
        self.assertEqual(job['state'],'failed')

    def test_changed_actual_input_can_receive_a_new_call(self):
        self.attempt();self.prompt+=' New host evidence changes this decision.'
        self.outcome='success';job=self.attempt()
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.charges(),2)
        self.assertEqual(job['state'],'succeeded')
        self.assertNotIn('local_decision_hold',D.read_json(self.state/'state.json',{}))

    def test_new_directory_cannot_repeat_the_same_spent_request(self):
        self.attempt();self.directory=self.state/'renamed-decision';self.attempt()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.charges(),1)

    def test_unspent_resource_interruption_does_not_prevent_later_admission(self):
        self.outcome='unspent';self.attempt()
        self.assertEqual(self.charges(),0)
        self.outcome='success';job=self.attempt()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.charges(),1)
        self.assertEqual(job['state'],'succeeded')

    def test_unspent_control_return_is_not_mislabelled_as_a_spent_hold(self):
        self.outcome='unspent_return';self.attempt()
        self.assertEqual(self.charges(),0)
        self.assertNotIn('local_decision_hold',D.read_json(self.state/'state.json',{}))
        self.assertEqual(D.read_json(self.state/'status.json',{})['phase'],'waiting_for_resource')
        self.outcome='success';job=self.attempt()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.charges(),1)
        self.assertEqual(job['state'],'succeeded')

    def test_invalid_typed_completion_stays_held_without_relaunch(self):
        self.outcome='invalid';first=self.attempt();second=self.attempt()
        self.assertEqual((first['state'],second['state']),('failed','failed'))
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.charges(),1)


if __name__=='__main__':unittest.main()
