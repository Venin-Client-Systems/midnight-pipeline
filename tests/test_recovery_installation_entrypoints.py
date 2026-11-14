"""Saved source adoption resumes through the actual guardian and CLI admission."""
from contextlib import ExitStack
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest import mock
import core_repair as R
import maintenance_recovery as M
import progress_recovery as RP
import test_recovery_maintenance_progress as F


class InstallationEntrypointTests(unittest.TestCase):
    def setUp(self):
        guardian_authority=mock.patch.object(R.GU,'check_authority',return_value={'permission_sha256':'fixture'});guardian_authority.start();self.addCleanup(guardian_authority.stop)
        self.f=F.MaintenanceProgressTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f;root=f.root
        def git(*args):return subprocess.check_output(['/usr/bin/git',*args],cwd=root,text=True,stderr=subprocess.DEVNULL).strip()
        self.git=git;git('init','-q');git('config','user.name','Fixture');git('config','user.email','fixture@localhost')
        (root/'.gitignore').write_text('runtime/\n');(root/'midnight.py').write_text('# fixture controller\n');(root/'dashboard.py').write_text('# fixture dashboard\n')
        git('add','.');git('-c','commit.gpgsign=false','commit','-qm','qualified original source');f.base=git('rev-parse','HEAD');self.base_tree=git('rev-parse','HEAD^{tree}')
        self.original=f.legacy();scope,_,capsule=f.observe();source=root/'runtime/maintenance'/self.original[0]['id'];source.mkdir(parents=True)
        (root/'source.py').write_text('def repair(value):\n    return value + 2\n');git('add','source.py')
        patch=git('diff','--binary',f.base)+'\n';(source/'candidate.patch').write_text(patch)
        tree=git('write-tree');git('-c','commit.gpgsign=false','commit','-qm','checked private merged fixture');self.commit=git('rev-parse','HEAD')
        binding={'base':f.base,'tree':tree,'patch_sha256':hashlib.sha256(patch.encode()).hexdigest(),'files':['source.py']}
        retained={'original_repair':self.original[0]['id'],'directory':str(source),'work':str(source/'work'),'binding':binding,'refs':[RP.ref(source/'candidate.patch')]}
        self.identity=f.manager.create('verification',scope,f.model,capsule,retained=retained)
        self.directory=root/'runtime/maintenance'/self.identity;self.directory.mkdir(parents=True);(self.directory/'candidate.patch').write_text(patch)
        payload=copy.deepcopy(f.manager.session(self.identity)['payload']);payload.update(stage='verified',binding=binding)
        f.manager.update(self.identity,'paused',payload=payload,result={'reason':'source adopted before installation metadata'})
        completion=self.directory/'fixture-completion.json';completion.write_text('{"fixture":"complete protected host result"}\n')
        verified={'base':f.base,'tree':tree,'patch_sha256':binding['patch_sha256'],'descriptor':f.model,'review':{'decision':'accept','issues':[]},
                  'checks':[{'code':0,'reason':'exited','completion_receipt':RP.ref(completion)}]}
        M.save(self.directory/'verified.json',verified);M.save(self.directory/'delivery.json',{'status':'merged','commit':self.commit})
        M.save(self.directory/'install-intent.json',{'previous':f.base,'installed':self.commit,'tree':tree})
        fence={'owner':'midnight-maintenance-install','generation':'native-entrypoint-fixture','previous':f.base,'installed':self.commit,'repair':self.identity}
        M.save(self.directory/'install-fence.json',fence);(root/'runtime/INSTALLING').write_text(json.dumps(fence,sort_keys=True)+'\n')
        M.save(root/'runtime/resilience/manifest.json',{'source':f.base});f.actions.meta('maintenance_recovery',{'version':2,'installed':f.base,'tree':self.base_tree},write=True)
        f.actions.meta('install_health',{'state':'verified','installed':f.base,'tree':self.base_tree,'previous':f.base,'at':time.time()-1000},write=True)
        self.assertFalse(M.enabled(root));self.assertTrue(M.installation_reconciliation(root,self.identity))

    def contexts(self):
        f=self.f;stack=ExitStack();self.addCleanup(stack.close);actual_read=R.GU.read
        frozen_manifest=Path(R.__file__).resolve().parents[2]/'manifest.json'
        def read(path,*args,**kwargs):
            if Path(path)==frozen_manifest:return {'canonical':str(f.root),'bundle':str(Path(R.__file__).resolve().parent)}
            return actual_read(path,*args,**kwargs)
        stack.enter_context(mock.patch.object(R.GU,'INSTALL_ROOT',f.root));stack.enter_context(mock.patch.object(R.GU,'read',side_effect=read))
        stack.enter_context(mock.patch.object(R.signal,'signal'));stack.enter_context(mock.patch.object(R,'permitted',return_value=True))
        stack.enter_context(mock.patch.object(R,'check_authority',return_value={'permission_sha256':'fixture'}));stack.enter_context(mock.patch.object(R,'publish_worker_repair'))
        stack.enter_context(mock.patch.object(R.GU,'restart_controller',return_value=True))
        actual_run=subprocess.run
        def services(command,*args,**kwargs):
            if command[0]=='/bin/launchctl':return subprocess.CompletedProcess(command,0,'','')
            return actual_run(command,*args,**kwargs)
        stack.enter_context(mock.patch.object(R.subprocess,'run',side_effect=services))
        for target,name in ((M,'diagnose'),(M,'local_decision'),(R.CP,'CloudPolicy'),(R.G,'GitDelivery'),(R,'fetch')):
            stack.enter_context(mock.patch.object(target,name,side_effect=AssertionError('host reconciliation must not enter '+name)))
        return stack

    def invoke(self,command=None):
        args=command or ['core_repair.py','--root',str(self.f.root),'--repair',self.identity,'--progress']
        with mock.patch.object(sys,'argv',args):return R.main()

    def assert_completed_without_coding(self):
        f=self.f;self.assertEqual(f.manager.session(self.identity)['state'],'complete');self.assertEqual(f.actions.repairs(active=False),self.original)
        self.assertFalse((f.root/'runtime/INSTALLING').exists());self.assertEqual(self.git('rev-parse','HEAD'),self.commit)
        health=f.actions.meta('install_health');self.assertEqual(health['installed'],self.commit);self.assertEqual(health['state'],'verifying')
        self.assertEqual(set(health['restart_requirements']),{'space.venin.midnight','space.venin.midnight-dashboard'})
        self.assertEqual(M.read(self.directory/'restart-receipt.json')['health_epoch'],health['restart_epoch'])
        with f.actions.read() as con:self.assertEqual(con.execute('SELECT count(*) FROM wf_charges').fetchone()[0],0)

    def test_progress_entrypoint_finishes_saved_adoption_while_new_coding_is_disabled(self):
        self.contexts();result=self.invoke();self.assertEqual(result,0,json.dumps(self.f.manager.session(self.identity)['result']));self.assert_completed_without_coding()

    def test_production_guardian_audit_dispatches_only_saved_installation_reconciliation(self):
        self.contexts();f=self.f;commands=[]
        def spawn(command,**kwargs):commands.append(command);self.assertIn('--install-only',command);self.assertEqual(self.invoke(command[2:]),0,json.dumps(f.manager.session(self.identity)['result']));return mock.Mock()
        value={'active_window':True,'paused':False,'installing':True,'controller':{},'projects':[],'jobs':[],'phases':[],'harness_requests':[]}
        with mock.patch.object(R.GU,'observation',return_value=value),mock.patch.object(R.GU,'diagnoses',return_value=[]):
            R.GU.audit(f.root,spawn=spawn)
        self.assertEqual(len(commands),1);self.assert_completed_without_coding()

    def test_foreign_fence_blocks_both_entrypoints_without_launching_or_charging(self):
        self.contexts();f=self.f;foreign=b'{"owner":"another-installer","generation":"separate"}\n';(f.root/'runtime/INSTALLING').write_bytes(foreign)
        self.assertFalse(M.installation_reconciliation(f.root,self.identity));spawn=mock.Mock()
        self.assertTrue(R.GU.repair_running(f.actions,spawn=spawn));spawn.assert_not_called()
        with self.assertRaisesRegex(ValueError,'exact saved installation'):self.invoke()
        self.assertEqual((f.root/'runtime/INSTALLING').read_bytes(),foreign);self.assertEqual(f.actions.repairs(active=False),self.original)

    def completed_fence_interruption(self,guardian):
        self.contexts();f=self.f;marker=R.D.marker
        def interrupted(path,present):
            if Path(path)==f.root/'runtime/INSTALLING' and not present:raise OSError('interrupted after installation completion persistence')
            return marker(path,present)
        with mock.patch.object(R.D,'marker',side_effect=interrupted):self.assertEqual(self.invoke(),1)
        self.assertTrue((self.directory/'installation-complete.json').exists());self.assertTrue((f.root/'runtime/INSTALLING').exists())
        current=f.manager.session(self.identity);self.assertEqual(current['state'],'paused')
        # The original executor has exited; its native lease cannot own replay.
        dead={'pid':99999999,'stamp':'dead fixture executor','boot_id':R.D.boot_id(),'birth_us':1}
        f.manager.update(self.identity,'paused',lease=dead,expected=current['lease'],result={**current['result'],'next_retry_at':0})
        with mock.patch.object(R.GU,'restart_controller',side_effect=AssertionError('completed restart cannot replay')):
            if guardian:
                calls=[]
                def spawn(command,**kwargs):calls.append(command);self.assertEqual(self.invoke(command[2:]),0);return mock.Mock()
                value={'active_window':True,'paused':False,'installing':True,'controller':{},'projects':[],'jobs':[],'phases':[],'harness_requests':[]}
                with mock.patch.object(R.GU,'observation',return_value=value),mock.patch.object(R.GU,'diagnoses',return_value=[]):R.GU.audit(f.root,spawn=spawn)
                self.assertEqual(len(calls),1)
            else:self.assertEqual(self.invoke(),0)
        self.assertTrue((self.directory/'fence-release-intent.json').exists());self.assert_completed_without_coding()

    def test_progress_replays_owned_fence_release_after_completion_persistence(self):self.completed_fence_interruption(False)
    def test_guardian_replays_owned_fence_release_after_completion_persistence(self):self.completed_fence_interruption(True)

    def test_interrupted_initial_health_registration_cannot_verify_old_loaded_processes(self):
        self.contexts();f=self.f;original=type(f.actions).meta;interrupted=False
        def meta(store,key,*args,**kwargs):
            nonlocal interrupted
            value=original(store,key,*args,**kwargs)
            if key=='install_health' and kwargs.get('write') and not interrupted:
                interrupted=True;raise OSError('interrupted after first health registration')
            return value
        delivery=M.read(self.directory/'delivery.json');tree=M.read(self.directory/'verified.json')['tree']
        with mock.patch.object(type(f.actions),'meta',new=meta):
            with self.assertRaisesRegex(OSError,'first health registration'):
                with R.P.source_lock(f.root/'runtime',deadline=time.time()+10):
                    R._install(f.root,delivery,f.base,tree,self.directory,time.time()+30)
        health=f.actions.meta('install_health');self.assertTrue((f.root/'runtime/INSTALLING').exists())
        self.assertEqual(set(health['restart_requirements']),{'space.venin.midnight','space.venin.midnight-dashboard'})
        instant=dt.datetime.fromtimestamp(health['at']+181,dt.timezone.utc)
        value={'active_window':True,'paused':False,'controller':{'updated_at':instant.isoformat(),'phase':'installing',
               'process':R.P.lease(),'code_sha256':health['controller_sha256']}}
        with mock.patch.object(R.GU,'window',return_value=True),mock.patch.object(R.GU,'restart_controller',return_value=False),mock.patch.object(R.GU,'restart_dashboard',return_value=False):
            R.GU.installation_health(f.root,value,instant)
        self.assertEqual(f.actions.meta('install_health')['state'],'rolled_back');self.assertTrue((f.root/'runtime/INSTALLING').exists())

    def test_legacy_reconcile_failure_cannot_delete_an_unowned_marker(self):
        self.contexts();f=self.f;incident=f.actions.incident('midnight','legacy_fixture','legacy','fixture',{})
        identity=f.actions.reserve_repair(incident,{});f.actions.repair(identity,state='unknown')
        foreign=b'{"owner":"foreign-owner"}\n';(f.root/'runtime/INSTALLING').write_bytes(foreign)
        with mock.patch.object(R,'reconcile',side_effect=ValueError('source mismatch')),mock.patch.object(sys,'argv',['core_repair.py','--root',str(f.root),'--repair',identity,'--reconcile']):
            self.assertEqual(R.main(),0)
        self.assertEqual((f.root/'runtime/INSTALLING').read_bytes(),foreign)


    def rejected_before_restored_proof(self,state):
        self.contexts();f=self.f;self.assertTrue(M.installation_reconciliation(f.root,self.identity))
        marker=f.root/'runtime/INSTALLING';before=marker.read_bytes();intent=(self.directory/'install-intent.json').read_bytes()
        # This continuation already passed reconciliation. Rollback obtains the
        # source lock before it can resume, restores source, but cannot yet prove
        # healthy replacement services; no durable rejection-map entry exists.
        with R.P.source_lock(f.root/'runtime',deadline=time.time()+10):
            self.git('reset','--hard',f.base)
            health={'state':state,'installed':self.commit,'tree':M.read(self.directory/'verified.json')['tree'],
                    'previous':f.base,'finalized':False,'restart_requests':{'controller':False,'dashboard':False}}
            f.actions.meta('install_health',health,write=True)
        self.assertIsNone(f.actions.meta('rejected_installations'))
        restarts=mock.Mock(side_effect=AssertionError('rejected generation must not restart a service'))
        with mock.patch.object(R.GU,'restart_controller',restarts):
            with self.assertRaisesRegex(ValueError,'Rejected generation health'):
                with R.P.source_lock(f.root/'runtime',deadline=time.time()+10):
                    R._install(f.root,M.read(self.directory/'delivery.json'),f.base,health['tree'],self.directory,time.time()+30)
        restarts.assert_not_called();self.assertEqual(self.git('rev-parse','HEAD'),f.base)
        self.assertEqual(marker.read_bytes(),before);self.assertEqual((self.directory/'install-intent.json').read_bytes(),intent)
        self.assertFalse((self.directory/'installation.json').exists());self.assertEqual(f.actions.meta('install_health'),health)
        self.assertEqual(f.actions.repairs(active=False),self.original)
        with f.actions.read() as con:self.assertEqual(con.execute('SELECT count(*) FROM wf_charges').fetchone()[0],0)

    def test_resumed_install_rejects_rolling_back_health_before_source_mutation(self):self.rejected_before_restored_proof('rolling_back')
    def test_resumed_install_rejects_rolled_back_health_before_restored_proof(self):self.rejected_before_restored_proof('rolled_back')
    def test_resumed_install_rejects_held_health_before_source_mutation(self):self.rejected_before_restored_proof('held')


    def service_effect_drift(self,kind,before_first=False):
        self.contexts();f=self.f;authority={'permission_sha256':'original'};commands=[];changed=False
        original_meta=type(f.actions).meta;actual_run=subprocess.run
        def drift():
            nonlocal changed
            if changed:return
            changed=True
            if kind=='pause':(f.root/'runtime/PAUSED').touch()
            elif kind=='authority':authority['permission_sha256']='changed'
            elif kind=='fence':(f.root/'runtime/INSTALLING').write_bytes(b'foreign owner replacement\n')
            elif kind=='health':
                health=original_meta(f.actions,'install_health');health['state']='held';original_meta(f.actions,'install_health',health,write=True)
            elif kind=='source':self.git('reset','--hard',f.base)
            else:raise AssertionError(kind)
        def meta(store,key,*args,**kwargs):
            value=original_meta(store,key,*args,**kwargs)
            if (before_first and key=='install_health' and kwargs.get('write')
                and list(self.directory.glob('restart-intent-*.json'))):drift()
            return value
        def controller():
            if not before_first:drift()
            return True
        def service(command,*args,**kwargs):
            if command[0]=='/bin/launchctl':commands.append(command);return subprocess.CompletedProcess(command,0,'','')
            return actual_run(command,*args,**kwargs)
        with mock.patch.object(R,'permitted',side_effect=lambda root,deadline:not (root/'runtime/PAUSED').exists()), \
             mock.patch.object(R,'check_authority',side_effect=lambda root:dict(authority)), \
             mock.patch.object(R.GU,'restart_controller',side_effect=controller) as restart, \
             mock.patch.object(type(f.actions),'meta',new=meta),mock.patch.object(R.subprocess,'run',side_effect=service):
            self.assertEqual(self.invoke(),1)
        self.assertTrue(changed);self.assertEqual(restart.call_count,0 if before_first else 1)
        self.assertEqual(commands,[],'dashboard must not be restarted after a boundary changes')
        self.assertEqual(f.manager.session(self.identity)['state'],'paused' if kind=='pause' else 'waiting');self.assertFalse((self.directory/'installation-complete.json').exists())
        self.assertTrue((f.root/'runtime/INSTALLING').exists());self.assertEqual(f.actions.repairs(active=False),self.original)
        with f.actions.read() as con:self.assertEqual(con.execute('SELECT count(*) FROM wf_charges').fetchone()[0],0)
        if kind=='pause':self.assertTrue((f.root/'runtime/PAUSED').exists())
        if kind=='fence':self.assertEqual((f.root/'runtime/INSTALLING').read_bytes(),b'foreign owner replacement\n')

    def test_saved_install_pause_after_restart_intent_prevents_controller_effect(self):self.service_effect_drift('pause',True)
    def test_saved_install_authority_after_restart_intent_prevents_controller_effect(self):self.service_effect_drift('authority',True)
    def test_saved_install_fence_after_restart_intent_prevents_controller_effect(self):self.service_effect_drift('fence',True)
    def test_saved_install_pause_during_controller_restart_prevents_dashboard_effect(self):self.service_effect_drift('pause')
    def test_saved_install_authority_during_controller_restart_prevents_dashboard_effect(self):self.service_effect_drift('authority')
    def test_saved_install_fence_during_controller_restart_prevents_dashboard_effect(self):self.service_effect_drift('fence')
    def test_saved_install_health_during_controller_restart_prevents_dashboard_effect(self):self.service_effect_drift('health')
    def test_saved_install_source_during_controller_restart_prevents_dashboard_effect(self):self.service_effect_drift('source')


if __name__=='__main__':unittest.main()
