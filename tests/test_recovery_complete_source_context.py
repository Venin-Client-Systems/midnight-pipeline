"""Recovery must see the current writable method beyond a bounded prefix."""
import contextlib
import copy
import datetime as dt
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import project_backend as B


class CompleteRecoverySourceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.project=self.root/'project';self.project.mkdir()
  self.state=self.root/'state';self.work=self.state/'runs'/'retained'/'work';self.work.mkdir(parents=True)
  self.text='# Preserve predecessor cases.\n'+'pass\n'*3000+'\ndef current_method():\n    return "THE_UNMET_CURRENT_METHOD"\n'
  self.path=self.work/'case.py';self.path.write_text(self.text)
  (self.project/'case.py').write_text('STALE CANONICAL SOURCE\n')
  self.task={'id':'original','description':'Frozen production; original scope remains mandatory.',
             'acceptance':['Original requirement '+str(i) for i in range(17)],'files':['case.py'],'local_worker_files':['case.py']}
  self.source=B.EC.source_context(self.work,self.task,[],{},max_bytes=16000)
  self.assertTrue(self.source['case.py']['truncated']);self.assertNotIn('THE_UNMET_CURRENT_METHOD',self.source['case.py']['content'])
  self.prior={'run':'retained'}
  for name,value in [('STATE',self.state),('ROOT',self.project)]:
   patch=mock.patch.object(B,name,value);patch.start();self.addCleanup(patch.stop)

 def capsule(self):
  return B.recovery_model_context(self.task,{'index':2,'acceptance':['Keep every current step condition']},
      {'kind':'semantic','fingerprint':'f'*64},self.prior,{'source':self.source},
      {'guidance':{'constraint':'Keep all original holds'}},[],{'issues':['Keep the audit gate']})

 def test_complete_current_method_and_all_original_obligations_reach_the_existing_model_path(self):
  before=copy.deepcopy(self.source);value=json.loads(B.recovery_model_json(self.capsule()))
  current=value['current_source']['case.py']
  self.assertEqual(current['content'],self.text);self.assertFalse(current['truncated'])
  self.assertEqual(current['sha256'],hashlib.sha256(self.path.read_bytes()).hexdigest())
  self.assertEqual(value['original_task']['acceptance'],self.task['acceptance'])
  self.assertEqual(value['current_microstep']['acceptance'],['Keep every current step condition'])
  self.assertEqual(value['committed_codex_guidance']['guidance']['constraint'],'Keep all original holds')
  self.assertEqual(value['codex_findings']['issues'],['Keep the audit gate']);self.assertEqual(self.source,before)

 def test_missing_retained_work_uses_only_matching_registered_source(self):
  self.prior={'run':'not-present'};(self.project/'case.py').write_text(self.text)
  self.assertEqual(self.capsule()['current_source']['case.py']['content'],self.text)

 def test_changed_source_cannot_be_combined_with_the_earlier_hash(self):
  self.path.write_text(self.text+'\n# changed\n')
  with self.assertRaises(B.ManagerHeld):self.capsule()

 def test_retained_source_symlink_is_rejected(self):
  target=self.root/'external.py';target.write_text(self.text);self.path.unlink();self.path.symlink_to(target)
  with self.assertRaises(B.ManagerHeld):self.capsule()

 def test_retained_work_symlink_is_rejected_even_with_matching_bytes(self):
  self.path.unlink();self.work.rmdir();self.work.symlink_to(self.project,target_is_directory=True)
  (self.project/'case.py').write_text(self.text)
  with self.assertRaises(B.ManagerHeld):self.capsule()

 def test_unsafe_retained_identity_is_rejected(self):
  self.prior={'run':'../../external'}
  with self.assertRaises(B.ManagerHeld):self.capsule()

 def test_absolute_source_path_cannot_read_other_state_even_with_matching_hash(self):
  name=str(self.path);self.task['local_worker_files']=[name];self.source={name:self.source['case.py']}
  with self.assertRaises(B.ManagerHeld):self.capsule()

 def test_missing_writable_evidence_holds_before_model_projection(self):
  self.source={}
  with self.assertRaises(B.ManagerHeld):self.capsule()

 def test_unavailable_writable_evidence_holds_before_model_projection(self):
  self.source={'case.py':{'unavailable':'Source exceeds bounded collection size'}}
  with self.assertRaises(B.ManagerHeld):self.capsule()

 def test_incomplete_hash_metadata_cannot_be_passed_through_as_complete_source(self):
  self.source={'case.py':{'content':'prefix','truncated':False}}
  with self.assertRaises(B.ManagerHeld):self.capsule()

 def test_complete_source_paths_are_validated_before_the_no_truncation_return(self):
  for name in (str(self.path),'../case.py','*.py',''):
   with self.subTest(name=name):
    self.task['local_worker_files']=[name];self.source={name:{'sha256':'a'*64,'content':self.text,'truncated':False}}
    with self.assertRaises(B.ManagerHeld):self.capsule()

 def test_complete_evidence_cannot_bypass_retained_run_validation(self):
  self.prior={'run':'../../external'};self.source['case.py']['truncated']=False
  with self.assertRaises(B.ManagerHeld):self.capsule()

 def test_complete_excerpts_still_reject_a_linked_retained_workspace(self):
  self.source['case.py'].update(content=self.text,truncated=False)
  self.path.unlink();self.work.rmdir();self.work.symlink_to(self.project,target_is_directory=True)
  (self.project/'case.py').write_text(self.text)
  with self.assertRaises(B.ManagerHeld):self.capsule()

 def test_complete_excerpts_still_reject_a_linked_retained_run_directory(self):
  self.source['case.py'].update(content=self.text,truncated=False)
  retained=self.work.parent;detached=self.root/'detached';retained.rename(detached);retained.symlink_to(detached,target_is_directory=True)
  with self.assertRaises(B.ManagerHeld):self.capsule()

 def test_dangling_workspace_link_cannot_silently_fall_back_to_registered_source(self):
  self.source['case.py'].update(content=self.text,truncated=False)
  self.path.unlink();self.work.rmdir();self.work.symlink_to(self.root/'missing',target_is_directory=True)
  (self.project/'case.py').write_text(self.text)
  with self.assertRaises(B.ManagerHeld):self.capsule()

 def prepare_source(self):
  failed=self.state/'runs'/'failed';failed.mkdir(parents=True,exist_ok=True)
  (failed/'task.json').write_text(json.dumps(self.task))
  identity={'id':'original-scope','episode':'original-episode','head':'a'*40,'profile':'original-profile'}
  store=mock.Mock();store.admission.return_value={'allowed':False};store.observe.return_value={'kind':'semantic','fingerprint':'f'*64,'refs':['retained-check']}
  store.narrowing_history.return_value=[]
  with contextlib.ExitStack() as stack:
   for name,value in [('progress_identity',identity),('progress_store',store),('latest_progress_run','failed'),('progress_candidate_refs',[]),('manager_context',{}),('recovery_principal_context',[]),('checkpoint_audit_guidance',None)]:
    stack.enter_context(mock.patch.object(B,name,return_value=value))
   stack.enter_context(mock.patch.object(B.Q,'source_candidate',return_value=self.prior))
   micro=stack.enter_context(mock.patch.object(B.MS,'MicroRecovery'));micro.return_value.get.return_value=None
   rescue=stack.enter_context(mock.patch.object(B,'rescue_store'));rescue.return_value.guidance.return_value=None
   stack.enter_context(mock.patch.object(B.RP,'certificates',return_value=[]));stack.enter_context(mock.patch.object(B.PR,'enabled',return_value=False))
   collect=stack.enter_context(mock.patch.object(B.EC,'source_context',wraps=B.EC.source_context))
   infer=stack.enter_context(mock.patch.object(B,'local_decision',side_effect=AssertionError('Unsafe writable source reached model inference')))
   with self.assertRaises(B.ManagerHeld):B.prepare_progress(self.task,{}, {'worker_model':{'model':'fixture'}},B.now()+dt.timedelta(minutes=1),diagnose=True)
   collect.assert_not_called();infer.assert_not_called()

 def test_real_preparation_checks_retained_root_before_source_collection_or_inference(self):
  self.path.unlink();self.work.rmdir();self.work.symlink_to(self.project,target_is_directory=True)
  (self.project/'case.py').write_text('value = 1\n')
  self.prepare_source()

 def test_real_preparation_checks_each_writable_file_before_collection_or_inference(self):
  external=self.root/'external.py';external.write_text('value = 1\n');self.path.unlink();self.path.symlink_to(external)
  self.prepare_source()

 def test_oversized_complete_source_holds_instead_of_returning_a_prefix(self):
  self.path.write_text('pass\n'*10000)
  self.source=B.EC.source_context(self.work,self.task,[],{},max_bytes=16000)
  with self.assertRaises(B.ManagerHeld):self.capsule()

 def test_complete_source_is_redacted_while_hash_still_names_raw_file(self):
  self.path.write_text(self.text+'API_KEY = "fixture-sensitive-value"\n')
  self.source=B.EC.source_context(self.work,self.task,[],{},max_bytes=16000)
  current=self.capsule()['current_source']['case.py']
  self.assertIn('THE_UNMET_CURRENT_METHOD',current['content']);self.assertNotIn('fixture-sensitive-value',current['content'])
  self.assertTrue(current['redacted']);self.assertFalse(current['truncated'])
  self.assertEqual(current['sha256'],hashlib.sha256(self.path.read_bytes()).hexdigest())

 def test_complete_contract_overflow_holds_without_trimming_requirements(self):
  original=self.task['acceptance']+['X'*30000+'FINAL_REQUIRED_CONSTRAINT'];self.task['acceptance']=original
  with self.assertRaises(B.ManagerHeld):self.capsule()
  self.assertEqual(self.task['acceptance'],original)


if __name__=='__main__':unittest.main()
