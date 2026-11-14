"""Production worker entry with an instrumented local provider, never live inference."""
import ast, contextlib, io, json, os, tempfile, time, unittest
from pathlib import Path
from unittest import mock
import structured_worker as S


def is_edit_request(body):
 return isinstance(body.get('format'),dict) and body['format'].get('required')==S.EDIT_SCHEMA['required']


class Provider:
 def __init__(self, *, capable=True, metadata=None, new_file=False, truncate_edit=False):
  self.capable=capable;self.metadata=metadata;self.new_file=new_file;self.truncate_edit=truncate_edit
  self.requests=[];self.timeouts=[];self.actions=iter(['read_file']*4+['edit_file','run_tests','finish'])

 def open(self, request, *, timeout):
  body=json.loads(request.data);self.requests.append((request.full_url,body));self.timeouts.append(timeout)
  if request.full_url.endswith('/api/show'):
   value=self.metadata if self.metadata is not None else {'capabilities':['completion','thinking'] if self.capable else ['completion']}
   if isinstance(value,Exception):raise value
   return io.BytesIO(json.dumps(value).encode())
  if self.capable and body.get('think') is not False:
   event={'model':'fixture','done':True,'done_reason':'length','prompt_eval_count':16000,'eval_count':body['options']['num_predict'],
          'message':{'role':'assistant','content':'','thinking':'Reasoning used the output budget'}}
  else:
   if is_edit_request(body):
    value={'old_text':'' if self.new_file else 'return a - b','new_text':'def add(a, b):\n    return a + b\n' if self.new_file else 'return a + b'}
   else:
    action=next(self.actions)
    value={'action':action,'path':'add.py' if action in ('read_file','edit_file') else '', 'summary':'Implement addition and verify the candidate'}
   event={'model':'fixture','done':True,'done_reason':'length' if self.truncate_edit and is_edit_request(body) else 'stop',
          'prompt_eval_count':16000,'eval_count':80,'message':{'role':'assistant','content':json.dumps(value)}}
  return io.BytesIO((json.dumps(event)+'\n').encode())


class WorkerPolicyTests(unittest.TestCase):
 def exercise(self, provider):
  with tempfile.TemporaryDirectory() as name:
   root=Path(name).resolve();work=root/'work';work.mkdir();target=work/'add.py'
   original='# Preserve this context.\n'*200+'def add(a, b):\n    return a - b\n'
   if not provider.new_file:target.write_text(original)
   (work/'PRD.md').write_text('Implement addition for positive and negative operands. Preserve every other byte.\n'+('Host contract context.\n'*2100))
   manifest={'version':1,'workspace':str(work),'task_id':'fixture','allowed_files':['add.py'],
             'timeout_seconds':20,'test_command':['/opt/homebrew/bin/python3','-I','-B','-c','fixed syntax check']}
   path=root/'worker-manifest.json';path.write_text(json.dumps(manifest));model={'model':'fixture','context':32768}
   def syntax(work,directory,manifest,deadline):
    ast.parse(target.read_text());directory.mkdir();log=directory/'check.log';log.write_text('Syntax passed\n')
    return {'code':0,'reason':'exited','command':manifest['test_command'],'output':log.read_text(),'log_sha256':S.digest(log)}
   before=Path.cwd();output=io.StringIO();error=None;result=None
   try:
    os.chdir(work)
    with mock.patch.object(S.M,'local_descriptor',return_value=model),mock.patch.object(S.B,'GPU_LOCK',root/'gpu.lock'),mock.patch.object(S,'run_tests',side_effect=syntax),mock.patch.object(S.urllib.request,'build_opener',return_value=provider),mock.patch.dict(os.environ,{'MIDNIGHT_WORKER_DESCRIPTOR':json.dumps(model)}),mock.patch('sys.argv',['worker','--manifest',str(path)]),contextlib.redirect_stdout(output):
     try:result=S.main()
     except Exception as exc:error=exc
    complete=S.submission_complete(root,work,'fixture',model)
    receipts={p.name:json.loads(p.read_text()) for p in (root/'worker-tools').glob('*.json')}
    current=target.read_text() if target.exists() else None
    return result,error,current,original,complete,receipts,output.getvalue()
   finally:os.chdir(before)

 def test_production_entry_completes_four_reads_edit_check_and_verified_finish(self):
  provider=Provider();result,error,current,original,complete,receipts,output=self.exercise(provider)
  self.assertIsNone(error,str(error));self.assertEqual(result,0,output);self.assertTrue(complete)
  self.assertEqual(current,original.replace('return a - b','return a + b'))
  self.assertEqual(sum('content' in v and v.get('path')=='add.py' for k,v in receipts.items() if k.startswith('tool-result-')),4)
  self.assertEqual(sum(url.endswith('/api/show') for url,_ in provider.requests),1)
  chats=[body for url,body in provider.requests if url.endswith('/api/chat')]
  self.assertEqual([body['options']['num_predict'] for body in chats],[1024]*5+[4096,1024,1024])
  self.assertTrue(all(body['think'] is False for body in chats))
  self.assertTrue(all(len(json.dumps(body['messages']).encode())<=90000 for body in chats))

 def test_cold_model_timeout_before_any_output_is_asked_again(self):
  class ColdStart(Provider):
   cold=2
   def open(self, request, *, timeout):
    if request.full_url.endswith('/api/chat') and self.cold:
     self.cold-=1;raise TimeoutError('timed out')
    return super().open(request,timeout=timeout)
  with mock.patch.object(S,'SLOW_START_RESERVE',0):result,error,current,original,complete,receipts,output=self.exercise(ColdStart())
  self.assertIsNone(error);self.assertEqual(result,0);self.assertTrue(complete)
  self.assertEqual(output.count('"model.slow_start"'),2)

 def test_model_that_never_answers_still_fails_the_attempt(self):
  class Dead(Provider):
   asked=0
   def open(self, request, *, timeout):
    if request.full_url.endswith('/api/chat'):
     self.asked+=1;raise TimeoutError('timed out')
    return super().open(request,timeout=timeout)
  provider=Dead()
  with mock.patch.object(S,'SLOW_START_RESERVE',0):result,error,current,original,complete,receipts,output=self.exercise(provider)
  self.assertFalse(complete);self.assertEqual(current,original)
  self.assertEqual(provider.asked,S.SLOW_START_RETRIES+1)

 def test_absent_file_uses_same_policy_and_verified_finish(self):
  provider=Provider(new_file=True);result,error,current,_,complete,_,_=self.exercise(provider)
  self.assertIsNone(error,str(error));self.assertEqual(result,0);self.assertTrue(complete)
  self.assertEqual(current,'def add(a, b):\n    return a + b\n')

 def test_non_thinking_model_never_receives_unsupported_option(self):
  provider=Provider(capable=False);result,error,_,_,complete,_,_=self.exercise(provider)
  self.assertIsNone(error,str(error));self.assertEqual(result,0);self.assertTrue(complete)
  self.assertTrue(all('think' not in body for _,body in provider.requests))
  self.assertEqual(sum(url.endswith('/api/show') for url,_ in provider.requests),1)

 def test_missing_malformed_or_unavailable_metadata_holds_before_generation(self):
  for metadata in ({},{'capabilities':'thinking'},{'capabilities':[None]},OSError('metadata unavailable')):
   with self.subTest(metadata=metadata):
    provider=Provider(metadata=metadata);_,error,current,original,complete,receipts,_=self.exercise(provider)
    self.assertIsNotNone(error);self.assertFalse(complete);self.assertEqual(current,original)
    self.assertFalse(any(url.endswith('/api/chat') for url,_ in provider.requests))
    self.assertFalse(receipts['decision-0.json']['partial_text_applied'])

 def test_truncated_edit_is_terminal_even_if_json_is_parseable(self):
  provider=Provider(truncate_edit=True);_,error,current,original,complete,receipts,_=self.exercise(provider)
  self.assertIsInstance(error,S.IncompleteInference);self.assertEqual(current,original);self.assertFalse(complete)
  self.assertEqual(receipts['edit-4.json']['completion_reason'],'length')
  self.assertEqual(sum(is_edit_request(body) for _,body in provider.requests),1)
  self.assertNotIn('decision-5.json',receipts)

 def test_expired_deadline_performs_no_network_request(self):
  provider=Provider()
  with mock.patch.object(S.urllib.request,'build_opener',return_value=provider),self.assertRaises(TimeoutError):
   S.infer({'model':'fixture','context':32768},[],time.monotonic()-1,schema=S.ACTION_SCHEMA,predict=1024)
  self.assertEqual(provider.requests,[])

 def test_metadata_consumes_only_remaining_deadline_and_cannot_start_late_generation(self):
  model={'model':'fixture','context':32768};provider=Provider();clock=[100.0];original=provider.open
  def open(request,*,timeout):
   result=original(request,timeout=timeout);clock[0]=100.25;return result
  with mock.patch.object(S.time,'monotonic',side_effect=lambda:clock[0]),mock.patch.object(S.urllib.request,'build_opener',return_value=provider),mock.patch.object(provider,'open',side_effect=open),S.inference_session(model),self.assertRaises(TimeoutError):
   S.infer(model,[],100.2,schema=S.ACTION_SCHEMA,predict=1024)
  self.assertEqual(len(provider.requests),1);self.assertTrue(provider.requests[0][0].endswith('/api/show'))
  self.assertGreater(provider.timeouts[0],0);self.assertLessEqual(provider.timeouts[0],.201)

 def test_policy_cache_is_exact_session_and_model_bound(self):
  model={'model':'fixture','context':32768};provider=Provider()
  with mock.patch.object(S.urllib.request,'build_opener',return_value=provider):
   with S.inference_session(model):
    S.infer(model,[],time.monotonic()+1,schema=S.ACTION_SCHEMA,predict=1024)
    with self.assertRaises(S.InferencePolicyError):
     S.infer(dict(model,digest='changed'),[],time.monotonic()+1,schema=S.ACTION_SCHEMA,predict=1024)
   with S.inference_session(model):S.infer(model,[],time.monotonic()+1,schema=S.ACTION_SCHEMA,predict=1024)
  self.assertEqual(sum(url.endswith('/api/show') for url,_ in provider.requests),2)
  self.assertEqual(sum(url.endswith('/api/chat') for url,_ in provider.requests),2)

 def test_parameter_drift_and_oversized_metadata_hold_without_generation(self):
  model={'model':'fixture','context':32768,'parameters_sha256':'0'*64}
  for metadata in ({'capabilities':['thinking'],'parameters':'changed'}, {'capabilities':['thinking'],'padding':'x'*(1024*1024)}):
   with self.subTest(oversized='padding' in metadata):
    provider=Provider(metadata=metadata)
    with mock.patch.object(S.urllib.request,'build_opener',return_value=provider),S.inference_session(model),self.assertRaises(S.InferencePolicyError):
     S.infer(model,[],time.monotonic()+1,schema=S.ACTION_SCHEMA,predict=1024)
    self.assertEqual(len(provider.requests),1)

 def test_native_provider_keeps_its_own_session_policy(self):
  model={'model':'native-fixture','context':32768};response=({'action':'read_file','path':'add.py','summary':'Read'}, {})
  with mock.patch.object(S.GP,'matches',return_value=True),mock.patch.object(S.GP,'infer',return_value=response) as native,mock.patch.object(S.urllib.request,'build_opener') as opener,S.inference_session(model):
   self.assertEqual(S.infer(model,[],time.monotonic()+1,schema=S.ACTION_SCHEMA,predict=1024),response)
  opener.assert_not_called();self.assertEqual(native.call_args.kwargs['predict'],1024)


if __name__=='__main__':unittest.main()
