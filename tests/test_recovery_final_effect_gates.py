"""Owner changes after durable intent cannot cross the final native effects."""
from pathlib import Path
import subprocess
import time
import unittest
from unittest import mock
import coordinator_install as C
import core_repair as R
import maintenance_recovery as M
import test_recovery_coordinator_serialization as S
import test_recovery_installation_entrypoints as I
import test_recovery_durable_coordinator as F


class FinalFenceTests(unittest.TestCase):
    def setUp(self):
        self.f=S.CoordinatorSerializationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def boundary(self,kind):
        f=self.f;allowed=True;changed=False
        def checkpoint(stage):
            nonlocal allowed,changed
            if stage!='fence_release_intent_persisted':return
            changed=True
            if kind=='fence':(f.root/'runtime/INSTALLING').write_bytes(b'foreign last-moment fence\n')
            elif kind=='pause':(f.root/'runtime/PAUSED').touch();allowed=False
            elif kind=='authority':allowed=False
            elif kind=='source':f.git('reset','--hard',f.intent['previous'])
            elif kind=='dirty':(f.root/'midnight.py').write_text('# owner edit\n')
            elif kind=='health':
                health=f.f.actions.meta('install_health');health.update(state='verifying',restart_epoch='later-epoch');f.f.actions.meta('install_health',health,write=True)
            else:raise AssertionError(kind)
        with self.assertRaises(RuntimeError):C.finish_installation(f.intent['bindings'],f.expected,lambda:allowed,**f.options,checkpoint=checkpoint)
        self.assertTrue(changed);self.assertFalse((f.directory/'installation-complete.json').exists())
        self.assertEqual((f.root/'runtime/INSTALLING').read_bytes(),b'foreign last-moment fence\n' if kind=='fence' else f.expected)
        if kind=='pause':self.assertTrue((f.root/'runtime/PAUSED').exists())

    def test_foreign_fence_after_release_intent_is_preserved(self):self.boundary('fence')
    def test_pause_after_release_intent_blocks_removal(self):self.boundary('pause')
    def test_authority_after_release_intent_blocks_removal(self):self.boundary('authority')
    def test_source_after_release_intent_blocks_removal(self):self.boundary('source')
    def test_owner_edit_after_release_intent_blocks_removal(self):self.boundary('dirty')
    def test_health_after_release_intent_blocks_removal(self):self.boundary('health')


class CanonicalMutationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=I.InstallationEntrypointTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups);self.fixture.contexts()

    def boundary(self,kind):
        fixture=self.fixture;f=fixture.f;fixture.git('reset','--hard',f.base);authority={'permission_sha256':'original'};changed=False;save=R.save
        def persist(path,value):
            nonlocal changed
            save(path,value)
            if Path(path)!=fixture.directory/'install-intent.json' or changed:return
            changed=True
            if kind=='fence':(f.root/'runtime/INSTALLING').write_bytes(b'foreign pre-merge fence\n')
            elif kind=='pause':(f.root/'runtime/PAUSED').touch()
            elif kind=='authority':authority['permission_sha256']='changed'
            elif kind=='source':
                (f.root/'midnight.py').write_text('# later owner generation\n');fixture.git('add','midnight.py');fixture.git('-c','commit.gpgsign=false','commit','-qm','owner changed source')
            elif kind=='dirty':(f.root/'midnight.py').write_text('# uncommitted owner edit\n')
            elif kind=='health':f.actions.meta('install_health',{'installed':fixture.commit,'tree':M.read(fixture.directory/'verified.json')['tree'],'state':'rolled_back'},write=True)
        with mock.patch.object(R,'save',side_effect=persist),mock.patch.object(R,'check_authority',side_effect=lambda root:dict(authority)), \
             mock.patch.object(R,'permitted',side_effect=lambda root,deadline:not (root/'runtime/PAUSED').exists()), \
             mock.patch.object(R.GU,'restart_controller',side_effect=AssertionError('blocked source cannot restart')):
            with self.assertRaises(ValueError):
                with R.P.source_lock(f.root/'runtime',deadline=time.time()+10):R._install(f.root,M.read(fixture.directory/'delivery.json'),f.base,M.read(fixture.directory/'verified.json')['tree'],fixture.directory,time.time()+30)
        self.assertTrue(changed);self.assertNotEqual(fixture.git('rev-parse','HEAD'),fixture.commit)
        self.assertFalse((fixture.directory/'installation.json').exists())
        if kind=='fence':self.assertEqual((f.root/'runtime/INSTALLING').read_bytes(),b'foreign pre-merge fence\n')
        if kind=='pause':self.assertTrue((f.root/'runtime/PAUSED').exists())
        if kind=='dirty':self.assertEqual((f.root/'midnight.py').read_text(),'# uncommitted owner edit\n')

    def test_foreign_fence_after_source_intent_blocks_merge(self):self.boundary('fence')
    def test_pause_after_source_intent_blocks_merge(self):self.boundary('pause')
    def test_authority_after_source_intent_blocks_merge(self):self.boundary('authority')
    def test_source_after_source_intent_blocks_merge(self):self.boundary('source')
    def test_owner_edit_after_source_intent_blocks_merge(self):self.boundary('dirty')
    def test_rollback_health_after_source_intent_blocks_merge(self):self.boundary('health')


class ArmedStopTests(unittest.TestCase):
    def setUp(self):
        self.f=F.DurableCoordinatorTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def boundary(self,kind):
        f=self.f;f.f.git('reset','--hard',f.f.base);(f.here/'installation-intent.json').unlink();actual_arm=C.arm_coordinator_resumer;actual_call=C.call;effects=[]
        def calls(command,timeout=45):
            if command[:2]==['git','fetch']:
                C.D.atomic_write(f.f.root/'.git/FETCH_HEAD',(f.f.bad+'\n').encode());return ''
            return actual_call(command,timeout=timeout)
        def arm(intent):
            result=actual_arm(intent)
            if kind=='fence':(f.f.runtime/'INSTALLING').write_bytes(b'foreign armed fence\n')
            elif kind=='source':
                (f.f.root/'midnight.py').write_text('# owner generation during arming\n');f.f.git('add','midnight.py');f.f.git('-c','commit.gpgsign=false','commit','-qm','owner source after arm')
            elif kind=='health':f.f.store.meta('install_health',{'state':'rolled_back','installed':f.f.bad,'tree':f.intent['tree']},write=True)
            return result
        service=subprocess.run
        def observe(command,*args,**kwargs):
            if command[0]=='/bin/launchctl' and command[1] in {'kill','kickstart'}:effects.append(command);raise AssertionError('Armed changed state cannot signal controller')
            return service(command,*args,**kwargs)
        with mock.patch.object(C,'call',side_effect=calls),mock.patch.object(C,'approved_authority',return_value={'fixed':'approved'}), \
             mock.patch.object(C.A,'check',return_value={'permission_sha256':'fixture'}),mock.patch.object(C,'arm_coordinator_resumer',side_effect=arm), \
             mock.patch.object(C.subprocess,'run',side_effect=observe):
            with self.assertRaises(RuntimeError):C.main()
        self.assertEqual(effects,[]);self.assertFalse((f.here/'installation.json').exists())
        if kind=='fence':self.assertEqual((f.f.runtime/'INSTALLING').read_bytes(),b'foreign armed fence\n')
        if kind=='source':self.assertEqual((f.f.root/'midnight.py').read_text(),'# owner generation during arming\n')

    def test_changed_fence_after_arming_prevents_controller_signal(self):self.boundary('fence')
    def test_changed_source_after_arming_prevents_controller_signal(self):self.boundary('source')
    def test_changed_health_after_arming_prevents_controller_signal(self):self.boundary('health')


class DirectCoordinatorAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.f=F.DurableCoordinatorTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def boundary(self,kind):
        f=self.f;f.f.git('reset','--hard',f.f.base);(f.here/'installation-intent.json').unlink();authority=True;changed=False;save=C.save
        def validate():return authority and not (f.f.runtime/'PAUSED').exists() and (f.f.runtime/'INSTALLING').read_bytes()==f.f.fence
        def persist(path,value):
            nonlocal authority,changed
            save(path,value)
            if Path(path)!=f.here/'installation-intent.json' or changed:return
            changed=True
            if kind=='pause':(f.f.runtime/'PAUSED').touch()
            elif kind=='authority':authority=False
            elif kind=='fence':(f.f.runtime/'INSTALLING').write_bytes(b'foreign coordinator merge fence\n')
            elif kind=='health':f.f.store.meta('install_health',{'installed':f.f.bad,'tree':f.intent['tree'],'state':'rolled_back'},write=True)
            elif kind=='dirty':(f.f.root/'midnight.py').write_text('# owner edit before coordinator merge\n')
            elif kind=='source':
                (f.f.root/'midnight.py').write_text('# owner generation before coordinator merge\n');f.f.git('add','midnight.py');f.f.git('-c','commit.gpgsign=false','commit','-qm','owner source before coordinator merge')
        with mock.patch.object(C,'save',side_effect=persist):
            with self.assertRaisesRegex(RuntimeError,'before coordinator merge'):
                with C.P.source_lock(f.f.runtime,deadline=time.time()+10):C.adopt(f.f.base,f.f.bad,f.intent['tree'],f.intent['bindings'],validate)
        self.assertTrue(changed);self.assertNotEqual(f.f.git('rev-parse','HEAD'),f.f.bad);self.assertFalse((f.here/'installation.json').exists())
        if kind=='fence':self.assertEqual((f.f.runtime/'INSTALLING').read_bytes(),b'foreign coordinator merge fence\n')
        if kind=='pause':self.assertTrue((f.f.runtime/'PAUSED').exists())
        if kind=='dirty':self.assertEqual((f.f.root/'midnight.py').read_text(),'# owner edit before coordinator merge\n')

    def test_direct_helper_pause_after_intent_prevents_merge(self):self.boundary('pause')
    def test_direct_helper_authority_after_intent_prevents_merge(self):self.boundary('authority')
    def test_direct_helper_fence_after_intent_prevents_merge(self):self.boundary('fence')
    def test_direct_helper_rejected_health_after_intent_prevents_merge(self):self.boundary('health')
    def test_direct_helper_owner_edit_after_intent_prevents_merge(self):self.boundary('dirty')
    def test_direct_helper_source_advance_after_intent_prevents_merge(self):self.boundary('source')


class SavedInstallationReleaseTests(unittest.TestCase):
    def setUp(self):
        self.f=I.InstallationEntrypointTests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.f.contexts()
        save=R.save
        def interrupted(path,value):
            if Path(path)==self.f.directory/'fence-release-intent.json':raise OSError('saved completion before release intent persistence')
            return save(path,value)
        with mock.patch.object(R,'save',side_effect=interrupted):self.assertEqual(self.f.invoke(),1)
        self.assertTrue((self.f.directory/'installation-complete.json').exists())
        self.assertTrue((self.f.directory/'installation-authority.json').exists())

    def boundary(self,kind):
        f=self.f;root=f.f.root;expected=(root/'runtime/INSTALLING').read_bytes();authority={'permission_sha256':'fixture'};save=R.save;changed=False
        def persist(path,value):
            nonlocal changed
            save(path,value)
            if Path(path)!=f.directory/'fence-release-intent.json' or changed:return
            changed=True
            if kind=='authority':authority['permission_sha256']='changed'
            elif kind=='source':f.git('reset','--hard',f.f.base)
            elif kind=='dirty':(root/'midnight.py').write_text('# owner edit after saved release intent\n')
            elif kind=='pause':(root/'runtime/PAUSED').touch()
            elif kind=='fence':(root/'runtime/INSTALLING').write_bytes(b'foreign saved completion fence\n')
            elif kind=='health':
                health=f.f.actions.meta('install_health');health['state']='held';f.f.actions.meta('install_health',health,write=True)
        with mock.patch.object(R,'save',side_effect=persist),mock.patch.object(R,'check_authority',side_effect=lambda root:dict(authority)), \
             mock.patch.object(R,'permitted',side_effect=lambda root,deadline:not (root/'runtime/PAUSED').exists()):
            with self.assertRaisesRegex(ValueError,'before saved fence release'):
                R.release_completed_installation_fence(root,M.read(f.directory/'delivery.json'),f.f.base,M.read(f.directory/'verified.json')['tree'],f.directory,time.time()+30)
        self.assertTrue(changed);self.assertEqual((root/'runtime/INSTALLING').read_bytes(),b'foreign saved completion fence\n' if kind=='fence' else expected)

    def test_saved_release_authority_change_after_intent_preserves_fence(self):self.boundary('authority')
    def test_saved_release_source_change_after_intent_preserves_fence(self):self.boundary('source')
    def test_saved_release_owner_edit_after_intent_preserves_fence(self):self.boundary('dirty')
    def test_saved_release_pause_after_intent_preserves_fence(self):self.boundary('pause')
    def test_saved_release_foreign_fence_after_intent_is_preserved(self):self.boundary('fence')
    def test_saved_release_health_change_after_intent_preserves_fence(self):self.boundary('health')

    def test_saved_release_requires_original_installation_authority(self):
        f=self.f;root=f.f.root;expected=(root/'runtime/INSTALLING').read_bytes()
        with mock.patch.object(R,'check_authority',return_value={'permission_sha256':'later-current-authority'}):
            with self.assertRaisesRegex(ValueError,'original approved authority'):
                R.release_completed_installation_fence(root,M.read(f.directory/'delivery.json'),f.f.base,M.read(f.directory/'verified.json')['tree'],f.directory,time.time()+30)
        self.assertEqual((root/'runtime/INSTALLING').read_bytes(),expected);self.assertFalse((f.directory/'fence-release-intent.json').exists())


class OrdinaryInstallationReleaseTests(unittest.TestCase):
    def setUp(self):
        self.f=I.InstallationEntrypointTests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.f.contexts()

    def boundary(self,kind,publication=False):
        f=self.f;root=f.f.root;expected=(root/'runtime/INSTALLING').read_bytes();authority={'permission_sha256':'fixture'};save=R.save;changed=False
        def drift():
            nonlocal changed
            if changed:return
            changed=True
            if kind=='authority':authority['permission_sha256']='changed'
            elif kind=='source':f.git('reset','--hard',f.f.base)
            elif kind=='dirty':(root/'midnight.py').write_text('# owner edit after ordinary completion\n')
            elif kind=='pause':(root/'runtime/PAUSED').touch()
            elif kind=='fence':(root/'runtime/INSTALLING').write_bytes(b'foreign ordinary completion fence\n')
            elif kind=='health':
                health=f.f.actions.meta('install_health');health['state']='held';f.f.actions.meta('install_health',health,write=True)
        def persist(path,value):
            save(path,value)
            if not publication and Path(path)==f.directory/'installation-complete.json':drift()
        with mock.patch.object(R,'save',side_effect=persist),mock.patch.object(R,'check_authority',side_effect=lambda root:dict(authority)), \
             mock.patch.object(R,'permitted',side_effect=lambda root,deadline:not (root/'runtime/PAUSED').exists()), \
             mock.patch.object(R,'publish_worker_repair',side_effect=lambda *args:drift() if publication else None):
            self.assertEqual(f.invoke(),1)
        self.assertTrue(changed);self.assertTrue((f.directory/'installation-complete.json').exists())
        self.assertEqual((root/'runtime/INSTALLING').read_bytes(),b'foreign ordinary completion fence\n' if kind=='fence' else expected)
        self.assertFalse((f.directory/'fence-release-intent.json').exists())

    def test_ordinary_completion_authority_drift_preserves_fence(self):self.boundary('authority')
    def test_ordinary_completion_source_drift_preserves_fence(self):self.boundary('source')
    def test_ordinary_completion_owner_edit_preserves_fence(self):self.boundary('dirty')
    def test_ordinary_completion_pause_preserves_fence(self):self.boundary('pause')
    def test_ordinary_completion_foreign_fence_is_preserved(self):self.boundary('fence')
    def test_ordinary_completion_rejected_health_preserves_fence(self):self.boundary('health')
    def test_publication_authority_drift_preserves_fence(self):self.boundary('authority',True)
    def test_publication_source_drift_preserves_fence(self):self.boundary('source',True)
    def test_publication_owner_edit_preserves_fence(self):self.boundary('dirty',True)
    def test_publication_pause_preserves_fence(self):self.boundary('pause',True)


if __name__=='__main__':unittest.main()
