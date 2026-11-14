import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import project_backend as B
import manager_recovery as M
import worker_quality as Q


class ManagerDispatchTests(unittest.TestCase):
    def intermediate_attempt(self):
        with mock.patch.object(self.s,'cloud',side_effect=self.cloud):task=self.call()
        for run in ('prior1','prior2'):M.ManagerRecovery(self.s.STATE,'notes').reserve(task,'value:1',self.head,'profile',run)
        proposal={'decision':'decompose','reason':'Smaller groups.','steps':[
            {'title':'One exact test','description':'Add the current scoped case.','acceptance':['Focused case passes.']},
            {'title':'Final tests','description':'Finish original criteria.','acceptance':['Full original contract remains.']}]}
        self.s.MS.MicroRecovery(self.s.STATE,'notes').approve(task,'value:1',self.head,'profile',proposal,
            dict(self.checkpoint,task_sha256=Q.task_fingerprint(task)),{'decision':'accept','issues':[],'receipt_sha256':'e'*64})
        self.s.quality_engine({}).get_finding.return_value={'id':'value','episode_id':'value:1'}
        self.state.update(attempts_today=0,local_attempts_today=0,cloud_calls_today=0)
        def clone(work,*args):
            self.git('clone','--quiet',str(self.root),str(work))
            (work/'.git/info/exclude').write_text('PRD.md\n.midnight-ready.json\n')
        def seed(task,state,config,work,base,directory):
            for name in self.parent['files']:(work/name).write_bytes((self.work/name).read_bytes())
            self.git('add','.',cwd=work)
            self.s.write(directory/'retry-seed.json',dict(self.checkpoint,run='failed'))
            self.s.write(directory/'retry-verification.json',{'first_verification':True})
            return 'Exact retained candidate.'
        def worker(*args,**kwargs):
            args[6]()
            (args[1]/'tests/test_value.py').write_text('import unittest\nclass ValueTest(unittest.TestCase):\n def test_value(self): self.assertEqual(2+2,4)\n')
            (args[2].parent/'worker-events.jsonl').write_text(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Implemented'}})+'\n')
            return {'code':0,'reason':'exited','seconds':1}
        def host(command,cwd,log,*args,**kwargs):
            actual=command[3:] if command[0]=='/usr/bin/sandbox-exec' else command
            with log.open('w') as out:result=subprocess.run(actual,cwd=cwd,env=kwargs.get('env'),stdout=out,stderr=subprocess.STDOUT,timeout=20)
            return {'code':result.returncode,'reason':'exited','seconds':0.1}
        def review(prompt,schema,directory,*args,**kwargs):
            directory.mkdir(parents=True,exist_ok=True);value={'decision':'accept','issues':[],'summary':'Current intermediate case passes.'}
            self.s.write(directory/'result.json',value);return value
        with mock.patch.object(self.s,'clone_workspace',side_effect=clone),mock.patch.object(self.s,'seed_retry',side_effect=seed), \
             mock.patch.object(self.s,'prepare_workspace'),mock.patch.object(self.s,'implementation_choice',return_value=('local','value:1')), \
             mock.patch.object(self.s,'charged_run',side_effect=worker),mock.patch.object(self.s,'run',side_effect=host), \
             mock.patch.object(self.s,'cloud',side_effect=review),mock.patch.object(self.s,'verify',side_effect=AssertionError('Intermediate cannot run final promotion path')):
            ok,run,feedback=self.s.attempt(task,self.state,{'worker_timeout_seconds':720,'protected_test_files':[]},self.s.now()+dt.timedelta(minutes=10))
        return task,ok,run,feedback

    def test_intermediate_attempt_runs_real_focused_tests_then_only_checkpoints(self):
        task,ok,run,feedback=self.intermediate_attempt()
        self.assertFalse(ok);self.assertIn('microstage_completed',self.state,feedback);self.assertEqual(self.state['microstage_completed'],run)
        self.assertEqual(self.s.MS.MicroRecovery(self.s.STATE,'notes').current(task,'value:1',self.head,'profile')['index'],1)
        self.assertEqual(self.git('rev-parse','HEAD'),self.head);self.assertNotIn(task['id'],self.state['done'])
        self.assertFalse((self.s.STATE/'runs'/run/'accepted.json').exists())
        self.assertIn('Ran 1 test',(self.s.STATE/'runs'/run/'micro-checks/check-0.log').read_text())

    def interrupted_micro_review(self):
        with mock.patch.object(self.s.MS.MicroRecovery,'complete_step',side_effect=RuntimeError('Crash after passing checks and saved review')):
            with self.assertRaisesRegex(RuntimeError,'Crash after passing checks'):self.intermediate_attempt()
        active=self.state['active_attempt'];return active['task'],active['run']

    def test_saved_intermediate_review_resumes_without_relaunching_coding_or_checks(self):
        task,run=self.interrupted_micro_review();directory=self.s.STATE/'runs'/run
        self.s.write(directory/'outcome.json',{'accepted':False,'outcome':'interrupted','run':run,'task':task['id']})
        counts=(self.state['local_attempts_today'],self.state['attempts_today'])
        with mock.patch.object(self.s,'cloud',side_effect=self.cloud),mock.patch.object(self.s,'run',side_effect=AssertionError('Bound checks must be reused')),mock.patch.object(self.s,'charged_run',side_effect=AssertionError('No new coding launch')),mock.patch.object(self.s,'verify',side_effect=AssertionError('No final promotion')):
            self.assertTrue(self.s.resume_micro_checkpoint(task,self.state,{},self.s.now()+dt.timedelta(minutes=10),self.head))
        self.assertEqual(counts,(self.state['local_attempts_today'],self.state['attempts_today']))
        self.assertEqual(self.s.MS.MicroRecovery(self.s.STATE,'notes').current(task,'value:1',self.head,'profile')['index'],1)
        self.assertEqual(self.s.read(directory/'outcome.json')['outcome'],'checkpoint')
        self.assertEqual(len(list(directory.glob('checkpoint-predecessor-*'))),1)
        with self.s.MS.MicroRecovery(self.s.STATE,'notes').store.reader() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM micro_dispatches').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM mr_dispatches').fetchone()[0],3)
        self.assertFalse((directory/'accepted.json').exists());self.assertEqual(self.state['accepted'],[])

    def test_micro_review_resume_refuses_rejected_unfinished_or_mutated_candidates(self):
        task,run=self.interrupted_micro_review();directory=self.s.STATE/'runs'/run
        with mock.patch.object(self.s,'cloud',side_effect=AssertionError('No review for rejected/unfinished/changed work')):
            for path,value in [('worker-completion.json',{'finished':False}),('micro-review/result.json',{'decision':'revise','issues':['Missing case']}),('micro-checks/checks.json',[{'code':1,'reason':'exited'}])]:
                original=(directory/path).read_bytes();self.s.write(directory/path,value)
                self.assertFalse(self.s.resume_micro_checkpoint(task,self.state,{},self.s.now()+dt.timedelta(minutes=1),self.head))
                (directory/path).write_bytes(original)
            (directory/'work/tests/test_value.py').write_text('mutated after checkpoint\n')
            with self.assertRaises(self.s.ManagerHeld):self.s.resume_micro_checkpoint(task,self.state,{},self.s.now()+dt.timedelta(minutes=1),self.head)

    def test_progress_policy_verifies_unfinished_saved_micro_candidate_without_coding(self):
        import shutil
        task,run=self.interrupted_micro_review();directory=self.s.STATE/'runs'/run
        self.s.write(directory/'worker-completion.json',{'finished':False})
        shutil.rmtree(directory/'micro-checks');shutil.rmtree(directory/'micro-review')
        counts=(self.state['local_attempts_today'],self.state['attempts_today'])
        def host(command,cwd,log,*args,**kwargs):
            actual=command[3:] if command[0]=='/usr/bin/sandbox-exec' else command
            with log.open('w') as out:result=subprocess.run(actual,cwd=cwd,env=kwargs.get('env'),stdout=out,stderr=subprocess.STDOUT,timeout=20)
            return {'code':result.returncode,'reason':'exited','seconds':0.1}
        with mock.patch.object(self.s,'progress_enabled',return_value=True),mock.patch.object(self.s,'run',side_effect=host),mock.patch.object(self.s,'cloud',side_effect=self.cloud),mock.patch.object(self.s,'charged_run',side_effect=AssertionError('No new coding dispatch')):
            self.assertTrue(self.s.resume_micro_checkpoint(task,self.state,{},self.s.now()+dt.timedelta(minutes=5),self.head))
        self.assertEqual(counts,(self.state['local_attempts_today'],self.state['attempts_today']))
        self.assertFalse(self.s.read(directory/'worker-completion.json')['finished'])
        self.assertEqual(self.s.read(directory/'saved-micro-verification.json')['new_coding_launches'],0)
        self.assertEqual(self.s.MS.MicroRecovery(self.s.STATE,'notes').current(task,'value:1',self.head,'profile')['index'],1)
        self.assertFalse((directory/'accepted.json').exists())

    def test_first_verification_intent_recovers_after_checks_without_coding(self):
        task,run=self.interrupted_micro_review();directory=self.s.STATE/'runs'/run
        self.s.write(directory/'worker-completion.json',{'finished':False});checkpoint=self.s.read(directory/'candidate-checkpoint.json')
        intent={'kind':'first_host_verification','run':run,'task_sha256':Q.task_fingerprint(task),'base':self.head,
            'profile':'profile','step':0,'patch_sha256':checkpoint['patch_sha256'],'worker_finished':False,'new_coding_launches':0,'product_accepted':False}
        counts=(self.state['local_attempts_today'],self.state['attempts_today'])
        with mock.patch.object(self.s,'progress_enabled',return_value=True),mock.patch.object(self.s,'run',side_effect=AssertionError('Bound checks must recover')),mock.patch.object(self.s,'cloud',side_effect=self.cloud),mock.patch.object(self.s,'charged_run',side_effect=AssertionError('No new coding launch')):
            self.s.write(directory/'saved-micro-verification.json',dict(intent,base='b'*40))
            with self.assertRaises(self.s.ManagerHeld):self.s.resume_micro_checkpoint(task,self.state,{},self.s.now()+dt.timedelta(minutes=2),self.head)
            self.s.write(directory/'saved-micro-verification.json',intent)
            self.assertTrue(self.s.resume_micro_checkpoint(task,self.state,{},self.s.now()+dt.timedelta(minutes=2),self.head))
        self.assertEqual(counts,(self.state['local_attempts_today'],self.state['attempts_today']));self.assertFalse(self.s.read(directory/'worker-completion.json')['finished'])

    def test_completed_sql_receipt_recovers_checkpoint_outcome_without_models(self):
        task,_,run,_=self.intermediate_attempt();directory=self.s.STATE/'runs'/run
        self.assertFalse((directory/'outcome.json').exists())
        with mock.patch.object(self.s,'cloud',side_effect=AssertionError('Model-free recovery')),mock.patch.object(self.s,'charged_run',side_effect=AssertionError('No coding replay')):
            self.s.recover(self.state);self.s.recover(self.state)
        self.assertEqual(self.s.read(directory/'outcome.json')['outcome'],'checkpoint')
        self.assertNotIn('active_attempt',self.state);self.assertEqual(self.state['accepted'],[])
        self.assertNotIn(task['id'],self.state['done']);self.assertFalse((directory/'accepted.json').exists())

    def test_checkpoint_audit_guidance_stays_bound_to_original_task_and_episode(self):
        task,_,run,_=self.intermediate_attempt();directory=self.s.STATE/'runs'/run
        q=self.s.QM.QualityMonitor(self.s.STATE,'notes');patch=self.s.read(directory/'candidate-checkpoint.json')['patch_sha256']
        token=q.reserve_checkpoint(task,'value:1',self.head,'profile',run,patch)
        q.finish(token,{'decision':'regression','summary':'A concrete existing criterion was missed.','issues':['Keep real parser calls.']},
            {'source_head':self.head,'checkpoint_run':run,'candidate_patch_sha256':patch,'provenance':'interactive_codex_operator'})
        self.assertEqual(self.s.checkpoint_audit_guidance(task,self.head)['issues'],['Keep real parser calls.'])
        self.assertIsNone(self.s.checkpoint_audit_guidance(dict(task,id='renamed'),self.head));self.assertIsNone(self.s.checkpoint_audit_guidance(task,'b'*40))
        engine=self.s.quality_engine({});engine.status.return_value={'material_findings':[{'id':'value'}]}
        with mock.patch.object(self.s,'current_repair_contract',return_value=True),mock.patch.object(self.s,'resume_micro_checkpoint',return_value=False),mock.patch.object(self.s,'import_episode_failures',side_effect=RuntimeError('Original task remains scheduled')):
            with self.assertRaisesRegex(RuntimeError,'Original task remains scheduled'):self.s.next_task(self.state,{},self.s.now()+dt.timedelta(minutes=1))
        engine.upsert_finding.assert_not_called()
        with mock.patch.object(self.s,'git',return_value='b'*40),mock.patch.object(self.s,'cloud',side_effect=AssertionError('No stale-source replanning')):
            self.assertIsNone(self.s.next_task(self.state,{},self.s.now()+dt.timedelta(minutes=1)))
        engine.upsert_finding.assert_not_called()
        M.ManagerRecovery(self.s.STATE,'notes').retire(task,'value:1',{'reason':'Contract was retired; the private audit must not create a shipped defect.'})
        with mock.patch.object(self.s,'cloud',side_effect=AssertionError('No new issue or planner')):
            self.assertIsNone(self.s.next_task(self.state,{},self.s.now()+dt.timedelta(minutes=1)))
        engine.upsert_finding.assert_not_called()
        self.assertIsNotNone(q.snapshot()['hold']);self.assertEqual(q.snapshot()['cloud_audit_reservations_7d']['routine'],1)

    def test_microstep_compiles_only_current_contract_and_skips_premature_full_verification(self):
        with mock.patch.object(self.s,'cloud',side_effect=self.cloud):task=self.call()
        recovery=M.ManagerRecovery(self.s.STATE,'notes')
        for run in ('prior1','prior2'):recovery.reserve(task,'value:1',self.head,'profile',run)
        proposal={'decision':'decompose','reason':'Smaller test groups.','steps':[
            {'title':'One exact parser case','description':'Assert the known valid nested value succeeds.','acceptance':['One focused realistic case.']},
            {'title':'Remaining boundary','description':'Complete remaining original regressions.','acceptance':['Keep the complete final contract.']}]}
        self.s.MS.MicroRecovery(self.s.STATE,'notes').approve(task,'value:1',self.head,'profile',proposal,
            dict(self.checkpoint,task_sha256=Q.task_fingerprint(task)),{'decision':'accept','issues':[],'receipt_sha256':'e'*64})
        self.s.MS.MicroRecovery(self.s.STATE,'notes').reserve(task,'value:1',self.head,'profile','prior-step')
        previous=self.s.STATE/'runs/prior-step/micro-checks';previous.mkdir(parents=True)
        self.s.write(previous/'checks.json',[{'code':1,'reason':'exited','command':['python']}]);(previous/'check-0.log').write_text('REAL CURRENT STEP ERROR')
        self.s.quality_engine({}).get_finding.return_value={'id':'value','episode_id':'value:1'}
        def clone(work,*args):self.git('clone','--quiet',str(self.root),str(work))
        def seed(task,state,config,work,base,directory):
            for name in self.parent['files']:(work/name).write_bytes((self.work/name).read_bytes())
            self.git('add','.',cwd=work)
            self.s.write(directory/'retry-seed.json',dict(self.checkpoint,run='failed'))
            self.s.write(directory/'retry-verification.json',{'first_verification':True})
            return 'Exact retained candidate.'
        captured=[]
        def stop(*args,**kwargs):
            captured.append((args[1]/'PRD.md').read_text());raise RuntimeError('stop before actual model launch')
        with mock.patch.object(self.s,'clone_workspace',side_effect=clone),mock.patch.object(self.s,'seed_retry',side_effect=seed), \
             mock.patch.object(self.s,'prepare_workspace'),mock.patch.object(self.s,'implementation_choice',return_value=('local','value:1')), \
             mock.patch.object(self.s,'checkpoint_audit_guidance',return_value={'issues':['CONCRETE CODEX CHECKPOINT FINDING']}), \
             mock.patch.object(self.s,'charged_run',side_effect=stop),mock.patch.object(self.s,'verify',side_effect=AssertionError('No premature full verification')):
            with self.assertRaisesRegex(RuntimeError,'stop before actual model launch'):
                self.s.attempt(task,self.state,{'managed_brief':json.dumps({'instruction':'Existing scope'}),'worker_timeout_seconds':720},self.s.now()+dt.timedelta(minutes=15))
        self.assertEqual(len(captured),1);prompt=captured[0]
        self.assertIn('One exact parser case',prompt);self.assertIn('One focused realistic case.',prompt)
        self.assertNotIn('Keep the complete original contract.',prompt)
        self.assertIn('unittest discover -s tests -p test_value.py -v',prompt)
        self.assertIn('REAL CURRENT STEP ERROR',prompt)
        self.assertIn('CONCRETE CODEX CHECKPOINT FINDING',prompt)

    def test_microsteps_use_the_registered_javascript_focused_runner(self):
        self.s.SETTINGS['focused_test_prefix']=['node','jest.js','--runTestsByPath']
        self.assertEqual(self.s.micro_checks({'worker_file':'tests/unit/pilot-runtime.test.ts'}),[['node','jest.js','--runTestsByPath','tests/unit/pilot-runtime.test.ts']])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)/'source';self.root.mkdir()
        self.backend=B.load_backend(self.root,'notes',settings={'source_dirs':['src','tests'],'product_dirs':['src']})
        self.backend.STATE=Path(self.tmp.name)/'runtime';self.backend.STATE.mkdir()
        self.s=self.backend
        def git(*args,cwd=None):
            return subprocess.check_output(['git','-c','commit.gpgsign=false',*args],cwd=cwd or self.root,text=True,stderr=subprocess.DEVNULL).strip()
        self.git=git;git('init','-q');git('config','user.name','Test');git('config','user.email','test@localhost')
        for name in ['src','tests']:(self.root/name).mkdir()
        (self.root/'src/value.py').write_text('old implementation\n');(self.root/'tests/test_value.py').write_text('old test\n')
        (self.root/'progress.txt').write_text('tracked baseline report\n')
        git('add','.');git('commit','-qm','original');self.head=git('rev-parse','HEAD')
        self.work=self.s.STATE/'runs'/'failed'/'work';self.work.parent.mkdir(parents=True)
        git('clone','--quiet',str(self.root),str(self.work))
        (self.work/'src/value.py').write_text('correct frozen implementation\n');(self.work/'tests/test_value.py').write_text('rejected incomplete test\n')
        git('add','.',cwd=self.work);self.patch=git('diff','--cached','--binary',self.head,cwd=self.work)+'\n'
        self.parent={'id':'old','kind':'repair','issue_episode':'value:1','finding_ids':['value'],
            'title':'Normalize an actual parser failure','description':'Repair the established existing behavior.',
            'files':['src/value.py','tests/test_value.py'],'acceptance':['Keep the complete original contract.']}
        self.checkpoint={'run':'failed','base':self.head,'files':self.parent['files'],
            'task_sha256':Q.task_fingerprint(self.parent),'patch_sha256':hashlib.sha256(self.patch.encode()).hexdigest()}
        self.state={'planned':[self.parent],'done':[],'accepted':[],'tries':{},'feedback':{},'retry_candidates':{'old':self.checkpoint}}
        self.proposal={'action':'implement','title':'Finish only the remaining regression tests',
            'description':'Frozen production code is correct. Finish only the listed regression test.',
            'files':['tests/test_value.py'],'acceptance':self.parent['acceptance']}
        self.patches=[mock.patch.object(self.s,'git',side_effect=git),
            mock.patch.object(self.s,'quality_engine',return_value=mock.Mock(profile_hash='profile')),
            mock.patch.object(self.s,'inspect_candidate',return_value={'usable':True,'unverified':False,'receipt':self.checkpoint}),
            mock.patch.object(self.s,'manager_context',return_value={'evidence_hash':'context'}),
            mock.patch.object(self.s,'engineering_direction',return_value='Existing scope only.'),mock.patch.object(self.s,'report')]
        for patch in self.patches:patch.start();self.addCleanup(patch.stop)

    def cloud(self,prompt,schema,directory,*args,**kwargs):
        directory.mkdir(parents=True,exist_ok=True)
        result=self.proposal if schema is self.s.PLAN_SCHEMA else {'decision':'accept','issues':[],'summary':'A safe narrower continuation.'}
        (directory/'result.json').write_text(json.dumps(result));return dict(result)

    def call(self):return self.s.coordinate_recovery(self.parent,self.state,{},self.s.now()+dt.timedelta(minutes=10),'value:1')

    def test_review_failure_replays_the_saved_plan_instead_of_caching_a_permanent_hold(self):
        failed=False
        def cloud(*args,**kwargs):
            nonlocal failed
            if args[1] is self.s.REVIEW_SCHEMA and not failed:
                failed=True;raise RuntimeError('simulated provider outage after plan commit')
            return self.cloud(*args,**kwargs)
        with mock.patch.object(self.s,'cloud',side_effect=cloud):
            with self.assertRaises(RuntimeError):self.call()
            self.assertNotIn('value:1',self.state.get('manager_recovery_wait',{}))
            task=self.call()
        self.assertEqual(task['local_worker_files'],['tests/test_value.py'])
        self.assertEqual(self.s.issue_episode(task,self.state),'value:1')
        self.assertEqual(len(M.ManagerRecovery(self.s.STATE,'notes').tasks()),1)

    def test_failure_after_independent_review_before_grant_remains_replayable(self):
        original=M.ManagerRecovery.approve;failed=False
        def approve(instance,*args,**kwargs):
            nonlocal failed
            if not failed:failed=True;raise RuntimeError('simulated crash before grant commit')
            return original(instance,*args,**kwargs)
        with mock.patch.object(self.s,'cloud',side_effect=self.cloud),mock.patch.object(M.ManagerRecovery,'approve',new=approve):
            with self.assertRaises(RuntimeError):self.call()
            self.assertNotIn('value:1',self.state.get('manager_recovery_wait',{}))
            self.assertIsNotNone(self.call())

    def test_full_candidate_bytes_and_frozen_scope_are_revalidated_for_review_replay(self):
        with mock.patch.object(self.s,'cloud',side_effect=self.cloud):task=self.call()
        self.s.require_checked_candidate(task,self.state,self.work,self.head,self.patch)
        (self.work/'src/value.py').write_text('mutated by a host test\n')
        with mock.patch.object(self.s,'cloud') as cloud:
            with self.assertRaises(M.RecoveryHeld):
                self.s.review_and_promote(task,self.state,{},self.s.now()+dt.timedelta(minutes=5),self.work.parent,
                    self.work,'failed',self.head,self.patch,self.parent['files'])
            cloud.assert_not_called()

    def test_git_index_flags_cannot_hide_changed_tracked_source(self):
        with mock.patch.object(self.s,'cloud',side_effect=self.cloud):task=self.call()
        self.s.require_checked_candidate(task,self.state,self.work,self.head,self.patch)
        self.git('update-index','--assume-unchanged','tests/test_value.py',cwd=self.work)
        (self.work/'tests/test_value.py').write_text('hidden changed test bytes\n')
        with self.assertRaises(self.s.ManagerHeld):self.s.require_checked_candidate(task,self.state,self.work,self.head,self.patch)
        self.git('update-index','--no-assume-unchanged','tests/test_value.py',cwd=self.work)
        self.git('checkout','--','tests/test_value.py',cwd=self.work)
        self.git('update-index','--skip-worktree','tests/test_value.py',cwd=self.work)
        (self.work/'tests/test_value.py').write_text('another hidden change\n')
        with self.assertRaises(self.s.ManagerHeld):self.s.require_checked_candidate(task,self.state,self.work,self.head,self.patch)

    def test_acceptance_crash_cannot_leave_a_completed_plan_on_the_pending_queue(self):
        with mock.patch.object(self.s,'cloud',side_effect=self.cloud):task=self.call()
        engine=self.s.quality_engine({});engine.profile={'checks':[]}
        engine.get_finding.return_value={'id':'value','episode_id':'value:1','state':'open'}
        folder=self.work.parent;original=self.s.write;failed=False
        def write(path,value):
            nonlocal failed
            if path.name=='quality-accepted.json' and not failed:
                failed=True;raise RuntimeError('crash after durable plan completion before marker')
            return original(path,value)
        with mock.patch.object(self.s,'write',side_effect=write):
            with self.assertRaises(RuntimeError):self.s.reconcile_quality_acceptance(task,self.state,{},folder,self.head)
        self.assertEqual(M.ManagerRecovery(self.s.STATE,'notes').tasks(),[])
        self.s.reconcile_quality_acceptance(task,self.state,{},folder,self.head)
        self.assertEqual(json.loads((folder/'quality-accepted.json').read_text())['commit'],self.head)

    def test_expected_manager_hold_during_tick_is_not_a_supervisor_failure(self):
        config={'max_attempts_per_night':None,'max_cloud_calls_per_night':None,'max_failures_per_night':None}
        path=self.root/self.s.CONFIG_FILE;path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(config))
        self.s.write(self.s.STATE/'state.json',self.state)
        with (mock.patch.object(self.s,'recover'),mock.patch.object(self.s,'next_task',side_effect=M.RecoveryHeld('source changed')),
             mock.patch.object(self.s.subprocess,'Popen') as popen,mock.patch.object(self.s,'event') as event,
             mock.patch.object(self.s.AS,'compact',side_effect=lambda runtime,project,state,tasks:state)):
            self.assertEqual(self.s.tick(once=True,managed_until=self.s.now()+dt.timedelta(minutes=10)),0)
        saved=self.s.read(self.s.STATE/'state.json');self.assertEqual(saved['failures_today'],0)
        self.assertNotIn('cooldown_until',saved)
        self.assertFalse(any(call.args[0]=='supervisor_error' for call in event.call_args_list))

    def test_interrupted_private_proof_init_and_stale_index_lock_do_not_strand_replay(self):
        import candidate_proof as C
        original=C.command;failed=False
        def command(args,**kw):
            nonlocal failed
            result=original(args,**kw)
            if args[0]=='init' and not failed:
                failed=True;raise RuntimeError('crash after init before alternates')
            return result
        with mock.patch.object(C,'command',side_effect=command):
            with self.assertRaises(RuntimeError):C.manifest(self.root,self.head,self.patch,self.work.parent)
        incomplete=next(self.work.parent.glob('candidate-proof-*.git'));(incomplete/'index.lock').write_text('stale')
        proof=C.verify(self.root,self.work,self.head,self.patch,self.work.parent,['src','tests'])
        self.assertEqual(proof['base'],self.head);self.assertTrue((incomplete/'index.lock').exists())
        self.assertEqual(len(list(self.work.parent.glob('candidate-proof-*.git'))),2)

    def test_tracked_progress_is_preserved_then_restored_before_source_verification(self):
        import candidate_proof as C
        report=self.work/'progress.txt';report.write_text('actual worker failure and progress\n')
        self.git('add','progress.txt',cwd=self.work)
        C.preserve_worker_artifacts(self.root,self.work,self.head,self.work.parent)
        self.assertEqual(report.read_text(),'tracked baseline report\n')
        saved=list((self.work.parent/'worker-artifacts').glob('*-progress.txt'))
        self.assertEqual(len(saved),1);self.assertEqual(saved[0].read_text(),'actual worker failure and progress\n')
        self.assertEqual(self.git('diff','--cached','--binary',self.head,'--','progress.txt',cwd=self.work),'')
        C.verify(self.root,self.work,self.head,self.patch,self.work.parent,['src','tests'])

    def test_host_checks_neutralizes_worker_bytecode_with_a_private_prefix(self):
        directory=self.s.STATE/'check';directory.mkdir()
        with mock.patch.object(self.s,'run',return_value={'code':0,'reason':'exited'}) as run:
            self.assertTrue(self.s.host_checks(self.work,directory,self.s.now()+dt.timedelta(minutes=2),{},[['python3','test.py']]))
        env=run.call_args.kwargs['env']
        self.assertEqual(env['PYTHONPYCACHEPREFIX'],str(directory/'test-tmp/pycache'))
        self.assertEqual(env['PYTHONDONTWRITEBYTECODE'],'1')


if __name__=='__main__':unittest.main()
