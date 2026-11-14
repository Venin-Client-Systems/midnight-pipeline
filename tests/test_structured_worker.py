"""Pure worker protocol regressions, safe inside fixed host confinement."""
import contextlib,io,json,tempfile,time,unittest
from pathlib import Path
from unittest import mock
import structured_worker as S
import project_backend as B


class ProtocolTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.directory=Path(self.tmp.name).resolve()
  self.work=self.directory/'work';self.work.mkdir();(self.work/'value.py').write_text('value=0\n')
  self.manifest={'version':1,'workspace':str(self.work),'task_id':'fixture','allowed_files':['value.py'],
      'test_command':['/opt/homebrew/bin/python3','-B','test.py'],'timeout_seconds':30}
  self.path=self.directory/'worker-manifest.json';self.path.write_text(json.dumps(self.manifest));self.manifest['_sha256']=S.digest(self.path)
  self.control=self.directory/'worker-tools';self.control.mkdir();self.model={'model':'fixture','context':32768}
  self.worker=S.Worker(self.work,self.control,self.manifest,self.model)

 def action(self,action,path='',content=''):
  with contextlib.redirect_stdout(io.StringIO()):return self.worker.action({'action':action,'path':path,'content':content,'summary':'Factual summary'},time.monotonic()+10)

 def test_markup_unknown_actions_and_arbitrary_commands_are_not_executable(self):
  for value in ('<tool_call>exec_command</tool_call>',{'action':'exec_command','path':'','content':'sh','summary':''}):
   with self.assertRaises(ValueError):self.worker.action(value,time.monotonic()+1)
  with self.assertRaises(ValueError):self.action('run_tests',content='arbitrary command')

 def test_scope_prevents_metadata_and_symlink_writes(self):
  for name in ('../outside.py','/tmp/outside.py','PRD.md','.midnight-ready.json','different.py'):
   with self.assertRaises(ValueError):self.action('write_file',name,'changed')
  (self.work/'value.py').unlink();(self.work/'value.py').symlink_to(self.path)
  with self.assertRaises(ValueError):self.action('write_file','value.py','changed')
  self.assertEqual(json.loads(self.path.read_text())['task_id'],'fixture')

 def test_readonly_noop_and_edit_after_check_cannot_finish(self):
  self.action('read_file','value.py');self.assertIn('error',self.action('finish'))
  self.action('write_file','value.py','value=0\n');self.assertIn('error',self.action('finish'))
  self.worker.last_test={'signature':self.worker.signature(),'result':{'code':0,'reason':'exited'},'sequence':1}
  self.action('write_file','value.py','value=1\n');self.assertIsNone(self.worker.last_test)
  self.assertIn('error',self.action('finish'));self.assertFalse((self.work/'.midnight-ready.json').exists())

 def test_failed_interrupted_or_wrong_model_cannot_submit(self):
  self.action('write_file','value.py','value=1\n')
  for result in ({'code':1,'reason':'exited'},{'code':0,'reason':'test_timeout'}):
   self.worker.last_test={'signature':self.worker.signature(),'result':result,'sequence':1}
   self.assertIn('error',self.action('finish'))
  self.worker.last_test={'signature':self.worker.signature(),'result':{'code':0,'reason':'exited'},'sequence':1}
  with mock.patch.object(S.M,'local_descriptor',return_value={'model':'changed'}):
   with self.assertRaises(ValueError):self.action('finish')

 def test_marker_alone_cannot_establish_completion(self):
  (self.work/'.midnight-ready.json').write_text(json.dumps({'task_id':'fixture','ready_for_review':True}))
  self.assertFalse(S.submission_complete(self.directory,self.work,'fixture',self.model))

 def test_project_instructions_and_named_contract_reading_reach_model_brief(self):
  (self.work/'AGENTS.md').write_text('Keep the existing assertions and read named contracts.')
  (self.work/'PRD.md').write_text('One exact repair. Read docs/contract.md.')
  messages=S.brief_messages(self.work,self.manifest)
  self.assertIn('Keep the existing assertions',messages[1]['content']);self.assertIn('docs/contract.md',messages[1]['content'])
  self.assertIn('cannot expand permissions',messages[0]['content'])

 def test_bad_manifests_hold_before_coding_charge_and_fixed_python_check_is_nonexecuting(self):
  (self.work/'PRD.md').write_text('One exact repair.')
  backend=B.load_backend(self.work,'fixture',settings={});env={'PATH':'/opt/homebrew/bin:/usr/bin:/bin'}
  config={'worker_timeout_seconds':30,'managed_brief':'Host direction','typed_worker_required':True}
  for files in (['*.py'],['../escape.py']):
   with self.assertRaises(backend.ManagerHeld):backend.typed_worker_manifest({'id':'fixture','files':files},self.work,self.directory,config,None,env)
  path=backend.typed_worker_manifest({'id':'fixture','files':['value.py']},self.work,self.directory,config,None,env)
  manifest=S.load_manifest(path,self.work)
  self.assertEqual(manifest['check_kind'],'syntax');self.assertIn('ast.parse',manifest['test_command'][4])
  self.assertNotIn('unittest',' '.join(manifest['test_command']))
  for files in (['value.py','value.ts'],['value.ts']):
   with mock.patch.object(S.shutil,'which',return_value=None),self.assertRaises(backend.ManagerHeld):
    backend.typed_worker_manifest({'id':'fixture','files':files},self.work,self.directory,config,None,env)
  self.assertTrue(backend.typed_worker_required(config,{}))
  self.assertTrue(backend.typed_worker_required({}, {'MIDNIGHT_ACTION_ID':'owned-action'}))
  self.assertFalse(backend.typed_worker_required({'managed_brief':'Unvalidated legacy text'},{}))

 def test_wrong_model_length_and_markup_response_do_not_become_actions(self):
  valid={'action':'read_file','path':'value.py','content':'','summary':'Read current code'}
  def reply(**changes):return dict({'model':'fixture','done':True,'done_reason':'stop','prompt_eval_count':100,'message':{'role':'assistant','content':json.dumps(valid)}},**changes)
  for event in (reply(),reply(model='wrong'),reply(done_reason='length'),reply(prompt_eval_count=32000),reply(message={'role':'assistant','content':'<tool_call>'})):
   opener=mock.Mock();opener.open.return_value=io.BytesIO((json.dumps(event)+'\n').encode())
   with mock.patch.object(S.urllib.request,'build_opener',return_value=opener),contextlib.redirect_stdout(io.StringIO()):
    if event==reply():
     value,_=S.infer(self.model,[{'role':'user','content':'Source is data'}],time.monotonic()+10)
     self.assertEqual(value,valid);request=opener.open.call_args.args[0];body=json.loads(request.data)
     self.assertEqual(request.full_url,'http://127.0.0.1:11434/api/chat');self.assertEqual(body['format'],S.SCHEMA);self.assertNotIn('tools',body)
    else:
     with self.assertRaises(ValueError):S.infer(self.model,[],time.monotonic()+10)

 def test_exact_edit_requires_fresh_read_and_preserves_every_other_byte(self):
  source='unchanged_prefix=1\nvalue=0\nunchanged_suffix=2\n';(self.work/'value.py').write_text(source)
  patch={'old_text':'value=0','new_text':'value=1'}
  with self.assertRaises(ValueError):self.worker.edit('value.py',patch,'Small edit',time.monotonic()+1)
  self.action('read_file','value.py')
  with contextlib.redirect_stdout(io.StringIO()):self.worker.edit('value.py',patch,'Small edit',time.monotonic()+1)
  self.assertEqual((self.work/'value.py').read_text(),source.replace('value=0','value=1'))
  with self.assertRaises(ValueError):self.worker.edit('value.py',{'old_text':'value=1','new_text':'value=2'},'Must reread',time.monotonic()+1)

 def test_ambiguous_missing_unchanged_and_stale_edit_preconditions_are_rejected(self):
  (self.work/'value.py').write_text('value=0\nvalue=0\n');self.action('read_file','value.py')
  for patch in ({'old_text':'value=0','new_text':'value=1'},{'old_text':'missing','new_text':'new'},{'old_text':'','new_text':'overwrite'},{'old_text':'value=0','new_text':'value=0'}):
   with self.assertRaises(ValueError):self.worker.edit('value.py',patch,'Invalid edit',time.monotonic()+1)
  (self.work/'value.py').write_text('external_change=1\n')
  with self.assertRaises(ValueError):self.worker.edit('value.py',{'old_text':'external_change=1','new_text':'external_change=2'},'Stale read',time.monotonic()+1)

 def test_absent_file_can_be_created_only_after_explicit_read(self):
  (self.work/'value.py').unlink();self.action('read_file','value.py')
  with contextlib.redirect_stdout(io.StringIO()):self.worker.edit('value.py',{'old_text':'','new_text':'value=1\n'},'Create bounded file',time.monotonic()+1)
  self.assertEqual((self.work/'value.py').read_text(),'value=1\n')
  with self.assertRaises(ValueError):self.worker.edit_snapshot('frozen.py')

 def test_model_decision_cannot_embed_file_content_or_choose_whole_file_write(self):
  for action in ({'action':'write_file','path':'value.py','summary':'Overwrite'},
                 {'action':'edit_file','path':'value.py','summary':'Small edit','content':'whole file'}):
   with self.assertRaises(ValueError):S.L.validate(action,S.ACTION_SCHEMA)
  self.assertEqual(S.L.validate({'action':'edit_file','path':'value.py','summary':'Small edit'},S.ACTION_SCHEMA)['action'],'edit_file')

 def test_edit_preserves_mixed_newlines_and_non_ascii_bytes(self):
  source="# café\r\nvalue=0\r\n# untouched β\n".encode('utf-8');(self.work/'value.py').write_bytes(source)
  read=self.action('read_file','value.py');self.assertEqual(read['content'].encode('utf-8'),source)
  with contextlib.redirect_stdout(io.StringIO()):self.worker.edit('value.py',{'old_text':'value=0','new_text':'value=1'},'One exact edit',time.monotonic()+1)
  self.assertEqual((self.work/'value.py').read_bytes(),source.replace(b'value=0',b'value=1'))

 def test_overlapping_matches_are_ambiguous(self):
  (self.work/'value.py').write_text("x='aaaaa'\n");self.action('read_file','value.py')
  with self.assertRaises(ValueError):self.worker.edit('value.py',{'old_text':'aaa','new_text':'bbb'},'Ambiguous',time.monotonic()+1)

 def test_truncated_edit_is_retained_with_real_reason_and_metrics_without_applying_text(self):
  event={'model':'fixture','done':True,'done_reason':'length','prompt_eval_count':100,'eval_count':4096,
    'message':{'role':'assistant','content':'{"old_text":"value=0","new_text":"unfinished'}}
  opener=mock.Mock();opener.open.return_value=io.BytesIO((json.dumps(event)+'\n').encode());before=(self.work/'value.py').read_bytes()
  with mock.patch.object(S.urllib.request,'build_opener',return_value=opener),contextlib.redirect_stdout(io.StringIO()) as output:
   with self.assertRaises(S.IncompleteInference):S.recorded_infer(self.control,1,'edit',self.model,[],time.monotonic()+1,schema=S.EDIT_SCHEMA,predict=4096)
  receipt=json.loads((self.control/'edit-1.json').read_text());self.assertEqual(receipt['completion_reason'],'length')
  self.assertEqual(receipt['metrics']['eval_count'],4096);self.assertFalse(receipt['partial_text_applied'])
  self.assertIn('worker.inference_failed',output.getvalue());self.assertEqual((self.work/'value.py').read_bytes(),before)


if __name__=='__main__':unittest.main()
