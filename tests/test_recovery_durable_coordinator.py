"""A native independent consumer survives a hard-killed coordinator helper."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock
import coordinator_install as C
import test_recovery_rollback_generation as F

HARNESS=r'''
import importlib.util,json,os,subprocess,sys,time
from pathlib import Path
mode,root,here,actual,home=sys.argv[1:];root=Path(root);here=Path(here);actual=Path(actual);home=Path(home)
sys.path.insert(0,str(actual))
if mode=='resumer':
 receipt=json.loads((here/'coordinator-resumer.json').read_text())
 spec=importlib.util.spec_from_file_location('frozen_coordinator',receipt['helper']);C=importlib.util.module_from_spec(spec);spec.loader.exec_module(C)
else:import coordinator_install as C
C.ROOT=root;C.HERE=here;C.Path.home=classmethod(lambda cls:home);C.authority_current=lambda bindings:True
C.approved_authority=lambda review:{'fixed':'approved'};C.A.check=lambda root:{'permission_sha256':'fixture'}
statepath=here/'native-services.json';actual_run=subprocess.run

def services(command,*args,**kwargs):
 if command[0]!='/bin/launchctl':return actual_run(command,*args,**kwargs)
 state=C.D.read_json(statepath);is_guardian=command[-1].endswith('midnight-guardian') or command[-1].endswith('midnight-guardian.plist')
 if command[1]=='kill':
  state['controller_stopped']=True;C.save(statepath,state)
  if mode=='stop-helper':
   (here/'native-kill-point').write_text('controller stop after independent recovery was armed')
   while True:time.sleep(1)
  return subprocess.CompletedProcess(command,0,'','')
 if command[1]=='bootstrap':
  value=__import__('plistlib').loads(Path(command[-1]).read_bytes());is_guardian=value['Label']=='space.venin.midnight-guardian'
 key='guardian' if is_guardian else 'resumer'
 if command[1]=='print':return subprocess.CompletedProcess(command,0 if state.get(key) else 113,'state = waiting\n' if state.get(key) else '', '' if state.get(key) else 'Could not find service fixture')
 if command[1]=='bootstrap':
  state[key]=True
  if key=='resumer' and not state.get('resumer_pid'):
   error=(here/'native-resumer.stderr.log').open('ab')
   proc=subprocess.Popen([sys.executable,'-I','-B',__file__,'resumer',str(root),str(here),str(actual),str(home)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=error,start_new_session=True)
   error.close()
   state['resumer_pid']=proc.pid
  C.save(statepath,state)
 elif command[1]=='bootout':
  state[key]=False;C.save(statepath,state)
  if key=='guardian' and mode=='helper':
   (here/'native-kill-point').write_text('guardian unloaded after independent resumer was loaded')
   while True:time.sleep(1)
 else:raise AssertionError(command)
 return subprocess.CompletedProcess(command,0,'','')
C.subprocess.run=services
intent=C.D.read_json(here/'fixture-intent.json');expected=(root/'runtime/INSTALLING').read_bytes()
actual_call=C.call
def calls(args,timeout=45):
 if args[:2]==['git','fetch']:
  C.D.atomic_write(root/'.git/FETCH_HEAD',(intent['installed']+'\n').encode());return ''
 return actual_call(args,timeout=timeout)
C.call=calls

def bundle():
 value=C.D.read_json(here/'new-manifest.json');C.save(root/'runtime/resilience/manifest.json',value)
 state=C.D.read_json(statepath);state['guardian']=True;C.save(statepath,state)
 return value

def restart(label):
 store=C.Actions(root/'runtime');health=store.meta('install_health');health.update(state='verified',verified_restart_epoch=health['restart_epoch']);store.meta('install_health',health,write=True)
 return {'requested':True}

def activate():
 value={'version':2,'installed':intent['installed'],'tree':intent['tree']};C.Actions(root/'runtime').meta('maintenance_recovery',value,write=True);return value

actual_finish=C.finish_installation
def released(stage):
 if mode=='release-helper' and stage=='fence_released':
  (here/'native-kill-point').write_text('owned fence removed before completion persistence')
  while True:time.sleep(1)
def finish(*args,**kwargs):
 kwargs.setdefault('checkpoint',released)
 kwargs.setdefault('install_bundle',bundle);kwargs.setdefault('restart',restart);kwargs.setdefault('activate',activate)
 return actual_finish(*args,**kwargs)
C.finish_installation=finish
if mode=='helper':
 with C.installation_lock():C.establish_guardian_generation(intent,expected,lambda:True,install_bundle=bundle)
elif mode=='adopt-helper':
 with C.installation_lock():
  C.prepare_coordinator_recovery(intent['previous'],intent['installed'],intent['tree'],intent['bindings'])
  def adopted(stage):
   if stage=='source_adopted':
    (here/'native-kill-point').write_text('source adopted before installation receipt')
    while True:time.sleep(1)
  with C.P.source_lock(root/'runtime',deadline=time.time()+15):C.adopt(intent['previous'],intent['installed'],intent['tree'],intent['bindings'],lambda:True,checkpoint=adopted)
elif mode=='intent-helper':
 actual_save=C.save
 def fail_intent(path,value):
  if Path(path)==here/'installation-intent.json':
   (here/'native-intent-failure').write_text('intent persistence failed after recovery was armed')
   raise OSError('fixture intent persistence failure')
  return actual_save(path,value)
 C.save=fail_intent
 C.main()
elif mode in {'stop-helper','normal-helper','release-helper'}:C.main()
else:
 receipt=C.D.read_json(here/'coordinator-resumer.json');until=time.time()+20
 while time.time()<until:
  if (here/'suspend-native-tick').exists():time.sleep(.03);continue
  result=C.resume_coordinator(receipt['helper_sha256'])
  C.save(here/'native-resumer-tick.json',result)
  if result.get('state') in {'complete','retired'}:
   C.disarm_coordinator_resumer();break
  time.sleep(.1)
'''


class DurableCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.f=F.RollbackGenerationTests();self.f.setUp();self.addCleanup(self.f.doCleanups);f=self.f
        f.root=f.root.resolve();f.runtime=f.root/'runtime';f.store=C.Actions(f.runtime)
        actual=Path(C.__file__).resolve().parent
        for name in subprocess.check_output(['/usr/bin/git','ls-files'],cwd=actual,text=True).splitlines():
            source=actual/name
            if source.is_file():
                target=f.root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source.read_bytes())
        (f.root/'.gitignore').write_text('runtime/\n')
        (f.root/'midnight.py').write_text('# restored controller\n');(f.root/'projects.json').write_text('{"projects":[]}\n');f.git('add','.');f.git('-c','commit.gpgsign=false','commit','-qm','fixture registry');f.base=f.git('rev-parse','HEAD')
        (f.root/'midnight.py').write_text('# rejected controller\n');f.git('add','midnight.py');f.git('-c','commit.gpgsign=false','commit','-qm','fixture checked candidate');f.bad=f.git('rev-parse','HEAD')
        self.actual=Path(C.__file__).resolve().parent;self.here=f.runtime/'durable-coordinator';self.here.mkdir()
        self.home=f.runtime/'fixture-home';(self.home/'Library/LaunchAgents').mkdir(parents=True)
        digest=hashlib.sha256(Path(C.__file__).read_bytes()).hexdigest();self.digest=digest
        patch=(f.git('diff','--binary',f.base,f.bad)+'\n').encode();(self.here/'candidate.patch').write_bytes(patch);patchsha=hashlib.sha256(patch).hexdigest()
        controls={'mode':'continuous','expires_at':None,'created_at':'2026-11-01T13:22:02.327074+00:00'};C.save(f.runtime/'override.json',controls)
        self.intent={'previous':f.base,'installed':f.bad,'tree':f.git('rev-parse','HEAD^{tree}'),'bindings':{'fixed':'approved',
            'fence_sha256':hashlib.sha256(f.fence).hexdigest(),'controls':controls,'pauses':{str(f.runtime/'PAUSED'):False},'patch_sha256':patchsha,'installer_sha256':digest}}
        C.save(self.here/'installation-intent.json',self.intent);C.save(self.here/'fixture-intent.json',self.intent);(self.here/'quiesce.json').write_bytes(f.fence)
        C.save(self.here/'deployment-review.json',{'decision':'accept','issues':[],'installer_sha256':digest})
        C.save(self.here/'verified.json',{'base':f.base,'tree':self.intent['tree'],'patch_sha256':patchsha,'review':{'decision':'accept','issues':[]},'checks':[{'code':0,'reason':'exited'}]});C.save(self.here/'delivery.json',{'status':'merged','commit':f.bad})
        f.store.meta('install_health',{'state':'verified','installed':f.base,'tree':f.git('rev-parse',f.base+'^{tree}'),'previous':f.base,'at':time.time()-1000},write=True)
        resilience=f.runtime/'resilience';resilience.mkdir();bootstrap=(self.actual/'guardian_bootstrap.py').read_bytes()
        (resilience/'guardian_bootstrap.py').write_bytes(bootstrap)
        manifests=[]
        for source in (f.base,f.bad):
            bundle=resilience/'bundles'/source;bundle.mkdir(parents=True)
            for name in ('guardian.py','guardian_bootstrap.py'):(bundle/name).write_bytes((self.actual/name).read_bytes())
            manifests.append({'source':source,'canonical':str(f.root),'bundle':str(bundle),'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.iterdir()}})
        C.save(resilience/'manifest.json',manifests[0]);C.save(self.here/'new-manifest.json',manifests[1])
        guardian=self.home/'Library/LaunchAgents/space.venin.midnight-guardian.plist';guardian.write_bytes(plistlib.dumps({
            'Label':'space.venin.midnight-guardian','ProgramArguments':[sys.executable,'-B',str(resilience/'guardian_bootstrap.py')],'WorkingDirectory':str(resilience)}))
        self.effects=[];self.loaded=False;self.fail_bootstrap=False;actual_run=subprocess.run
        def services(command,*args,**kwargs):
            if command[0]!='/bin/launchctl':return actual_run(command,*args,**kwargs)
            if command[1]=='print':return subprocess.CompletedProcess(command,0 if self.loaded else 113,'state = waiting\n' if self.loaded else '', '' if self.loaded else 'Could not find service fixture')
            self.effects.append(command)
            if command[1]=='bootstrap':
                if self.fail_bootstrap:raise subprocess.CalledProcessError(5,command)
                self.loaded=True
            elif command[1]=='bootout':self.loaded=False
            else:raise AssertionError(command)
            return subprocess.CompletedProcess(command,0,'','')
        for patch in (mock.patch.object(C,'ROOT',f.root),mock.patch.object(C,'HERE',self.here),mock.patch.object(C,'git',side_effect=f.git),
                      mock.patch.object(C.Path,'home',return_value=self.home),mock.patch.object(C,'authority_current',return_value=True),
                      mock.patch.object(C.subprocess,'run',side_effect=services)):
            patch.start();self.addCleanup(patch.stop)

    def native_recovery(self,mode):
        f=self.f
        if mode in {'adopt-helper','stop-helper'}:
            f.git('reset','--hard',f.base);(self.here/'installation-intent.json').unlink()
        harness=self.here/'native-harness.py';harness.write_text(HARNESS)
        C.save(self.here/'native-services.json',{'guardian':True,'resumer':False})
        # Restore the real native process launcher; the child's service adapter
        # models only launchctl. Both consumers execute the actual checked code.
        helper=subprocess.Popen([sys.executable,'-I','-B',str(harness),mode,str(f.root),str(self.here),str(self.actual),str(self.home)],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True)
        f.children.append(helper);self.addCleanup(helper.stderr.close)
        deadline=time.time()+8
        while not (self.here/'native-kill-point').exists() and helper.poll() is None and time.time()<deadline:time.sleep(.03)
        if not (self.here/'native-kill-point').exists():
            helper.kill();helper.wait(3);self.fail('native helper failed before kill point: '+helper.stderr.read().decode()[-1500:])
        state=C.D.read_json(self.here/'native-services.json');self.assertTrue(state['resumer']);self.assertEqual(state['guardian'],mode!='helper')
        lease=C.P.lease(state['resumer_pid']);self.assertTrue(C.P.alive(lease),(self.here/'native-resumer.stderr.log').read_text());self.addCleanup(lambda:C.P.stop_tree(lease,grace=1) if C.P.alive(lease) else None)
        # The independent tick observes the still-live flow owner and yields.
        deadline=time.time()+3
        while C.D.read_json(self.here/'native-resumer-tick.json').get('state')!='owner_active' and time.time()<deadline:time.sleep(.03)
        self.assertEqual(C.D.read_json(self.here/'native-resumer-tick.json').get('state'),'owner_active')
        definition=plistlib.loads(Path(C.D.read_json(self.here/'coordinator-resumer.json')['plist']).read_bytes())
        helper.kill();helper.wait(3);self.assertEqual(helper.returncode,-signal.SIGKILL)
        deadline=time.time()+12
        while C.D.read_json(self.here/'coordinator-resumer-status.json').get('state')!='complete' and time.time()<deadline:time.sleep(.05)
        status=C.D.read_json(self.here/'coordinator-resumer-status.json');self.assertEqual(status.get('state'),'complete',status)
        self.assertEqual(C.D.read_json(self.here/'installation-complete.json')['installed'],f.bad)
        self.assertTrue(C.D.read_json(self.here/'native-services.json')['guardian']);self.assertFalse((f.runtime/'INSTALLING').exists())
        self.assertEqual(f.git('rev-parse','HEAD'),f.bad);self.assertEqual(f.store.meta('maintenance_recovery')['installed'],f.bad)
        self.assertEqual(definition['StartInterval'],60);self.assertTrue(definition['RunAtLoad'])
        deadline=time.time()+3
        while Path(C.D.read_json(self.here/'coordinator-resumer.json')['plist']).exists() and time.time()<deadline:time.sleep(.03)
        self.assertFalse(Path(C.D.read_json(self.here/'coordinator-resumer.json')['plist']).exists())
        self.assertEqual(C.D.read_json(self.here/'coordinator-resumer-retired.json')['state'],'retired')
        with f.store.read() as con:self.assertEqual(con.execute('SELECT count(*) FROM wf_charges').fetchone()[0],0)

    def test_native_resumer_finishes_after_helper_sigkill_without_manual_reinvocation(self):self.native_recovery('helper')
    def test_native_resumer_finishes_after_source_adoption_sigkill_without_manual_reinvocation(self):self.native_recovery('adopt-helper')
    def test_native_resumer_is_armed_before_controller_stop_and_recovers_sigkill(self):self.native_recovery('stop-helper')

    def test_scheduled_resumer_continues_after_pre_adoption_intent_write_failure(self):
        f=self.f;f.git('reset','--hard',f.base);(self.here/'installation-intent.json').unlink()
        harness=self.here/'native-harness.py';harness.write_text(HARNESS)
        C.save(self.here/'native-services.json',{'guardian':True,'resumer':False})
        suspended=self.here/'suspend-native-tick';suspended.touch()
        helper=subprocess.Popen([sys.executable,'-I','-B',str(harness),'intent-helper',str(f.root),str(self.here),str(self.actual),str(self.home)],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True)
        f.children.append(helper);self.addCleanup(helper.stderr.close)
        helper.wait(8);self.assertNotEqual(helper.returncode,0)
        self.assertIn('fixture intent persistence failure',helper.stderr.read().decode())
        self.assertTrue((self.here/'native-intent-failure').exists())
        self.assertTrue((self.here/'coordinator-resumer-ready.json').exists())
        self.assertFalse((self.here/'installation-intent.json').exists())
        self.assertEqual((f.runtime/'INSTALLING').read_bytes(),f.fence)
        self.assertTrue(C.D.read_json(self.here/'installation-held.json')['fence_retained'])
        self.assertEqual(f.git('rev-parse','HEAD'),f.base)
        state=C.D.read_json(self.here/'native-services.json');lease=C.P.lease(state['resumer_pid'])
        self.assertTrue(state['resumer']);self.assertTrue(C.P.alive(lease))
        self.addCleanup(lambda:C.P.stop_tree(lease,grace=1) if C.P.alive(lease) else None)
        # The already scheduled native consumer handles the next tick itself.
        suspended.unlink();deadline=time.time()+12
        while C.D.read_json(self.here/'coordinator-resumer-status.json').get('state')!='complete' and time.time()<deadline:time.sleep(.05)
        status=C.D.read_json(self.here/'coordinator-resumer-status.json');self.assertEqual(status.get('state'),'complete',status)
        self.assertEqual(C.D.read_json(self.here/'installation-complete.json')['installed'],f.bad)
        self.assertEqual(f.git('rev-parse','HEAD'),f.bad);self.assertFalse((f.runtime/'INSTALLING').exists())
        deadline=time.time()+3
        while Path(C.D.read_json(self.here/'coordinator-resumer.json')['plist']).exists() and time.time()<deadline:time.sleep(.03)
        self.assertFalse(Path(C.D.read_json(self.here/'coordinator-resumer.json')['plist']).exists())
        self.assertEqual(C.D.read_json(self.here/'coordinator-resumer-retired.json')['state'],'retired')
        with f.store.read() as con:self.assertEqual(con.execute('SELECT count(*) FROM wf_charges').fetchone()[0],0)

    def test_resumer_bootstrap_failure_prevents_guardian_unload(self):
        self.fail_bootstrap=True;quiesce=mock.Mock(side_effect=AssertionError('unarmed recovery must not unload guardian'))
        with self.assertRaises(subprocess.CalledProcessError):C.establish_guardian_generation(self.intent,self.f.fence,lambda:True,quiesce=quiesce)
        quiesce.assert_not_called();self.assertFalse((self.here/'guardian-quiescence.json').exists())
        self.assertEqual((self.f.runtime/'INSTALLING').read_bytes(),self.f.fence)

    def test_changed_frozen_resumer_cannot_be_rearmed(self):
        receipt=C.arm_coordinator_resumer(self.intent);Path(receipt['helper']).write_text('# changed frozen helper\n');self.effects.clear()
        with self.assertRaisesRegex(RuntimeError,'Frozen coordinator resumer changed'):C.arm_coordinator_resumer(self.intent)
        self.assertEqual(self.effects,[])

    def test_changed_resumer_service_definition_is_preserved(self):
        receipt=C.arm_coordinator_resumer(self.intent);plist=Path(receipt['plist']);plist.write_bytes(b'foreign service definition');self.effects.clear()
        with self.assertRaisesRegex(RuntimeError,'service definition changed'):C.arm_coordinator_resumer(self.intent)
        self.assertEqual(plist.read_bytes(),b'foreign service definition');self.assertEqual(self.effects,[])

    def test_unaccepted_helper_cannot_arm_independent_recovery(self):
        C.save(self.here/'deployment-review.json',{'decision':'hold','issues':['not accepted'],'installer_sha256':self.digest})
        with self.assertRaisesRegex(RuntimeError,'exact reviewed private installation'):C.arm_coordinator_resumer(self.intent)
        self.assertEqual(self.effects,[]);self.assertFalse((self.here/'coordinator-resumer.json').exists())


    def frozen_consumer(self,receipt):
        spec=importlib.util.spec_from_file_location('retirement_coordinator',receipt['helper']);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.ROOT=self.f.root;module.HERE=self.here
        return module

    def completed_resumer(self):
        receipt=C.arm_coordinator_resumer(self.intent)
        fields={'installed':self.f.bad,'tree':self.intent['tree'],'fence_sha256':self.intent['bindings']['fence_sha256']}
        for name,value in (('guardian-manifest.json',{'source':self.f.bad}),('service-restarts.json',{'installed':self.f.bad}),('activation.json',{'version':2,'installed':self.f.bad,'tree':self.intent['tree']})):C.save(self.here/name,value)
        C.save(self.here/'fence-release-intent.json',{**fields,'receipts':{name:hashlib.sha256((self.here/name).read_bytes()).hexdigest() for name in ('guardian-manifest.json','service-restarts.json','activation.json')}})
        C.save(self.here/'installation-complete.json',{**fields,'state':'complete'})
        return receipt

    def assert_retired_tick(self,receipt):
        module=self.frozen_consumer(receipt);resume=mock.Mock(side_effect=AssertionError('retired installer cannot resume'))
        with mock.patch.object(module,'ensure_guardian_evaluator',side_effect=AssertionError('retired installer cannot reload evaluator')):
            result=module.resume_coordinator(receipt['helper_sha256'],resume=resume)
        self.assertEqual(result['state'],'retired');resume.assert_not_called()

    def test_retirement_removes_owned_plist_and_login_tick_cannot_resume(self):
        receipt=self.completed_resumer();plist=Path(receipt['plist']);self.assertTrue(C.disarm_coordinator_resumer())
        self.assertFalse(plist.exists());self.assert_retired_tick(receipt);self.assertTrue(C.disarm_coordinator_resumer())
        self.assertFalse(plist.exists());self.assertEqual(C.D.read_json(self.here/'coordinator-resumer-retired.json')['state'],'retired')

    def test_retirement_before_plist_removal_recovers_idempotently(self):
        receipt=self.completed_resumer();plist=Path(receipt['plist']);marker=C.D.marker
        def interrupted(path,present):
            if Path(path)==plist and not present:raise OSError('retirement committed before plist removal')
            return marker(path,present)
        with mock.patch.object(C.D,'marker',side_effect=interrupted):
            with self.assertRaisesRegex(OSError,'before plist removal'):C.disarm_coordinator_resumer()
        self.assertTrue(plist.exists());self.assert_retired_tick(receipt)
        self.assertTrue(C.disarm_coordinator_resumer());self.assertFalse(plist.exists())

    def test_retirement_after_plist_removal_recovers_bootout_without_reload(self):
        receipt=self.completed_resumer();plist=Path(receipt['plist']);actual=subprocess.run
        def interrupted(command,*args,**kwargs):
            if command[:2]==['/bin/launchctl','bootout']:raise OSError('plist removed before service bootout')
            return actual(command,*args,**kwargs)
        with mock.patch.object(C.subprocess,'run',side_effect=interrupted):
            with self.assertRaisesRegex(OSError,'before service bootout'):C.disarm_coordinator_resumer()
        self.assertFalse(plist.exists());self.assert_retired_tick(receipt)
        self.assertTrue(C.disarm_coordinator_resumer());self.assertFalse(plist.exists())

    def test_retirement_preserves_replaced_plist_and_does_not_bootout_its_owner(self):
        receipt=self.completed_resumer();plist=Path(receipt['plist']);marker=C.D.marker
        def interrupted(path,present):
            if Path(path)==plist and not present:plist.write_bytes(b'foreign replacement');raise OSError('plist replaced during retirement')
            return marker(path,present)
        with mock.patch.object(C.D,'marker',side_effect=interrupted):
            with self.assertRaises(OSError):C.disarm_coordinator_resumer()
        self.effects.clear()
        with self.assertRaisesRegex(RuntimeError,'Preserve changed'):C.disarm_coordinator_resumer()
        self.assertEqual(plist.read_bytes(),b'foreign replacement');self.assertEqual(self.effects,[]);self.assert_retired_tick(receipt)


    def terminal_tick_after_change(self,dirty):
        receipt=self.completed_resumer();module=self.frozen_consumer(receipt)
        if dirty:(self.f.root/'midnight.py').write_text('# owner uncommitted changes\n')
        else:
            (self.f.root/'midnight.py').write_text('# next reviewed generation\n');self.f.git('add','midnight.py');self.f.git('-c','commit.gpgsign=false','commit','-qm','subsequent source')
        before=self.f.git('rev-parse','HEAD');working=(self.f.root/'midnight.py').read_bytes()
        with mock.patch.object(module,'main',side_effect=AssertionError('terminal tick cannot reinstall')) as main, \
             mock.patch.object(module,'ensure_guardian_evaluator',side_effect=AssertionError('terminal tick cannot reload guardian')):
            result=module.resume_coordinator(receipt['helper_sha256'])
            self.assertEqual(result['state'],'complete');self.assertTrue(result['terminal_receipts']);main.assert_not_called()
            self.assertTrue(module.disarm_coordinator_resumer())
        self.assertFalse(Path(receipt['plist']).exists());self.assertEqual(self.f.git('rev-parse','HEAD'),before)
        self.assertEqual((self.f.root/'midnight.py').read_bytes(),working)

    def test_terminal_tick_retires_after_source_advances_without_installation_effects(self):self.terminal_tick_after_change(False)
    def test_terminal_tick_retires_with_dirty_owner_source_without_installation_effects(self):self.terminal_tick_after_change(True)

    def test_normal_main_retires_immediately_after_bound_completion(self):
        receipt=self.completed_resumer()
        with mock.patch.object(C,'_main',return_value={'state':'complete'}) as main:C.main()
        main.assert_called_once();self.assertFalse(Path(receipt['plist']).exists());self.assert_retired_tick(receipt)

    def test_native_normal_helper_removes_its_resumer_before_the_next_tick(self):
        f=self.f;harness=self.here/'native-harness.py';harness.write_text(HARNESS);C.save(self.here/'native-services.json',{'guardian':True,'resumer':False})
        helper=subprocess.Popen([sys.executable,'-I','-B',str(harness),'normal-helper',str(f.root),str(self.here),str(self.actual),str(self.home)],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True)
        f.children.append(helper);self.addCleanup(helper.stderr.close);helper.wait(timeout=10)
        self.assertEqual(helper.returncode,0,helper.stderr.read().decode()[-1000:])
        receipt=C.D.read_json(self.here/'coordinator-resumer.json');self.assertFalse(Path(receipt['plist']).exists())
        self.assertEqual(C.D.read_json(self.here/'coordinator-resumer-retired.json')['state'],'retired')
        state=C.D.read_json(self.here/'native-services.json');self.assertTrue(state['guardian']);self.assertFalse(state['resumer'])
        lease=C.P.lease(state['resumer_pid']);self.addCleanup(lambda:C.P.stop_tree(lease,grace=1) if C.P.alive(lease) else None)
        (f.root/'midnight.py').write_text('# later generation after normal completion\n');f.git('add','midnight.py');f.git('-c','commit.gpgsign=false','commit','-qm','source advanced before later tick')
        self.assert_retired_tick(receipt);self.assertFalse(Path(receipt['plist']).exists())


    def qualified_cli_adapter(self):
        f=self.f;effect=self.here/'frozen-entry-effects.json';dependency=f.root/'phase_runtime.py'
        stub="\nimport json as _fixture_json\n_fixture_run=subprocess.run\ndef _fixture_service(command,*args,**kwargs):\n if command[0]=='/bin/launchctl':\n  Path("+repr(str(effect))+").write_text(_fixture_json.dumps(command))\n  return subprocess.CompletedProcess(command,0,'','')\n return _fixture_run(command,*args,**kwargs)\nsubprocess.run=_fixture_service\n"
        dependency.write_text(dependency.read_text()+stub);f.git('add','phase_runtime.py');f.git('-c','commit.gpgsign=false','commit','-qm','isolated service adapter in qualified fixture')
        f.bad=f.git('rev-parse','HEAD');tree=f.git('rev-parse','HEAD^{tree}');patch=(f.git('diff','--binary',f.base,f.bad)+'\n').encode();patchsha=hashlib.sha256(patch).hexdigest()
        self.intent.update(installed=f.bad,tree=tree);self.intent['bindings']['patch_sha256']=patchsha
        C.save(self.here/'installation-intent.json',self.intent);C.save(self.here/'fixture-intent.json',self.intent)
        verified=C.D.read_json(self.here/'verified.json');verified.update(tree=tree,patch_sha256=patchsha);C.save(self.here/'verified.json',verified)
        C.save(self.here/'delivery.json',{'status':'merged','commit':f.bad});(self.here/'candidate.patch').write_bytes(patch)
        prior=C.D.read_json(self.here/'new-manifest.json');old=Path(prior['bundle']);new=f.runtime/'resilience/bundles'/f.bad;new.mkdir(parents=True)
        for name in prior['files']:(new/name).write_bytes((old/name).read_bytes())
        C.save(self.here/'new-manifest.json',{**prior,'source':f.bad,'bundle':str(new)})
        return effect

    def test_actual_launchagent_entrypoint_retires_with_unimportable_canonical_dependency(self):
        f=self.f;effect=self.qualified_cli_adapter();receipt=self.completed_resumer();definition=plistlib.loads(Path(receipt['plist']).read_bytes())
        # Use the actual argv from the generated LaunchAgent. No review-checkout
        # sys.path, import hooks or manually loaded helper are supplied here.
        broken=f.root/'durability.py';broken.write_text('def deliberately_unimportable(\n');before=f.git('rev-parse','HEAD')
        result=subprocess.run(definition['ProgramArguments'],cwd=self.here,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr[-1500:]);self.assertIn('"terminal_receipts": true',result.stdout)
        self.assertFalse(Path(receipt['plist']).exists());self.assertEqual(C.D.read_json(self.here/'coordinator-resumer-retired.json')['state'],'retired')
        self.assertEqual(broken.read_text(),'def deliberately_unimportable(\n');self.assertEqual(f.git('rev-parse','HEAD'),before)
        command=__import__('json').loads(effect.read_text());self.assertEqual(command[:2],['/bin/launchctl','bootout']);self.assertTrue(command[-1].endswith(receipt['label']))

    def test_actual_entrypoint_rejects_tampered_frozen_dependency_before_effects(self):
        receipt=self.completed_resumer();definition=plistlib.loads(Path(receipt['plist']).read_bytes())
        dependency=Path(receipt['helper']).parent/'source/durability.py';dependency.write_bytes(dependency.read_bytes()+b'\n# changed frozen dependency\n')
        result=subprocess.run(definition['ProgramArguments'],cwd=self.here,capture_output=True,text=True,timeout=20)
        self.assertNotEqual(result.returncode,0);self.assertIn('Frozen coordinator dependency changed',result.stderr)
        self.assertTrue(Path(receipt['plist']).exists());self.assertFalse((self.here/'coordinator-resumer-retired.json').exists())


    def released_entrypoint(self,dirty,foreign):
        f=self.f;effect=self.qualified_cli_adapter();harness=self.here/'native-harness.py';harness.write_text(HARNESS)
        C.save(self.here/'native-services.json',{'guardian':True,'resumer':False});(self.here/'suspend-native-tick').touch()
        helper=subprocess.Popen([sys.executable,'-I','-B',str(harness),'release-helper',str(f.root),str(self.here),str(self.actual),str(self.home)],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True)
        f.children.append(helper);self.addCleanup(helper.stderr.close);deadline=time.time()+10
        while not (self.here/'native-kill-point').exists() and helper.poll() is None and time.time()<deadline:time.sleep(.03)
        if not (self.here/'native-kill-point').exists():
            helper.kill();helper.wait(3);self.fail('release checkpoint was not reached: '+helper.stderr.read().decode()[-1200:])
        self.assertFalse((f.runtime/'INSTALLING').exists());self.assertFalse((self.here/'installation-complete.json').exists())
        state=C.D.read_json(self.here/'native-services.json');lease=C.P.lease(state['resumer_pid']);self.assertTrue(C.P.alive(lease))
        self.addCleanup(lambda:C.P.stop_tree(lease,grace=1) if C.P.alive(lease) else None)
        (f.root/'midnight.py').write_text('# owner source after dispatch release\n')
        if not dirty:f.git('add','midnight.py');f.git('-c','commit.gpgsign=false','commit','-qm','source advanced after release')
        foreign_bytes=b'foreign later installation fence\n'
        if foreign:(f.runtime/'INSTALLING').write_bytes(foreign_bytes)
        before=f.git('rev-parse','HEAD');working=(f.root/'midnight.py').read_bytes();health=f.store.meta('install_health')
        helper.kill();helper.wait(3);self.assertEqual(helper.returncode,-signal.SIGKILL)
        receipt=C.D.read_json(self.here/'coordinator-resumer.json');definition=plistlib.loads(Path(receipt['plist']).read_bytes())
        result=subprocess.run(definition['ProgramArguments'],cwd=self.here,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr[-1500:]);self.assertIn('"terminal_receipts": true',result.stdout)
        self.assertFalse(Path(receipt['plist']).exists());self.assertFalse((self.here/'installation-complete.json').exists())
        retired=C.D.read_json(self.here/'coordinator-resumer-retired.json');self.assertEqual(retired['completion_basis'],'bound_release_journal')
        self.assertEqual(retired['release_journal_sha256'],hashlib.sha256((self.here/'fence-release-intent.json').read_bytes()).hexdigest())
        self.assertEqual(f.git('rev-parse','HEAD'),before);self.assertEqual((f.root/'midnight.py').read_bytes(),working);self.assertEqual(f.store.meta('install_health'),health)
        if foreign:self.assertEqual((f.runtime/'INSTALLING').read_bytes(),foreign_bytes)
        else:self.assertFalse((f.runtime/'INSTALLING').exists())
        command=__import__('json').loads(effect.read_text());self.assertEqual(command[:2],['/bin/launchctl','bootout']);self.assertTrue(command[-1].endswith(receipt['label']))

    def test_actual_entrypoint_retires_after_release_then_source_advance_preserving_foreign_fence(self):self.released_entrypoint(False,True)
    def test_actual_entrypoint_retires_after_release_then_dirty_owner_edit(self):self.released_entrypoint(True,False)

    def test_bound_release_with_original_fence_still_present_cannot_retire(self):
        receipt=self.completed_resumer();(self.here/'installation-complete.json').unlink();self.effects.clear()
        self.assertFalse(C.completed_installation(receipt,self.intent));self.assertFalse(C.disarm_coordinator_resumer())
        self.assertTrue(Path(receipt['plist']).exists());self.assertEqual((self.f.runtime/'INSTALLING').read_bytes(),self.f.fence);self.assertEqual(self.effects,[])


if __name__=='__main__':unittest.main()
