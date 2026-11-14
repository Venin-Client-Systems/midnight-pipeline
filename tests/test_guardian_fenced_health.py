"""Weekend installation health is proof-bound, never a coding-window grant."""
import contextlib
import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import guardian as G
import git_delivery as Delivery
import maintenance_recovery as Maintenance
from action_store import Actions


class FencedInstallationHealthTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.runtime=self.root/'runtime';self.runtime.mkdir()
        self.instant=dt.datetime(2026,11,7,10,tzinfo=dt.timezone.utc)
        self.previous='a'*40;self.installed='b'*40;self.tree='c'*40
        (self.root/'midnight.py').write_text('# installed controller\n')
        (self.root/'dashboard.py').write_text('# installed dashboard\n')
        (self.root/'guardian.py').write_bytes(Path(G.__file__).read_bytes())
        self.fence={'owner':'reviewed-installer','generation':'d'*32,'source':self.previous}
        self.gate={'version':1,'owner':self.fence['owner'],'generation':self.fence['generation'],
                   'base':self.previous,'candidate_tree':self.tree,'plan_sha256':'e'*64}
        self.save(self.runtime/'INSTALLING',self.fence);self.save(self.runtime/'WEEKDAY_ACTIVATION_PENDING',self.gate)
        self.health={'previous':self.previous,'installed':self.installed,'tree':self.tree,'repair':'checked-release',
                     'state':'verifying','at':self.instant.timestamp()-200,'restart_epoch':'f'*32,
                     'activation_gate':{'sha256':hashlib.sha256((self.runtime/'WEEKDAY_ACTIVATION_PENDING').read_bytes()).hexdigest(),
                                        'plan_sha256':self.gate['plan_sha256']},
                     'installation_fence_sha256':hashlib.sha256((self.runtime/'INSTALLING').read_bytes()).hexdigest(),
                     'controller_sha256':hashlib.sha256((self.root/'midnight.py').read_bytes()).hexdigest(),
                     'dashboard_sha256':hashlib.sha256((self.root/'dashboard.py').read_bytes()).hexdigest(),
                     'restart_requirements':{name:{'epoch':'f'*32,'not_before':self.instant.timestamp()-200}
                         for name in ('space.venin.midnight','space.venin.midnight-dashboard')}}
        self.store=Actions(self.runtime);self.store.meta('install_health',self.health,write=True)
        self.controller={'phase':'installing','updated_at':self.instant.isoformat(),
                         'code_sha256':self.health['controller_sha256'],
                         'process':{'pid':123,'birth_us':int((self.instant.timestamp()-100)*1000000)}}
        self.save(self.runtime/'status.json',self.controller)
        project=self.runtime/'projects/fixture';project.mkdir(parents=True)
        self.save(self.root/'projects.json',{'timezone':'Australia/Adelaide',
            'schedule':{'start':'00:00','end':'09:00','weekdays':['mon','tue','wed','thu','fri']},
            'projects':[{'id':'fixture','on_schedule':True,'state_dir':str(project)}]})
        self.value=G.observation(self.root,self.instant)
        self.assertFalse(self.value['active_window'])
        stack=contextlib.ExitStack();self.addCleanup(stack.close)
        self.git=stack.enter_context(mock.patch.object(Delivery,'git',side_effect=self.git_result))
        stack.enter_context(mock.patch.object(G.P,'alive',return_value=True))
        stack.enter_context(mock.patch.object(G,'dashboard_health',return_value=True))

    @staticmethod
    def save(path,value):path.write_text(json.dumps(value,sort_keys=True)+'\n')

    def git_result(self,root,*args):
        return {('rev-parse','HEAD'):self.installed,('rev-parse','HEAD^{tree}'):self.tree,
                ('status','--porcelain'):''}[args]

    def check(self):G.installation_health(self.root,self.value,self.instant)

    def assertHeld(self):self.assertEqual(self.store.meta('install_health')['state'],'verifying')

    def test_exact_weekend_generation_verifies_without_releasing_either_fence(self):
        before={name:(self.runtime/name).read_bytes() for name in ('INSTALLING','WEEKDAY_ACTIVATION_PENDING')}
        self.check()
        health=self.store.meta('install_health');self.assertEqual(health['state'],'verified')
        self.assertEqual(health['verified_restart_epoch'],self.health['restart_epoch'])
        self.assertEqual(health['verified_controller_process'],self.controller['process'])
        self.assertEqual(before,{name:(self.runtime/name).read_bytes() for name in before})
        self.assertFalse(G.observation(self.root,self.instant)['active_window'])
        self.assertEqual(self.store.jobs(),[]);self.assertEqual(self.store.phases(),[])

    def test_weekend_audit_runs_health_only_without_repair_or_bundle_publication(self):
        with mock.patch.object(G,'repair_running') as repair, mock.patch.object(G,'launch_repair') as launch, \
             mock.patch.object(Maintenance,'publish_generation') as publish:
            result=G.audit(self.root,instant=self.instant,restart=mock.Mock(),spawn=mock.Mock())
        self.assertEqual(self.store.meta('install_health')['state'],'verified')
        self.assertEqual(result['state'],'outside_window')
        repair.assert_not_called();launch.assert_not_called();publish.assert_not_called()
        self.assertEqual(self.store.incidents(),[])

    def test_owner_pause_blocks_exact_health(self):
        (self.runtime/'PAUSED').touch()
        self.check();self.assertHeld()

    def test_missing_replaced_and_symlink_fences_cannot_verify(self):
        for name in ('INSTALLING','WEEKDAY_ACTIVATION_PENDING'):
            path=self.runtime/name;original=path.read_bytes()
            with self.subTest(name=name,state='absent'):
                path.unlink();self.check();self.assertHeld()
            with self.subTest(name=name,state='foreign'):
                path.write_text('{"owner":"other"}');self.check();self.assertHeld()
            with self.subTest(name=name,state='symlink'):
                path.unlink();target=self.runtime/(name+'.saved');target.write_bytes(original)
                path.symlink_to(target);self.check();self.assertHeld();path.unlink()
            path.write_bytes(original)

    def test_activation_generation_source_and_tree_must_match_installer(self):
        path=self.runtime/'WEEKDAY_ACTIVATION_PENDING'
        for key in ('owner','generation','base','candidate_tree','plan_sha256'):
            with self.subTest(key=key):
                self.save(path,{**self.gate,key:'wrong'});self.check();self.assertHeld()
        self.save(path,self.gate)

    def test_source_or_loaded_generation_mismatch_cannot_verify_or_rollback(self):
        for args,value in [(('rev-parse','HEAD'),'0'*40),(('rev-parse','HEAD^{tree}'),'0'*40),
                           (('status','--porcelain'),' M midnight.py')]:
            with self.subTest(args=args):
                self.git.side_effect=lambda root,*actual,wrong=args,result=value:result if actual==wrong else self.git_result(root,*actual)
                self.check();self.assertHeld()
        self.git.side_effect=self.git_result
        for name in ('midnight.py','dashboard.py','guardian.py'):
            path=self.root/name;original=path.read_bytes()
            with self.subTest(file=name):
                path.write_bytes(original+b'# changed\n');self.check();self.assertHeld()
            path.write_bytes(original)
        self.value['controller']['code_sha256']='stale';self.check();self.assertHeld()
        self.assertFalse(any(call.args[1:3]==('reset','--hard') for call in self.git.call_args_list))

    def test_unhealthy_dashboard_remains_fenced_without_rollback_or_work(self):
        with mock.patch.object(G,'dashboard_health',return_value=False),mock.patch.object(G,'finalize_rollback') as rollback:
            self.check()
        self.assertHeld();rollback.assert_not_called()
        self.assertTrue((self.runtime/'INSTALLING').exists())
        self.assertFalse(any(call.args[1:3]==('reset','--hard') for call in self.git.call_args_list))

    def test_fence_replacement_before_health_commit_is_rechecked_under_source_lock(self):
        @contextlib.contextmanager
        def changed_lock(*args,**kwargs):
            self.save(self.runtime/'WEEKDAY_ACTIVATION_PENDING',{**self.gate,'generation':'0'*32})
            yield
        with mock.patch.object(G.P,'source_lock',side_effect=changed_lock):self.check()
        self.assertHeld()

    def test_weekday_missing_gate_cannot_verify(self):
        self.value['active_window']=True
        (self.runtime/'WEEKDAY_ACTIVATION_PENDING').unlink()
        self.check();self.assertHeld()

    def test_weekday_replaced_gate_cannot_verify(self):
        self.value['active_window']=True
        self.save(self.runtime/'WEEKDAY_ACTIVATION_PENDING',{**self.gate,'generation':'0'*32})
        self.check();self.assertHeld()

    def test_valid_but_wrong_plan_hash_is_rejected_even_with_matching_gate_digest(self):
        self.value['active_window']=True
        path=self.runtime/'WEEKDAY_ACTIVATION_PENDING'
        self.save(path,{**self.gate,'plan_sha256':'0'*64})
        # Independent expected plan binding must be checked, not merely syntax
        # or a byte digest which can consistently identify the wrong plan.
        self.health['activation_gate']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        self.store.meta('install_health',self.health,write=True)
        self.check();self.assertHeld()

    def test_weekday_valid_owned_gate_verifies_without_releasing_fences(self):
        self.value['active_window']=True
        self.check();self.assertEqual(self.store.meta('install_health')['state'],'verified')
        self.assertTrue((self.runtime/'INSTALLING').exists());self.assertTrue((self.runtime/'WEEKDAY_ACTIVATION_PENDING').exists())

    def test_weekday_gate_replacement_under_source_lock_prevents_health_commit(self):
        self.value['active_window']=True
        @contextlib.contextmanager
        def changed_lock(*args,**kwargs):
            (self.runtime/'WEEKDAY_ACTIVATION_PENDING').unlink()
            yield
        with mock.patch.object(G.P,'source_lock',side_effect=changed_lock):self.check()
        self.assertHeld()


if __name__=='__main__':unittest.main()
