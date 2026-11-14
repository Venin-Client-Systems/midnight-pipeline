"""Current reviewed authority remains bound through long installation recovery."""
import copy
import shutil
import subprocess
from pathlib import Path
import unittest
from unittest import mock
import coordinator_install as C
import install_guardian as I
import maintenance_authority as A
import maintenance_recovery as M
import test_recovery_coordinator_serialization as F


class AuthorityBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=F.CoordinatorSerializationTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.f=self.fixture;self.changed=False
        self.approved={'fixed':'approved bindings'}
        def authority(*args):return {'fixed':'changed policy or permissions'} if self.changed else dict(self.approved)
        patch=mock.patch.object(C,'approved_authority',side_effect=authority);patch.start();self.addCleanup(patch.stop)
        patch=mock.patch.object(C.A,'check',return_value={'automation':'AUT-0004'});patch.start();self.addCleanup(patch.stop)

    def finish(self,**kwargs):
        return C.finish_installation(self.f.intent['bindings'],self.f.expected,
            lambda:C.authority_current(self.f.intent['bindings']),**self.f.options,**kwargs)

    def drift(self,stage):
        before=self.f.git('rev-parse','HEAD');activation=self.f.options['activate']
        def checkpoint(actual):
            if actual==stage:self.changed=True
        if stage=='activation':
            def activate():
                value=activation();self.changed=True;return value
            self.f.options['activate']=activate
        with self.assertRaisesRegex(RuntimeError,'authority changed'):
            self.finish(checkpoint=checkpoint)
        self.assertEqual(self.f.git('rev-parse','HEAD'),before)
        self.assertEqual((self.f.root/'runtime/INSTALLING').read_bytes(),self.f.expected)
        self.assertFalse((self.f.root/'runtime/PAUSED').exists())
        self.assertFalse((self.f.directory/'installation-complete.json').exists())
        if stage!='activation':self.assertEqual(self.f.calls['activate'],0)

    def test_policy_or_permissions_change_after_bundle_publication_blocks_health(self):self.drift('bundle_published_before_health')
    def test_policy_or_permissions_change_during_restarts_blocks_activation(self):self.drift('service_restarted')
    def test_policy_or_permissions_change_after_restart_intent_prevents_effect(self):
        self.drift('service_restart_intent_recorded');self.assertEqual(self.f.calls['restart'],[])
    def test_policy_or_permissions_change_after_activation_blocks_release(self):self.drift('activation')

    def test_changed_authority_blocks_adoption_with_unchanged_source_and_pauses(self):
        self.changed=True;before=self.f.git('rev-parse','HEAD')
        with self.assertRaisesRegex(RuntimeError,'authority changed'):
            C.adopt(self.f.intent['previous'],self.f.intent['installed'],self.f.intent['tree'],self.f.intent['bindings'],lambda:C.authority_current(self.f.intent['bindings']))
        self.assertEqual(self.f.git('rev-parse','HEAD'),before);self.assertEqual((self.f.root/'runtime/INSTALLING').read_bytes(),self.f.expected)

    def test_expected_permission_digest_cannot_be_resealed_from_a_matching_phrase(self):
        original={'Automations':{'K5':'progress-based orchestrator recovery approved'}};changed=copy.deepcopy(original);changed['Automations']['K5']+=' changed authority'
        path=self.f.root/'runtime/resilience/authority.json';M.save(path,{'automation':'AUT-0004','permission_sha256':A.digest(original),'schema_version':2});before=path.read_bytes()
        with mock.patch.object(A,'permissions',return_value=changed):
            with self.assertRaisesRegex(ValueError,'exact reviewed'):A.seal(self.f.root,expected_permission_sha256=A.digest(original))
            with self.assertRaisesRegex(ValueError,'exact reviewed'):A.seal(self.f.root)
        self.assertEqual(path.read_bytes(),before)

    def bundle_fixture(self):
        root=self.f.root/'disposable-bundle';root.mkdir();actual=Path(I.__file__).resolve().parent
        for name in subprocess.check_output(['/usr/bin/git','ls-files'],cwd=actual,text=True).splitlines():
            source=actual/name
            if source.is_file():
                target=root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        (root/'.gitignore').write_text('runtime/\nfixture-home/\n')
        def git(*args):return subprocess.check_output(['/usr/bin/git',*args],cwd=root,text=True,stderr=subprocess.DEVNULL).strip()
        git('init','-q');git('config','user.name','Fixture');git('config','user.email','fixture@localhost');git('add','.');git('-c','commit.gpgsign=false','commit','-qm','reviewed disposable source')
        fixture_home=root/'fixture-home';(fixture_home/'Library/LaunchAgents').mkdir(parents=True)
        original={'Automations':{'K5':'progress-based orchestrator recovery approved'}}
        path=root/'runtime/resilience/authority.json';M.save(path,{'automation':'AUT-0004','permission_sha256':A.digest(original),'schema_version':2})
        return root,fixture_home,original,path,git('rev-parse','HEAD')

    def test_real_bundle_keeps_the_exact_reviewed_permission_digest(self):
        root,fixture_home,original,path,head=self.bundle_fixture()
        with mock.patch.object(I.Path,'home',return_value=fixture_home),mock.patch.object(A,'permissions',return_value=original):
            manifest=I.install(root,load=False,expected_permission_sha256=A.digest(original))
        self.assertEqual(manifest['source'],head);self.assertEqual(M.read(path)['permission_sha256'],A.digest(original))
        self.assertEqual(M.read(path)['schema_version'],2)

    def test_permission_drift_between_bundle_publication_and_seal_cannot_reseal(self):
        root,fixture_home,original,path,head=self.bundle_fixture();before=path.read_bytes();changed=copy.deepcopy(original)
        changed['Automations']['K5']+=' authority changed during publication'
        with mock.patch.object(I.Path,'home',return_value=fixture_home),mock.patch.object(A,'permissions',side_effect=[original,changed]):
            with self.assertRaisesRegex(ValueError,'exact reviewed'):I.install(root,load=False,expected_permission_sha256=A.digest(original))
        self.assertEqual(path.read_bytes(),before)
        self.assertFalse((fixture_home/'Library/LaunchAgents/space.venin.midnight-guardian.plist').exists())

    def test_bundle_publication_rejects_mismatched_permissions_before_writing(self):
        original={'Automations':{'K5':'progress-based orchestrator recovery approved'}};changed={'Automations':{'K5':'progress-based orchestrator recovery different'}}
        with mock.patch.object(A,'permissions',return_value=changed),mock.patch.object(I.D,'atomic_write',side_effect=AssertionError('Mismatched authority must not publish or seal')):
            with self.assertRaisesRegex(ValueError,'reviewed permission digest'):I.install(self.f.root,load=False,expected_permission_sha256=A.digest(original))


if __name__=='__main__':unittest.main()
