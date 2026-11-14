"""Additional native sandbox checks, run explicitly alongside the frozen suite."""
import contextlib,io,json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest import mock
import project_backend as B
import structured_worker as S


class WorkerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.directory=Path(self.tmp.name).resolve();self.work=self.directory/'work';self.work.mkdir()
  (self.work/'value.py').write_text('value=0\n');(self.work/'frozen.py').write_text('frozen=True\n')
  self.manifest={'version':1,'workspace':str(self.work),'task_id':'fixture','allowed_files':['value.py'],
      'test_command':[sys.executable,'-B','-c','import value; assert value.value == 1'],'timeout_seconds':30}
  self.path=self.directory/'worker-manifest.json';self.path.write_text(json.dumps(self.manifest))
  self.model={'model':'fixture','context':32768};self.manifest['_sha256']=S.digest(self.path)
  self.control=self.directory/'worker-tools';self.control.mkdir()
  self.worker=S.Worker(self.work,self.control,self.manifest,self.model)

 def action(self,action,path='',content=''):
  with contextlib.redirect_stdout(io.StringIO()):return self.worker.action({'action':action,'path':path,'content':content,'summary':'Factual summary'},time.monotonic()+10)

 def test_no_shell_commands_scope_escape_metadata_or_symlink_writes(self):
  (self.work/'link.py').symlink_to(self.work/'frozen.py')
  for path in ('../escape.py','/tmp/escape.py','.git/config','.env','node_modules/a.py','link.py'):
   with self.subTest(path=path),self.assertRaises(ValueError):S.safe_path(self.work,path)
  for path in ('frozen.py','PRD.md','.midnight-ready.json','extra.py'):
   with self.assertRaises(ValueError):self.action('write_file',path,'changed')
  with self.assertRaises(ValueError):self.action('exec_command',content='arbitrary shell')
  with self.assertRaises(ValueError):self.action('run_tests',content='arbitrary shell')
  self.assertEqual((self.work/'frozen.py').read_text(),'frozen=True\n')

 def test_noop_failed_or_stale_test_cannot_finish(self):
  self.assertIn('error',self.action('finish'))
  self.action('write_file','value.py','value=2\n');self.assertNotEqual(self.action('run_tests')['code'],0)
  self.assertIn('error',self.action('finish'))
  self.action('write_file','value.py','value=1\n');self.assertEqual(self.action('run_tests')['code'],0)
  self.action('write_file','value.py','value=3\n');self.assertIn('error',self.action('finish'))
  self.assertFalse((self.work/'.midnight-ready.json').exists())

 def test_real_test_then_edit_handoff_is_exact_and_tampering_is_rejected(self):
  self.action('write_file','value.py','value=1\n');self.assertEqual(self.action('run_tests')['code'],0)
  with mock.patch.object(S.M,'local_descriptor',return_value=self.model):self.assertTrue(self.action('finish')['finished'])
  self.assertTrue(S.submission_complete(self.directory,self.work,'fixture',self.model))
  self.assertFalse(S.submission_complete(self.directory,self.work,'another-task',self.model))
  self.assertFalse(S.submission_complete(self.directory,self.work,'fixture',dict(self.model,context=8192)))
  (self.work/'value.py').write_text('value=2\n');self.assertFalse(S.submission_complete(self.directory,self.work,'fixture',self.model))
  (self.work/'value.py').write_text('value=1\n');self.assertTrue(S.submission_complete(self.directory,self.work,'fixture',self.model))
  self.path.write_text('{}');self.assertFalse(S.submission_complete(self.directory,self.work,'fixture',self.model))

 def test_exit_zero_and_forged_marker_without_host_receipt_are_unfinished(self):
  (self.work/'.midnight-ready.json').write_text(json.dumps({'task_id':'fixture','ready_for_review':True}))
  self.assertFalse(S.submission_complete(self.directory,self.work,'fixture',self.model))

 def test_sandbox_denies_network_and_candidate_external_and_private_writes(self):
  secret=Path.home()/('midnight-worker-denial-'+str(time.time_ns()))
  secret.write_text('private');self.addCleanup(secret.unlink)
  probe='''import socket,pathlib
blocked=0
for call in (lambda: socket.create_connection(('127.0.0.1',8766)),
             lambda: pathlib.Path('value.py').write_text('bad'),
             lambda: pathlib.Path(%r).read_text(),
             lambda: pathlib.Path(%r).write_text('bad')):
    try: call()
    except OSError: blocked+=1
assert blocked==4, blocked
'''%(str(secret),str(self.directory/'outside.txt'))
  manifest=dict(self.manifest,test_command=[sys.executable,'-B','-c',probe])
  result=S.run_tests(self.work,self.control/'denials',manifest,time.monotonic()+10)
  self.assertEqual(result['code'],0,result);self.assertEqual((self.work/'value.py').read_text(),'value=0\n')
  self.assertFalse((self.directory/'outside.txt').exists())

 def test_untrusted_test_output_and_lifetime_are_bounded(self):
  manifest=dict(self.manifest,test_command=[sys.executable,'-B','-c',"print('x'*100000)"])
  result=S.run_tests(self.work,self.control/'output',manifest,time.monotonic()+10)
  self.assertEqual(result['reason'],'test_output_bound');self.assertNotEqual(result['code'],0)
  self.assertLessEqual((self.control/'output/check.log').stat().st_size,65536)
  manifest['test_command']=[sys.executable,'-B','-c','import time; time.sleep(60)']
  result=S.run_tests(self.work,self.control/'timeout',manifest,time.monotonic()+.3)
  self.assertEqual(result['reason'],'test_timeout');self.assertNotEqual(result['code'],0)

 def test_detached_subprocess_and_fork_are_denied_before_any_child_can_escape(self):
  probe='''import os,subprocess,sys
blocked=0
for call in (os.fork, lambda: subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)):
    try: call()
    except PermissionError: blocked+=1
assert blocked==2, blocked
'''
  manifest=dict(self.manifest,test_command=[sys.executable,'-B','-c',probe])
  result=S.run_tests(self.work,self.control/'no-detach',manifest,time.monotonic()+5)
  self.assertEqual(result['code'],0,result)
  proof=json.loads((self.control/'no-detach/execution.json').read_text());self.assertTrue(proof['drained']);self.assertEqual(len(proof['members']),1)

 def test_manifest_requires_host_location_exact_files_and_fixed_command(self):
  self.assertEqual(S.load_manifest(self.path,self.work)['allowed_files'],['value.py'])
  for changes in ({'allowed_files':['*.py']},{'allowed_files':['../outside']},{'test_command':'sh -c anything'},
                  {'timeout_seconds':True},{'workspace':str(self.directory)},{'allowed_files':['PRD.md']}):
   self.path.write_text(json.dumps(dict(self.manifest,**changes)))
   with self.assertRaises(ValueError):S.load_manifest(self.path,self.work)

 def test_product_python_worker_check_parses_without_importing_candidate(self):
  (self.work/'value.py').write_text("raise RuntimeError('candidate must not execute inside implementation worker')\n")
  manifest=dict(self.manifest,test_command=S.syntax_command(['value.py'],'/opt/homebrew/bin:/usr/bin:/bin'))
  result=S.run_tests(self.work,self.control/'syntax',manifest,time.monotonic()+5)
  self.assertEqual(result['code'],0,result);self.assertIn('semantic host checks remain required',result['output'])
  (self.work/'value.py').write_text('def invalid syntax:\n')
  result=S.run_tests(self.work,self.control/'bad-syntax',manifest,time.monotonic()+5)
  self.assertNotEqual(result['code'],0)

 def test_actual_isolated_worker_entry_cannot_import_candidate_json(self):
  (self.work/'json.py').write_text("raise RuntimeError('CANDIDATE_IMPORT_EXECUTED')\n")
  env=dict(os.environ,PYTHONPATH=str(self.work));env.pop('MIDNIGHT_WORKER_DESCRIPTOR',None)
  result=subprocess.run([str(Path(S.__file__).with_name('local_worker.py'))],cwd=self.work,env=env,capture_output=True,text=True,timeout=10)
  self.assertNotEqual(result.returncode,0);self.assertNotIn('CANDIDATE_IMPORT_EXECUTED',result.stderr)
  self.assertIn('MIDNIGHT_WORKER_DESCRIPTOR',result.stderr)

 def test_unchanged_worker_cannot_borrow_first_host_only_verification_exception(self):
  backend=B.load_backend(self.work,'fixture',settings={});patch='unchanged retained patch'
  backend.write(self.directory/'retry-seed.json',{'run':'saved','patch_sha256':S.digest(self.work/'value.py')})
  backend.write(self.directory/'retry-seed.json',{'run':'saved','patch_sha256':__import__('hashlib').sha256(patch.encode()).hexdigest()})
  backend.write(self.directory/'retry-verification.json',{'first_verification':True})
  with mock.patch.object(backend,'git',return_value=''):
   self.assertFalse(backend.retry_progress(self.directory,patch,['value.py'],self.work))
   backend.write(self.directory/'worker-process.json',{'code':0,'reason':'exited'})
   self.assertTrue(backend.retry_progress(self.directory,patch,['value.py'],self.work))


if __name__=='__main__':unittest.main()
