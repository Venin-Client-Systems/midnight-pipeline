"""Recovery roles receive persistent manager corrections without new authority."""
import json,tempfile,unittest
from pathlib import Path
from unittest import mock
import project_backend as B

class RecoveryGuidanceTests(unittest.TestCase):
 def test_both_recovery_roles_receive_the_saved_principal_correction(self):
  for role,actor in [('diagnosis','planner:notes'),('diagnosis_review','reviewer:notes')]:
   with self.subTest(role=role),tempfile.TemporaryDirectory() as name:
    root=Path(name);backend=B.load_backend(root,'notes');backend.STATE=root/'state';backend.CLOUD_RUNTIME=root/'shared'
    directory=root/'decision';answer={'decision':'accept','summary':'Exact source supports one narrower repair','issues':[]}
    workspace=mock.Mock();workspace.context.return_value={'principal_decisions':[{'body':'Nested case is already correct; fix the exact malformed JSON error assertion.'}]}
    def run(*args,**kwargs):
     self.assertIn('Nested case is already correct',(directory/'input.txt').read_text());args[6]()
     backend.write(directory/'result.json',answer);backend.write(directory/'local-metrics.json',{'model':'fixture'});(directory/'events.jsonl').write_text('{}\n');return {'code':0,'reason':'exited'}
    with mock.patch.object(backend.PR,'enabled',return_value=True),mock.patch.object(backend.CW,'Workspace',return_value=workspace),mock.patch.object(backend,'git',return_value='a'*40),mock.patch.object(backend,'journal_charge'),mock.patch.object(backend,'charged_run',side_effect=run):
     self.assertEqual(backend.local_decision('Inspect only actual source',backend.REVIEW_SCHEMA,directory,None,{}, {'worker_model':{'model':'fixture'}},role=role),answer)
    workspace.context.assert_called_once_with(actor,limit=6)
 def test_only_committed_scoped_decisions_change_recovery_context(self):
  workspace=mock.Mock();workspace.context.side_effect=[{'principal_decisions':[{'id':'fixed','body':'Same source correction'}],'recent':['first ordinary post']},{'principal_decisions':[{'id':'fixed','body':'Same source correction'}],'recent':['later routine post']}]
  with mock.patch.object(B.CW,'Workspace',return_value=workspace):
   first=B.recovery_principal_context();self.assertEqual(first,B.recovery_principal_context());self.assertEqual(first[0]['id'],'fixed')
 def test_unavailable_principal_context_stays_explicit(self):
  with mock.patch.object(B.CW,'Workspace',side_effect=OSError('Unavailable private history')):self.assertEqual(B.recovery_principal_context(),{'unavailable':True})

if __name__=='__main__':unittest.main()
