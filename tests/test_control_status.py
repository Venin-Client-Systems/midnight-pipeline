import datetime as dt
import unittest
from control_status import project_control


class ControlStatusTests(unittest.TestCase):
    def setUp(self):self.now=dt.datetime(2026,11,1,tzinfo=dt.timezone.utc)

    def test_guided_transition_error_names_blocker_and_recheck(self):
        status={'phase':'needs_attention','detail':'Two failed local attempts require a separately journaled guided successor'}
        result=project_control(status,{'cooldown_until':'2026-11-01T00:15:00+00:00'},instant=self.now)
        self.assertEqual(result['code'],'guided_retry_blocked')
        self.assertEqual(result['next_retry_at'],'2026-11-01T00:15:00+00:00')
        self.assertEqual(result['recovery_owner'],'codex')

    def test_planner_log_reference_is_evidence_blocker_and_expired_timer_is_not_future(self):
        state={'cooldown_until':'2026-10-31T23:00:00+00:00','planning_wait':{
            'until':'2026-11-01T01:00:00+00:00','reason':'No available tool can read the failure log.'}}
        result=project_control({'phase':'quality_wait'},state,instant=self.now)
        self.assertEqual(result['code'],'missing_failure_evidence')
        self.assertEqual(result['next_retry_at'],'2026-11-01T01:00:00+00:00')

    def test_verified_active_process_has_precedence_over_old_wait(self):
        result=project_control({'phase':'running'},{'planning_wait':{'until':'2026-11-01T01:00:00+00:00'}},
                               instant=self.now,process_running=True,activity='local_worker')
        self.assertEqual(result['stage'],'Coding');self.assertEqual(result['state'],'active')

    def test_owner_pause_is_not_automatic_recovery(self):
        result=project_control({}, {},paused=True)
        self.assertEqual(result['code'],'owner_pause');self.assertEqual(result['recovery_owner'],'owner')

    def test_profile_missing_proof_is_a_visible_automatic_blocker(self):
        value=project_control({'phase':'profile_needs_manager','detail':'No available tool proves required test execution'}, {},instant=self.now)
        self.assertEqual(value['state'],'blocked');self.assertEqual(value['code'],'profile_evidence_missing')
        self.assertEqual(value['recovery_owner'],'automatic')

    def test_durable_resource_wait_remains_explained_without_heartbeat_progress(self):
        status={'phase':'waiting_for_resource','updated_at':(self.now-dt.timedelta(minutes=8)).isoformat(),
            'control':{'state':'waiting','code':'resource_host','explanation':'Waiting for the host check slot'}}
        phase={'state':'waiting','deadline':self.now.timestamp()+60}
        result=project_control(status,{},instant=self.now,supervisor_running=True,owned_phase=phase)
        self.assertEqual(result['state'],'waiting');self.assertEqual(result['code'],'resource_host')
        phase['deadline']=self.now.timestamp()-1
        self.assertEqual(project_control(status,{},instant=self.now,supervisor_running=True,owned_phase=phase)['code'],'phase_deadline')

    def test_stale_preparation_without_verified_supervisor_cannot_claim_activity(self):
        result=project_control({'phase':'preparing_task','updated_at':'2026-10-29T00:00:00+00:00'},
                               {},instant=self.now)
        self.assertEqual(result['state'],'blocked');self.assertEqual(result['code'],'stale_preparation')

    def test_fresh_verified_preparation_takes_precedence_over_old_planner_hold(self):
        result=project_control({'phase':'preparing_task','updated_at':self.now.isoformat()},
            {'planning_wait':{'until':'2026-11-01T01:00:00+00:00','reason':'Old unrelated hold'}},
            instant=self.now,supervisor_running=True)
        self.assertEqual(result['state'],'active');self.assertEqual(result['stage'],'Preparing workspace')

    def test_feature_manager_hold_is_not_mislabelled_as_idle(self):
        state={'quality_decision':{'action':'needs_manager','task':{'id':'feature'}},
               'issue_episodes':{'feature':{'episode':'feature-episode'}},
               'rescue_decisions':{'feature-episode':{'decision':'hold','summary':'Current scope needs evidence.'}}}
        result=project_control({'phase':'quality_wait'},state,instant=self.now)
        self.assertEqual(result['code'],'manager_hold');self.assertEqual(result['state'],'blocked')


if __name__=='__main__':unittest.main()
