"""Real worker-loop regression for duplicated read history blocking all edits."""
import contextlib,io,json,os,tempfile,unittest
from pathlib import Path
from unittest import mock
import structured_worker as S


class EditContextTests(unittest.TestCase):
 def exercise(self,host_size,source_size):
  with tempfile.TemporaryDirectory() as name:
   root=Path(name).resolve();work=root/'work';work.mkdir();file=work/'case.py'
   original='# '+('x'*source_size)+'\nvalue = 0\n';file.write_text(original)
   manifest={'task_id':'fixture','allowed_files':['case.py'],'timeout_seconds':10,'test_command':['fixed-syntax']}
   path=root/'manifest.json';path.write_text(json.dumps(manifest));model={'model':'fixture','context':32768}
   contract='Keep all seventeen acceptance clauses, exact scope, holds and final gates.\n'+('C'*host_size)+'\nLast original obligation must remain complete.'
   base=[{'role':'system','content':'Fixed host worker instructions. No permission expansion.'},{'role':'user','content':contract}]
   actions=iter(['read_file','read_file','edit_file','run_tests']);edits=[];output=io.StringIO()
   def infer(model,messages,deadline,*,schema,predict):
    if schema is S.EDIT_SCHEMA:
     self.assertEqual(messages[:2],base)
     request=json.loads(messages[-1]['content']);self.assertEqual(request['current_file'],original)
     self.assertLessEqual(len(json.dumps(messages).encode()),90000);edits.append(request)
     return {'old_text':'value = 0','new_text':'value = 1'},{}
    action=next(actions,'finish')
    return {'action':action,'path':'case.py' if action in ('read_file','edit_file') else '',
            'summary':'Change one value and preserve the full contract'},{}
   result={'code':0,'reason':'exited','output':'fixed syntax passed'}
   before=Path.cwd()
   try:
    os.chdir(work)
    with mock.patch.object(S,'load_manifest',return_value=manifest),mock.patch.object(S,'brief_messages',return_value=base.copy()),mock.patch.object(S,'infer',side_effect=infer),mock.patch.object(S.M,'local_descriptor',return_value=model),mock.patch.object(S.B,'GPU_LOCK',root/'gpu.lock'),mock.patch.object(S,'run_tests',return_value=result),mock.patch.dict(os.environ,{'MIDNIGHT_WORKER_DESCRIPTOR':json.dumps(model)}),mock.patch('sys.argv',['worker','--manifest',str(path)]),contextlib.redirect_stdout(output):
     try:outcome=S.main()
     except RuntimeError as error:outcome=str(error)
   finally:os.chdir(before)
   return outcome,file.read_text(),edits,output.getvalue(),(root/'worker-tools/submission.json').exists()

 def test_complete_contract_and_source_still_edit_after_duplicate_reads(self):
  outcome,text,edits,output,submitted=self.exercise(57000,12800)
  self.assertEqual(outcome,0,output[-1800:]);self.assertEqual(len(edits),1)
  self.assertTrue(text.endswith('value = 1\n'));self.assertTrue(submitted)

 def test_genuinely_oversized_complete_input_holds_before_edit_inference(self):
  outcome,text,edits,output,submitted=self.exercise(78000,20000)
  self.assertNotEqual(outcome,0);self.assertEqual(edits,[]);self.assertFalse(submitted)
  self.assertTrue(text.endswith('value = 0\n'))
  self.assertIn('host scope is required before edit inference',output)


if __name__=='__main__':unittest.main()
