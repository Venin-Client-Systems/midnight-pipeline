"""Maintenance evidence, launch commitment and retained history regressions."""
import concurrent.futures
import copy
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import action_store as A
import maintenance_recovery as M
import phase_runtime as P
import progress_recovery as RP


class MaintenanceProgressTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.source=self.root/'source.py'
        self.source.write_text('def repair(value):\n    return value + 1\n')
        self.actions=A.Actions(self.root/'runtime')
        self.fault={'detail':'database is locked','traceback':'source.py in repair'}
        self.incident=self.actions.incident('midnight','controller_error','controller',A.fingerprint(self.fault),self.fault)
        self.manager=M.MaintenanceRecovery(self.root,self.incident)
        self.model={'model':'fixture-cloud','profile_hash':'qualified-fixture'}
        self.policy={'source_files':['source.py']}
        self.base='a'*40

    def legacy(self,count=4):
        for _ in range(count):
            identity=self.actions.reserve_repair(self.incident,{'source':self.base})
            self.actions.repair(identity,state='failed',payload={'reason':'retained_failure'})
        if count==4:
            self.assertIsNone(self.actions.reserve_repair(self.incident,{}))
            self.assertEqual(self.actions.get_incident(self.incident)['state'],'held')
        return copy.deepcopy(self.actions.repairs(active=False))

    def observe(self,*,fault=None,base=None,candidate=None):
        return self.manager.observe(fault or self.actions.get_incident(self.incident),base or self.base,
                                    self.model,self.policy,candidate)

    def offer(self,scope,*,symbol='repair',instruction='Repair only the demonstrated metadata connection failure.'):
        review={'decision':'accept','issues':[],'unmet':True,'smaller':True,
                'summary':'One demonstrably unmet original source unit.'}
        path=self.root/('review-'+str(time.time_ns())+'.json')
        path.write_text(json.dumps(review))
        proposal={'action':'narrow','instruction':instruction,'path':'source.py',
                  'symbol':symbol,'evidence_quote':'    return value + 1\n','reason':'Actual supplied source and failure.'}
        return self.manager.offer_reviewed(scope,self.model,proposal,review,[RP.ref(path)],self.root)

    def owned(self,scope,capsule):
        identity=self.manager.create('implementation',scope,self.model,capsule)
        self.assertIsNotNone(identity)
        lease=P.lease()
        job=self.actions.submit('midnight',{'continuation':identity},time.time()+300)
        self.assertTrue(self.actions.claim(job['id'],lease))
        self.manager.update(identity,'running',lease=lease)
        return identity,job,lease

    def reserve(self,session,scope):
        return self.actions.reserve_progress_repair(self.incident,{'source':self.base},
                            self.manager,scope,self.model,session)

    def test_four_spent_attempts_migrate_without_a_fresh_initial_grant(self):
        originals=self.legacy()
        scope,_,capsule=self.observe()
        self.assertFalse(self.manager.admission(scope,self.model)['allowed'])
        self.assertEqual(self.actions.repairs(active=False),originals)
        self.assertEqual(self.actions.get_incident(self.incident)['attempts'],4)
        self.manager.create('diagnosis',scope,self.model,capsule)
        self.assertEqual(self.actions.get_incident(self.incident)['attempts'],4)
        self.assertEqual(self.actions.repairs(active=False),originals)

    def test_fresh_maintenance_label_grants_no_initial_coding_or_invented_charge(self):
        scope,_,_=self.observe()
        self.assertFalse(self.manager.admission(scope,self.model)['allowed'])
        self.assertEqual(self.actions.get_incident(self.incident)['attempts'],0)
        self.assertEqual(self.actions.repairs(active=False),[])

    def test_two_launchers_consume_one_proof_and_preserve_every_old_charge(self):
        originals=self.legacy()
        scope,_,capsule=self.observe()
        self.offer(scope)
        session,_,_=self.owned(scope,capsule)
        def contender(_):
            try:return self.reserve(session,scope)
            except ValueError:return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(contender,range(2)))
        self.assertEqual(len([x for x in results if x]),1)
        self.assertEqual(self.actions.get_incident(self.incident)['attempts'],5)
        after={r['id']:r for r in self.actions.repairs(active=False)}
        for old in originals:self.assertEqual(after[old['id']],old)
        self.assertFalse(self.manager.admission(scope,self.model)['allowed'])
        self.assertEqual(len([e for e in self.actions.events() if e['kind']=='maintenance_implementation_reserved']),1)

    def test_crash_before_commit_rolls_back_proof_and_launch_together(self):
        self.legacy()
        scope,_,capsule=self.observe()
        self.offer(scope)
        session,_,_=self.owned(scope,capsule)
        event=self.actions.event
        def interrupted(con,project,kind,*args,**kwargs):
            if kind=='maintenance_implementation_reserved':raise RuntimeError('before commit')
            return event(con,project,kind,*args,**kwargs)
        with mock.patch.object(self.actions,'event',side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError,'before commit'):self.reserve(session,scope)
        self.assertEqual(self.actions.get_incident(self.incident)['attempts'],4)
        self.assertTrue(self.manager.admission(scope,self.model)['allowed'])
        self.assertEqual(self.reserve(session,scope),self.incident+'-5')

    def test_crash_after_commit_before_child_creation_remains_spent_on_restart(self):
        self.legacy()
        scope,_,capsule=self.observe()
        proof=self.offer(scope)
        session,job,lease=self.owned(scope,capsule)
        self.reserve(session,scope)
        restarted=M.MaintenanceRecovery(self.root,self.incident)
        self.assertFalse(restarted.admission(scope,self.model)['allowed'])
        self.assertEqual(restarted.session(session)['payload']['stage'],'implementation_launched')
        with self.assertRaisesRegex(ValueError,'already charged'):self.reserve(session,scope)
        self.assertEqual(self.actions.get_incident(self.incident)['attempts'],5)
        self.assertEqual(self.offer(scope,instruction='Rewording the same fix grants nothing.'),proof)
        self.assertFalse(restarted.admission(scope,self.model)['allowed'])
        self.actions.finish(job['id'],'interrupted',{'reason':'simulated_process_loss'},lease=lease)

    def test_rename_cosmetic_edit_redeploy_and_quote_cannot_renew_a_spent_unit(self):
        self.legacy()
        scope,_,capsule=self.observe()
        proof=self.offer(scope)
        session,job,lease=self.owned(scope,capsule)
        repair=self.reserve(session,scope)
        self.actions.repair(repair,state='failed',expected_lease=lease)
        self.manager.update(session,'waiting')
        self.actions.finish(job['id'],'failed',{},lease=lease)
        self.source.write_text('# a cosmetic change\ndef renamed(value):\n    """A new description."""\n    return value + 1\n')
        changed,_,_=self.observe(base='b'*40)
        self.assertNotEqual(changed['id'],scope['id'])
        self.assertEqual(self.offer(changed,symbol='renamed'),proof)
        self.assertFalse(self.manager.admission(changed,self.model)['allowed'])
        self.assertEqual(self.actions.get_incident(self.incident)['attempts'],5)

    def test_model_candidate_and_kernel_changes_invalidate_unused_admission(self):
        self.legacy()
        scope,_,_=self.observe()
        self.offer(scope)
        self.assertTrue(self.manager.admission(scope,self.model)['allowed'])
        self.assertFalse(self.manager.admission(scope,dict(self.model,model='unqualified-other'))['allowed'])
        self.observe(candidate={'binding':{'patch_sha256':'b'*64},'semantic':{'source.py':'changed'}})
        self.assertFalse(self.manager.admission(scope,self.model)['allowed'])

    def test_unchanged_timestamp_observation_does_not_schedule_more_inference(self):
        self.legacy()
        self.manager.clock=lambda:0
        self.manager.actions.clock=lambda:0
        scope,_,capsule=self.observe()
        session=self.manager.create('diagnosis',scope,self.model,capsule)
        self.manager.park(self.manager.session(session),'No demonstrated new intervention')
        updated=copy.deepcopy(self.actions.get_incident(self.incident))
        updated['payload'].update(at='2026-11-04T12:00:00+00:00',age_seconds=999,phase='phase-new')
        _,_,same=self.observe(fault=updated)
        self.assertEqual(same['key'],capsule['key'])
        self.assertEqual(self.manager.control()['diagnosed'],same['key'])
        self.assertEqual(M.active_sessions(self.root),[])
        self.assertEqual(self.actions.get_incident(self.incident)['attempts'],4)

    def test_preparation_and_verification_records_never_consume_coding_proof(self):
        self.legacy()
        scope,_,capsule=self.observe()
        self.offer(scope)
        session,job,lease=self.owned(scope,capsule)
        for stage in ('candidate','checked','verified'):
            payload=self.manager.session(session)['payload'];payload['stage']=stage
            self.manager.update(session,'running',payload=payload)
        self.assertTrue(self.manager.admission(scope,self.model)['allowed'])
        self.assertEqual(self.actions.get_incident(self.incident)['attempts'],4)
        self.actions.finish(job['id'],'succeeded',{'reason':'host_only_continuation'},lease=lease)

    def test_worker_cannot_activate_an_unreviewed_authority_or_replace_kernel(self):
        with mock.patch('maintenance_authority.check',return_value={'schema_version':1}):
            with self.assertRaisesRegex(ValueError,'version-two'):M.activate(self.root)
        import core_repair as R
        protected=set(M.KERNEL)
        self.assertFalse(protected.intersection(R.POLICY['source_files']))

    def test_changed_incident_key_retains_original_lineage_and_spent_history(self):
        originals=self.legacy()
        alias=self.actions.incident('midnight','controller_error','new-spelling',A.fingerprint(self.fault),self.fault)
        canonical=M.original_incident(self.root,self.actions.get_incident(alias))
        self.assertEqual(canonical,self.incident)
        scope,_,_=self.manager.observe(self.actions.get_incident(alias),self.base,self.model,self.policy)
        self.assertFalse(self.manager.admission(scope,self.model)['allowed'])
        after={r['id']:r for r in self.actions.repairs(active=False)}
        for old in originals:self.assertEqual(after[old['id']],old)

    def test_verification_backoff_releases_resources_and_does_not_block_other_projects(self):
        self.legacy()
        scope,_,capsule=self.observe()
        identity=self.manager.create('verification',scope,self.model,capsule)
        self.manager.update(identity,'paused',result={'next_retry_at':time.time()+300,'coding_relaunch_allowed':False})
        with mock.patch.object(M,'spawn_session') as spawn:
            self.assertTrue(M.running(self.root))
        spawn.assert_not_called()
        self.assertIsNotNone(self.actions.submit('other-project',{},time.time()+300))
        self.assertEqual(self.actions.phases(),[])
        self.assertEqual(self.actions.get_incident(self.incident)['attempts'],4)

    def test_only_complete_infrastructure_error_evidence_allows_host_retry(self):
        self.legacy();scope,_,capsule=self.observe();_,job,_=self.owned(scope,capsule)
        directory=self.root/'classification';check=directory/'verification-one';check.mkdir(parents=True)
        (check/'checks.log').write_text('ERROR: test_fixture (Fixture)\nTraceback (most recent call last):\nsubprocess.TimeoutExpired: fixed command timed out\n====================\nRan 1 tests\n')
        phase=self.actions.phase(job['id'],'midnight','host','fixed-classification',time.time()+60,{'log':str(check/'checks.log')})
        self.actions.phase_update(phase['id'],'running',lease=P.lease())
        self.actions.phase_update(phase['id'],'failed',result={'code':1,'log_sha256':RP.ref(check/'checks.log')['sha256']})
        M.save(self.root/'runtime/phases'/phase['id']/'launch.json',{'command':['fixed-verifier','candidate','--receipt',str(check/'completion.json'),'--token','fixed-token']})
        expected=['test_fixture.Fixture.test_example']
        original_receipt={'schema_version':1,'part':'candidate','token':'fixed-token','selected':expected,'executed':expected,'tests_run':1,'passed':False,'failures':0,'errors':1,'skipped':0}
        M.save(check/'completion.json',original_receipt)
        context={'directory':str(check),'job':job['id'],'expected':expected,'operation':'full_checks'}
        classify=lambda:M.collect_failure(directory,RuntimeError('host failed'),root=self.root,verification=context)
        self.assertTrue(classify()['infrastructure'])
        M.save(check/'completion.json',{'failures':0,'errors':2,'tests_run':2})
        self.assertFalse(classify()['infrastructure'])
        M.save(check/'completion.json',{'failures':1,'errors':1,'tests_run':2})
        self.assertFalse(classify()['infrastructure'])
        # A newer lower-sorting regression directory cannot inherit the old
        # complete timeout receipt selected above.
        newer=directory/'verification-aaa';newer.mkdir();(newer/'regression-on-base.log').write_text('FAIL: real assertion\nAssertionError: original obligation unmet\n')
        M.save(check/'completion.json',original_receipt)
        (newer/'checks.log').write_text('FAIL: real assertion\nAssertionError: original obligation unmet\n')
        second=self.actions.phase(job['id'],'midnight','host','newer-assertion',time.time()+60,{'log':str(newer/'checks.log')})
        self.actions.phase_update(second['id'],'running',lease=P.lease())
        self.actions.phase_update(second['id'],'failed',result={'code':1,'log_sha256':RP.ref(newer/'checks.log')['sha256']})
        M.save(self.root/'runtime/phases'/second['id']/'launch.json',{'command':['fixed-verifier','candidate','--receipt',str(newer/'completion.json'),'--token','second-token']})
        M.save(newer/'completion.json',{**original_receipt,'token':'second-token','failures':1,'errors':0})
        self.assertFalse(M.collect_failure(directory,RuntimeError('assertion failed'),root=self.root,
            verification={'directory':str(newer),'job':job['id'],'expected':expected,'operation':'full_checks'})['infrastructure'])
        self.assertFalse(M.collect_failure(directory,RuntimeError('regression failed'),root=self.root,
            verification={'directory':str(newer),'job':job['id'],'operation':'regression'})['infrastructure'])

    def prepared_checked_session(self, *, review=True):
        self.legacy()
        patch='fixture exact candidate\n'
        binding={'base':self.base,'tree':'c'*40,'patch_sha256':hashlib.sha256(patch.encode()).hexdigest(),
                 'files':['source.py','tests/test_recovery_fixture.py']}
        original=self.actions.repairs(active=False)[0]
        source=self.root/'runtime/maintenance'/original['id'];source.mkdir(parents=True)
        (source/'candidate.patch').write_text(patch);M.save(source/'incident.json',self.actions.get_incident(self.incident))
        retained={'original_repair':original['id'],'directory':str(source),'binding':binding,'refs':[RP.ref(source/'candidate.patch'),RP.ref(source/'incident.json')]}
        scope,_,capsule=self.observe(candidate=retained)
        identity=self.manager.create('verification',scope,self.model,capsule,retained=retained)
        directory=self.root/'runtime/maintenance'/identity
        (directory/'work').mkdir(parents=True);(directory/'canonical').mkdir()
        completion=directory/'completion.json';completion.write_text(json.dumps({'passed':True,'tests_run':1}))
        checks=[{'code':0,'reason':'exited','completion_receipt':RP.ref(completion)}]
        M.save(directory/'checks.json',{'candidate':binding,'checks':checks})
        M.save(directory/'candidate-binding.json',binding)
        M.save(directory/'candidate.patch.receipt.json',{'sha256':binding['patch_sha256']})
        (directory/'candidate.patch').write_text(patch)
        M.save(directory/'review-intent.json',{'candidate':binding,'model':self.model})
        if review:
            M.save(directory/'independent-review-receipt.json',{'review':{'decision':'accept','summary':'Actual exact candidate accepted.','issues':[]},
                    'patch_sha256':binding['patch_sha256'],'model':self.model,'process':{'code':0}})
        M.save(directory/'delivery.json',{'status':'merged','commit':'d'*40})
        payload=self.manager.session(identity)['payload'];payload.update(stage='checked',binding=binding)
        self.manager.update(identity,'queued',payload=payload)
        return identity,directory,binding,patch

    def execute_fake_host(self,identity,binding,patch):
        import core_repair as R
        stack=ExitStack()
        self.addCleanup(stack.close)
        def git(root,*args):
            if args==('status','--porcelain'):return ''
            if args==('rev-parse','HEAD'):return self.base
            if args==('write-tree',):return binding['tree']
            if args==('add','--all'):return ''
            self.fail('Unexpected fixture Git command: '+str(args))
        for target,name,kwargs in [
            (R,'permitted',{'return_value':True}),
            (R,'maintenance_current',{'return_value':True}),
            (R,'check_authority',{}),
            (R.G,'git',{'side_effect':git}),
            (R,'allowed_patch',{'return_value':(binding['files'],patch)}),
        ]:stack.enter_context(mock.patch.object(target,name,**kwargs))
        implementation=stack.enter_context(mock.patch.object(R,'command',side_effect=AssertionError('Saved host stages must not run a model')))
        review=stack.enter_context(mock.patch.object(R,'independent_review',side_effect=AssertionError('Saved review must not be repeated')))
        install=stack.enter_context(mock.patch.object(R,'install',return_value=True))
        return M.execute(self.root,identity),implementation,review,install

    def test_saved_checks_and_review_continue_after_four_charges_without_inference(self):
        identity,_,binding,patch=self.prepared_checked_session()
        result,implementation,review,install=self.execute_fake_host(identity,binding,patch)
        self.assertEqual(result,0,self.manager.session(identity)['result'])
        implementation.assert_not_called();review.assert_not_called();install.assert_called_once()
        self.assertEqual(self.actions.get_incident(self.incident)['attempts'],4)
        self.assertEqual(self.manager.session(identity)['state'],'complete')
        self.assertFalse(self.manager.session(identity)['result']['accepted_product_achievement'])
        self.assertEqual(self.actions.jobs(),[])

    def test_uncertain_review_parks_without_another_provider_call_or_new_charge(self):
        identity,directory,binding,patch=self.prepared_checked_session(review=False)
        M.save(directory/'review-invocation.json',{'patch_sha256':binding['patch_sha256'],'invocation':{'descriptor':self.model,'invocation_id':'e'*32,'command':['fixed-review']}})
        job=self.actions.submit('midnight',{'continuation':identity},time.time()+300);lease=P.lease();self.actions.claim(job['id'],lease)
        self.actions.charge(job['id'],'e'*32,'maintenance_review');self.actions.finish(job['id'],'interrupted',{},lease=lease)
        result,implementation,review,install=self.execute_fake_host(identity,binding,patch)
        self.assertEqual(result,1)
        for operation in (implementation,review,install):operation.assert_not_called()
        self.assertEqual(self.actions.get_incident(self.incident)['attempts'],4)
        self.assertEqual(self.manager.session(identity)['state'],'paused')
        self.assertEqual(self.actions.jobs(),[])

    def test_completed_review_process_reconstructs_receipt_without_preparing_a_call(self):
        import core_repair as R
        self.legacy()
        self.observe()
        directory=self.root/'saved-review';directory.mkdir()
        binding={'patch_sha256':'f'*64}
        raw='{"type":"turn.completed"}\n'
        M.save(directory/'review-invocation.json',{'patch_sha256':binding['patch_sha256'],
             'invocation':{'invocation_id':'e'*32,'descriptor':self.model,'command':['fixed-review']}})
        M.save(directory/'review-process.json',{'code':0,'reason':'exited','log_sha256':hashlib.sha256(raw.encode()).hexdigest()})
        M.save(directory/'review.json',{'decision':'accept','summary':'Retained exact review.','issues':[]})
        (directory/'review-events.jsonl').write_text(raw)
        with mock.patch.object(R.CP,'CloudPolicy') as cloud:
            receipt=M.recover_review(self.root,directory,binding,self.model,R)
        cloud.return_value.prepare_invocation.assert_not_called()
        cloud.return_value.record_result.assert_called_once()
        self.assertEqual(receipt['review']['decision'],'accept')
        self.assertEqual(M.read(directory/'independent-review-receipt.json'),receipt)
        self.assertEqual(self.actions.get_incident(self.incident)['attempts'],4)

    def test_real_local_supervisor_adapter_records_two_maintenance_roles_without_coding(self):
        import core_repair as R
        import model_manager
        self.legacy();scope,_,capsule=self.observe()
        identity,job,lease=self.owned(scope,capsule)
        directory=self.root/'runtime/maintenance'/identity;directory.mkdir(parents=True)
        def supervise(root,owned,kind,command,work,log,seconds,deadline,**kw):
            self.assertEqual(kind,'local');kw['on_launch']()
            manifest=M.read(work/'local-request.json')
            value=({'action':'narrow','instruction':'Repair the actual connection error in this source unit.',
                    'path':'source.py','symbol':'repair','evidence_quote':'    return value + 1\n','reason':'The original failure remains unmet.'}
                   if manifest['role']=='diagnosis' else
                   {'decision':'accept','unmet':True,'smaller':True,'summary':'One concrete original obligation.','issues':[]})
            M.save(work/'result.json',value);M.save(work/'local-metrics.json',{'model':'fixture-local'})
            log.write_text('{"type":"turn.completed"}\n')
            return {'code':0,'reason':'exited','log_sha256':RP.ref(log)['sha256']}
        with mock.patch.object(R,'command',side_effect=supervise) as inference, \
             mock.patch.object(model_manager,'dispatch_descriptor',return_value={'model':'fixture-local','context':32768}), \
             mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':job['id']}):
            M.diagnose(self.root,self.manager,self.manager.session(identity),directory,time.time()+300)
            M.diagnose(self.root,self.manager,self.manager.session(identity),directory,time.time()+300)
        self.assertEqual(inference.call_count,2)
        self.assertTrue(self.manager.admission(scope,self.model)['allowed'])
        self.assertEqual(self.actions.session_charges('maintenance:'+identity,'midnight'),{'maintenance_local_supervision':2})
        self.assertEqual(self.actions.get_incident(self.incident)['attempts'],4)
        self.actions.finish(job['id'],'succeeded',{},lease=lease)

    def test_implementation_callback_commits_real_charge_and_never_replays_after_failure(self):
        import core_repair as R
        identity,directory,binding,patch=self.prepared_checked_session()
        scope=self.manager.session(identity)['payload']['scope'];self.offer(scope)
        payload=self.manager.session(identity)['payload'];payload.update(stage='preparing',action='implementation')
        self.manager.update(identity,'queued',payload=payload)
        M.save(directory/'baseline-receipt.json',{'base':self.base,'checks':M.read(directory/'checks.json')['checks']})
        M.save(directory/'workspace-ready.json',{'base':self.base,'seed_tree':binding['tree'],'working_patch_sha256':hashlib.sha256((self.base+'\n').encode()).hexdigest(),'status_sha256':A.fingerprint('')})
        def command(root,job,kind,args,cwd,log,seconds,deadline,**kw):
            self.assertEqual(kind,'cloud');kw['on_launch']()
            log.write_text('{"type":"turn.completed"}\n')
            return {'code':0,'reason':'exited','log_sha256':hashlib.sha256(log.read_bytes()).hexdigest()}
        with ExitStack() as stack:
            for target,name,kwargs in [
                (R,'permitted',{'return_value':True}),(R,'maintenance_current',{'return_value':True}),
                (R,'check_authority',{}),(R.G,'git',{'side_effect':lambda root,*a:'' if a[0] in {'status','add'} else binding['tree'] if a[0]=='write-tree' else self.base}),
                (R,'allowed_patch',{'return_value':(binding['files'],patch)}),
                (R,'run_checks',{'return_value':[{'code':0,'reason':'exited'}]}),
                (R,'command',{'side_effect':command}),
                (M,'regression',{'side_effect':RuntimeError('simulated interruption after implementation')}),
                (M,'prepare_workspace',{'return_value':(directory/'canonical',directory/'work')}),
            ]:stack.enter_context(mock.patch.object(target,name,**kwargs))
            cloud=stack.enter_context(mock.patch.object(R.CP,'CloudPolicy'))
            cloud.return_value.prepare_invocation.return_value={'command':['fixture-implementation'],'env':{},'descriptor':self.model,'invocation_id':'e'*32}
            self.assertEqual(M.execute(self.root,identity),1)
            self.assertEqual(M.execute(self.root,identity),0)
        self.assertEqual(cloud.return_value.prepare_invocation.call_count,1)
        self.assertEqual(self.actions.get_incident(self.incident)['attempts'],5)
        self.assertEqual(self.actions.session_charges('maintenance:'+identity,'midnight'),{'maintenance_implementation':1})
        self.assertFalse(self.manager.admission(scope,self.model)['allowed'])


if __name__=='__main__':unittest.main()
