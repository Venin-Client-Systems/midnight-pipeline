"""A task that must create a file can still be diagnosed after a failed attempt.

Regression: exampleapp R005F declared tests/test_unit_workspace.py, the worker
never created it, and the diagnosis precheck treated the absent file as a
trust-boundary violation, holding the whole project indefinitely.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import project_backend as B


class NewWritableFileRecoveryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.project=self.root/'project';self.project.mkdir()
  self.state=self.root/'state';self.work=self.state/'runs'/'retained'/'work';(self.work/'pkg').mkdir(parents=True)
  (self.work/'tests').mkdir();(self.work/'pkg'/'workspace.py').write_text('def load():\n    return None\n')
  self.task={'id':'R005F','description':'Load one workspace.','acceptance':['Loads','Rejects writes'],
             'files':['pkg/workspace.py','tests/test_unit_workspace.py']}
  self.prior={'run':'retained'}
  for name,value in [('STATE',self.state),('ROOT',self.project)]:
   patch=mock.patch.object(B,name,value);patch.start();self.addCleanup(patch.stop)

 def test_absent_file_in_existing_directory_may_be_created(self):
  self.assertTrue(B.recovery_missing_writable(self.work,self.state,'tests/test_unit_workspace.py'))

 def test_absent_file_in_absent_directory_may_be_created(self):
  self.assertTrue(B.recovery_missing_writable(self.work,self.state,'newpkg/sub/test_new.py'))

 def test_existing_file_is_not_reported_missing(self):
  self.assertFalse(B.recovery_missing_writable(self.work,self.state,'pkg/workspace.py'))

 def test_dangling_symlink_at_path_is_rejected(self):
  (self.work/'tests'/'test_unit_workspace.py').symlink_to(self.root/'nowhere.py')
  self.assertFalse(B.recovery_missing_writable(self.work,self.state,'tests/test_unit_workspace.py'))

 def test_symlinked_parent_directory_is_rejected(self):
  outside=self.root/'outside';outside.mkdir()
  (self.work/'linked').symlink_to(outside,target_is_directory=True)
  self.assertFalse(B.recovery_missing_writable(self.work,self.state,'linked/test_new.py'))

 def test_parent_that_is_a_file_is_rejected(self):
  (self.work/'blocker').write_text('x')
  self.assertFalse(B.recovery_missing_writable(self.work,self.state,'blocker/test_new.py'))

 def test_path_outside_boundary_is_rejected(self):
  self.assertFalse(B.recovery_missing_writable(self.root/'elsewhere',self.state,'tests/x.py'))

 def test_missing_entry_reaches_model_input_as_explicit_new_file(self):
  source=B.EC.source_context(self.work,self.task,[],{},max_bytes=16000)
  self.assertNotIn('tests/test_unit_workspace.py',source)
  source['tests/test_unit_workspace.py']=B.recovery_missing_entry()
  complete=B.recovery_current_source(self.task,self.prior,source)
  entry=complete['tests/test_unit_workspace.py']
  self.assertTrue(entry['missing']);self.assertEqual(entry['content'],'');self.assertFalse(entry['truncated'])
  capsule=B.recovery_model_context(self.task,None,{'kind':'semantic','fingerprint':'f'*64},self.prior,
      {'source':source},None,[],{})
  rendered=json.loads(B.recovery_model_json(capsule))
  self.assertIn('requires creating it',json.dumps(rendered['current_source']['tests/test_unit_workspace.py']))
  self.assertIn('pkg/workspace.py',rendered['current_source'])


if __name__=='__main__':
 unittest.main()
