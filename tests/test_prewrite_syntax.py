"""Rejected Python edits preserve the last valid checkpoint and can be corrected."""
import contextlib,io,tempfile,time,unittest
from pathlib import Path
import structured_worker as S

class PrewriteSyntaxTests(unittest.TestCase):
 def test_bad_literal_is_rejected_without_poisoning_file_or_test_receipt(self):
  with tempfile.TemporaryDirectory() as name:
   root=Path(name);work=root/'work';work.mkdir();path=work/'case.py';path.write_text('value = 1\n')
   worker=S.Worker(work,root/'worker',{'allowed_files':['case.py']},{'model':'fixture'})
   worker.action({'action':'read_file','path':'case.py','content':'','summary':'Read current'},time.monotonic()+3)
   previous={'signature':worker.signature(),'result':{'code':0,'reason':'exited'}};worker.last_test=previous
   with self.assertRaisesRegex(ValueError,'Python syntax rejected before write'):
    worker.edit('case.py',{'old_text':'value = 1','new_text':'self.assertNotIn("valid": "json", output)'},'Invalid quote',time.monotonic()+3)
   self.assertEqual(path.read_text(),'value = 1\n');self.assertEqual(worker.writes,set());self.assertIs(worker.last_test,previous)
   with contextlib.redirect_stdout(io.StringIO()):worker.edit('case.py',{'old_text':'value = 1','new_text':'value = {"valid": "json"}'},'Correct literal',time.monotonic()+3)
   self.assertEqual(path.read_text(),'value = {"valid": "json"}\n');self.assertEqual(worker.writes,{'case.py'});self.assertIsNone(worker.last_test)
 def test_invalid_creation_is_absent_and_existing_invalid_draft_can_be_repaired(self):
  with tempfile.TemporaryDirectory() as name:
   root=Path(name);work=root/'work';work.mkdir();worker=S.Worker(work,root/'worker',{'allowed_files':['case.py']},{'model':'fixture'})
   worker.action({'action':'read_file','path':'case.py','content':'','summary':'Read absent'},time.monotonic()+3)
   with self.assertRaises(ValueError):worker.edit('case.py',{'old_text':'','new_text':'value = {'},'Incomplete creation',time.monotonic()+3)
   self.assertFalse((work/'case.py').exists())
   (work/'case.py').write_text('value = {\n');worker.action({'action':'read_file','path':'case.py','content':'','summary':'Read retained invalid draft'},time.monotonic()+3)
   with contextlib.redirect_stdout(io.StringIO()):worker.edit('case.py',{'old_text':'value = {','new_text':'value = {}'},'Restore valid syntax',time.monotonic()+3)
   self.assertEqual((work/'case.py').read_text(),'value = {}\n')

if __name__=='__main__':unittest.main()
