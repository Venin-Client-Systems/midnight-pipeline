"""Retained commissioning cannot fall through to unrelated model work."""
import contextlib
import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import project_backend as B
import progress_recovery as R
import worker_quality as Q


class CommissionedSelection(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.b=B.load_backend(self.root,'fixture',settings={})
        self.b.STATE=self.root/'state';self.b.STATE.mkdir();self.b.CLOUD_RUNTIME=self.root/'runtime'
        self.task={'id':'retained','kind':'repair','title':'Repair existing behavior','finding_ids':['f1']}
        self.scope=R.scope('fixture','issue:1',self.task,'a'*40,'profile','micro-3')
        self.binding={'task_id':self.task['id'],'task_hash':Q.task_fingerprint(self.task),'recovery_scope':self.scope}
        self.state={'done':[],'accepted':[],'tries':{'retained':4},'planned':[]}
        self.config={'routine_provider':'local'};self.cutoff=self.b.now()+dt.timedelta(hours=1)
        self.engine=mock.Mock(profile_hash='profile')
        self.engine.status.return_value={'material_findings':[{'id':'f1'}],'feature_allowed':False,'assessments':{}}
        self.engine.next_decision.return_value={'action':'implement_locally','task':self.task}
        self.rescue=mock.Mock();self.rescue.eligibility.return_value={'status':'local_retries_required','allowed':False}
        stack=contextlib.ExitStack();self.addCleanup(stack.close);self.mocks={}
        values={'commissioning_binding':self.binding,'reconcile_saved_audits':None,'git':'a'*40,
                'quality_engine':self.engine,'all_tasks':[self.task],'current_repair_contract':True,
                'task_valid':True,'resume_micro_checkpoint':False,'protected_test_baseline':set(),
                'protected_task_paths':[],'import_episode_failures':'issue:1','issue_episode':'issue:1',
                'rescue_store':self.rescue,'unchecked_candidate':False,'progress_enabled':False,
                'progress_identity':self.scope}
        for name,value in values.items():
            self.mocks[name]=stack.enter_context(mock.patch.object(self.b,name,return_value=value))
        stack.enter_context(mock.patch.object(self.b,'task_done',side_effect=lambda state,key:key in state['done']))
        recovery=stack.enter_context(mock.patch.object(self.b.MR,'ManagerRecovery'));recovery.return_value.tasks.return_value=[]
        monitor=stack.enter_context(mock.patch.object(self.b.QM,'QualityMonitor'))
        monitor.return_value.snapshot.return_value={};monitor.return_value.due.return_value=True
        self.profile=stack.enter_context(mock.patch.object(self.b.PM,'status',return_value={'feature_hold':False}))
        self.effects={name:stack.enter_context(mock.patch.object(self.b,name,return_value=None)) for name in
                      ('plan_task','grow_quality_profile','run_assessment','run_quality_audit',
                       'guided_successor','coordinate_microsteps','coordinate_recovery')}
        self.progress=stack.enter_context(mock.patch.object(self.b,'prepare_progress',return_value=(self.scope,
            {'allowed':False,'state':'held','reason':'Retained failure needs diagnosis'})))
        self.report=stack.enter_context(mock.patch.object(self.b,'report'))

    def select(self):return self.b.next_task(self.state,self.config,self.cutoff)

    def no_unrelated_calls(self):
        for call in self.effects.values():call.assert_not_called()
        self.assertEqual(self.state['tries'],{'retained':4});self.assertEqual(self.state['accepted'],[])

    def test_admitted_retained_task_is_returned_without_unrelated_inference(self):
        self.assertEqual(self.select(),self.task);self.no_unrelated_calls()
        self.assertEqual([item['id'] for item in self.engine.next_decision.call_args.args[1]],['retained'])

    def test_held_task_and_unbound_finding_never_plan_a_replacement(self):
        self.rescue.eligibility.return_value={'status':'manager_hold','allowed':False}
        self.engine.status.return_value['material_findings'].append({'id':'unrelated'})
        self.engine.next_decision.return_value={'action':'wait'}
        with self.assertRaisesRegex(self.b.ManagerHeld,'not admitted'):self.select()
        self.no_unrelated_calls()

    def test_assessment_decision_is_a_hold_instead_of_an_audit(self):
        self.engine.next_decision.return_value={'action':'assess','assessment_kind':'baseline'}
        with self.assertRaisesRegex(self.b.ManagerHeld,'not admitted'):self.select()
        self.no_unrelated_calls()

    def test_feature_profile_hold_does_not_run_profile_growth(self):
        self.profile.return_value={'feature_hold':True}
        self.engine.next_decision.return_value={'action':'implement_locally','task':{'id':'retained'}}
        with self.assertRaisesRegex(self.b.ManagerHeld,'quality profile'):self.select()
        self.no_unrelated_calls()

    def test_invalidated_contract_cannot_fall_into_feature_or_baseline_planning(self):
        self.mocks['current_repair_contract'].return_value=False
        self.engine.status.return_value['feature_allowed']=True
        self.engine.next_decision.return_value={'action':'wait'}
        with self.assertRaisesRegex(self.b.ManagerHeld,'contract or prerequisites'):self.select()
        self.no_unrelated_calls();self.progress.assert_not_called()

    def test_unmet_prerequisite_holds_before_checkpoint_or_any_inference(self):
        self.task['depends_on']=['unfinished-predecessor']
        self.binding['task_hash']=Q.task_fingerprint(self.task)
        self.scope=R.scope('fixture','issue:1',self.task,'a'*40,'profile','micro-3')
        self.binding['recovery_scope']=self.scope;self.mocks['progress_identity'].return_value=self.scope
        with self.assertRaisesRegex(self.b.ManagerHeld,'contract or prerequisites'):self.select()
        self.mocks['resume_micro_checkpoint'].assert_not_called();self.progress.assert_not_called();self.no_unrelated_calls()

    def test_changed_recovery_scope_holds_before_resuming_a_checkpoint(self):
        self.mocks['progress_identity'].return_value=dict(self.scope,stage='micro-4')
        with self.assertRaisesRegex(self.b.ManagerHeld,'scope changed'):self.select()
        self.mocks['resume_micro_checkpoint'].assert_not_called();self.progress.assert_not_called();self.no_unrelated_calls()

    def test_different_decision_task_is_rejected_even_if_otherwise_ready(self):
        self.engine.next_decision.return_value={'action':'implement_locally','task':dict(self.task,id='different')}
        with self.assertRaisesRegex(self.b.ManagerHeld,'different task'):self.select()
        self.no_unrelated_calls()

    def test_guidance_requirement_cannot_create_a_successor_contract(self):
        self.rescue.eligibility.return_value={'status':'guidance_required','allowed':False}
        with self.assertRaisesRegex(self.b.ManagerHeld,'successor'):self.select()
        self.no_unrelated_calls()

    def test_contract_mutation_during_selection_is_caught_before_return(self):
        def decide(*args,**kwargs):
            self.task['title']='Changed acceptance scope'
            return {'action':'implement_locally','task':self.task}
        self.engine.next_decision.side_effect=decide
        with self.assertRaisesRegex(self.b.ManagerHeld,'contract changed'):self.select()
        self.no_unrelated_calls()

    def test_same_scope_diagnosis_can_admit_the_retained_task(self):
        self.mocks['progress_enabled'].return_value=True
        self.engine.next_decision.side_effect=[{'action':'needs_manager'},{'action':'implement_locally','task':self.task}]
        self.progress.side_effect=[(self.scope,{'allowed':False,'state':'held','reason':'Diagnosis needed'}),
                                   (self.scope,{'allowed':True,'state':'ready','reason':'Verified narrow intervention'})]
        self.assertEqual(self.select(),self.task)
        self.assertEqual(self.progress.call_count,2)
        self.assertTrue(self.progress.call_args.kwargs['diagnose'])
        self.assertTrue(all(call.args[0]['id']=='retained' for call in self.progress.call_args_list))
        self.no_unrelated_calls()

    def test_normal_uncommissioned_selection_retains_unbound_planning(self):
        self.mocks['commissioning_binding'].return_value=None
        self.rescue.eligibility.return_value={'status':'manager_hold','allowed':False}
        self.engine.status.return_value['material_findings'].append({'id':'unrelated'})
        self.engine.next_decision.return_value={'action':'wait'}
        self.assertIsNone(self.select())
        self.effects['plan_task'].assert_called_once()
        self.assertEqual(self.effects['plan_task'].call_args.kwargs['findings'],[{'id':'unrelated'}])


if __name__=='__main__':unittest.main()
