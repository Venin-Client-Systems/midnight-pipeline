"""Previous evaluator compatibility and native quiescence at the upgrade boundary."""
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import time
import types
import unittest
from unittest import mock
import coordinator_install as C
import test_recovery_rollback_generation as F

PREVIOUS='1c53c9a9571f17727ed9bb3ee500a3d73a0baa6c'


class GuardianHandoffTests(unittest.TestCase):
    def setUp(self):
        resumer=mock.patch.object(C,'arm_coordinator_resumer',return_value={'loaded':True});resumer.start();self.addCleanup(resumer.stop)
        self.f=F.RollbackGenerationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.actual=Path(C.__file__).resolve().parent
        self.previous=subprocess.check_output(['/usr/bin/git','show',PREVIOUS+':guardian.py'],cwd=self.actual,text=True)

    def test_actual_previous_evaluator_cleared_the_fence_after_failed_restarts(self):
        f=self.f;module=types.ModuleType('previous_guardian');module.__file__=str(self.actual/'guardian.py')
        exec(compile(self.previous,module.__file__,'exec'),module.__dict__)
        instant=dt.datetime.now(dt.timezone.utc);value={'active_window':True,'paused':False,'controller':{'phase':'needs_attention','process':f.old,'updated_at':instant.isoformat()}}
        with mock.patch.object(module,'window',return_value=True),mock.patch.object(module,'restart_controller',return_value=False),mock.patch.object(module,'restart_dashboard',return_value=False):
            module.installation_health(f.root,value,instant)
        self.assertEqual(f.git('rev-parse','HEAD'),f.base)
        self.assertFalse(f.store.meta('install_health')['finalized']);self.assertFalse((f.runtime/'INSTALLING').exists())

    def handoff(self,point):
        f=self.f;directory=f.runtime/'handoff';directory.mkdir()
        previous=directory/'previous-guardian.py';previous.write_text(self.previous)
        ready=directory/'old-ready';trigger=directory/'old-trigger';executed=directory/'old-executed'
        launcher='''import datetime as dt,json,sys,time,types
from pathlib import Path
actual,root,source,ready,trigger,executed=map(Path,sys.argv[1:]);sys.path.insert(0,str(actual))
m=types.ModuleType('previous_guardian');m.__file__=str(actual/'guardian.py');exec(compile(source.read_text(),m.__file__,'exec'),m.__dict__)
ready.write_text('loaded actual previous evaluator')
while not trigger.exists():time.sleep(.01)
health=m.Actions(root/'runtime').meta('install_health');instant=dt.datetime.fromtimestamp(health['at']+201,dt.timezone.utc)
m.window=lambda *a:True;m.restart_controller=lambda:False;m.restart_dashboard=lambda:False
m.installation_health(root,{'active_window':True,'paused':False,'controller':{'phase':'needs_attention'}},instant)
executed.write_text('previous evaluator executed')
'''
        child=subprocess.Popen([sys.executable,'-I','-B','-c',launcher,str(self.actual),str(f.root),str(previous),str(ready),str(trigger),str(executed)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        f.children.append(child);self.addCleanup(child.stderr.close)
        until=time.time()+5
        while not ready.exists() and child.poll() is None and time.time()<until:time.sleep(.01)
        self.assertTrue(ready.exists(),'actual previous evaluator must load before the handoff')
        old=F.G.P.lease(child.pid);self.assertTrue(F.G.P.alive(old))
        f.git('reset','--hard',f.base);prior={'state':'verified','installed':f.base,'tree':f.git('rev-parse','HEAD^{tree}'),'previous':f.base,'at':time.time()-1000}
        f.store.meta('install_health',prior,write=True);tree=f.git('rev-parse',f.bad+'^{tree}')
        unloaded=False;events=[];actual_quiesce=C.quiesce_guardian;actual_run=subprocess.run
        def service(command,**kwargs):
            nonlocal unloaded
            if command[0]!='/bin/launchctl':return actual_run(command,**kwargs)
            self.assertEqual(command[0],'/bin/launchctl')
            if command[1]=='print':
                return subprocess.CompletedProcess(command,113 if unloaded else 0,'' if unloaded else 'state = running\n pid = '+str(child.pid)+'\n','Could not find service fixture' if unloaded else '')
            self.assertEqual(command[1],'bootout');unloaded=True
            # Simulate a service unload that leaves its child alive: the helper
            # must drain the exact native identity, rather than trust exit zero.
            return subprocess.CompletedProcess(command,0,'','')
        def quiesce():
            with mock.patch.object(C.subprocess,'run',side_effect=service):result=actual_quiesce(checkpoint=checkpoint)
            self.assertFalse(F.G.P.alive(old));events.append('quiesced');return result
        def bundle():
            self.assertFalse(F.G.P.alive(old));self.assertIn(f.store.meta('install_health').get('installed'),{f.base,f.bad})
            events.append('reviewed_bundle');value={'source':f.bad,'reviewed_evaluator':'fixture'}
            F.G.D.atomic_write(f.runtime/'resilience/manifest.json',(json.dumps(value)+'\n').encode());return value
        def checkpoint(stage):
            if stage==point:raise OSError('interrupted guardian handoff at '+point)
        with mock.patch.object(C,'ROOT',f.root),mock.patch.object(C,'HERE',directory),mock.patch.object(C,'git',side_effect=f.git):
            intent=C.adopt(f.base,f.bad,tree,{'fence_sha256':__import__('hashlib').sha256(f.fence).hexdigest()},lambda:True)
            self.assertEqual(f.store.meta('install_health'),prior)
            with self.assertRaisesRegex(OSError,'interrupted guardian handoff'):
                C.establish_guardian_generation(intent,f.fence,lambda:True,install_bundle=bundle,quiesce=quiesce,checkpoint=checkpoint)
            if point=='guardian_service_unloaded':
                self.assertTrue(F.G.P.alive(old));journal=json.loads((directory/'guardian-quiescence-intent.json').read_text());self.assertIn(old,journal['processes'])
                C.establish_guardian_generation(intent,f.fence,lambda:True,install_bundle=bundle,quiesce=quiesce)
            trigger.write_text('an old evaluator must not survive to observe newly armed health')
            child.wait(timeout=3);self.assertFalse(executed.exists());self.assertEqual((f.runtime/'INSTALLING').read_bytes(),f.fence)
            health=f.store.meta('install_health')
            if point in {'health_registered','health_armed_before_handoff_receipt','guardian_service_unloaded'}:
                self.assertEqual(events,['quiesced','reviewed_bundle']);self.assertEqual(health['installed'],f.bad);self.assertEqual(health['state'],'verifying')
                if point in {'health_registered','guardian_service_unloaded'}:
                    no_effect=mock.Mock(side_effect=AssertionError('completed handoff must not quiesce or republish again'))
                    C.establish_guardian_generation(intent,f.fence,lambda:True,install_bundle=no_effect,quiesce=no_effect)
                    no_effect.assert_not_called()
                else:
                    self.assertFalse((directory/'guardian-handoff.json').exists())
                    C.establish_guardian_generation(intent,f.fence,lambda:True,install_bundle=bundle,quiesce=lambda:{'unloaded':True,'processes':[]})
                    self.assertEqual(f.store.meta('install_health')['at'],health['at'])
            else:
                self.assertEqual(health,prior)
                self.assertFalse((directory/'guardian-handoff.json').exists())

    def test_old_evaluator_is_drained_before_new_health_and_interruption_is_replayable(self):self.handoff('health_registered')
    def test_interruption_after_health_before_handoff_receipt_reconciles_without_old_evaluator(self):self.handoff('health_armed_before_handoff_receipt')
    def test_interrupted_bootout_retains_native_old_evaluator_identities_for_replay(self):self.handoff('guardian_service_unloaded')
    def test_interruption_after_quiescence_keeps_previous_health_and_owned_fence(self):self.handoff('guardian_quiesced')
    def test_interruption_after_publish_cannot_arm_health_under_previous_evaluator(self):self.handoff('bundle_published_before_health')


if __name__=='__main__':unittest.main()
