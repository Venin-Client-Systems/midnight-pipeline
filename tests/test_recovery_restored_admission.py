"""Verified rollback restores admission without reinstalling rejected code."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest import mock
import coordinator_install as C
import core_repair as R
import maintenance_authority as A
import maintenance_recovery as M
import project_action as PA
import test_recovery_rollback_generation as F


class RestoredAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.f=F.RollbackGenerationTests();self.f.setUp();self.addCleanup(self.f.doCleanups);f=self.f
        f.store.meta('maintenance_recovery',{'version':2,'installed':f.base},write=True)
        M.save(f.runtime/'resilience/manifest.json',{'source':f.base})
        patch=mock.patch.object(A,'check',return_value={'permission_sha256':'f'*64});patch.start();self.addCleanup(patch.stop)
        self.incident=f.store.incident('midnight','controller_error','failed-install','actual-proof',{'detail':'fixture failed installation'})
        self.manager=M.MaintenanceRecovery(f.root,self.incident)
        self.original=[]
        for _ in range(4):
            identity=f.store.reserve_repair(self.incident,{'source':f.base});f.store.repair(identity,state='failed',payload={'reason':'original spent fixture'})
        self.original=copy.deepcopy(f.store.repairs(active=False));scope,_,capsule=self.manager.observe(f.store.get_incident(self.incident),f.base,{'model':'fixture'},{'source_files':['midnight.py']})
        self.session=self.manager.create('diagnosis',scope,{'model':'fixture'},capsule);directory=f.runtime/'maintenance'/self.session;directory.mkdir(parents=True)
        M.save(directory/'delivery.json',{'status':'merged','commit':f.bad})
        M.save(directory/'install-intent.json',{'previous':f.base,'installed':f.bad,'tree':f.git('rev-parse','HEAD^{tree}')})
        dead={'pid':99999999,'stamp':'dead failed installer','boot_id':R.D.boot_id(),'birth_us':1};self.manager.update(self.session,'paused',lease=dead)
        job=f.store.submit('midnight',{'continuation':self.session},time.time()+300);f.store.claim(job['id'],dead);f.store.charge(job['id'],'original-local-fixture-charge','maintenance_local_supervision')
        self.job=job;self.assertFalse(M.enabled(f.root))

    def assert_restored(self):
        f=self.f;health=f.store.meta('install_health');self.assertEqual(health['state'],'verified');self.assertEqual(health['installed'],f.base)
        self.assertTrue(health['restoration_complete']);self.assertTrue(M.enabled(f.root));self.assertFalse((f.runtime/'INSTALLING').exists())
        rejected=f.store.meta('rejected_installations');self.assertEqual(rejected[f.bad]['state'],'rolled_back');self.assertEqual(rejected[f.bad]['installed'],f.bad)
        self.assertEqual(rejected[f.bad]['restored_generation_proof']['source'],f.base)
        self.assertEqual(self.manager.session(self.session)['state'],'retired');self.assertEqual(f.store.get(self.job['id'])['state'],'interrupted')
        self.assertEqual(f.store.repairs(active=False),self.original)
        with f.store.read() as con:self.assertEqual(con.execute('SELECT count(*) FROM wf_charges').fetchone()[0],1)

    def test_fresh_native_rollback_proof_restores_current_admission_and_retires_failed_work(self):
        self.f.evaluate(self.f.controller_restart,self.f.dashboard_restart);self.assert_restored()

    def test_failed_restored_generation_proof_cannot_enable_maintenance(self):
        state=self.f.evaluate(lambda:False,lambda:False)
        self.assertEqual(state['state'],'rolled_back');self.assertFalse(M.enabled(self.f.root));self.assertTrue((self.f.runtime/'INSTALLING').exists())
        self.assertIsNone(self.f.store.meta('rejected_installations'));self.assertEqual(self.manager.session(self.session)['state'],'paused')

    def test_transition_after_commit_before_fence_release_resumes_from_current_guardian(self):
        marker=R.D.marker
        def interrupted(path,present):
            if not present:raise OSError('restored health committed before fence removal')
            return marker(path,present)
        with mock.patch.object(R.D,'marker',side_effect=interrupted):
            with self.assertRaisesRegex(OSError,'restored health committed'):self.f.evaluate(self.f.controller_restart,self.f.dashboard_restart)
        health=self.f.store.meta('install_health');self.assertEqual(health['state'],'verified');self.assertFalse(M.enabled(self.f.root))
        with mock.patch.object(R.GU,'window',return_value=True),mock.patch.object(R.GU.urllib.request,'urlopen',side_effect=self.f.response):M.publish_generation(self.f.root,health)
        self.assert_restored()

    def test_restored_admission_waits_for_failed_installation_ownership_to_drain(self):
        f=self.f
        with mock.patch.object(PA,'drain',return_value=False):state=f.evaluate(f.controller_restart,f.dashboard_restart)
        self.assertEqual(state['state'],'rolled_back');self.assertFalse(state['finalized']);self.assertFalse(M.enabled(f.root));self.assertTrue((f.runtime/'INSTALLING').exists())
        controller=mock.Mock(side_effect=AssertionError('confirmed rollback restart must not repeat'));dashboard=mock.Mock(side_effect=AssertionError('confirmed rollback restart must not repeat'))
        f.evaluate(controller,dashboard);controller.assert_not_called();dashboard.assert_not_called();self.assert_restored()

    def test_rejected_generation_cannot_be_readopted_after_current_health_is_restored(self):
        f=self.f;f.evaluate(f.controller_restart,f.dashboard_restart);health=f.store.meta('install_health');tree=health['rollback_record']['tree']
        with mock.patch.object(R,'permitted',return_value=True),mock.patch.object(R,'fetch',side_effect=AssertionError('Rejected source must not fetch or re-enter installation')):
            with self.assertRaisesRegex(ValueError,'Rejected installed'):R.install(f.root,{'status':'merged','commit':f.bad},f.base,tree,f.runtime/'failed-replay',time.time()+30)
            with self.assertRaisesRegex(ValueError,'Rejected installed'):R._install(f.root,{'status':'merged','commit':f.bad},f.base,tree,f.runtime/'failed-replay',time.time()+30)
        with mock.patch.object(C,'ROOT',f.root),mock.patch.object(C,'HERE',f.runtime/'coordinator-replay'):
            with self.assertRaisesRegex(RuntimeError,'Rejected generation'):C.adopt(f.base,f.bad,tree,{},lambda:True)
        self.assertEqual(f.git('rev-parse','HEAD'),f.base);self.assertFalse((f.runtime/'INSTALLING').exists())

    def test_prior_frozen_source_is_republished_only_for_the_proven_restored_commit(self):
        f=self.f;M.save(f.runtime/'resilience/manifest.json',{'source':f.bad});calls=[]
        def install(root,**kwargs):
            self.assertEqual(f.git('rev-parse','HEAD'),f.base);self.assertFalse((f.runtime/'INSTALLING').exists());self.assertEqual(kwargs,{'load':False,'expected_permission_sha256':'f'*64})
            calls.append(True);value={'source':f.base};M.save(f.runtime/'resilience/manifest.json',value);return value
        with mock.patch('install_guardian.install',side_effect=install):f.evaluate(f.controller_restart,f.dashboard_restart)
        self.assertEqual(calls,[True]);self.assert_restored()


class AdmissionDependencyTests(unittest.TestCase):
    def test_transitive_task_and_microstep_state_cannot_enter_automatic_patches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for dependency in ('active_state.py','micro_recovery.py'):
                with self.subTest(dependency=dependency),mock.patch.object(R.G,'git',return_value=dependency+'\ntests/test_recovery_attempt.py'):
                    self.assertIn(dependency,M.KERNEL);self.assertNotIn(dependency,R.POLICY['source_files'])
                    with self.assertRaisesRegex(ValueError,'Protected maintenance path'):R.allowed_patch(root,'base')

    def test_transitive_state_changes_cannot_publish_a_new_frozen_bundle(self):
        actual=Path(M.__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=R.Actions(root/'runtime');store.meta('maintenance_recovery',{'version':2},write=True)
            M.save(root/'runtime/resilience/manifest.json',{'source':'old'})
            for dependency in ('active_state.py','micro_recovery.py'):
                for name in M.KERNEL:
                    target=root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(actual/name,target)
                (root/dependency).write_text('# changed admission dependency\n')
                with self.subTest(dependency=dependency),mock.patch.object(R.G,'git',side_effect=lambda _root,*args:'' if args[0]=='status' else 'new'):
                    with self.assertRaisesRegex(ValueError,dependency):M.publish_generation(root,{'state':'verified','installed':'new'})


if __name__=='__main__':unittest.main()
