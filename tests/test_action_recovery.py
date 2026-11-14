import datetime as dt
import functools
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest import mock
import action_store as A
import phase_runtime as P
import project_action as PA
import project_backend as B
import durability as D


class ActionRecoveryTests(unittest.TestCase):
    def test_backend_capability_timeout_is_confined_to_its_action_before_product_spawn(self):
        import midnight as M
        job=self.store.submit('notes',{'source_head':'base','brief':{},'worker_model':{},'session':'session'},time.time()+60)
        other=self.store.submit('inventory',{},time.time()+60)
        timing={'active':True,'cutoff':dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=1)}
        with mock.patch.object(PA,'ROOT',self.root),mock.patch.object(PA,'source_head',return_value='base'), \
             mock.patch.object(M,'registry',return_value={'projects':[self.project]}),mock.patch.object(M,'window',return_value=timing), \
             mock.patch.object(M,'validate_backend',side_effect=subprocess.TimeoutExpired(['fixture','capabilities'],10)):
            launch=mock.Mock()
            with self.assertRaises(subprocess.TimeoutExpired):PA.execute(self.runtime,job['id'],spawn=launch)
            launch.assert_not_called()
        self.assertEqual(self.store.get(job['id'])['state'],'failed');self.assertEqual(self.store.get(other['id'])['state'],'queued')

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.runtime=self.root/'runtime';self.runtime.mkdir()
        self.store=A.Actions(self.runtime)
        self.project={'id':'notes','on_schedule':True,'path':str(self.root),'state_dir':str(self.root/'project'),
            'controller':'fake.py'}
        (self.root/'project').mkdir()

    def job(self):
        job=self.store.submit('notes',{'source_head':'base','brief':{},'worker_model':{},'session':'session'},time.time()+60)
        self.assertTrue(self.store.claim(job['id'],P.lease()));return job

    def test_disabled_or_paused_project_never_spawns(self):
        import midnight as M
        registry={'projects':[self.project]}
        timing={'active':True,'cutoff':dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=1)}
        for change in ('disabled','project_pause','pipeline_pause'):
            with self.subTest(change=change):
                job=self.store.submit('notes',{'source_head':'base'},time.time()+60)
                self.project['on_schedule']=change!='disabled'
                pause=(self.root/'project'/'PAUSED') if change=='project_pause' else self.runtime/'PAUSED'
                if change!='disabled':pause.touch()
                with mock.patch.object(PA,'ROOT',self.root),mock.patch.object(M,'registry',return_value=registry),mock.patch.object(M,'window',return_value=timing):
                    launch=mock.Mock();self.assertEqual(PA.execute(self.runtime,job['id'],spawn=launch),0);launch.assert_not_called()
                self.assertEqual(self.store.get(job['id'])['state'],'interrupted')
                pause.unlink(missing_ok=True)

    def test_backend_registration_rejects_a_forged_parent_before_state_access(self):
        job=self.job()
        with self.assertRaises(ValueError):PA.register_backend(self.runtime,job['id'],'notes')
        self.assertFalse((self.runtime/'actions'/job['id']/'backend-lease.json').exists())

    def test_guard_startup_ignores_candidate_python_imports(self):
        work=self.root/'candidate';work.mkdir();canary=self.root/'outside-candidate'
        (work/'argparse.py').write_text(f'from pathlib import Path\nPath({str(canary)!r}).write_text("unsafe startup")\n')
        env=dict(os.environ,PYTHONPATH=str(work),PYTHONHOME=str(work/'fake-python-home'))
        result=subprocess.run([sys.executable,'-I','-B',str(Path(P.__file__).with_name('phase_guard.py')),'--help'],
            cwd=work,env=env,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr);self.assertFalse(canary.exists())

    def test_new_execution_member_invalidates_old_clear_snapshot_and_keeps_capacity(self):
        job=self.job();parent={'pid':99999999,'stamp':'gone','boot_id':D.boot_id(),'group':True}
        phase=self.store.phase(job['id'],'notes','host','check',time.time()+30,{})
        self.store.phase_update(phase['id'],'running',lease=parent);self.store.execution(phase['id'],parent)
        snapshot=self.store.executions()[0]['lease']
        with subprocess.Popen(['/bin/sleep','20']) as child:
            member=P.lease(child.pid)
            self.store.execution_members(phase['id'],[member])
            try:
                self.assertFalse(self.store.clear_execution(phase['id'],snapshot))
                self.assertEqual(len(self.store.executions()),1)
                with self.assertRaises(P.PhaseInterrupted):
                    with P.resource(self.runtime,'host',time.time()+.08,poll=.01,pressure=lambda:False):pass
            finally:child.terminate();child.wait(timeout=3)

    def test_real_guard_runs_command_and_journals_before_execution(self):
        job=self.job();folder=self.root/'result';folder.mkdir();marker=folder/'ran'
        args=[sys.executable,'-c',f'from pathlib import Path; Path({str(marker)!r}).write_text("ok")']
        begin=time.monotonic()
        with mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':job['id']}):
            with P.phase(self.runtime,'notes','host',args,folder,folder/'output',time.time()+15) as identity:
                launch=folder/'launch.json';launch.write_text(json.dumps({'command':args,'cwd':str(folder),'slot_name':P.SLOT_NAMES[P.SLOT_FD]}))
                proc=subprocess.Popen([sys.executable,'-B',str(Path(P.__file__).with_name('phase_guard.py')),
                    '--runtime',str(self.runtime),'--phase',identity,'--launch',str(launch),'--slot',str(P.SLOT_FD)],pass_fds=(P.SLOT_FD,),start_new_session=True)
                self.assertEqual(proc.wait(timeout=8),0)
                item=next(p for p in self.store.phases() if p['id']==identity)
                self.assertEqual(item['lease']['pid'],proc.pid);self.assertTrue(item['lease']['group'])
                self.store.phase_update(identity,'succeeded',result={'code':0})
        self.assertEqual(marker.read_text(),'ok');self.assertLess(time.monotonic()-begin,5)

    def test_dead_guard_with_surviving_group_fences_the_slot_until_drained(self):
        job=self.job();folder=self.root/'result';folder.mkdir();marker=folder/'child'
        args=[sys.executable,'-c',f'import os,signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)']
        group=None
        try:
            with mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':job['id']}):
                with P.phase(self.runtime,'notes','local',args,folder,folder/'output',time.time()+20) as identity:
                    launch=folder/'launch.json';launch.write_text(json.dumps({'command':args,'cwd':str(folder),'slot_name':P.SLOT_NAMES[P.SLOT_FD]}))
                    proc=subprocess.Popen([sys.executable,'-B',str(Path(P.__file__).with_name('phase_guard.py')),
                        '--runtime',str(self.runtime),'--phase',identity,'--launch',str(launch),'--slot',str(P.SLOT_FD)],pass_fds=(P.SLOT_FD,),start_new_session=True)
                    end=time.monotonic()+5
                    while not marker.exists() and time.monotonic()<end:time.sleep(.02)
                    self.assertTrue(marker.exists())
                    group=next(p for p in self.store.executions() if p['phase']==identity)['lease']
                    proc.kill();proc.wait(timeout=3)
                # The flock is now free, but the immutable live group still
                # owns capacity. A different project must not start Qwen.
                with self.assertRaises(P.PhaseInterrupted):
                    with P.resource(self.runtime,'local',time.time()+.1,poll=.01,pressure=lambda:False):pass
                self.assertTrue(P.stop_tree(group,grace=.05))
                self.store.phase_update(identity,'interrupted',result={'reason':'crash_reconciled'})
                with P.resource(self.runtime,'local',time.time()+1,pressure=lambda:False):pass
        finally:
            if group:P.stop_tree(group,grace=.05)

    def test_resource_wait_does_not_charge_or_start_a_process(self):
        instance=B.load_backend(self.root,'notes');instance.STATE=self.root/'project';instance.CLOUD_RUNTIME=self.runtime
        job=self.job();charge=mock.Mock()
        with P.resource(self.runtime,'local',time.time()+1,pressure=lambda:False):
            with mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':job['id']}),mock.patch.object(instance,'_run_process') as process:
                with self.assertRaises(P.PhaseInterrupted):
                    instance.charged_run(['fake-local'],self.root,self.root/'worker.log',10,
                        dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=.08),{},charge,progress=self.root/'events')
                charge.assert_not_called();process.assert_not_called()

    def test_workspace_preparation_has_live_host_ownership_and_drains_at_cutoff(self):
        instance=B.load_backend(self.root,'notes');instance.STATE=self.root/'project';instance.CLOUD_RUNTIME=self.runtime
        job=self.job();marker=self.root/'clone-started';tool=self.root/'fake-git'
        tool.write_text(f'#!{sys.executable}\nimport time\nfrom pathlib import Path\nPath({str(marker)!r}).write_text("started")\ntime.sleep(15)\n')
        tool.chmod(0o700);instance.GIT=str(tool);errors=[]
        def clone():
            try:instance.clone_workspace(self.root/'work',self.root,dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=5),{})
            except Exception as exc:errors.append(exc)
        with mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':job['id']}), \
             mock.patch.object(P,'resource',functools.partial(P.resource,pressure=lambda:False)):
            thread=threading.Thread(target=clone);thread.start();end=time.monotonic()+4
            while not marker.exists() and time.monotonic()<end:time.sleep(.01)
            self.assertTrue(marker.exists())
            phases=self.store.phases();self.assertEqual(len(phases),1)
            self.assertEqual(phases[0]['kind'],'host');self.assertEqual(phases[0]['state'],'running')
            self.assertTrue(P.live_tree(self.store.executions()[0]['lease']))
            thread.join(8);self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],P.PhaseInterrupted)
        self.assertFalse(self.store.executions());self.assertFalse(self.store.phases())
        self.assertEqual(self.store.session_charges('session','notes'),{})

    def test_workspace_command_cap_is_an_interruption_before_the_job_cutoff(self):
        instance=B.load_backend(self.root,'notes');instance.STATE=self.root/'project';instance.CLOUD_RUNTIME=self.runtime
        job=self.job();tool=self.root/'fake-git'
        tool.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(15)\n');tool.chmod(0o700);instance.GIT=str(tool)
        original=instance.run
        def capped(command,cwd,log,seconds,cutoff,state):return original(command,cwd,log,.4,cutoff,state)
        cutoff=dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=20)
        with mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':job['id']}),mock.patch.object(instance,'run',side_effect=capped), \
             mock.patch.object(P,'resource',functools.partial(P.resource,pressure=lambda:False)):
            with self.assertRaises(P.PhaseInterrupted):instance.clone_workspace(self.root/'work',self.root,cutoff,{})
        self.assertLess(dt.datetime.now(dt.timezone.utc),cutoff)
        self.assertFalse(self.store.executions());self.assertFalse(self.store.phases())
        self.assertEqual(self.store.session_charges('session','notes'),{})

    def test_changed_verification_checkpoint_holds_for_manager_before_cloning(self):
        instance=B.load_backend(self.root,'notes');instance.STATE=self.root/'project';instance.CLOUD_RUNTIME=self.runtime
        task={'id':'VERIFY','verification_only':True};state={}
        with mock.patch.object(instance,'git',side_effect=lambda *args,**kwargs:'a'*40 if args[:2]==('rev-parse','HEAD') else ''), \
             mock.patch.object(instance,'feature_gate'),mock.patch.object(instance,'inspect_candidate',return_value={'usable':False,'status':'unusable','reason':'source_or_acceptance_changed'}), \
             mock.patch.object(instance,'clone_workspace') as clone:
            with self.assertRaises(instance.ManagerHeld):instance.attempt(task,state,{},dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=1))
        clone.assert_not_called();self.assertFalse(state.get('active_attempt'));self.assertEqual(state.get('failures_today',0),0)

    def test_scope_feedback_names_only_offenders_and_preserves_the_rejected_draft(self):
        directory=self.root/'receipt';directory.mkdir();work=directory/'work';work.mkdir();(work/'src').mkdir();(work/'tests').mkdir()
        def git(*args):return subprocess.check_output(['git',*args],cwd=work,text=True,stderr=subprocess.PIPE).strip()
        git('init','--quiet');git('config','user.name','Fixture');git('config','user.email','fixture@localhost')
        (work/'src/value.py').write_text('value=1\n');(work/'tests/test_value.py').write_text('expected=1\n')
        git('add','.');git('-c','commit.gpgsign=false','commit','--quiet','-m','fixture');base=git('rev-parse','HEAD')
        (work/'src/value.py').write_text('value=2\n');(work/'tests/test_value.py').write_text('expected=2\n')
        extra=work/'validate_fix.py';extra.write_text('print("unique diagnostic")\n')
        instance=B.load_backend(self.root,'notes',settings={'source_dirs':['src','tests'],'product_dirs':['src'],'source_globs':['src/*.py']})
        task={'id':'REPAIR','files':['src/value.py','tests/test_value.py']}
        before={str(p.relative_to(work)):p.read_bytes() for p in (work/'src/value.py',work/'tests/test_value.py',extra)}
        _,patch,problem=instance.checkpoint_candidate(task,{}, {'protected_test_files':[]},work,base,directory,'run')
        self.assertFalse(patch);self.assertIn('validate_fix.py',problem)
        self.assertNotIn('src/value.py',problem);self.assertNotIn('tests/test_value.py',problem)
        self.assertTrue(all((work/name).read_bytes()==data for name,data in before.items()))

    def test_tick_finalizes_a_held_dispatch_without_a_failure_or_recovery_loop(self):
        instance=B.load_backend(self.root,'notes',config_file='config.json');instance.STATE=self.root/'project';instance.CLOUD_RUNTIME=self.runtime
        instance.write(self.root/'config.json',{'max_attempts_per_night':None,'max_cloud_calls_per_night':None,'max_failures_per_night':None})
        task={'id':'VERIFY','verification_only':True}
        with mock.patch.object(instance,'recover'),mock.patch.object(instance,'next_task',return_value=task), \
             mock.patch.object(instance,'git',side_effect=lambda *args,**kwargs:'a'*40 if args[:2]==('rev-parse','HEAD') else ''), \
             mock.patch.object(instance,'feature_gate'),mock.patch.object(instance,'inspect_candidate',return_value={'usable':False,'status':'unusable','reason':'source_or_acceptance_changed'}), \
             mock.patch.object(instance,'clone_workspace') as clone,mock.patch.object(instance.subprocess,'Popen'):
            self.assertEqual(instance.tick(once=True,commission=True),0)
        state=instance.read(instance.STATE/'state.json');clone.assert_not_called()
        self.assertNotIn('active_attempt',state);self.assertEqual(state['failures_today'],0)
        self.assertEqual(state['local_attempts_today'],0);self.assertEqual(state['cloud_calls_today'],0)
        self.assertEqual(instance.read(instance.STATE/'status.json')['phase'],'manager_hold')
        receipts=list((instance.STATE/'held-dispatches').glob('*.json'));self.assertEqual(len(receipts),1)
        self.assertEqual(instance.read(receipts[0])['dispatch']['phase'],'dispatching')

    def test_stale_verification_does_not_hide_an_independent_ready_repair(self):
        instance=B.load_backend(self.root,'notes');instance.STATE=self.root/'project';instance.CLOUD_RUNTIME=self.runtime
        blocked={'id':'VERIFY','kind':'repair','verification_only':True,'finding_ids':['one'],'issue_episode':'one:1'}
        ready={'id':'READY','kind':'repair','finding_ids':['two'],'issue_episode':'two:1'}
        engine=mock.Mock();engine.status.return_value={'material_findings':[{'id':'one'},{'id':'two'}],'feature_allowed':False}
        def choose(head,tasks,**kwargs):
            runnable=[t for t in tasks if not t['needs_manager'] and not t['exhausted']]
            return {'action':'implement_locally','task':runnable[0]}
        engine.next_decision.side_effect=choose
        rescue=mock.Mock();rescue.eligibility.return_value={'status':'local_retries_required','allowed':False}
        state={}
        with mock.patch.object(instance,'git',return_value='a'*40),mock.patch.object(instance,'quality_engine',return_value=engine), \
             mock.patch.object(instance,'all_tasks',return_value=[blocked,ready]),mock.patch.object(instance,'task_valid',return_value=True), \
             mock.patch.object(instance,'task_done',return_value=False),mock.patch.object(instance,'import_episode_failures',side_effect=lambda t,s:t['issue_episode']), \
             mock.patch.object(instance,'rescue_store',return_value=rescue),mock.patch.object(instance,'unchecked_candidate',return_value=False), \
             mock.patch.object(instance,'inspect_candidate',return_value={'usable':False,'status':'unusable','reason':'source_or_acceptance_changed'}), \
             mock.patch.object(instance.Q,'source_candidate',return_value={'run':'retained'}),mock.patch.object(instance.PM,'status',return_value={'feature_hold':False}):
            selected=instance.next_task(state,{},dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=1))
        self.assertEqual(selected['id'],'READY');self.assertEqual(state['verification_holds']['VERIFY']['reason'],'source_or_acceptance_changed')

    def test_terminal_backend_result_keeps_a_surviving_execution_fenced(self):
        instance=B.load_backend(self.root,'notes');instance.STATE=self.root/'project';instance.CLOUD_RUNTIME=self.runtime
        job=self.job();marker=self.root/'worker-pid';output=[];group=None
        args=[sys.executable,'-c',f'import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(30)']
        try:
            with mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':job['id']}),mock.patch.object(P,'stop_tree',return_value=False):
                thread=threading.Thread(target=lambda:output.append(instance.run(args,self.root,self.root/'command.log',25,
                    dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=30),{})))
                thread.start();end=time.monotonic()+5
                while not marker.exists() and time.monotonic()<end:time.sleep(.02)
                self.assertTrue(marker.exists());group=self.store.executions()[0]['lease']
                os.kill(group['pid'],signal.SIGKILL);thread.join(6);self.assertFalse(thread.is_alive())
                self.assertEqual(output[0]['code'],-9)
                self.assertEqual(self.store.phases(active=False)[0]['state'],'failed')
                self.assertTrue(P.live_tree(group))
                with self.assertRaises(P.PhaseInterrupted):
                    with P.resource(self.runtime,'host',time.time()+.1,poll=.01,pressure=lambda:False):pass
            self.assertTrue(P.stop_tree(group,grace=.05))
            with P.resource(self.runtime,'host',time.time()+1,pressure=lambda:False):pass
        finally:
            if group:P.stop_tree(group,grace=.05)

    def test_guard_death_at_spawn_never_executes_an_unregistered_command(self):
        job=self.job();marker=self.root/'actual-command';folder=self.root/'launch';folder.mkdir()
        args=[sys.executable,'-c',f'import os,time;from pathlib import Path;Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(15)']
        script=('import sys,json,os,signal;sys.path.insert(0,sys.argv[1]);import phase_guard as G\n'
            'G.D.boot_id();original=G.subprocess.Popen\n'
            'def crash(*args,**kwargs):\n p=original(*args,**kwargs);os.kill(os.getpid(),signal.SIGKILL);return p\n'
            'G.subprocess.Popen=crash\n'
            'G.execute(G.Path(sys.argv[2]),sys.argv[3],json.loads(G.Path(sys.argv[4]).read_text()),int(sys.argv[5]))\n')
        execution=None
        try:
            with mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':job['id']}):
                with P.phase(self.runtime,'notes','host',args,self.root,folder/'output',time.time()+20) as identity:
                    launch=folder/'launch.json';launch.write_text(json.dumps({'command':args,'cwd':str(self.root),'slot_name':P.SLOT_NAMES[P.SLOT_FD]}))
                    proc=subprocess.Popen([sys.executable,'-B','-c',script,str(Path(P.__file__).parent),str(self.runtime),identity,str(launch),str(P.SLOT_FD)],
                        pass_fds=(P.SLOT_FD,),start_new_session=True)
                    self.assertEqual(proc.wait(timeout=5),-9)
                time.sleep(.3)
                execution=self.store.executions()[0]['lease']
                if marker.exists():
                    self.assertTrue(execution['members']);self.assertTrue(P.live_tree(execution))
                    with self.assertRaises(P.PhaseInterrupted):
                        with P.resource(self.runtime,'host',time.time()+.1,poll=.01,pressure=lambda:False):pass
                else:
                    with P.resource(self.runtime,'host',time.time()+1,pressure=lambda:False):pass
        finally:
            if execution:P.stop_tree(execution,grace=.05)


if __name__=='__main__':unittest.main()
