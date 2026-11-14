"""Overlapping installer recovery cannot release or restart a stale generation."""
from contextlib import contextmanager
import hashlib
import threading
import time
import unittest
from unittest import mock
import coordinator_install as C
import maintenance_recovery as M
import test_recovery_maintenance_lifecycle as L


class CoordinatorSerializationTests(unittest.TestCase):
    def setUp(self):
        self.owner=L.MaintenanceLifecycleTests();self.owner.setUp();self.addCleanup(self.owner.doCleanups)
        self.owner.installer_interruption('source_adopted');f=self.owner.f;self.f=f;self.root=f.root;self.directory=f.root/'evidence'
        self.intent=M.read(self.directory/'installation-intent.json');self.expected=b'exact serialized coordinator fence\n';(self.root/'runtime/INSTALLING').write_bytes(self.expected)
        def git(*args):return L.subprocess.check_output(['/usr/bin/git',*args],cwd=self.root,text=True,stderr=L.subprocess.DEVNULL).strip()
        self.git=git;self.calls={'bundle':0,'restart':[],'activate':0};self.second_effect=threading.Event()
        def bundle():
            self.calls['bundle']+=1;value={'source':self.intent['installed']};M.save(self.root/'runtime/resilience/manifest.json',value);return value
        def restart(label):
            self.calls['restart'].append((threading.current_thread().name,label))
            if threading.current_thread().name=='overlapping-helper':self.second_effect.set()
            health=f.actions.meta('install_health');health.update(state='verified',verified_restart_epoch=health['restart_epoch']);f.actions.meta('install_health',health,write=True)
            return {'requested':True}
        def activate():
            self.calls['activate']+=1;value={'version':2,'installed':self.intent['installed'],'tree':self.intent['tree']};f.actions.meta('maintenance_recovery',value,write=True);return value
        self.options={'install_bundle':bundle,'restart':restart,'activate':activate}
        for name,value in (('ROOT',self.root),('HERE',self.directory)):
            patch=mock.patch.object(C,name,value);patch.start();self.addCleanup(patch.stop)
        patch=mock.patch.object(C,'git',side_effect=git);patch.start();self.addCleanup(patch.stop)

    def finish(self,**kwargs):return C.finish_installation(self.intent['bindings'],self.expected,lambda:True,**self.options,**kwargs)

    def test_overlapping_helper_waits_across_restart_activation_and_interrupted_release(self):
        paused=threading.Event();resume=threading.Event();second_started=threading.Event();results={};first=True
        def checkpoint(stage):
            if stage=='service_restarted' and not paused.is_set():
                paused.set()
                if not resume.wait(5):raise RuntimeError('test did not release first helper')
        marker=C.D.marker
        def interrupted(path,present):
            nonlocal first
            if not present and first:first=False;raise OSError('persisted activation before interrupted fence release')
            return marker(path,present)
        def run(name,**kwargs):
            if name=='second':second_started.set()
            try:results[name]=self.finish(**kwargs)
            except BaseException as exc:results[name]=exc
        a=threading.Thread(target=run,args=('first',),kwargs={'checkpoint':checkpoint},name='first-helper',daemon=True)
        b=threading.Thread(target=run,args=('second',),name='overlapping-helper',daemon=True)
        with mock.patch.object(C.D,'marker',side_effect=interrupted):
            try:
                a.start();self.assertTrue(paused.wait(5));b.start();self.assertTrue(second_started.wait(2))
                self.assertFalse(self.second_effect.wait(.3),'overlapping helper restarted a service while the original flow owned installation')
                resume.set();a.join(5);b.join(5)
            finally:
                resume.set();a.join(5)
                if b.ident:b.join(5)
        self.assertFalse(a.is_alive());self.assertFalse(b.is_alive());self.assertIsInstance(results['first'],OSError)
        self.assertEqual(results['second']['state'],'complete');self.assertEqual(len(self.calls['restart']),2);self.assertEqual(self.calls['activate'],1)
        self.assertTrue(all(name=='first-helper' for name,_ in self.calls['restart']));self.assertFalse((self.root/'runtime/INSTALLING').exists())

    def test_existing_activation_cannot_release_after_health_changes_inside_final_lock(self):
        marker=C.D.marker
        def interrupted(path,present):
            if not present:raise OSError('activation saved before fence removal')
            return marker(path,present)
        with mock.patch.object(C.D,'marker',side_effect=interrupted):
            with self.assertRaisesRegex(OSError,'activation saved'):self.finish()
        self.assertTrue((self.directory/'activation.json').exists());self.assertTrue((self.root/'runtime/INSTALLING').exists())
        source_lock=C.P.source_lock;changed=False
        @contextmanager
        def interleaved(*args,**kwargs):
            nonlocal changed
            with source_lock(*args,**kwargs) as value:
                if not changed:
                    changed=True;health=self.f.actions.meta('install_health');health.update(state='verifying',restart_epoch='later-unverified-restart')
                    self.f.actions.meta('install_health',health,write=True)
                yield value
        with mock.patch.object(C.P,'source_lock',side_effect=interleaved):
            with self.assertRaisesRegex(RuntimeError,'exact verified current restart generation'):self.finish()
        self.assertTrue(changed);self.assertEqual((self.root/'runtime/INSTALLING').read_bytes(),self.expected)
        self.assertEqual(len(self.calls['restart']),2);self.assertEqual(self.calls['activate'],1)


    def restart_intent_drift(self,kind):
        allowed=True;marker=self.root/'runtime/INSTALLING';changed=False
        def checkpoint(stage):
            nonlocal allowed,changed
            if stage!='service_restart_intent_recorded' or changed:return
            changed=True
            if kind=='fence':marker.write_bytes(b'foreign replacement fence\n')
            elif kind=='controls':
                (self.root/'runtime/PAUSED').write_text('owner pause\n');allowed=False
            elif kind=='health':
                health=self.f.actions.meta('install_health');health.update(state='rolled_back',finalized=False)
                self.f.actions.meta('install_health',health,write=True)
            elif kind=='source':self.git('reset','--hard',self.intent['previous'])
            else:raise AssertionError(kind)
        with self.assertRaisesRegex(RuntimeError,'before service restart effect'):
            C.finish_installation(self.intent['bindings'],self.expected,lambda:allowed,**self.options,checkpoint=checkpoint)
        self.assertTrue(changed);self.assertEqual(self.calls['restart'],[]);self.assertEqual(self.calls['activate'],0)
        self.assertFalse((self.directory/'service-restarts.json').exists());self.assertFalse((self.directory/'installation-complete.json').exists())
        self.assertEqual(marker.read_bytes(),b'foreign replacement fence\n' if kind=='fence' else self.expected)
        self.assertEqual(self.git('rev-parse','HEAD'),self.intent['previous'] if kind=='source' else self.intent['installed'])
        if kind=='controls':self.assertTrue((self.root/'runtime/PAUSED').exists())
        if kind=='health':self.assertEqual(self.f.actions.meta('install_health')['state'],'rolled_back')

    def test_replaced_fence_after_restart_intent_prevents_service_effect(self):self.restart_intent_drift('fence')
    def test_owner_pause_after_restart_intent_prevents_service_effect(self):self.restart_intent_drift('controls')
    def test_rollback_health_after_restart_intent_prevents_service_effect(self):self.restart_intent_drift('health')
    def test_restored_source_after_restart_intent_prevents_service_effect(self):self.restart_intent_drift('source')

    def test_restart_effect_retains_source_lock_against_a_waiting_rollback(self):
        begin=threading.Event();waiting=threading.Event();acquired=threading.Event();errors=[]
        def rollback():
            try:
                if not begin.wait(5):raise RuntimeError('restart intent did not arrive')
                waiting.set()
                with C.P.source_lock(self.root/'runtime',deadline=time.time()+5):acquired.set()
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=rollback,daemon=True)
        def checkpoint(stage):
            if stage=='service_restart_intent_recorded':
                begin.set();self.assertTrue(waiting.wait(2));self.assertFalse(acquired.wait(.1),'rollback acquired the source lock before the restart effect')
        def restart(label):
            self.assertFalse(acquired.wait(.1),'restart ran outside rollback serialization')
            raise OSError('isolated interruption at protected restart effect')
        options={**self.options,'restart':restart};thread.start()
        try:
            with self.assertRaisesRegex(OSError,'protected restart effect'):
                C.finish_installation(self.intent['bindings'],self.expected,lambda:True,**options,checkpoint=checkpoint)
        finally:begin.set();thread.join(6)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertTrue(acquired.is_set())
        self.assertEqual((self.root/'runtime/INSTALLING').read_bytes(),self.expected);self.assertEqual(self.calls['activate'],0)


if __name__=='__main__':unittest.main()
