"""Rollback and guardian unload are serialized using real isolated Git/SQLite."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock
import coordinator_install as C
import test_recovery_rollback_generation as F


class HandoffSerializationTests(unittest.TestCase):
    def setUp(self):
        resumer=mock.patch.object(C,'arm_coordinator_resumer',return_value={'loaded':True});resumer.start();self.addCleanup(resumer.stop)
        self.f=F.RollbackGenerationTests();self.f.setUp();self.addCleanup(self.f.doCleanups);f=self.f
        self.directory=f.runtime/'serialized-handoff';self.directory.mkdir();self.loaded=True;self.effects=[];self.authority=True
        self.intent={'previous':f.base,'installed':f.bad,'tree':f.git('rev-parse','HEAD^{tree}'),
                     'bindings':{'fence_sha256':hashlib.sha256(f.fence).hexdigest()}}
        C.save(self.directory/'installation-intent.json',self.intent)
        self.resilience=f.runtime/'resilience';bundle=self.resilience/'bundles'/f.bad;bundle.mkdir(parents=True)
        actual=Path(C.__file__).resolve().parent
        for name in ('guardian.py','guardian_bootstrap.py'):(bundle/name).write_bytes((actual/name).read_bytes())
        self.manifest={'source':f.bad,'canonical':str(f.root),'bundle':str(bundle),
                       'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.iterdir()}}
        C.save(self.resilience/'manifest.json',self.manifest);(self.resilience/'guardian_bootstrap.py').write_bytes((bundle/'guardian_bootstrap.py').read_bytes())
        self.home=f.runtime/'fixture-home';plists=self.home/'Library/LaunchAgents';plists.mkdir(parents=True)
        self.plist=plists/'space.venin.midnight-guardian.plist';self.plist.write_bytes(plistlib.dumps({
            'Label':'space.venin.midnight-guardian','ProgramArguments':[sys.executable,'-B',str(self.resilience/'guardian_bootstrap.py')],
            'WorkingDirectory':str(self.resilience)}))
        actual_run=subprocess.run
        def service(command,*args,**kwargs):
            if command[0]!='/bin/launchctl':return actual_run(command,*args,**kwargs)
            if command[1]=='print':return subprocess.CompletedProcess(command,0 if self.loaded else 113,
                'pid = '+str(f.old['pid'])+'\n' if self.loaded and F.G.P.alive(f.old) else '',
                '' if self.loaded else 'Could not find service fixture')
            if command[1]=='bootout':self.effects.append(('bootout',f.git('rev-parse','HEAD')));self.loaded=False
            elif command[1]=='bootstrap':self.effects.append(('bootstrap',f.git('rev-parse','HEAD')));self.loaded=True
            else:raise AssertionError(command)
            return subprocess.CompletedProcess(command,0,'','')
        for patch in (mock.patch.object(C,'ROOT',f.root),mock.patch.object(C,'HERE',self.directory),
                      mock.patch.object(C,'git',side_effect=f.git),mock.patch.object(C.subprocess,'run',side_effect=service),
                      mock.patch.object(C.Path,'home',return_value=self.home)):
            patch.start();self.addCleanup(patch.stop)

    def valid(self):return self.authority and not (self.f.runtime/'PAUSED').exists()
    def establish(self,checkpoint=lambda stage:None):
        return C.establish_guardian_generation(self.intent,self.f.fence,self.valid,
            install_bundle=lambda:self.manifest,checkpoint=checkpoint)

    def test_actual_rollback_between_source_check_and_bootout_waits_until_evaluator_is_restored(self):
        f=self.f;begin=threading.Event();started=threading.Event();done=threading.Event();errors=[]
        def rollback():
            try:
                if not begin.wait(5):raise RuntimeError('quiescence boundary not reached')
                started.set();f.evaluate(lambda:False,lambda:False);done.set()
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=rollback,daemon=True);actual=C.quiesce_guardian
        def boundary(stage):
            if stage=='guardian_quiescence_intent':
                begin.set();self.assertTrue(started.wait(2));self.assertFalse(done.wait(.2),'rollback changed source before guardian bootout')
        def quiesce(**kwargs):return actual(checkpoint=boundary,**kwargs)
        def interrupted(stage):
            if stage=='guardian_quiesced':raise OSError('interrupted after unload before bundle publication')
        thread.start()
        try:
            with mock.patch.object(C,'quiesce_guardian',side_effect=quiesce):
                with self.assertRaisesRegex(OSError,'after unload'):self.establish(interrupted)
        finally:begin.set();thread.join(6)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertTrue(done.is_set())
        self.assertEqual(self.effects,[('bootout',f.bad),('bootstrap',f.bad)])
        self.assertTrue(self.loaded);self.assertEqual(f.git('rev-parse','HEAD'),f.base)
        health=f.store.meta('install_health');self.assertEqual(health['state'],'rolled_back');self.assertFalse(health['finalized'])
        self.assertEqual((f.runtime/'INSTALLING').read_bytes(),f.fence)
        self.assertTrue(C.D.read_json(self.directory/'guardian-availability.json')['available'])
        before=list(self.effects)
        with self.assertRaisesRegex(RuntimeError,'Source or health changed'):self.establish()
        self.assertEqual(self.effects,before);self.assertTrue(self.loaded)

    def test_completed_actual_rollback_before_handoff_prevents_any_unload(self):
        f=self.f;f.evaluate(lambda:False,lambda:False)
        with self.assertRaisesRegex(RuntimeError,'Source or health changed'):self.establish()
        self.assertEqual(self.effects,[]);self.assertTrue(self.loaded);self.assertEqual(f.git('rev-parse','HEAD'),f.base)

    def unload_drift(self,kind):
        f=self.f;actual=C.quiesce_guardian
        def boundary(stage):
            if stage!='guardian_quiescence_intent':return
            if kind=='fence':(f.runtime/'INSTALLING').write_bytes(b'foreign replacement\n')
            elif kind=='pause':(f.runtime/'PAUSED').touch()
            elif kind=='authority':self.authority=False
            elif kind=='health':
                health=f.store.meta('install_health');health['state']='held';f.store.meta('install_health',health,write=True)
            elif kind=='source':f.git('reset','--hard',f.base)
            else:raise AssertionError(kind)
        def quiesce(**kwargs):return actual(checkpoint=boundary,**kwargs)
        with mock.patch.object(C,'quiesce_guardian',side_effect=quiesce):
            with self.assertRaisesRegex(RuntimeError,'before guardian unload'):self.establish()
        self.assertEqual(self.effects,[]);self.assertTrue(self.loaded)
        self.assertEqual((f.runtime/'INSTALLING').read_bytes(),b'foreign replacement\n' if kind=='fence' else f.fence)
        if kind=='pause':self.assertTrue((f.runtime/'PAUSED').exists())
        self.assertFalse((self.directory/'guardian-handoff.json').exists())

    def test_replaced_fence_before_guardian_unload_prevents_effect(self):self.unload_drift('fence')
    def test_owner_pause_before_guardian_unload_prevents_effect(self):self.unload_drift('pause')
    def test_changed_authority_before_guardian_unload_prevents_effect(self):self.unload_drift('authority')
    def test_rejected_health_before_guardian_unload_prevents_effect(self):self.unload_drift('health')
    def test_restored_source_before_guardian_unload_prevents_effect(self):self.unload_drift('source')

    def test_modified_bootstrap_cannot_be_reloaded_for_availability(self):
        self.loaded=False;(self.resilience/'guardian_bootstrap.py').write_text('# changed bootstrap\n')
        with self.assertRaisesRegex(RuntimeError,'bootstrap differs'):C.ensure_guardian_evaluator(self.intent)
        self.assertEqual(self.effects,[])

    def test_previous_evaluator_cannot_be_reloaded_for_armed_candidate_health(self):
        self.loaded=False;old=self.resilience/'bundles'/self.f.base;old.mkdir()
        new=Path(self.manifest['bundle'])
        for path in new.iterdir():(old/path.name).write_bytes(path.read_bytes())
        C.save(self.resilience/'manifest.json',{**self.manifest,'source':self.f.base,'bundle':str(old)})
        with self.assertRaisesRegex(RuntimeError,'Previous evaluator'):C.ensure_guardian_evaluator(self.intent)
        self.assertEqual(self.effects,[])


if __name__=='__main__':unittest.main()
