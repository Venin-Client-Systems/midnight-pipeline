"""Campaign transitions use temporary ledgers and fake provider/process observations."""
import copy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import performance_review as F
import performance_review_cloud as C
import performance_review_schedule as S
from action_store import Actions
import phase_runtime as P
import test_performance_review as review_fixtures
import test_performance_review_cloud as cloud_fixtures


class AmendmentTests(unittest.TestCase):
    def setUp(self):
        self.fixture = review_fixtures.PerformanceReviewTests('test_baseline_excludes_historical_eleven_and_two_acceptances')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.root, self.directory, self.review = self.fixture.root, self.fixture.directory, self.fixture.review
        self.baseline = self.fixture.initialise(); self.start = self.baseline['started_at']
        self.cloud_model = C.CP.config_descriptor(); self.original_model = self.fixture.model
        self.new_model = dict(self.original_model, parameters_sha256='d' * 64)
        self.authority = {'permission_sha256': 'e' * 64}
        self.files = {name: F.digest(F.read_bytes(Path(F.__file__).parent / name)) for name in C.SOURCES}
        self.original = {'version': 1, 'campaign': self.directory.name, 'baseline_sha256': C.sealed(self.baseline),
            'source': {'commit': self.fixture.commit, 'files': self.files, 'cloud_config_sha256': 'f' * 64},
            'model': self.cloud_model, 'authority': self.authority, 'runtime_controls': {'selected_model': self.original_model},
            'maximum_attempts': 7, 'timeout_seconds': 900, 'enabled': True}
        F.write_once(self.directory / 'cloud-review-plan.json', self.original)
        agents = self.root / 'agents'; agents.mkdir()
        specs = S.specifications(self.root, self.directory, sys.executable, agents)
        self.schedule = {'baseline_sha256': C.sealed(self.baseline), 'entrypoint_sha256': '9' * 64,
                         'uid': os.getuid(), 'schedules': {k: {a: b for a, b in v.items() if a != 'raw'} for k, v in specs.items()}}
        for value in specs.values():Path(value['path']).write_bytes(value['raw'])
        F.write_once(self.directory / 'schedule.json', self.schedule)
        self.owner = self.root / 'owner.json'; self.fixture.save(self.owner, {'direction': 'Weekdays, retain original campaign'})
        self.controls = {'selected_model': self.new_model, 'continuous_override': None,
                         'work_schedule': {'timezone': 'Australia/Adelaide', 'schedule': {'start': '00:00', 'end': '09:00', 'weekdays': ['mon','tue','wed','thu','fri']}}}
        self.next = {'source_tree': 'b' * 40, 'source_files': self.files, 'cloud_config_sha256': 'f' * 64,
                     'runtime_controls': self.controls}
        self.intent = self.review.prepare_amendment(self.next, F.reference(self.owner))
        self.health = {'state': 'verified', 'installed': 'c' * 40, 'tree': 'b' * 40,
                       'verified_controller_process': {'pid': 123, 'birth_us': 11},
                       'verified_dashboard': {'process': {'pid': 124, 'birth_us': 12}}}
        self.activation_at = self.start + F.DAY + 100
        self.refs = self.release(self.intent)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(Actions, 'meta', return_value=self.health))
        self.stack.enter_context(patch.object(Actions, 'jobs', return_value=[]))
        self.stack.enter_context(patch.object(P, 'alive', return_value=True))
        self.stack.enter_context(patch.object(C, 'source', return_value={'commit': 'c' * 40, 'files': self.files, 'cloud_config_sha256': 'f' * 64}))
        self.stack.enter_context(patch.object(C, 'runtime_controls', return_value=self.controls))
        self.stack.enter_context(patch.object(C.A, 'check', return_value=self.authority))
        self.stack.enter_context(patch.object(C.CP, 'CloudPolicy', return_value=Mock(descriptor=lambda: self.cloud_model)))
        self.fixture.git.side_effect = lambda root, *args: 'b' * 40 if args == ('rev-parse','HEAD^{tree}') else 'c' * 40

    def release(self, intent):
        review = {'decision': 'accept', 'issues': [], 'amendment_sha256': C.sealed(intent)}
        self.fixture.save(self.root / 'review.json', review)
        verified = {'tree': 'b' * 40, 'base': self.fixture.commit, 'patch_sha256': '8' * 64,
                    'review': review, 'source_review_sha256': F.reference(self.root / 'review.json')['sha256'],
                    'descriptor': self.cloud_model, 'checks': [{'code': 0, 'reason': 'exited', 'completion': {'complete': True}}]}
        values = {'verified': verified, 'review': review,
            'provider': {'returncode': 0, 'completed': 'recorded', 'profile_mismatch': False, 'requested': self.cloud_model},
            'delivery': {'status': 'merged', 'candidate_tree': 'b' * 40, 'commit': 'c' * 40,
                         'base': self.fixture.commit, 'patch_sha256': '8' * 64},
            'proof': {'installed': 'c' * 40, 'tree': 'b' * 40, 'source_clean': True, 'fence_present': False,
                      'selected_model': self.new_model, 'generation_health': self.health, 'observed_at': F.iso(self.activation_at)}}
        for name, value in values.items():self.fixture.save(self.root / (name + '.json'), value)
        return {name: F.reference(self.root / (name + '.json')) for name in values}

    def activate(self, **kwargs):
        return self.review.activate_amendment(self.intent, self.refs, instant=kwargs.get('instant',self.activation_at + 1))

    def test_preparation_is_read_only_and_does_not_renew_intervals_or_allowance(self):
        self.assertFalse((self.directory / 'amendments').exists())
        self.assertEqual(self.intent['started_at'], self.baseline['started_at'])
        self.assertEqual(self.intent['ends_at'], self.baseline['ends_at']);self.assertEqual(self.intent['maximum_attempts'],7)
        for field, value in [('ends_at',self.baseline['ends_at']+1),('started_at',self.start+1),('maximum_attempts',8)]:
            bad=dict(self.intent,**{field:value})
            with self.subTest(field=field), self.assertRaises(F.Unavailable):self.review.activate_amendment(bad,self.refs,self.activation_at+1)

    def test_activation_preserves_original_files_and_closed_report_and_is_idempotent(self):
        self.fixture.review.tick(self.start + F.DAY)
        paths=[self.directory/name for name in ('baseline.json','cloud-review-plan.json','schedule.json','reports/day-01.json')]
        before={path:path.read_bytes() for path in paths}
        activated=self.activate();self.assertEqual(activated,self.activate(instant=self.activation_at+1000))
        self.assertEqual(before,{path:path.read_bytes() for path in paths})
        self.assertEqual(self.review.plans()[0],self.original);self.assertEqual(len(self.review.plans()),2)
        self.assertEqual(C.review_plan(self.root,self.directory),activated['plan'])
        self.fixture.review.tick(self.start+2*F.DAY)
        self.assertEqual(before[self.directory/'reports/day-01.json'],(self.directory/'reports/day-01.json').read_bytes())
        second=F.unseal(self.directory/'reports/day-02.json')
        self.assertEqual(second['start'],self.start+F.DAY);self.assertEqual(second['end'],self.start+2*F.DAY)

    def test_changed_collector_requires_exact_approved_activation(self):
        baseline=copy.deepcopy(self.baseline);baseline['collector_sha256']='1'*64
        self.fixture.save(self.directory/'baseline.json',F.seal(baseline))
        self.original['baseline_sha256']=C.sealed(baseline)
        self.fixture.save(self.directory/'cloud-review-plan.json',F.seal(self.original))
        self.schedule['baseline_sha256']=C.sealed(baseline)
        self.fixture.save(self.directory/'schedule.json',F.seal(self.schedule))
        with self.assertRaises(F.Unavailable):self.review.baseline()
        self.intent=self.review.prepare_amendment(self.next,F.reference(self.owner));self.refs=self.release(self.intent)
        self.activate();self.assertEqual(self.review.baseline(),baseline)

    def test_arbitrary_accept_json_or_incomplete_release_cannot_activate(self):
        path=self.root/'review.json';value=json.loads(path.read_text());value['amendment_sha256']='0'*64
        self.fixture.save(path,value);self.refs['review']=F.reference(path)
        with self.assertRaises(F.Unavailable):self.activate()
        self.assertFalse((self.directory/'amendments').exists())

    def test_fresh_proof_must_match_live_health_source_controls_and_owner_birth(self):
        for target,name,replacement in [(P,'alive',False),(Actions,'meta',{}),(C,'runtime_controls',{}),
                                        (C,'source',{'commit':'f'*40}), (C.A,'check',{})]:
            with self.subTest(name=name),patch.object(target,name,return_value=replacement),self.assertRaises(F.Unavailable):self.activate()
        with self.assertRaises(F.Unavailable):self.activate(instant=self.activation_at+301)
        self.assertFalse((self.directory/'amendments').exists())

    def test_active_paid_review_and_replaced_amendment_are_preserved(self):
        with patch.object(Actions,'jobs',return_value=[{'project':C.PROJECT}]),self.assertRaises(F.Unavailable):self.activate()
        self.activate();before=(self.directory/'amendments/0001.json').read_bytes()
        altered=copy.deepcopy(self.intent);altered['next']['runtime_controls']['selected_model']['context']=65536
        with self.assertRaises(F.Unavailable):self.review.activate_amendment(altered,self.refs,self.activation_at+1)
        self.assertEqual((self.directory/'amendments/0001.json').read_bytes(),before)

    def test_posthealth_gate_can_remain_owned_during_amendment_activation(self):
        path=self.root/'runtime/WEEKDAY_ACTIVATION_PENDING';path.write_text('owned pending activation')
        self.activate();self.assertEqual(path.read_text(),'owned pending activation')

    def test_tampered_reference_and_missing_chain_member_fail_closed(self):
        self.activate();self.owner.write_text('{"changed":true}')
        with self.assertRaises(F.Unavailable):self.review.baseline()
        self.owner.write_text(json.dumps({'direction':'Weekdays, retain original campaign'}))
        (self.directory/'amendments/0001.json').rename(self.directory/'amendments/0002.json')
        with self.assertRaises(F.Unavailable):self.review.baseline()

    def test_original_schedule_receipt_survives_authorised_entrypoint_change(self):
        self.activate();agents=self.root/'agents'
        with patch.object(S.Path,'home',return_value=self.root),patch.object(S,'specifications',
                return_value=S.specifications(self.root,self.directory,sys.executable,agents)):
            self.assertEqual(S.ownership(self.root,self.directory),self.schedule)
        self.assertEqual(F.unseal(self.directory/'schedule.json'),self.schedule)

    def test_old_and_new_profiles_remain_separate_after_activation(self):
        self.activate()
        self.fixture.accepted('old-profile',self.start+2*F.DAY-2,model=self.original_model)
        self.fixture.accepted('new-profile',self.start+2*F.DAY-1,commit='e'*40,model=self.new_model)
        self.fixture.phase('old-decision',self.start+2*F.DAY-1,model=self.original_model)
        self.fixture.review.tick(self.start+2*F.DAY)
        report=F.unseal(self.directory/'reports/day-02.json');counts=report['cloud_summary']['projects']['exampleapp']['by_cohort']
        self.assertEqual(sum(v['accepted_commits'] for v in counts.values()),2)
        cohorts=report['profile_cohorts']
        self.assertEqual({tuple(v['approved_profile_epochs']) for v in cohorts.values()},{(0,),(1,)})
        self.assertTrue(all(v['source'] is None for v in cohorts.values()))
        self.assertEqual(C.cohort_index(report),cohorts)
        self.assertEqual(len(C.configuration_index(report)),2)

    def test_decision_source_comes_from_owning_job_not_current_install(self):
        self.activate();self.fixture.phase('old-owner',self.start+2*F.DAY-1,model=self.original_model)
        with review_fixtures.sql(self.root/'runtime/workflow.sqlite3') as con:
            con.execute('UPDATE wf_jobs SET payload=? WHERE id=?',
                (json.dumps({'worker_model':self.original_model,'source_head':self.fixture.commit}),'job-old-owner'))
        self.fixture.review.tick(self.start+2*F.DAY)
        report=F.unseal(self.directory/'reports/day-02.json')
        decision=report['typed_decisions'][0]
        self.assertEqual(decision['coordinator_source'],self.fixture.commit)
        self.assertNotEqual(decision['coordinator_source'],self.health['installed'])
        self.assertEqual(next(iter(report['profile_cohorts'].values()))['source'],self.fixture.commit)

    def test_control_coverage_distinguishes_pause_off_hours_and_unknown(self):
        states=[('scheduled_unpaused',True,False),('scheduled_paused',True,True),('outside_schedule',False,False)]
        for index,(_,eligible,paused) in enumerate(states):
            for offset in (index*180,index*180+60):
                sample={'at':self.start+offset,'slot':offset//60,'active_available':True,'active':[],
                    'telemetry':{'available':False},'source':self.fixture.commit,'amendment_sha256':None,
                    'controls':{'available':True,'scheduled_eligible':eligible,'global_paused':paused,'installing':False,
                                'project_paused':{'secondapp':False,'exampleapp':False},'schedule_sha256':'a'*64}}
                F.write_once(self.directory/'samples'/f'{offset//60:05}.json',sample)
        value=self.review.resources(1,self.start,self.start+F.DAY)['sampled_control_seconds']['exampleapp']
        self.assertEqual(value['scheduled_unpaused'],60);self.assertEqual(value['scheduled_paused'],60)
        self.assertEqual(value['outside_schedule'],60);self.assertEqual(value['unknown'],F.DAY-180)


class PaidContinuityTests(unittest.TestCase):
    def setUp(self):
        self.fixture=cloud_fixtures.CloudPerformanceTests('test_one_qualified_review_deduplicates_and_keeps_host_evidence_private')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def test_old_spent_attempt_and_new_plan_share_same_seven_day_slots(self):
        f=self.fixture;self.assertEqual(f.run_day()['state'],'completed')
        original=F.unseal(f.directory/'cloud-review-plan.json');amended=dict(original,version=2,amendment_sha256='a'*64)
        before=(f.directory/'cloud/day-01/attempt.json').read_bytes()
        with patch.object(F.Review,'plans',return_value=[original,amended]),patch.object(C,'review_plan',return_value=amended):
            self.assertEqual(f.run_day()['state'],'spent')
            for day in range(2,8):
                f.publish(day);self.assertEqual(f.run_day(day,f.start+8*F.DAY)['state'],'completed')
            self.assertEqual(C.reconcile_due(f.root,f.directory)['spent_days'],list(range(1,8)))
            for day in range(1,8):self.assertEqual(f.run_day(day,f.start+9*F.DAY)['state'],'spent')
        self.assertEqual(len(f.cloud.calls),7);self.assertEqual((f.directory/'cloud/day-01/attempt.json').read_bytes(),before)

    def test_scheduled_mode_is_allowed_for_explicitly_amended_controls_outside_work_hours(self):
        f=self.fixture;C.D.delete(f.root/'runtime/override.json')
        C.D.save_json(f.root/'projects.json',{'timezone':'Australia/Adelaide','schedule':
            {'start':'00:00','end':'09:00','weekdays':['mon','tue','wed','thu','fri']},'projects':[]})
        controls=C.runtime_controls(f.root,f.baseline)
        original=F.unseal(f.directory/'cloud-review-plan.json');amended=dict(original,version=2,runtime_controls=controls,amendment_sha256='a'*64)
        with patch.object(F.Review,'plans',return_value=[original,amended]),patch.object(C,'review_plan',return_value=amended):
            self.assertEqual(f.run_day()['state'],'completed')
        self.assertEqual(len(f.cloud.calls),1)
        self.assertIsNone(controls['continuous_override']);self.assertEqual(controls['work_schedule']['schedule']['weekdays'],['mon','tue','wed','thu','fri'])

    def test_posthealth_gate_or_link_holds_without_spending(self):
        f=self.fixture;path=f.root/'runtime/WEEKDAY_ACTIVATION_PENDING'
        for linked in (False,True):
            if linked:path.symlink_to(f.root/'missing')
            else:path.write_text('pending')
            self.assertEqual(f.run_day()['state'],'held');path.unlink()
        self.assertEqual(f.cloud.calls,[])
        self.assertFalse((f.directory/'cloud/day-01/attempt.json').exists())


class PreviousSourceRegressionTests(unittest.TestCase):
    """Existing API behavior; selected old-source runs fail assertions, not imports."""
    def setUp(self):
        self.fixture=review_fixtures.PerformanceReviewTests('test_model_attribution_uses_receipt_profile_not_current_selection')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups);self.fixture.initialise()

    def test_distinct_glm_profiles_have_stable_report_cohorts(self):
        f=self.fixture
        f.accepted('original',f.start+10)
        f.accepted('changed',f.start+20,commit='e'*40,model=dict(f.model,parameters_sha256='f'*64))
        f.review.tick(f.start+F.DAY)
        report=f.report(1);cohorts=report.get('profile_cohorts')
        self.assertIsInstance(cohorts,dict,'Distinct profile identities must survive configuration changes')
        self.assertEqual(len(cohorts),2)

    def test_paused_sample_pair_has_explicit_exposure(self):
        f=self.fixture
        for offset in (0,60):
            F.write_once(f.directory/'samples'/f'{offset//60:05}.json',
                {'at':f.start+offset,'slot':offset//60,'active_available':True,'active':[],
                 'telemetry':{'available':False},'source':f.commit,
                 'controls':{'available':True,'scheduled_eligible':True,'global_paused':True,'installing':False,
                             'project_paused':{'secondapp':False,'exampleapp':False},'schedule_sha256':'a'*64}})
        value=f.review.resources(1,f.start,f.start+F.DAY).get('sampled_control_seconds')
        self.assertIsInstance(value,dict,'Observed owner pause must not become unlabelled idle time')
        self.assertEqual(value['exampleapp']['scheduled_paused'],60)

    def test_original_day_boundary_and_report_replay_remain_unchanged(self):
        f=self.fixture;f.accepted('one',f.start+10);f.review.tick(f.start+F.DAY)
        path=f.directory/'reports/day-01.json';before=path.read_bytes()
        f.review.tick(f.start+F.DAY+60)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(f.report(1)['end'],f.start+F.DAY)


if __name__=='__main__':unittest.main()
