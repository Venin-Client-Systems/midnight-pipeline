import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import guardian as G
import git_delivery as Delivery
from action_store import Actions


class GuardianTests(unittest.TestCase):
    def setUp(self):
        authority=mock.patch.object(G,'check_authority',return_value={'permission_sha256':'fixture'});authority.start();self.addCleanup(authority.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.runtime=self.root/'runtime';self.runtime.mkdir()
        self.instant=dt.datetime.now(dt.timezone.utc)
        self.project=self.root/'project';self.project.mkdir()
        self.save(self.root/'projects.json',{'timezone':'Australia/Adelaide','schedule':{'start':'00:00','end':'09:00'},
            'projects':[{'id':'notes','on_schedule':True,'state_dir':str(self.project)}]})
        self.save(self.runtime/'override.json',{'expires_at':(self.instant+dt.timedelta(hours=2)).isoformat()})
        self.save(self.runtime/'status.json',{'phase':'working','updated_at':self.instant.isoformat()})
        self.save(self.project/'status.json',{'phase':'needs_attention','detail':'Internal transition did not persist'})
        self.save(self.project/'state.json',{})

    def save(self,path,value):path.write_text(json.dumps(value))

    def test_pause_and_expiry_never_start_recovery_or_clear_controls(self):
        (self.runtime/'PAUSED').touch();restart=mock.Mock();spawn=mock.Mock()
        result=G.audit(self.root,instant=self.instant,restart=restart,spawn=spawn)
        self.assertEqual(result['state'],'paused');restart.assert_not_called();spawn.assert_not_called()
        self.assertTrue((self.runtime/'PAUSED').exists())
        self.assertEqual(Actions(self.runtime).incidents(),[])

    def test_fresh_controller_internal_error_is_not_hidden_by_its_timestamp(self):
        value=G.observation(self.root,self.instant);value['projects'][0]['status']={'phase':'quality_wait'}
        value['controller'].update(phase='needs_attention',detail='Cannot restore the owned dispatch transition')
        self.assertTrue(any(i[0]=='controller_error' for i in G.diagnoses(value,{},self.instant)))

    def test_expected_backoff_does_not_age_into_an_immediate_idle_restart(self):
        store=Actions(self.runtime);store.meta('guardian',{'idle_projects':{'notes':self.instant.timestamp()-999}},write=True)
        expiry=self.instant+dt.timedelta(minutes=15)
        self.save(self.project/'status.json',{'phase':'needs_attention','detail':'Git clone exited 128'})
        self.save(self.project/'state.json',{'cooldown_until':expiry.isoformat()})
        restart=mock.Mock();spawn=mock.Mock()
        with mock.patch.dict(G.POLICY,{'unexplained_block_seconds':30}):
            result=G.audit(self.root,instant=self.instant,restart=restart,spawn=spawn)
            self.assertEqual(result['issues'],0);self.assertNotIn('notes',result['idle_projects'])
            self.save(self.project/'status.json',{'phase':'waiting_for_supervisor_retry'})
            for seconds in (1,20):
                at=expiry+dt.timedelta(seconds=seconds);self.save(self.runtime/'status.json',{'phase':'working','updated_at':at.isoformat()})
                self.assertEqual(G.audit(self.root,instant=at,restart=restart,spawn=spawn)['issues'],0)
            restart.assert_not_called();spawn.assert_not_called()
            at=expiry+dt.timedelta(seconds=35);self.save(self.runtime/'status.json',{'phase':'working','updated_at':at.isoformat()})
            result=G.audit(self.root,instant=at,restart=restart,spawn=spawn)
        self.assertEqual(result['issues'],1);restart.assert_called_once();spawn.assert_not_called()

    def test_owned_action_without_phase_still_has_its_no_progress_deadline(self):
        value=G.observation(self.root,self.instant);value['projects'][0]['status']={'phase':'preparing_task'}
        value['jobs']=[{'id':'owned','project':'notes','state':'running','created':self.instant.timestamp()-121,'lease':{'fixture':True}}]
        with mock.patch.object(G.P,'alive',return_value=True):
            self.assertTrue(any(i[0]=='action_no_progress' for i in G.diagnoses(value,{},self.instant)))

    def test_bounded_preparation_is_not_an_action_without_progress(self):
        value=G.observation(self.root,self.instant);value['projects'][0]['status']={'phase':'preparing_task'}
        value['jobs']=[{'id':'clone-job','project':'notes','state':'running','created':self.instant.timestamp()-300,'lease':{'fixture':True}}]
        value['phases']=[{'id':'clone-phase','job':'clone-job','project':'notes','kind':'host','state':'running','deadline':self.instant.timestamp()+120}]
        with mock.patch.object(G.P,'alive',return_value=True):
            self.assertFalse(any(i[0]=='action_no_progress' for i in G.diagnoses(value,{},self.instant)))
        value['phases'][0]['deadline']=self.instant.timestamp()-1
        with mock.patch.object(G.P,'alive',return_value=True):
            self.assertTrue(any(i[0]=='phase_deadline' for i in G.diagnoses(value,{},self.instant)))

    def test_dashboard_recovery_does_not_start_a_worker_while_paused(self):
        value=G.observation(self.root,self.instant);value['paused']=True;value['dashboard']={'available':False}
        issues=G.diagnoses(value,{},self.instant)
        self.assertEqual([i[0] for i in issues],['dashboard_unresponsive'])

    def test_known_fault_gets_one_deterministic_recovery_then_four_charged_patches(self):
        spawn=mock.Mock();restart=mock.Mock();store=Actions(self.runtime)
        with mock.patch.dict(G.POLICY,{'unexplained_block_seconds':100000}),mock.patch.object(G.subprocess,'check_output',return_value='a'*40):
            G.audit(self.root,instant=self.instant,restart=restart,spawn=spawn)
            self.assertEqual(len(store.incidents()),1);self.assertEqual(len(store.repairs()),0)
            for index in range(4):
                at=self.instant+dt.timedelta(seconds=65*(index+1))
                self.save(self.runtime/'status.json',{'phase':'working','updated_at':at.isoformat()})
                G.audit(self.root,instant=at,restart=restart,spawn=spawn)
                repair=store.repairs()[0];self.assertEqual(repair['number'],index+1)
                store.repair(repair['id'],state='failed',payload={'reason':'focused regression failed'})
            at=self.instant+dt.timedelta(seconds=65*5)
            self.save(self.runtime/'status.json',{'phase':'working','updated_at':at.isoformat()})
            G.audit(self.root,instant=at,restart=restart,spawn=spawn)
        self.assertEqual(spawn.call_count,4);self.assertEqual(store.incidents()[0]['state'],'held')
        self.assertEqual(store.incidents()[0]['attempts'],4)

    def test_a_cloud_repair_does_not_reset_product_idle_timer(self):
        value={'paused':False,'active_window':True,'installing':False,'controller':{'updated_at':self.instant.isoformat()},
            'jobs':[],'phases':[{'id':'maintenance','job':'m','project':'midnight','state':'running','kind':'cloud','deadline':self.instant.timestamp()+1000}],
            'projects':[{'id':'notes','paused':False,'status':{'phase':'retry_or_next_task'},'state':{}}]}
        meta={'idle_projects':{'notes':self.instant.timestamp()-301}}
        issues=G.diagnoses(value,meta,self.instant)
        self.assertTrue(any(i[0]=='runnable_idle' for i in issues))

    def test_explained_manager_or_provider_wait_is_not_an_orchestrator_patch(self):
        value=G.observation(self.root,self.instant)
        for phase in ('manager_hold','quality_wait','waiting_for_provider'):
            value['projects'][0]['status']={'phase':phase}
            with self.subTest(phase=phase):
                self.assertEqual(G.diagnoses(value,{'idle_projects':{'notes':self.instant.timestamp()-9999}},self.instant),[])

    def test_one_idle_project_is_detected_while_another_uses_cloud(self):
        value=G.observation(self.root,self.instant)
        value['projects'][0]['status']={'phase':'retry_or_next_task'}
        value['projects'].append({'id':'inventory','paused':False,'status':{},'state':{}})
        value['jobs']=[{'id':'other','project':'inventory','state':'running','lease':{'pid':12},'created':self.instant.timestamp()}]
        value['phases']=[{'id':'phase','job':'other','project':'inventory','kind':'cloud','state':'running','deadline':self.instant.timestamp()+500}]
        with mock.patch.object(G.P,'alive',return_value=True):
            issues=G.diagnoses(value,{'idle_projects':{'notes':self.instant.timestamp()-121}},self.instant)
        self.assertTrue(any(i[0]=='runnable_idle' for i in issues))

    def health(self,state='verifying'):
        value={'previous':'old','installed':'new','repair':'repair-1','tree':'tree',
            'at':self.instant.timestamp()-200,'state':state,'controller_sha256':'new-code','dashboard_sha256':'new-dashboard'}
        Actions(self.runtime).meta('install_health',value,write=True);return value

    def test_rollback_intent_recovers_after_source_was_already_restored(self):
        _,_,value=self.owned_health_fence('rolling_back')
        def git(root,*args):return 'old' if args==('rev-parse','HEAD') else ''
        with mock.patch.object(Delivery,'git',side_effect=git) as commands,mock.patch.object(G,'restart_controller',return_value=True), \
             mock.patch.object(G.subprocess,'run',return_value=mock.Mock(returncode=0)),mock.patch.object(G,'restored_generation_proof',return_value={'source':'old'}),mock.patch.object(G.P,'stop_tree',return_value=False):
            G.installation_health(self.root,value,self.instant)
        health=Actions(self.runtime).meta('install_health');self.assertEqual(health['state'],'rolled_back');self.assertTrue(health['finalized'])
        self.assertFalse((self.runtime/'INSTALLING').exists())
        self.assertFalse(any(c.args[1:3]==('reset','--hard') for c in commands.call_args_list))

    def test_old_loaded_code_cannot_verify_a_fresh_install(self):
        self.health();value=G.observation(self.root,self.instant)
        value['controller'].update(code_sha256='old-code',process={'pid':123})
        def git(root,*args):return 'new' if args==('rev-parse','HEAD') else ''
        with mock.patch.object(G.P,'alive',return_value=True),mock.patch.object(G,'dashboard_health',return_value=True), \
             mock.patch.object(Delivery,'git',side_effect=git),mock.patch.object(G,'restart_controller',return_value=True), \
             mock.patch.object(G.subprocess,'run',return_value=mock.Mock(returncode=0)),mock.patch.object(G,'restored_generation_proof',return_value={'source':'old'}),mock.patch.object(G.P,'stop_tree',return_value=False):
            G.installation_health(self.root,value,self.instant)
        self.assertEqual(Actions(self.runtime).meta('install_health')['state'],'rolled_back')

    def test_exact_loaded_generations_and_live_leases_verify_health(self):
        self.health();value=G.observation(self.root,self.instant)
        value['controller'].update(code_sha256='new-code',process={'pid':123})
        with mock.patch.object(G.P,'alive',return_value=True),mock.patch.object(G,'dashboard_health',return_value=True), \
             mock.patch.object(Delivery,'git') as git:
            G.installation_health(self.root,value,self.instant)
        self.assertEqual(Actions(self.runtime).meta('install_health')['state'],'verified');git.assert_not_called()

    def test_recovered_health_clears_only_its_retained_rollback_drain_fence(self):
        self.health();store=Actions(self.runtime);value=G.observation(self.root,self.instant)
        job=store.submit('notes',{},self.instant.timestamp()+100);store.claim(job['id'],{'pid':123})
        with mock.patch.object(G.P,'alive',return_value=True),mock.patch.object(G,'dashboard_health',return_value=False),mock.patch.object(Delivery,'git',side_effect=lambda _r,*a:'new' if a==('rev-parse','HEAD') else ''),mock.patch.object(G.os,'kill'):
            G.installation_health(self.root,value,self.instant)
        self.assertTrue((self.runtime/'INSTALLING').exists());self.assertIn('rollback_fence',store.meta('install_health'))
        value['controller'].update(code_sha256='new-code',process={'pid':123})
        with mock.patch.object(G.P,'alive',return_value=True),mock.patch.object(G,'dashboard_health',return_value=True),mock.patch.object(Delivery,'git',return_value='new'):
            G.installation_health(self.root,value,self.instant)
        self.assertEqual(store.meta('install_health')['state'],'verified');self.assertFalse((self.runtime/'INSTALLING').exists())
        self.assertNotIn('rollback_fence',store.meta('install_health'))

    def test_healthy_install_preserves_a_fence_it_did_not_create(self):
        self.health();G.D.marker(self.runtime/'INSTALLING',True);value=G.observation(self.root,self.instant)
        value['controller'].update(code_sha256='new-code',process={'pid':123})
        with mock.patch.object(G.P,'alive',return_value=True),mock.patch.object(G,'dashboard_health',return_value=True):G.installation_health(self.root,value,self.instant)
        self.assertEqual(Actions(self.runtime).meta('install_health')['state'],'verified');self.assertTrue((self.runtime/'INSTALLING').exists())

    def owned_health_fence(self,state='verifying'):
        self.health(state);store=Actions(self.runtime);health=store.meta('install_health')
        health['rollback_fence']={'owner':'guardian-install-health','generation':'a'*32,'installed':health['installed'],'repair':health['repair']}
        store.meta('install_health',health,write=True)
        G.D.atomic_write(self.runtime/'INSTALLING',(json.dumps(health['rollback_fence'],sort_keys=True)+'\n').encode())
        value=G.observation(self.root,self.instant);value['controller'].update(code_sha256='new-code',process={'pid':123})
        return store,health,value

    def test_replaced_fence_with_same_source_is_preserved(self):
        store,_,value=self.owned_health_fence();G.D.marker(self.runtime/'INSTALLING',False);G.D.marker(self.runtime/'INSTALLING',True)
        with mock.patch.object(G.P,'alive',return_value=True),mock.patch.object(G,'dashboard_health',return_value=True),mock.patch.object(Delivery,'git',return_value='new'):G.installation_health(self.root,value,self.instant)
        self.assertEqual((self.runtime/'INSTALLING').read_bytes(),b'{}\n');self.assertEqual(store.meta('install_health')['state'],'verified')
        self.assertNotIn('rollback_fence_cleared_at',store.meta('install_health'))

    def test_crash_after_fence_intent_cannot_claim_a_later_marker(self):
        store,health,value=self.owned_health_fence();G.D.marker(self.runtime/'INSTALLING',False)
        # Ownership intent survives, but this marker comes from another installer.
        G.D.atomic_write(self.runtime/'INSTALLING',b'{"owner":"other-installer"}\n')
        with mock.patch.object(G.P,'alive',return_value=True),mock.patch.object(G,'dashboard_health',return_value=True),mock.patch.object(Delivery,'git',return_value='new'):G.installation_health(self.root,value,self.instant)
        self.assertEqual((self.runtime/'INSTALLING').read_bytes(),b'{"owner":"other-installer"}\n');self.assertEqual(store.meta('install_health')['state'],'verified')

    def test_changed_install_health_generation_cannot_clear_the_old_marker(self):
        store,health,value=self.owned_health_fence();original=Actions.meta;calls=[]
        def meta(target,key,*args,**kwargs):
            if key=='install_health' and not kwargs.get('write'):
                calls.append(key)
                if len(calls)==2:
                    newer=dict(health,repair='a-later-install');original(target,key,newer,write=True)
            return original(target,key,*args,**kwargs)
        with mock.patch.object(Actions,'meta',side_effect=meta,autospec=True),mock.patch.object(G.P,'alive',return_value=True),mock.patch.object(G,'dashboard_health',return_value=True):G.installation_health(self.root,value,self.instant)
        self.assertTrue((self.runtime/'INSTALLING').exists());self.assertEqual(store.meta('install_health')['repair'],'a-later-install');self.assertEqual(store.meta('install_health')['state'],'verifying')

    def test_failed_rollback_restarts_hold_without_stopping_healthy_jobs(self):
        _,_,value=self.owned_health_fence('rolling_back')
        def git(root,*args):return 'old' if args==('rev-parse','HEAD') else ''
        with mock.patch.object(Delivery,'git',side_effect=git),mock.patch.object(G,'restart_controller',return_value=False),mock.patch.object(G,'restart_dashboard',return_value=False):
            G.installation_health(self.root,value,self.instant)
        store=Actions(self.runtime);self.assertFalse(store.meta('install_health')['finalized']);self.assertTrue((self.runtime/'INSTALLING').exists())
        intents=store.meta('install_health')['rollback_restart_intents'];fence=(self.runtime/'INSTALLING').read_bytes()
        job=store.submit('notes',{},self.instant.timestamp()+100);store.claim(job['id'],G.P.lease())
        with mock.patch.object(Delivery,'git',side_effect=git) as commands,mock.patch.object(G,'restart_controller',return_value=True) as controller,mock.patch.object(G,'restart_dashboard',return_value=True) as dashboard,mock.patch.object(G,'restored_generation_proof',return_value={'source':'old'}):
            G.installation_health(self.root,value,self.instant)
        controller.assert_not_called();dashboard.assert_not_called()
        self.assertFalse(store.meta('install_health')['finalized']);self.assertEqual(store.get(job['id'])['state'],'running')
        self.assertEqual(store.meta('install_health')['rollback_restart_intents'],intents)
        self.assertEqual(store.meta('install_health')['rollback_waiting'],'uncertain_restart_controller')
        self.assertEqual((self.runtime/'INSTALLING').read_bytes(),fence)
        self.assertFalse(any(c.args[1:3]==('reset','--hard') for c in commands.call_args_list))

    def test_controller_restart_success_survives_dashboard_timeout(self):
        _,_,value=self.owned_health_fence('rolled_back')
        def git(root,*args):return 'old' if args==('rev-parse','HEAD') else ''
        with mock.patch.object(Delivery,'git',side_effect=git),mock.patch.object(G,'restart_controller',return_value=True), \
             mock.patch.object(G,'restart_dashboard',side_effect=__import__('subprocess').TimeoutExpired(['launchctl'],10)):
            G.installation_health(self.root,value,self.instant)
        state=Actions(self.runtime).meta('install_health')
        self.assertTrue(state['restart_requests']['controller']);self.assertFalse(state['finalized'])
        self.assertTrue((self.runtime/'INSTALLING').exists())
        fence=(self.runtime/'INSTALLING').read_bytes()
        with mock.patch.object(Delivery,'git',side_effect=git),mock.patch.object(G,'restart_controller') as controller, \
             mock.patch.object(G,'restart_dashboard',return_value=True) as dashboard,mock.patch.object(G,'restored_generation_proof',return_value={'source':'old'}):G.installation_health(self.root,value,self.instant)
        controller.assert_not_called();dashboard.assert_not_called();after=Actions(self.runtime).meta('install_health')
        self.assertFalse(after['finalized']);self.assertTrue(after['restart_requests']['controller'])
        self.assertEqual(after['rollback_restart_intents'],state['rollback_restart_intents'])
        self.assertEqual(after['rollback_waiting'],'uncertain_restart_dashboard');self.assertEqual((self.runtime/'INSTALLING').read_bytes(),fence)


if __name__=='__main__':unittest.main()
