import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import worker_quality as Q


class SharedWorkerQualityTests(unittest.TestCase):
    def test_retained_rejected_work_is_explicit_before_old_task_instructions(self):
        with tempfile.TemporaryDirectory() as name:
            folder=Path(name)
            self.assertIn('Fresh task',Q.repair_preamble(folder))
            Q.write(folder/'retry-seed.json',{'run':'saved-rejected-candidate'})
            self.assertIn('REJECTED',Q.repair_preamble(folder))
            self.assertIn('change the candidate',Q.repair_preamble(folder))

    def test_manager_receipts_explain_real_response_and_timeout_without_copying_raw_logs(self):
        with tempfile.TemporaryDirectory() as name:
            folder=Path(name)
            Q.write(folder/'task.json',{'id':'bounded-repair'})
            Q.write(folder/'worker-process.json',{'reason':'task_timeout','code':-15,'seconds':720})
            Q.write(folder/'worker-completion.json',{'response_observed':True})
            (folder/'worker-events.jsonl').write_text('PRIVATE_RAW_EVENT')
            proof=Q.attempt_evidence(folder)
            self.assertTrue(proof['response_observed']);self.assertEqual(proof['process']['seconds'],720)
            self.assertNotIn('PRIVATE_RAW_EVENT',json.dumps(proof))
            (folder/'worker-completion.json').unlink()
            self.assertIsNone(Q.attempt_evidence(folder)['response_observed'])
    def test_successor_resumes_its_own_latest_candidate_before_parent(self):
        parent={'id':'old','description':'Original scope'}
        task={'id':'successor','retry_of':{'task_id':'old','task_sha256':Q.task_fingerprint(parent)}}
        old={'task_sha256':Q.task_fingerprint(parent),'run':'parent-run'}
        latest={'task_sha256':Q.task_fingerprint(task),'run':'newer-repair'}
        state={'retry_candidates':{'old':old,'successor':latest}}
        self.assertEqual(Q.source_candidate(task,state)['run'],'newer-repair')
        state['retry_candidates']['successor']['task_sha256']='0'*64
        self.assertEqual(Q.source_candidate(task,state)['run'],'parent-run')

    def test_host_handoff_exit_codes_do_not_escape_no_change_accounting(self):
        with tempfile.TemporaryDirectory() as name:
            folder=Path(name)
            for code in (0,130,-15):
                Q.write(folder/'worker-process.json',{'reason':'ready_for_review','code':code})
                self.assertEqual(Q.substantive_failure(folder,'Unchanged retry candidate'),'worker_no_change')
                self.assertIsNone(Q.substantive_failure(folder,'Unchanged retry candidate',interrupted=True))
            Q.write(folder/'worker-process.json',{'reason':'exited','code':1})
            self.assertIsNone(Q.substantive_failure(folder,'Unchanged retry candidate'))

    def test_host_only_checks_and_cloud_runs_cannot_count_as_local_failures(self):
        with tempfile.TemporaryDirectory() as name:
            folder=Path(name)
            Q.write(folder/'checks.json',[{'reason':'exited','code':1}])
            self.assertIsNone(Q.substantive_failure(folder,'Independent checks failed'))
            Q.write(folder/'worker-process.json',{'reason':'exited','code':0})
            for kind in ('cloud','saved_candidate'):
                Q.write(folder/'implementation-backend.json',{'kind':kind})
                self.assertIsNone(Q.substantive_failure(folder,'Independent checks failed'))

    def test_unchanged_unverified_draft_can_receive_its_first_host_verification(self):
        with tempfile.TemporaryDirectory() as name:
            folder=Path(name);patch='candidate'
            Q.write(folder/'retry-seed.json',{'run':'prior','patch_sha256':hashlib.sha256(patch.encode()).hexdigest()})
            self.assertFalse(Q.retry_progress(folder,patch,['src/a.py'],first_verification=True))
            value=Q.read(folder/'retry-progress.json');self.assertTrue(value['unchanged_candidate']);self.assertFalse(value['blocked'])
            self.assertTrue(Q.retry_progress(folder,patch,['src/a.py'],first_verification=False))
    def test_failed_feedback_does_not_bury_one_failure_under_passing_logs(self):
        with tempfile.TemporaryDirectory() as name:
            folder=Path(name)
            Q.write(folder/'checks.json',[{'code':0,'reason':'exited'},{'code':1,'reason':'exited'}])
            (folder/'check-0.log').write_text('passing output '*10000)
            (folder/'check-1.log').write_text('AssertionError: unknown field accepted')
            feedback=Q.check_feedback(folder)
            self.assertIn('unknown field accepted',feedback);self.assertNotIn('passing output',feedback)
            self.assertTrue((folder/'check-feedback.json').exists())

    def test_small_behaviour_and_shared_model_entry_are_in_every_policy(self):
        text=Q.policy()['instruction']
        self.assertIn('one observable behaviour',text)
        self.assertIn('complete integration acceptance',text)
        self.assertIn('MIDNIGHT_LOCAL_CODE_BIN',text)

    def test_controls_apply_independently_to_multiple_project_task_names(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            for project,identity in [('inventory','stock-17'),('notes','export-2')]:
                folder=root/project;folder.mkdir()
                state={'review_findings':{identity:{'run':'previous','review':{'issues':['Preserve existing records']}}}}
                plan=Q.repair_plan({'id':identity},state,folder)
                self.assertEqual([(i['id'],i['finding']) for i in plan['items']],[('R1','Preserve existing records')])
                self.assertEqual(plan['items'][0]['source_task'],identity)
                self.assertEqual(plan['task'],identity)
                patch='fixture patch\n'
                Q.write(folder/'retry-seed.json',{'run':'previous','patch_sha256':hashlib.sha256(patch.encode()).hexdigest()})
                self.assertTrue(Q.retry_progress(folder,patch,['src/output.txt']))
                self.assertFalse(Q.retry_progress(folder,patch+'new change\n',['src/output.txt'],'+new change'))
                progress=json.loads((folder/'retry-progress.json').read_text())
                self.assertEqual(progress['delta'],'+new change')
                self.assertFalse(plan['worker_claim_is_acceptance'])

    def test_policy_receipt_binds_instruction_and_installed_module(self):
        policy=Q.policy()
        self.assertEqual(policy['sha256'],hashlib.sha256(policy['instruction'].encode()).hexdigest())
        self.assertEqual(policy['module_sha256'],hashlib.sha256(Path(Q.__file__).read_bytes()).hexdigest())

    def test_long_findings_are_preserved_and_oversize_plans_stop_explicitly(self):
        with tempfile.TemporaryDirectory() as name:
            folder=Path(name);finding='x'*5000
            state={'review_findings':{'T1':{'run':'previous','review':{'issues':[finding]}}}}
            plan=Q.repair_plan({'id':'T1'},state,folder)
            self.assertEqual(plan['items'][0]['finding'],finding)
            issues=['Issue '+str(i) for i in range(31)]
            state['review_findings']['T1']['review']['issues']=issues
            with self.assertRaisesRegex(ValueError,'Codex must split'):Q.repair_plan({'id':'T1'},state,folder)
            retained=json.loads((folder/'repair-plan.json').read_text())
            self.assertEqual(retained['complete_review']['issues'],issues)
            self.assertEqual(len(retained['items']),31)

    def test_coordinator_successor_binds_exact_original_task_without_resetting_history(self):
        old={'id':'old','description':'Original scope'};digest=Q.task_fingerprint(old)
        receipt={'task_sha256':digest,'run':'failed-draft'}
        state={'retry_candidates':{'old':receipt},'tries':{'old':2}}
        new={'id':'repair','retry_of':{'task_id':'old','task_sha256':digest}}
        self.assertEqual(Q.source_candidate(new,state),receipt)
        new['retry_of']['task_sha256']='0'*64;self.assertIsNone(Q.source_candidate(new,state))
        self.assertEqual(state['tries'],{'old':2})
        state['review_findings']={'old':{'run':'previous-review','review':{'issues':['Keep the original contract']}}}
        new['retry_of']['task_sha256']=digest
        with tempfile.TemporaryDirectory() as name:
            plan=Q.repair_plan(new,state,Path(name))
        self.assertEqual(plan['items'][0]['finding'],'Keep the original contract')
        self.assertEqual(plan['source_task'],'old')
        state['review_findings']['repair']={'run':'successor-review','review':{'issues':['Fix the new edge case']}}
        with tempfile.TemporaryDirectory() as name:plan=Q.repair_plan(new,state,Path(name))
        self.assertEqual([i['finding'] for i in plan['items']],['Keep the original contract','Fix the new edge case'])
        self.assertEqual([(r['task'],r['run']) for r in plan['complete_reviews']],[('old','previous-review'),('repair','successor-review')])


if __name__=='__main__':unittest.main()
