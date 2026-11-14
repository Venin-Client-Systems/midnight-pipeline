"""Shared adapter contracts, with independent synthetic projects and no models."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import project_backend as B
import worker_quality as Q


class ProjectBackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)

    def backend(self,identity,**settings):
        root=self.root/identity;root.mkdir()
        instance=B.load_backend(root,identity,settings=settings)
        instance.STATE=self.root/'state'/identity
        instance.STATE.mkdir(parents=True)
        return instance

    def test_project_globals_scopes_and_episode_lineage_are_independent(self):
        a=self.backend('notes',source_dirs=['src'],protected_paths=['src/credentials'])
        b=self.backend('inventory',source_dirs=['lib'])
        self.assertNotEqual(a.ROOT,b.ROOT);self.assertNotEqual(a.STATE,b.STATE)
        self.assertTrue(a.permitted('src/value.ts',['src/*']))
        self.assertFalse(a.permitted('src/credentials/key.ts',['src/*']))
        self.assertFalse(b.permitted('src/value.ts',['src/*']))
        task={'id':'repair','description':'same exact behaviour'}
        self.assertNotEqual(a.issue_episode(task,{}),b.issue_episode(task,{}))
        a.STOP=True;self.assertFalse(b.STOP)

    def test_coordinator_scope_is_enforced_beyond_task_supplied_patterns(self):
        s=self.backend('notes',source_dirs=['src','scripts','tests'],
                       worker_allowed_patterns=['scripts/testing/*','tests/unit/pilot-*.test.ts'])
        self.assertFalse(s.permitted('src/lib/compliance/logic.ts',['src/*']))
        self.assertFalse(s.permitted('scripts/deploy.mjs',['scripts/*']))
        self.assertTrue(s.permitted('scripts/testing/preflight.mjs',['scripts/testing/*']))

    def test_retry_anchors_include_configured_acceptance_brief_and_instructions(self):
        s=self.backend('notes',source_dirs=['src'],acceptance_dir='.midnight/acceptance',
                       brief='.midnight/BRIEF.md',protected_files=['CLAUDE.md'])
        s.CONFIG_FILE='.midnight/config.json'
        task={'id':'r','files':['src/value.ts']}
        state={'retry_candidates':{'r':{'task_sha256':Q.task_fingerprint(task),'files':['src/value.ts'],'base':'a'*40}}}
        calls=[]
        import subprocess
        def git(*args,**kwargs):
            calls.append(args)
            if args[0]=='diff':raise subprocess.CalledProcessError(1,args)
            if args[0]=='ls-tree' and args[-1]=='.midnight/acceptance':return '.midnight/acceptance/original.py'
            return ''
        with mock.patch.object(s,'git',side_effect=git):
            self.assertIn('not reusable',s.seed_retry(task,state,{'protected_test_files':[]},s.ROOT,'b'*40,self.root))
        self.assertIn('.midnight/acceptance',calls[1])
        diff=next(c for c in calls if c[0]=='diff')
        for path in ('.midnight/acceptance/original.py','.midnight/config.json','.midnight/BRIEF.md','CLAUDE.md'):
            self.assertIn(path,diff)

    def test_entrypoint_binds_stop_handlers_to_only_the_selected_backend(self):
        import signal
        with mock.patch.object(signal,'signal') as register:
            a=self.backend('notes');b=self.backend('inventory')
            register.assert_not_called()
            with mock.patch.object(a,'main',return_value=0) as main:
                self.assertEqual(a.entrypoint(),0)
            main.assert_called_once()
        handlers=dict(call.args for call in register.call_args_list)
        self.assertIs(handlers[signal.SIGTERM],a.stopping)
        handlers[signal.SIGTERM](signal.SIGTERM,None)
        self.assertTrue(a.STOP);self.assertFalse(b.STOP)

    def test_verification_only_requires_coordinator_recipe_and_exact_parent(self):
        s=self.backend('notes',source_dirs=['src'],verification_checks={'V1':[['python3','-m','unittest']]})
        task={'id':'V1','kind':'repair','title':'Collect missing check receipt','description':'Validate the retained exact candidate.',
              'files':['src/value.py'],'acceptance':['Fresh host checks and independent approval.'],'verification_only':True}
        self.assertFalse(s.task_valid(task))
        task['retry_of']={'task_id':'original','task_sha256':'a'*64}
        self.assertTrue(s.task_valid(task))
        task['id']='unregistered';self.assertFalse(s.task_valid(task))

    def test_configured_gates_keep_predecessor_oracle_and_coordinator_recipe(self):
        s=self.backend('notes',host_checks=[{'command':['node','trusted-check.js'],'timeout_seconds':30}],
                       acceptance_dir='acceptance',verification_checks={'V1':[['git','diff','--check']]})
        oracle=s.ROOT/'acceptance'/'original.py';oracle.parent.mkdir();oracle.write_text('# trusted predecessor')
        task={'id':'V1','verification_only':True,'acceptance_oracles':['original']}
        with mock.patch.object(s,'host_checks',return_value=True) as check:
            self.assertTrue(s.verify(s.ROOT,self.root,None,{},task))
        self.assertEqual(check.call_args.args[4],[['git','diff','--check'],['node','trusted-check.js'],[s.PYTHON,str(oracle)]])
        oracle.unlink()
        with self.assertRaisesRegex(RuntimeError,'oracle is missing'):
            s.verify(s.ROOT,self.root,None,{},task)

    def test_dependency_preparation_is_locked_and_never_installs(self):
        dependency=self.root/'dependencies';dependency.mkdir()
        s=self.backend('notes',dependency_dir=str(dependency),dependency_lock_sha256='0'*64)
        (s.ROOT/'package-lock.json').write_text('{}')
        work=self.root/'work';work.mkdir()
        with self.assertRaisesRegex(RuntimeError,'protected lock'):s.prepare_workspace(work)
        import hashlib
        s.SETTINGS['dependency_lock_sha256']=hashlib.sha256(b'{}').hexdigest()
        s.prepare_workspace(work)
        self.assertEqual((work/'node_modules').resolve(),dependency.resolve())

    def test_read_policy_allows_only_host_output_logs_beside_worker_files(self):
        s=self.backend('notes');folder=self.root/'check';folder.mkdir()
        def result(*args,**kwargs):return {'code':0,'reason':'exited','seconds':0}
        with mock.patch.object(s,'run',side_effect=result):
            self.assertTrue(s.host_checks(s.ROOT,folder,None,{},[['node','-e','1']]))
        profile=(folder/'validation.sb').read_text()
        self.assertIn('(literal "'+str(folder/'check-0.log')+'")',profile)
        self.assertNotIn('(subpath "'+str(folder)+'")',profile)

    def test_review_check_output_is_real_and_bounded(self):
        folder=self.root/'receipts';folder.mkdir()
        Q.write(folder/'checks.json',[{'command':['node','test.js'],'code':0,'reason':'exited'}])
        (folder/'check-0.log').write_text('START '+'x'*1000+' END')
        receipt=Q.check_evidence(folder,max_total=20,max_each=20)[0]
        self.assertEqual(receipt['command'],['node','test.js'])
        self.assertTrue(receipt['output_is_tail']);self.assertLessEqual(len(receipt['combined_stdout_stderr']),20)
        self.assertTrue(receipt['combined_stdout_stderr'].endswith('END'))

    def test_pending_prerequisite_does_not_replan_its_bound_finding(self):
        import datetime as dt
        s=self.backend('notes')
        first={'id':'first','kind':'repair','finding_ids':['fixture']}
        later={'id':'later','kind':'repair','finding_ids':['runtime'],'depends_on':['first']}
        state={'done':[]}
        engine=mock.Mock()
        engine.status.return_value={'material_findings':[{'id':'fixture'},{'id':'runtime'}]}
        engine.next_decision.side_effect=lambda head,tasks,**kw: {'action':'implement_locally','task':tasks[0]}
        rescue=mock.Mock();rescue.eligibility.return_value={'status':'local_retries_required','allowed':False}
        with mock.patch.object(s,'git',return_value='a'*40), \
             mock.patch.object(s,'quality_engine',return_value=engine), \
             mock.patch.object(s,'all_tasks',return_value=[first,later]), \
             mock.patch.object(s,'task_done',side_effect=lambda value,identity:identity in value['done']), \
             mock.patch.object(s,'task_valid',return_value=True), \
             mock.patch.object(s,'import_episode_failures',return_value='episode'), \
             mock.patch.object(s,'issue_episode',return_value='episode'), \
             mock.patch.object(s,'rescue_store',return_value=rescue), \
             mock.patch.object(s,'unchecked_candidate',return_value=False), \
             mock.patch.object(s,'plan_task') as plan, \
             mock.patch.object(s.PM,'status',return_value={'feature_hold':False}):
            self.assertEqual(s.next_task(state,{},s.now()+dt.timedelta(minutes=5)),first)
            self.assertEqual([x['id'] for x in engine.next_decision.call_args.args[1]],['first'])
            state['done']=['first']
            engine.status.return_value={'material_findings':[{'id':'runtime'}]}
            self.assertEqual(s.next_task(state,{},s.now()+dt.timedelta(minutes=5)),later)
            plan.assert_not_called()

    def test_stale_unverified_draft_cannot_suppress_required_guidance(self):
        import datetime as dt
        import hashlib
        import subprocess
        s=self.backend('notes',source_dirs=['src','tests'])
        def git(*args):
            return subprocess.check_output(['git','-c','commit.gpgsign=false',*args],cwd=s.ROOT,text=True,stderr=subprocess.DEVNULL).strip()
        git('init','-q');git('config','user.name','Test');git('config','user.email','test@localhost')
        (s.ROOT/'src').mkdir();file=s.ROOT/'src/value.py';file.write_text('value=1\n')
        git('add','.');git('commit','-qm','initial');old=git('rev-parse','HEAD')
        file.write_text('value=2\n');patch=git('diff','--binary')+'\n';git('restore','src/value.py')
        file.write_text('value=3\n');git('add','.');git('commit','-qm','accepted independent source change')
        task={'id':'T1','kind':'repair','title':'Repair one observable value','description':'Repair the exact observed value while preserving current source.',
              'files':['src/value.py'],'acceptance':['Required value and existing tests pass.'],'finding_ids':['value']}
        state={'done':[],'accepted':[],'planned':[],'feedback':{},'tries':{}}
        receipt={'run':'old-draft','base':old,'task_sha256':Q.task_fingerprint(task),'files':['src/value.py'],
                 'patch_sha256':hashlib.sha256(patch.encode()).hexdigest(),'verified_before':False}
        state['retry_candidates']={'T1':receipt}
        folder=s.STATE/'runs/old-draft';folder.mkdir(parents=True);(folder/'candidate.patch').write_text(patch)
        episode=s.issue_episode(task,state);store=s.rescue_store()
        for i in range(2):store.record_local_failure('notes',episode,'T1','failed-'+str(i),'worker_no_change',{'observed':True})
        engine=mock.Mock();engine.status.return_value={'material_findings':[{'id':'value'}]}
        engine.next_decision.side_effect=lambda head,tasks,**kw:{'action':'implement_locally','task':tasks[0]}
        with mock.patch.object(s,'quality_engine',return_value=engine), \
             mock.patch.object(s,'all_tasks',side_effect=lambda value:[task]+value['planned']), \
             mock.patch.object(s.PM,'status',return_value={'feature_hold':False}), \
             mock.patch.object(s,'cloud',return_value={'summary':'Preserve the current source and repair the value.',
                   'steps':['Edit the exact required value only.','Run the protected regression and host checks.']}) as cloud:
            selected=s.next_task(state,{},s.now()+dt.timedelta(minutes=5))
            self.assertNotEqual(selected['id'],'T1')
            self.assertEqual(selected['retry_of']['task_sha256'],Q.task_fingerprint(task))
            self.assertEqual(s.next_task(state,{},s.now()+dt.timedelta(minutes=5))['id'],selected['id'])
        cloud.assert_called_once();self.assertEqual(len(state['planned']),1)
        self.assertEqual(store.task_failures('notes',episode,'T1'),2)
        self.assertEqual(state['retry_candidates']['T1'],receipt)

    def test_committed_guidance_resumes_without_a_second_cloud_call(self):
        s=self.backend('notes')
        task={'id':'T1','title':'Repair one observable value','description':'Repair exactly the current observed value.',
              'files':['notes/value.py'],'acceptance':['Preserve current behaviour.']}
        state={'planned':[]};episode=s.issue_episode(task,state);store=s.rescue_store()
        for i in range(2):store.record_local_failure('notes',episode,'T1','failure-'+str(i),'worker_no_change',{'observed':True})
        guidance={'summary':'Preserve source and fix the value.','steps':['Read the exact function.','Repair and verify it.']}
        store.record_guidance('notes',episode,'committed-guidance',guidance)
        with mock.patch.object(s,'task_valid',return_value=True),mock.patch.object(s,'cloud') as cloud:
            one=s.guided_successor(task,state,{},None,episode)
            two=s.guided_successor(task,state,{},None,episode)
        self.assertEqual(one,two);self.assertEqual(len(state['planned']),1);cloud.assert_not_called()

    def test_receipt_aware_hold_is_reconsidered_only_for_changed_context(self):
        context={'evidence_hash':'new','canonical_or_attempt_failures':[{'origin':'canonical'}]}
        saved={'task_sha256':'task','attempt_evidence_version':2,'decision':'hold'}
        self.assertFalse(B.cached_rescue_current(saved,'task',context))
        saved['evidence_hash']='new';self.assertTrue(B.cached_rescue_current(saved,'task',context))
        context['evidence_hash']='new-output';self.assertFalse(B.cached_rescue_current(saved,'task',context))

    def test_normal_dispatch_reconciles_sql_guidance_before_project_state(self):
        import datetime as dt
        s=self.backend('notes')
        task={'id':'T1','kind':'repair','finding_ids':['value'],'title':'Repair one observable value',
              'description':'Repair exactly the current observed value.','files':['notes/value.py'],
              'acceptance':['Preserve current behaviour.']}
        state={'done':[],'accepted':[],'planned':[]};episode=s.issue_episode(task,state);store=s.rescue_store()
        for i in range(2):store.record_local_failure('notes',episode,'T1','failure-'+str(i),'worker_no_change',{'observed':True})
        store.record_guidance('notes',episode,'committed-guidance',
                              {'summary':'Preserve source and fix the value.','steps':['Read the exact function.','Repair and verify it.']})
        engine=mock.Mock();engine.status.return_value={'material_findings':[{'id':'value'}]}
        engine.next_decision.side_effect=lambda head,tasks,**kw:{'action':'implement_locally','task':tasks[0]}
        with mock.patch.object(s,'quality_engine',return_value=engine),mock.patch.object(s,'git',return_value='a'*40), \
             mock.patch.object(s,'all_tasks',side_effect=lambda value:[task]+value['planned']), \
             mock.patch.object(s.PM,'status',return_value={'feature_hold':False}),mock.patch.object(s,'cloud') as cloud:
            selected=s.next_task(state,{},s.now()+dt.timedelta(minutes=5))
        self.assertNotEqual(selected['id'],'T1');self.assertEqual(len(state['planned']),1)
        self.assertEqual(selected['retry_of']['task_sha256'],Q.task_fingerprint(task));cloud.assert_not_called()

    def test_generic_finding_requires_exact_observed_source_profile_and_affected_file(self):
        import json
        s=self.backend('notes');folder=s.STATE/'assessments'/'baseline';folder.mkdir(parents=True)
        (folder/'assessment-base.json').write_text(json.dumps({'head':'a'*40,'profile_hash':'profile'}))
        (folder/'checks.json').write_text(json.dumps([{'command':['node','test'],'reason':'exited','code':1}]))
        path=folder/'check-0.log';path.write_text('FAIL tests/unit/fixture.test.ts\nExpected true, received false')
        finding={'evidence':[str(path)],'episode_id':'canonical:2'}
        binding={'head':'a'*40,'profile_hash':'profile'};task={'files':['tests/unit/fixture.test.ts']}
        self.assertTrue(s.canonical_finding_covered(finding,task,binding,0,['node','test']))
        binding['head']='b'*40;self.assertFalse(s.canonical_finding_covered(finding,task,binding,0,['node','test']))
        binding.update(head='a'*40,profile_hash='different')
        self.assertFalse(s.canonical_finding_covered(finding,task,binding,0,['node','test']))
        binding['profile_hash']='profile'
        self.assertFalse(s.canonical_finding_covered(finding,{'files':['src/unrelated.ts']},binding,0,['node','test']))

    def test_old_accepted_run_cannot_close_a_new_canonical_regression(self):
        s=self.backend('notes');folder=s.STATE/'runs'/'accepted';folder.mkdir(parents=True)
        task={'id':'repair','kind':'repair','finding_ids':['original'],'files':['notes/value.py']}
        Q.write(folder/'quality-checks/checks.json',[{'command':['node','test'],'reason':'exited','code':0}])
        Q.write(folder/'quality-checks/profile-binding.json',{'head':'a'*40,'profile_hash':'profile','task_sha256':Q.task_fingerprint(task)})
        engine=mock.Mock();engine.profile={'checks':[{'id':'unit','command':['node','test']}]};engine.profile_hash='profile'
        engine.get_finding.return_value={'state':'open','episode_id':'canonical:2'}
        with mock.patch.object(s,'quality_engine',return_value=engine),mock.patch.object(s,'git',return_value='b'*40), \
             mock.patch.object(s,'canonical_finding_covered',return_value=True),mock.patch.object(s,'rescue_store'):
            s.reconcile_quality_acceptance(task,{}, {},folder,'a'*40)
        self.assertNotIn('host-check-unit',[call.args[0] for call in engine.resolve_finding.call_args_list])

    def test_multiple_finding_episodes_are_pinned_without_optional_task_episode(self):
        s=self.backend('notes');folder=s.STATE/'runs'/'accepted';folder.mkdir(parents=True)
        task={'id':'repair','kind':'repair','finding_ids':['one','two'],'files':['notes/value.py']}
        Q.write(folder/'finding-bindings.json',{'head':'a'*40,'profile_hash':'profile',
            'task_sha256':Q.task_fingerprint(task),'findings':[{'id':'one','episode_id':'one:1'},{'id':'two','episode_id':'two:7'}]})
        engine=mock.Mock();engine.profile={'checks':[]};engine.profile_hash='profile'
        findings={'one':{'state':'open','episode_id':'one:1'},'two':{'state':'open','episode_id':'two:7'}}
        engine.get_finding.side_effect=lambda identity:findings[identity]
        with mock.patch.object(s,'quality_engine',return_value=engine),mock.patch.object(s,'git',return_value='b'*40),mock.patch.object(s,'rescue_store'):
            s.reconcile_quality_acceptance(task,{}, {},folder,'b'*40)
            self.assertEqual({c.args[0] for c in engine.resolve_finding.call_args_list},{'one','two'})
            (folder/'quality-accepted.json').unlink();engine.resolve_finding.reset_mock()
            findings['two']['episode_id']='two:8'
            s.reconcile_quality_acceptance(task,{}, {},folder,'b'*40)
        self.assertEqual([c.args[0] for c in engine.resolve_finding.call_args_list],['one'])
        self.assertEqual(Q.read(folder/'quality-accepted.json')['unresolved_findings'],['two'])


if __name__=='__main__':unittest.main()
