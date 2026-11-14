"""Input growth may omit identified read-only bodies, never repair authority."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import project_backend as B


class RecoveryCapsulePackingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  root=Path(self.tmp.name);self.work=root/'state/runs/retained/work';self.work.mkdir(parents=True)
  self.source={}
  self.put('case.py','#'+('W'*18000)+'\ndef remaining_obligation():\n    return False\n')
  self.put('frozen.py','#'+('R'*18000)+'\n')
  self.task={'id':'original','title':'Original repair','description':'D'*6000,
             'acceptance':['Required condition '+str(i)+' '+('A'*300) for i in range(8)],
             'files':['case.py','frozen.py'],'local_worker_files':['case.py']}
  self.step={'index':3,'total':4,'description':'Current obligation; preserve final acceptance',
             'acceptance':['Every current step condition']}
  self.failure={'kind':'semantic','fingerprint':'f'*64,'failed_checks':1}
  self.prior={'run':'retained','base':'a'*40,'patch_sha256':'b'*64}
  self.host={'source':self.source,'principal_direction':[{'body':'Keep current scope and full original acceptance'}],
             'checks':{'checks':[{'command':['python3','-m','unittest'],'code':1,'reason':'exited',
                 'combined_stdout_stderr':'AssertionError: required behavior is still absent'}]}}
  self.guidance={'guidance':{'steps':['Keep the original committed guidance']}}
  self.history=[{'anchor':{'unit_id':'u'*64,'path':'case.py','symbol':'already_spent'}}]
  self.findings={'issues':['Keep the original corrective audit hold']}
  for name,value in [('STATE',root/'state'),('ROOT',root/'project')]:
   patch=mock.patch.object(B,name,value);patch.start();self.addCleanup(patch.stop)

 def put(self,name,body):
  (self.work/name).write_text(body)
  self.source[name]={'content':body,'sha256':hashlib.sha256(body.encode()).hexdigest(),
                     'truncated':False,'redacted':False}

 def capsule(self):
  return B.recovery_model_context(self.task,self.step,self.failure,self.prior,self.host,
                                   self.guidance,self.history,self.findings)

 def fitting_capsule(self):
  try:return self.capsule()
  except B.ManagerHeld as exc:self.fail('Complete writable evidence and contract fit after optional read-only projection: '+str(exc))

 def test_read_only_body_growth_fits_without_losing_any_original_contract_or_writable_source(self):
  before=copy.deepcopy(self.host);value=self.fitting_capsule()
  self.assertLessEqual(len(B.recovery_model_json(value).encode()),44000)
  self.assertEqual(value['current_source']['case.py'],self.source['case.py'])
  self.assertEqual(value['original_task'],self.task)
  self.assertEqual(value['current_microstep'],self.step)
  self.assertEqual(value['failure'],self.failure)
  self.assertEqual(value['prior_candidate'],self.prior)
  self.assertEqual(value['principal_direction'],self.host['principal_direction'])
  self.assertEqual(value['committed_codex_guidance'],self.guidance)
  self.assertEqual(value['codex_findings'],self.findings)
  self.assertEqual(value['used_work_units'][0]['unit_id'],'u'*64)
  self.assertIn('required behavior is still absent',value['host_check_receipts']['checks'][0]['combined_stdout_stderr'])
  self.assertEqual(self.host,before)

 def test_omission_is_explicit_and_retains_raw_identity_and_exact_omitted_body_digest(self):
  value=json.loads(B.recovery_model_json(self.fitting_capsule()))['current_source']['frozen.py']
  self.assertNotIn('content',value)
  self.assertEqual(value['content_omitted'],'read_only_context_budget')
  self.assertEqual(value['sha256'],self.source['frozen.py']['sha256'])
  body=self.source['frozen.py']['content']
  self.assertEqual(value['omitted_utf8_bytes'],len(body.encode()))
  self.assertEqual(value['omitted_content_sha256'],hashlib.sha256(body.encode()).hexdigest())

 def test_all_original_paths_are_mandatory_when_there_is_no_narrower_worker_scope(self):
  self.task.pop('local_worker_files');before=copy.deepcopy(self.host)
  with self.assertRaises(B.ManagerHeld):self.capsule()
  self.assertEqual(self.host,before)

 def test_complete_writable_overflow_still_holds_and_never_returns_a_prefix(self):
  self.put('case.py','#'+('W'*45000)+'\n');before=copy.deepcopy(self.host)
  with self.assertRaises(B.ManagerHeld):self.capsule()
  self.assertEqual(self.host,before)

 def test_source_bodies_remain_complete_when_the_whole_context_already_fits(self):
  self.task['description']='Keep original scope';self.put('frozen.py','frozen = True\n')
  value=self.fitting_capsule()
  self.assertEqual(value['current_source'],self.source)
  self.assertNotIn('content_omitted',value['current_source']['frozen.py'])

 def test_largest_optional_body_is_omitted_first_and_other_read_only_context_survives(self):
  self.task['description']='Keep original scope';self.put('case.py','#'+('W'*10000)+'\n')
  self.put('smaller.py','#'+('S'*13000)+'\n');self.task['files'].append('smaller.py')
  self.put('frozen.py','#'+('R'*20000)+'\n')
  value=self.fitting_capsule()
  self.assertNotIn('content',value['current_source']['frozen.py'])
  self.assertEqual(value['current_source']['smaller.py'],self.source['smaller.py'])
  self.assertEqual(value['current_source']['case.py'],self.source['case.py'])

 def test_original_serialization_limit_still_rejects_oversized_review_evidence(self):
  value=self.fitting_capsule();value['proposal']={'reason':'X'*48000}
  with self.assertRaises(B.ManagerHeld):B.recovery_model_json(value)


if __name__=='__main__':unittest.main()
