"""Plan admission never substitutes for completed-code acceptance."""
import json,tempfile,unittest
from pathlib import Path
import project_backend as B
import progress_recovery as P

class NarrowPlanReviewTests(unittest.TestCase):
 def accepted(self):return {'admission':'admit','source_supports_proposed_repair':True,'original_obligation_is_unmet':True,'within_current_step_and_scope':True,'repeats_prior_work':False,'preserves_full_final_acceptance':True,'reason':'The existing regex assertion is insufficient; this plan fixes that one unmet part. Output capture and parser-mock removal remain required later.'}
 def test_incomplete_current_code_can_admit_one_supported_repair_but_not_accept_product(self):
  with tempfile.TemporaryDirectory() as name:
   root=Path(name);r=P.Recovery(root,'notes');task={'id':'retained','files':['tests/value.py'],'acceptance':['Exact errors plus full integration']};scope=P.scope('notes','failure:1',task,'a'*40,'profile');model={'model':'fixture'}
   r.enroll(scope,task,model,legacy_tries=4);review=B.narrowing_review_result(self.accepted());path=root/'admission.json';path.write_text(json.dumps(review))
   r.offer_narrowing(scope,model,obligation=0,instruction='Fix only the exact error assertion; preserve final checks',refs=[P.ref(path)],review=review)
   r.reserve(scope,'new-local-run',model);self.assertFalse(r.admission(scope,model)['allowed']);self.assertNotEqual(r.snapshot()['scopes'][0]['admission']['state'],'complete')
 def test_contradictory_or_repeated_plan_fails_host_admission(self):
  for field in ('admission','source_supports_proposed_repair','original_obligation_is_unmet','within_current_step_and_scope','repeats_prior_work','preserves_full_final_acceptance'):
   result=self.accepted();result[field]='reject' if field=='admission' else not result[field]
   with self.subTest(field=field):self.assertEqual(B.narrowing_review_result(result)['decision'],'revise')
 def test_completed_code_review_or_missing_evidence_cannot_be_misread_as_plan_admission(self):
  for result in ({'decision':'accept','summary':'All tests pass','issues':[]},{'admission':'admit'},dict(self.accepted(),source_supports_proposed_repair='yes')):
   with self.assertRaises(ValueError):B.narrowing_review_result(result)

if __name__=='__main__':unittest.main()
