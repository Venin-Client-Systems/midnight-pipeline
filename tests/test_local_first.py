"""Local management, budgeted audits and persistent scheduling regressions."""
import contextlib
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

import midnight as M
import guardian as G
import local_supervisor as L
import quality_monitor as Q
import phase_runtime as P
import project_backend as B
from action_store import Actions

REVIEW={'decision':'accept','summary':'Supplied change is consistent with its scoped tests.','issues':[],
        'confidence':'medium','correctness':4,'security':4,'maintainability':4,'tests':4}
CHECKS=[{'code':0,'reason':'exited','command':['python','test.py']}]
DATA={'timezone':'Australia/Adelaide','schedule':{'start':'00:00','end':'09:00'}}


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=2_000_000
        self.q=Q.QualityMonitor(self.root,'alpha',clock=lambda:self.now)

    def accepted(self,run='r1',head='a'*40,patch='b'*64,review=None,checks=None):
        self.q.record_review(run,'c'*40,patch,{'model':'fixture'},review or REVIEW,CHECKS if checks is None else checks)
        self.q.accepted(run,head)

    def audit(self,decision='healthy',kind='routine',source='a'*40,head=None):
        token=self.q.reserve(source,kind,head=head)
        result={'decision':decision,'summary':'Concrete supplied sample.','issues':['src/a.py: boundary regression at input X'] if decision=='regression' else []}
        evidence={'source_head':head or source,'receipt':'private-receipt','candidate_patch_sha256':source if kind=='corrective' else None}
        self.q.finish(token,result,evidence,approved_patch=source if kind=='corrective' and decision=='healthy' else None)
        return token,result,evidence

    def test_empty_snapshot_does_not_create_runtime_or_invoke_models(self):
        with mock.patch.object(L,'infer',side_effect=AssertionError('Read only')):
            self.assertEqual(self.q.snapshot()['local_reviews'],[])
            self.assertFalse(self.q.due('a'*40));self.assertTrue(self.q.delivery_allowed('b'*64))
        self.assertEqual(list(self.root.iterdir()),[])

    def test_only_real_passed_host_checks_and_strong_local_acceptance_bind_a_merge(self):
        for i,changes in enumerate(({'confidence':'low'},{'security':3},{'decision':'revise'},{'issues':['Still broken']})):
            with self.assertRaises(ValueError):self.accepted(run='bad'+str(i),review=dict(REVIEW,**changes))
        for i,checks in enumerate(([],[{'code':1,'reason':'exited'}],[{'code':0,'reason':'task_timeout'}])):
            with self.assertRaises(ValueError):self.accepted(run='checks'+str(i),checks=checks)
        self.accepted();self.assertTrue(self.q.due('a'*40));self.assertFalse(self.q.due('d'*40))
        self.assertEqual(self.q.snapshot()['accepted_local_reviews'],1)

    def test_review_and_accepted_commit_receipts_are_immutable_and_idempotent(self):
        self.accepted();self.accepted()
        with self.assertRaises(ValueError):self.q.accepted('r1','d'*40)
        with self.assertRaises(ValueError):self.q.record_review('r1','c'*40,'b'*64,{'model':'fixture'},dict(REVIEW,tests=5),CHECKS)

    def test_unknown_reservation_charged_and_project_budgets_separate(self):
        self.accepted();self.q.reserve('a'*40)
        self.assertFalse(self.q.due('a'*40))
        with self.assertRaises(ValueError):self.q.reserve('a'*40)
        other=Q.QualityMonitor(self.root,'beta',clock=lambda:self.now)
        self.assertEqual(other.snapshot()['cloud_audit_reservations_7d']['routine'],0)
        self.assertEqual(self.q.snapshot()['audits'][0]['state'],'reserved_unknown')
        self.now+=Q.WINDOW+1
        self.assertFalse(self.q.due('a'*40))
        with self.assertRaises(ValueError):self.q.reserve('a'*40)
        self.accepted('r2','d'*40);self.assertTrue(self.q.due('d'*40))

    def test_concurrent_audit_reservations_allow_only_one(self):
        self.accepted();barrier=threading.Barrier(2);results=[]
        def reserve():
            barrier.wait()
            try:results.append(self.q.reserve('a'*40))
            except ValueError:results.append('held')
        threads=[threading.Thread(target=reserve) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join(10);self.assertFalse(t.is_alive())
        self.assertEqual(results.count('held'),1)
        self.assertEqual(len(self.q.snapshot()['audits']),1)

    def test_audit_result_source_bound_and_conflicting_replay_rejected(self):
        self.accepted();token=self.q.reserve('a'*40)
        value={'decision':'healthy','summary':'Scoped sample.','issues':[]}
        with self.assertRaises(ValueError):self.q.finish(token,value,{'source_head':'e'*40})
        self.q.finish(token,value,{'source_head':'a'*40});self.q.finish(token,value,{'source_head':'a'*40})
        with self.assertRaises(ValueError):self.q.finish(token,dict(value,summary='Changed'),{'source_head':'a'*40})
        with self.assertRaises(ValueError):self.q.finish(token,dict(value,issues=['Unfixed']),{'source_head':'a'*40})

    def test_regression_blocks_all_merges_and_corrective_approval_is_exact_patch(self):
        self.accepted();self.audit('regression');self.assertFalse(self.q.delivery_allowed('b'*64))
        self.assertTrue(self.q.due('a'*40,'corrective'))
        with self.assertRaises(ValueError):self.q.reserve('b'*64,'corrective')
        self.audit('healthy','corrective','e'*64,'a'*40)
        self.assertTrue(self.q.delivery_allowed('e'*64,'a'*40));self.assertFalse(self.q.delivery_allowed('b'*64,'a'*40))
        self.assertFalse(self.q.delivery_allowed('e'*64,'d'*40));self.assertFalse(self.q.delivery_allowed('e'*64))
        self.assertIsNotNone(self.q.snapshot()['hold'])
        self.q.record_review('fixed','a'*40,'e'*64,{'model':'fixture'},REVIEW,CHECKS);self.q.accepted('fixed','f'*40)
        self.assertIsNone(self.q.snapshot()['hold'])
        self.assertEqual(self.q.snapshot()['cloud_audit_reservations_7d'],{'routine':1,'corrective':1})

    def test_corrective_outcome_cannot_approve_another_patch(self):
        self.accepted();self.audit('regression');token=self.q.reserve('e'*64,'corrective',head='a'*40)
        value={'decision':'healthy','summary':'Scoped sample.','issues':[]}
        with self.assertRaises(ValueError):self.q.finish(token,value,{'source_head':'a'*40,'candidate_patch_sha256':'e'*64},approved_patch='f'*64)
        with self.assertRaises(ValueError):self.q.finish(token,value,{'source_head':'a'*40,'candidate_patch_sha256':'f'*64},approved_patch='e'*64)
        self.assertFalse(self.q.delivery_allowed('e'*64))

    def test_identical_patch_on_another_base_cannot_clear_corrective_hold(self):
        self.accepted();self.audit('regression');self.audit('healthy','corrective','e'*64,'a'*40)
        self.accepted('other-base','f'*40,'e'*64)
        self.assertIsNotNone(self.q.snapshot()['hold'])
        self.assertFalse(self.q.delivery_allowed('e'*64,'c'*40))


class QualityRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.s=B.load_backend(self.root,'fixture');self.s.STATE=self.root/'runtime'
        self.q=Q.QualityMonitor(self.s.STATE,'fixture')

    def saved_audit(self,token,result):
        folder=self.s.STATE/'quality-audits'/token;folder.mkdir(parents=True);model=self.s.CP.config_descriptor()
        for name,value in [('schema.json',Q.AUDIT_SCHEMA),('result.json',result),('cloud-model.json',model),('process.json',{'code':0,'reason':'exited'}),('tool-validation.json',{})]:self.s.write(folder/name,value)
        (folder/'input.txt').write_text('Exact supplied sample.');(folder/'events.jsonl').write_text('{}\n')
        key=self.s.AW.fingerprint({'prompt':'Exact supplied sample.','schema':Q.AUDIT_SCHEMA,'model':model,'source':'a'*40})
        required=['result.json','events.jsonl','process.json','cloud-model.json','tool-validation.json']
        self.s.write(folder/'decision-receipt.json',{'input_hash':key,'files':{n:hashlib.sha256((folder/n).read_bytes()).hexdigest() for n in required}})

    def test_corrective_regression_replays_pending_revision_before_any_model_call(self):
        for finished in (False,True):
            with self.subTest(finished=finished):
                self.s.STATE=self.root/str(finished);q=Q.QualityMonitor(self.s.STATE,'fixture')
                q.record_review('old','c'*40,'b'*64,{'model':'fixture'},REVIEW,CHECKS);q.accepted('old','a'*40)
                token=q.reserve('a'*40);q.finish(token,{'decision':'regression','summary':'Concrete defect.','issues':['Boundary broken']},{'source_head':'a'*40})
                patch='unchanged rejected patch';digest=hashlib.sha256(patch.encode()).hexdigest();token=q.reserve(digest,'corrective',head='a'*40)
                result={'decision':'regression','summary':'Specific corrective defect.','issues':['src/a.py fails at input Y']};self.saved_audit(token,result)
                if finished:q.finish(token,result,self.s.audit_evidence(self.s.STATE/'quality-audits'/token,'a'*40,digest,'corrective'))
                task={'id':'T1','title':'Repair boundary','files':['src/a.py']};state={'pending':{'run':'new','phase':'delivery'}}
                with mock.patch.object(self.s.CP,'validate_supervisor_events',return_value={}),mock.patch.object(self.s,'cloud',side_effect=AssertionError('No repeated cloud or local review')),mock.patch.object(self.s,'require_checked_candidate'):
                    ok,run,feedback=self.s.review_and_promote(task,state,{},self.s.now()+dt.timedelta(minutes=1),self.s.STATE/'runs/new',self.root/'work','new','a'*40,patch,['src/a.py'])
                self.assertFalse(ok);self.assertNotIn('pending',state)
                self.assertEqual(state['review_findings']['T1']['review']['issues'],result['issues'])
                self.assertEqual(q.snapshot()['cloud_audit_reservations_7d'],{'routine':1,'corrective':1})

    def test_validated_saved_audit_is_finalized_before_further_delivery_without_cloud(self):
        self.q.record_review('old','c'*40,'b'*64,{'model':'fixture'},REVIEW,CHECKS);self.q.accepted('old','a'*40)
        token=self.q.reserve('a'*40);folder=self.s.STATE/'quality-audits'/token;folder.mkdir(parents=True)
        result={'decision':'regression','summary':'Specific source defect.','issues':['src/a.py fails at input X']}
        model=self.s.CP.config_descriptor()
        for name,value in [('schema.json',Q.AUDIT_SCHEMA),('result.json',result),('cloud-model.json',model),('process.json',{'code':0,'reason':'exited'}),('tool-validation.json',{})]:self.s.write(folder/name,value)
        (folder/'input.txt').write_text('Exact supplied sample.');(folder/'events.jsonl').write_text('{}\n')
        key=self.s.AW.fingerprint({'prompt':'Exact supplied sample.','schema':Q.AUDIT_SCHEMA,'model':model,'source':'a'*40})
        required=['result.json','events.jsonl','process.json','cloud-model.json','tool-validation.json']
        self.s.write(folder/'decision-receipt.json',{'input_hash':key,'files':{n:hashlib.sha256((folder/n).read_bytes()).hexdigest() for n in required}})
        with mock.patch.object(self.s,'cloud',side_effect=AssertionError('No paid replay')),mock.patch.object(self.s.CP,'validate_supervisor_events',return_value={}):
            self.s.reconcile_saved_audits();self.s.reconcile_saved_audits()
        self.assertEqual(self.q.snapshot()['audits'][0]['state'],'regression')
        self.assertFalse(self.q.delivery_allowed('b'*64,'a'*40))

    def test_acceptance_crash_replays_binding_and_clears_only_exact_corrective_hold(self):
        for corrective in (False,True):
            with self.subTest(corrective=corrective):
                runtime=self.root/str(corrective);self.s.STATE=runtime;q=Q.QualityMonitor(runtime,'fixture')
                if corrective:
                    q.record_review('old','c'*40,'b'*64,{'model':'fixture'},REVIEW,CHECKS);q.accepted('old','a'*40)
                    token=q.reserve('a'*40);q.finish(token,{'decision':'regression','summary':'Concrete defect.','issues':['Boundary broken']},{'source_head':'a'*40})
                    token=q.reserve('e'*64,'corrective',head='a'*40);q.finish(token,{'decision':'healthy','summary':'Exact repair.','issues':[]},{'source_head':'a'*40,'candidate_patch_sha256':'e'*64},approved_patch='e'*64)
                q.record_review('new','a'*40,'e'*64,{'model':'fixture'},REVIEW,CHECKS)
                directory=runtime/'runs/new';directory.mkdir(parents=True);task={'id':'T1','title':'Scoped repair','files':['src/a.py']};self.s.write(directory/'task.json',task)
                initial={'accepted':[],'done':[],'pending':{'run':'new'},'retry_candidates':{}}
                import copy
                state=copy.deepcopy(initial)
                with mock.patch.object(Q.QualityMonitor,'accepted',side_effect=RuntimeError('Crash after accepted receipt')),mock.patch.object(self.s,'git',return_value='local'):
                    with self.assertRaises(RuntimeError):self.s.accept_candidate(task,state,{},directory,'new','f'*40)
                def git(*args,**kw):return self.s.RUN_TRAILER+': new' if args[0]=='show' else ''
                state=copy.deepcopy(initial)
                with mock.patch.object(self.s,'git',side_effect=git),mock.patch.object(self.s,'reconcile_quality_acceptance'),mock.patch.object(self.s,'index_run'):
                    self.s.recover(state);self.s.recover(state)
                latest=q.snapshot()['local_reviews'][0]
                self.assertEqual(latest['accepted_commit'],'f'*40);self.assertIsNone(q.snapshot()['hold'])
                self.assertNotIn('pending',state);self.assertEqual(len(state['accepted']),1)

    def test_promotion_intent_recovery_binds_review_before_clearing_intent(self):
        self.q.record_review('new','a'*40,'e'*64,{'model':'fixture'},REVIEW,CHECKS)
        task={'id':'T1','title':'Scoped repair','files':['src/a.py']};self.s.write(self.s.STATE/'promotion-intent.json',{'run':'new','task':task})
        state={'accepted':[],'done':[],'retry_candidates':{}}
        with mock.patch.object(self.s,'resume_promotion',return_value='f'*40),mock.patch.object(self.s,'reconcile_quality_acceptance'),mock.patch.object(self.s,'index_run'):
            self.s.recover(state)
        self.assertEqual(self.q.snapshot()['local_reviews'][0]['accepted_commit'],'f'*40)
        self.assertEqual(self.s.read(self.s.STATE/'promotion-intent.json'),{})

    def test_corrective_rejection_returns_concrete_feedback_to_a_new_local_attempt(self):
        self.q.record_review('old','c'*40,'b'*64,{'model':'fixture'},REVIEW,CHECKS);self.q.accepted('old','a'*40)
        token=self.q.reserve('a'*40);self.q.finish(token,{'decision':'regression','summary':'Concrete defect.','issues':['Boundary broken']},{'source_head':'a'*40})
        directory=self.s.STATE/'runs/new';work=directory/'work';(work/'src').mkdir(parents=True);(work/'src/a.py').write_text('candidate\n')
        self.s.write(directory/'checks.json',CHECKS)
        task={'id':'T1','title':'Repair boundary','files':['src/a.py']};state={'pending':{'run':'new'}}
        def git(*args,**kw):return 'a'*40 if args[0]=='rev-parse' else ''
        rejected={'decision':'regression','summary':'Repair still wrong.','issues':['src/a.py: input Y still crosses the boundary']}
        with mock.patch.object(self.s,'git',side_effect=git) as calls,mock.patch.object(self.s,'require_checked_candidate'), \
             mock.patch.object(self.s,'cloud',return_value=dict(REVIEW)),mock.patch.object(self.s,'run_quality_audit',return_value=rejected):
            ok,run,feedback=self.s.review_and_promote(task,state,{'routine_provider':'local','worker_model':{'model':'fixture'}},
                self.s.now()+dt.timedelta(minutes=5),directory,work,'new','a'*40,'changed patch',['src/a.py'])
        self.assertFalse(ok);self.assertNotIn('pending',state)
        self.assertEqual(state['review_findings']['T1']['review']['issues'],rejected['issues'])
        self.assertFalse(any(c.args[0] in ('apply','commit') for c in calls.call_args_list))


class LocalSupervisorTests(unittest.TestCase):
    def response(self,**changes):
        return dict({'model':'fixture','done':True,'done_reason':'stop','prompt_eval_count':100,
            'eval_count':60,'message':{'role':'assistant','content':json.dumps(REVIEW)}},**changes)

    def infer(self,reply,descriptor=None,prompt='Inspect exact supplied change.'):
        descriptor=descriptor or {'model':'fixture','context':32768}
        opener=mock.Mock()
        opener.open.side_effect=lambda request,**kwargs:io.BytesIO(json.dumps(
            {'capabilities':[]} if request.full_url.endswith('/api/show') else reply).encode())
        with mock.patch.object(L.M,'local_descriptor',return_value=descriptor):
            value=L.infer(prompt,Q.RATED_REVIEW_SCHEMA,descriptor,opener=opener)
        return value,opener

    def test_loopback_json_request_has_fresh_context_no_tools_and_pinned_limits(self):
        (value,metrics),opener=self.infer(self.response())
        request=opener.open.call_args.args[0];body=json.loads(request.data)
        self.assertEqual(request.full_url,'http://127.0.0.1:11434/api/chat')
        self.assertEqual(value,REVIEW);self.assertEqual(metrics['eval_count'],60)
        self.assertEqual([m['role'] for m in body['messages']],['system','user'])
        self.assertNotIn('tools',body);self.assertFalse(body['stream'])
        self.assertEqual(body['format'],Q.RATED_REVIEW_SCHEMA);self.assertEqual(body['options']['num_ctx'],32768)

    def test_incomplete_wrong_model_tools_and_missing_context_metrics_rejected(self):
        for changes in ({'done':False},{'done_reason':'length'},{'model':'other'},{'prompt_eval_count':None},
                        {'prompt_eval_count':30000},{'message':{'role':'assistant','content':json.dumps(REVIEW),'tool_calls':[{}]}},
                        {'message':{'role':'user','content':json.dumps(REVIEW)}}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.infer(self.response(**changes))

    def test_changed_model_and_oversize_context_fail_without_request(self):
        with mock.patch.object(L.M,'local_descriptor',return_value={'model':'other','context':32768}):
            with self.assertRaises(ValueError):L.infer('x',Q.RATED_REVIEW_SCHEMA,{'model':'fixture','context':32768})
        with self.assertRaises(ValueError):self.infer(self.response(),prompt='x'*96001)

    def test_ratings_are_strict_integers_and_bounded_and_unknown_fields_rejected(self):
        for changes in ({'correctness':True},{'security':6},{'tests':-1},{'confidence':'certain'},{'extra':'injected'}):
            with self.assertRaises(ValueError):L.validate(dict(REVIEW,**changes),Q.RATED_REVIEW_SCHEMA)
        with self.assertRaises(ValueError):L.validate(float('nan'),{'type':'number'})

    def test_routine_backend_management_never_enters_cloud_policy(self):
        with tempfile.TemporaryDirectory() as name:
            backend=B.load_backend(Path(name),'fixture')
            with mock.patch.object(backend,'local_decision',return_value=REVIEW) as local, \
                 mock.patch.object(backend.CP,'CloudPolicy',side_effect=AssertionError('No cloud fallback')):
                self.assertEqual(backend.cloud('review',Q.RATED_REVIEW_SCHEMA,Path(name),None,{}, {'routine_provider':'local'}),REVIEW)
                self.assertEqual(local.call_args.args[-1],'review')

    def test_local_cache_requires_complete_exact_receipt_then_reuses_without_inference(self):
        with tempfile.TemporaryDirectory() as name:
            backend=B.load_backend(Path(name),'fixture');backend.STATE=Path(name)/'state'
            directory=Path(name)/'review';config={'worker_model':{'model':'fixture','context':32768}}
            state={};calls=[]
            def run(*args,**kw):
                calls.append(kw);args[6]()
                for file,value in [('result.json',REVIEW),('local-metrics.json',{'model':'fixture'})]:backend.write(directory/file,value)
                (directory/'events.jsonl').write_text('{}\n')
                return {'code':0,'reason':'exited'}
            with mock.patch.object(backend,'git',return_value='a'*40),mock.patch.object(backend,'charged_run',side_effect=run),mock.patch.object(backend,'journal_charge'):
                self.assertEqual(backend.local_decision('x',Q.RATED_REVIEW_SCHEMA,directory,None,state,config),REVIEW)
                self.assertEqual(backend.local_decision('x',Q.RATED_REVIEW_SCHEMA,directory,None,state,config),REVIEW)
                self.assertEqual(len(calls),1);self.assertEqual(calls[0]['resource_kind'],'local')
                self.assertEqual(state.get('cloud_calls_today',0),0)
                receipt=backend.read(directory/'local-decision-receipt.json');receipt['files']={'result.json':hashlib.sha256((directory/'result.json').read_bytes()).hexdigest()}
                backend.write(directory/'local-decision-receipt.json',receipt)
                backend.local_decision('x',Q.RATED_REVIEW_SCHEMA,directory,None,state,config)
                self.assertEqual(len(calls),2)

    def test_real_action_journal_charges_local_review_once_without_cloud_or_coding(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);backend=B.load_backend(root,'fixture');backend.STATE=root/'project-runtime';backend.CLOUD_RUNTIME=root/'shared-runtime'
            store=Actions(backend.CLOUD_RUNTIME);job=store.submit('fixture',{'session':'original-session'},time.time()+600)
            store.claim(job['id'],{'pid':1});directory=backend.STATE/'review';state={}
            def run(*args,**kw):
                self.assertEqual(kw['resource_kind'],'local');args[6]()
                backend.write(directory/'result.json',REVIEW);backend.write(directory/'local-metrics.json',{'model':'fixture'})
                (directory/'events.jsonl').write_text('{}\n');return {'code':0,'reason':'exited'}
            with mock.patch.dict('os.environ',{'MIDNIGHT_ACTION_ID':job['id']}),mock.patch.object(backend,'git',return_value='a'*40),mock.patch.object(backend,'charged_run',side_effect=run) as process:
                for _ in range(2):backend.local_decision('Exact review',Q.RATED_REVIEW_SCHEMA,directory,None,state,{'worker_model':{'model':'fixture','context':32768}})
            self.assertEqual(process.call_count,1)
            self.assertEqual(Actions(backend.CLOUD_RUNTIME).session_charges('original-session','fixture'),{'local_supervision':1})
            self.assertEqual(state['local_supervision_calls_today'],1);self.assertEqual(state.get('cloud_calls_today',0),0)
            self.assertEqual(state.get('local_attempts_today',0),0)


class ContinuousTests(unittest.TestCase):
    def test_persistent_mode_is_explicit_no_expiry_and_restores_saved_schedule(self):
        instant=dt.datetime(2026,11,1,12,tzinfo=dt.timezone.utc)
        override={'mode':'continuous','expires_at':None,'limits':M.CONTINUOUS_LIMITS}
        for future in (instant,instant+dt.timedelta(days=800)):
            result=M.window(future,DATA,override)
            self.assertTrue(result['active']);self.assertEqual(result['mode'],'continuous')
            self.assertEqual(result['cutoff'],future+dt.timedelta(hours=24));self.assertFalse(result['override_active'])
        self.assertFalse(M.window(instant,DATA)['active'])
        self.assertEqual(M.window(instant,DATA)['mode'],'scheduled')
        with self.assertRaises((ValueError,TypeError)):M.window(instant,DATA,{'expires_at':None})

    def test_backend_limits_honor_explicit_mode_but_not_expired_malformed_mode(self):
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/'override.json';instant=dt.datetime.now(dt.timezone.utc)
            path.write_text(json.dumps({'mode':'continuous','expires_at':None,'limits':M.CONTINUOUS_LIMITS}))
            self.assertEqual(B.temporary_limits(path,instant),M.CONTINUOUS_LIMITS)
            path.write_text(json.dumps({'mode':'continuous','expires_at':(instant-dt.timedelta(seconds=1)).isoformat(),'limits':M.CONTINUOUS_LIMITS}))
            self.assertEqual(B.temporary_limits(path,instant),{})

    def test_resource_fifo_prevents_new_phase_stealing_slot_then_releases_for_next(self):
        with tempfile.TemporaryDirectory() as name:
            runtime=Path(name);store=Actions(runtime);deadline=time.time()+60
            with mock.patch.dict('os.environ',{'MIDNIGHT_ACTION_ID':'fixture'}):
                first=store.phase('first','alpha','host','first',deadline,{})
                second=store.phase('second','beta','host','second',deadline,{})
                with self.assertRaises(P.PhaseInterrupted):
                    with P.resource(runtime,'host',time.time()+.06,pressure=lambda:False,poll=.005,turn=(second['created'],second['id'])):pass
                store.phase_update(first['id'],'succeeded',result={})
                with P.resource(runtime,'host',deadline,pressure=lambda:False,turn=(second['created'],second['id'])):pass


if __name__=='__main__':unittest.main()
