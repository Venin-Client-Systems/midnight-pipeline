"""Actual rollback checks current controls after every durable restart intent."""
import datetime as dt
from pathlib import Path
import unittest
from unittest import mock
import guardian as G
import test_recovery_rollback_generation as F


class RollbackRestartControlTests(unittest.TestCase):
    def setUp(self):
        self.f=F.RollbackGenerationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def boundary(self,kind,service):
        f=self.f;original=type(f.store).meta;changed=False;window=True;authority={'permission_sha256':'original'};later=None
        def meta(store,key,*args,**kwargs):
            nonlocal changed,window,later
            value=original(store,key,*args,**kwargs)
            if key!='install_health' or not kwargs.get('write') or changed:return value
            health=args[0] if args else kwargs.get('value',{})
            if service not in health.get('rollback_restart_intents',{}):return value
            changed=True
            if kind=='pause':(f.runtime/'PAUSED').touch()
            elif kind=='fence':(f.runtime/'INSTALLING').write_bytes(b'foreign rollback fence\n')
            elif kind=='authority':authority['permission_sha256']='changed'
            elif kind=='window':window=False
            elif kind=='source':f.git('reset','--hard',f.bad)
            elif kind=='health':
                later={**health,'repair':'later-health-generation'};original(store,'install_health',later,write=True)
            else:raise AssertionError(kind)
            return value
        controller=mock.Mock(side_effect=f.controller_restart);dashboard=mock.Mock(side_effect=f.dashboard_restart)
        instant=dt.datetime.now(dt.timezone.utc);value={'active_window':True,'paused':False,'controller':{
            'phase':'needs_attention','process':f.old,'updated_at':instant.isoformat(),'code_sha256':f.sha('midnight.py')}}
        with mock.patch.object(type(f.store),'meta',new=meta),mock.patch.object(G,'window',side_effect=lambda *args:window), \
             mock.patch.object(G,'check_authority',side_effect=lambda root:dict(authority)), \
             mock.patch.object(G,'restart_controller',controller),mock.patch.object(G,'restart_dashboard',dashboard):
            G.installation_health(f.root,value,instant)
        self.assertTrue(changed);self.assertEqual(controller.call_count,0 if service=='controller' else 1);dashboard.assert_not_called()
        self.assertTrue((f.runtime/'INSTALLING').exists());self.assertEqual(f.git('rev-parse','HEAD'),f.bad if kind=='source' else f.base)
        health=f.store.meta('install_health');self.assertFalse(health.get('finalized',False))
        if kind=='pause':self.assertTrue((f.runtime/'PAUSED').exists())
        if kind=='fence':self.assertEqual((f.runtime/'INSTALLING').read_bytes(),b'foreign rollback fence\n')
        if kind=='health':self.assertEqual(health,later)
        with f.store.read() as con:self.assertEqual(con.execute('SELECT count(*) FROM wf_charges').fetchone()[0],0)

    def test_pause_after_controller_rollback_intent_blocks_restart(self):self.boundary('pause','controller')
    def test_fence_after_controller_rollback_intent_blocks_restart(self):self.boundary('fence','controller')
    def test_authority_after_controller_rollback_intent_blocks_restart(self):self.boundary('authority','controller')
    def test_window_after_controller_rollback_intent_blocks_restart(self):self.boundary('window','controller')
    def test_source_after_controller_rollback_intent_blocks_restart(self):self.boundary('source','controller')
    def test_health_after_controller_rollback_intent_blocks_restart(self):self.boundary('health','controller')
    def test_pause_after_dashboard_rollback_intent_blocks_restart(self):self.boundary('pause','dashboard')
    def test_fence_after_dashboard_rollback_intent_blocks_restart(self):self.boundary('fence','dashboard')
    def test_authority_after_dashboard_rollback_intent_blocks_restart(self):self.boundary('authority','dashboard')
    def test_window_after_dashboard_rollback_intent_blocks_restart(self):self.boundary('window','dashboard')
    def test_source_after_dashboard_rollback_intent_blocks_restart(self):self.boundary('source','dashboard')
    def test_health_after_dashboard_rollback_intent_blocks_restart(self):self.boundary('health','dashboard')


    def drain_boundary(self,kind):
        f=self.f;original=type(f.store).meta;changed=False
        def meta(store,key,*args,**kwargs):
            nonlocal changed
            value=original(store,key,*args,**kwargs)
            health=args[0] if args else {}
            if key=='install_health' and kwargs.get('write') and health.get('rollback_waiting')=='drain_rejected_controller' and not changed:
                changed=True
                if kind=='pause':(f.runtime/'PAUSED').touch()
                else:(f.runtime/'INSTALLING').write_bytes(b'foreign owner before process drainage\n')
            return value
        instant=dt.datetime.now(dt.timezone.utc);value={'active_window':True,'paused':False,'controller':{'phase':'needs_attention','process':f.old}}
        with mock.patch.object(type(f.store),'meta',new=meta),mock.patch.object(G,'window',return_value=True), \
             mock.patch.object(G,'restart_controller',return_value=True),mock.patch.object(G,'restart_dashboard',return_value=True), \
             mock.patch.object(G.P,'stop_tree',return_value=False) as stop:
            G.installation_health(f.root,value,instant)
        self.assertTrue(changed);stop.assert_not_called();self.assertTrue(G.P.alive(f.old));self.assertTrue((f.runtime/'INSTALLING').exists())
        if kind=='pause':self.assertTrue((f.runtime/'PAUSED').exists())
        else:self.assertEqual((f.runtime/'INSTALLING').read_bytes(),b'foreign owner before process drainage\n')

    def test_pause_after_drain_intent_preserves_the_rejected_native_process(self):self.drain_boundary('pause')
    def test_replaced_fence_after_drain_intent_preserves_the_rejected_native_process(self):self.drain_boundary('fence')


    def initial_release_boundary(self,kind):
        import maintenance_recovery as M
        f=self.f;self.assertFalse(M.configured(f.root));original=type(f.store).meta;changed=False;window=True;authority={'permission_sha256':'fixture'};later=None
        def meta(store,key,*args,**kwargs):
            nonlocal changed,window,later
            value=original(store,key,*args,**kwargs);health=args[0] if args else {}
            if key!='install_health' or not kwargs.get('write') or not health.get('finalized') or changed:return value
            changed=True
            if kind=='pause':(f.runtime/'PAUSED').touch()
            elif kind=='window':window=False
            elif kind=='authority':authority['permission_sha256']='changed'
            elif kind=='source':f.git('reset','--hard',f.bad)
            elif kind=='dirty':(f.root/'midnight.py').write_text('# owner edit after initial rollback proof\n')
            elif kind=='fence':(f.runtime/'INSTALLING').write_bytes(b'foreign initial rollback fence\n')
            elif kind=='controller':G.P.stop_tree(health['restored_generation_proof']['controller'],grace=1)
            elif kind=='dashboard':G.P.stop_tree(health['restored_generation_proof']['dashboard']['process'],grace=1)
            elif kind=='health':
                later={**health,'repair':'later-initial-health-generation'};original(store,'install_health',later,write=True)
            return value
        instant=dt.datetime.now(dt.timezone.utc);value={'active_window':True,'paused':False,'controller':{'phase':'needs_attention','process':f.old}}
        with mock.patch.object(type(f.store),'meta',new=meta),mock.patch.object(G,'window',side_effect=lambda *args:window), \
             mock.patch.object(G,'check_authority',side_effect=lambda root:dict(authority)), \
             mock.patch.object(G,'restart_controller',side_effect=f.controller_restart),mock.patch.object(G,'restart_dashboard',side_effect=f.dashboard_restart), \
             mock.patch.object(G.urllib.request,'urlopen',side_effect=f.response), \
             mock.patch.object(M,'restore_rollback_admission',side_effect=AssertionError('Initial upgrade must exercise unconfigured rollback branch')):
            G.installation_health(f.root,value,instant)
        self.assertTrue(changed);health=f.store.meta('install_health')
        if kind=='health':self.assertEqual(health,later)
        else:self.assertFalse(health['finalized'])
        self.assertEqual((f.runtime/'INSTALLING').read_bytes(),b'foreign initial rollback fence\n' if kind=='fence' else f.fence)
        if kind=='dirty':self.assertIn('owner edit',(f.root/'midnight.py').read_text())
        if kind=='pause':self.assertTrue((f.runtime/'PAUSED').exists())

    def test_initial_upgrade_rollback_pause_after_completion_keeps_fence(self):self.initial_release_boundary('pause')
    def test_initial_upgrade_rollback_window_after_completion_keeps_fence(self):self.initial_release_boundary('window')
    def test_initial_upgrade_rollback_authority_after_completion_keeps_fence(self):self.initial_release_boundary('authority')
    def test_initial_upgrade_rollback_source_after_completion_keeps_fence(self):self.initial_release_boundary('source')
    def test_initial_upgrade_rollback_owner_edit_after_completion_keeps_fence(self):self.initial_release_boundary('dirty')
    def test_initial_upgrade_rollback_foreign_fence_after_completion_is_preserved(self):self.initial_release_boundary('fence')
    def test_initial_upgrade_rollback_controller_death_after_completion_keeps_fence(self):self.initial_release_boundary('controller')
    def test_initial_upgrade_rollback_dashboard_death_after_completion_keeps_fence(self):self.initial_release_boundary('dashboard')
    def test_initial_upgrade_rollback_later_health_after_completion_is_preserved(self):self.initial_release_boundary('health')


if __name__=='__main__':unittest.main()
