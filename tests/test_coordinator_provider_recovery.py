"""Completed provider receipts recover locally; uncertain calls never relaunch."""
from contextlib import contextmanager,nullcontext
import hashlib,json,subprocess,tempfile,unittest
from pathlib import Path
from unittest import mock
import coordinator as K
import progress_recovery as R
import test_coordinator as fixtures

REAL_ASK=K.ask


class ProviderRecoveryTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.runtime=Path(temp.name).resolve()
  self.directory=self.runtime/'coordinator/runs'/('d'*32);self.directory.mkdir(parents=True)
  self.answer={'action':'owner','rationale':'A real scope decision is needed.','instruction':'','owner_question':'Choose the original scope.'}
  self.prompt='Bounded original evidence';self.descriptor={'backend':'codex','model':'fixture-cloud','reasoning_effort':'max','profile_hash':'a'*64}
  self.identity='b'*32;self.provider=self.runtime/'cloud-invocations'/self.identity;self.provider.mkdir(parents=True)
  self.request={'invocation_id':self.identity,'purpose':'coordination','cwd':str(self.directory),'output_path':str(self.directory/'result.json'),'requested':self.descriptor}
  self.write(self.provider/'requested.json',self.request)
  (self.directory/'input.txt').write_text(self.prompt);self.write(self.directory/'schema.json',K.SCHEMA)
  events=self.directory/'events-0.jsonl';events.write_text('{"type":"turn.completed"}\n')
  self.write(self.directory/'result.json',self.answer)
  self.call={'version':1,'invocation_id':self.identity,'provider':'codex','descriptor':self.descriptor,
     'input_sha256':hashlib.sha256(self.prompt.encode()).hexdigest(),'sources':K.source_binding(),
     'refs':[R.ref(self.provider/'requested.json'),R.ref(self.directory/'input.txt'),R.ref(self.directory/'schema.json')]}
  self.write(self.directory/'provider-call-0.json',self.call)
  self.write(self.directory/'provider-completion-0.json',{'call':R.ref(self.directory/'provider-call-0.json'),'result':{'code':0,'reason':'exited'},'events':R.ref(events),'output':R.ref(self.directory/'result.json')})
  self.receipt=dict(self.request,returncode=0,completed='2026-11-13T00:00:00+00:00',failure=None,profile_mismatch=False)
  self.write(self.provider/'result.json',self.receipt)
  self.journal={'input_sha256':self.call['input_sha256'],'binding':{'sources':self.call['sources']}}
  self.manager=K.CP.CloudPolicy(self.runtime)
  self.seed_invocation(self.identity,self.descriptor)

 def write(self,path,value):path.write_text(json.dumps(value)+'\n')

 def seed_invocation(self,identity,descriptor):
  with self.manager._database() as con:
   con.execute('INSERT INTO cp_invocations VALUES(?,?,0,?,?,0,NULL)',(identity,None,'coordination',json.dumps(descriptor)))

 def database_result(self,identity):
  with self.manager._database() as con:return con.execute('SELECT result FROM cp_invocations WHERE id=?',(identity,)).fetchone()[0]

 def test_completed_result_before_answer_journal_recovers_without_provider(self):
  with mock.patch.object(K.CP,'CloudPolicy') as provider:
   result=K.recovered_answer(self.runtime,self.directory,self.journal)
  self.assertIsNotNone(result);self.assertEqual(result[0],self.answer)
  provider.return_value.run_with_fallback.assert_not_called()
  provider.return_value.record_result.assert_not_called()
  self.assertTrue((self.directory/'answer-receipt.json').exists())

 def test_saved_process_completion_can_finish_local_provider_recording_once(self):
  (self.provider/'result.json').unlink()
  def record(identity,events,code):
   self.assertEqual(identity,self.identity);self.assertEqual(code,0)
   self.write(self.provider/'result.json',self.receipt);return self.receipt
  with mock.patch.object(K.CP,'CloudPolicy') as provider:
   provider.return_value.restore_result_file.return_value=False
   provider.return_value.record_result.side_effect=record
   self.assertEqual(K.recovered_answer(self.runtime,self.directory,self.journal)[0],self.answer)
   self.assertEqual(K.recovered_answer(self.runtime,self.directory,self.journal)[0],self.answer)
   self.assertEqual(provider.return_value.record_result.call_count,1)
   provider.return_value.run_with_fallback.assert_not_called()

 def test_missing_process_completion_stays_uncertain(self):
  (self.directory/'provider-completion-0.json').unlink()
  with mock.patch.object(K.CP,'CloudPolicy') as provider:
   self.assertIsNone(K.recovered_answer(self.runtime,self.directory,self.journal))
   provider.assert_not_called()

 def test_changed_input_output_or_provider_binding_is_not_recovered(self):
  originals={p:p.read_bytes() for p in [self.directory/'input.txt',self.directory/'result.json',self.provider/'requested.json',self.provider/'result.json']}
  for path in originals:
   with self.subTest(path=path.name):
    path.write_text('{}')
    with self.assertRaises((ValueError,RuntimeError,KeyError)):K.recovered_answer(self.runtime,self.directory,self.journal)
    path.write_bytes(originals[path])

 def as_claude(self):
  self.descriptor['backend']='claude';self.call['provider']='claude'
  self.write(self.provider/'requested.json',self.request);self.write(self.provider/'result.json',self.receipt)
  self.call['refs']=[R.ref(self.provider/'requested.json'),R.ref(self.directory/'input.txt'),R.ref(self.directory/'schema.json')]
  self.write(self.directory/'provider-call-0.json',self.call)
  events=self.directory/'events-0.jsonl'
  events.write_text(json.dumps({'type':'system','subtype':'init','claude_code_version':'fixture','tools':['StructuredOutput'],'mcp_servers':[]})+'\n'+json.dumps({'type':'result','structured_output':self.answer})+'\n')
  self.write(self.directory/'provider-completion-0.json',{'call':R.ref(self.directory/'provider-call-0.json'),'result':{'code':0,'reason':'exited'},'events':R.ref(events),'output':None})
  with self.manager._database() as con:con.execute('UPDATE cp_invocations SET descriptor=? WHERE id=?',(json.dumps(self.descriptor),self.identity))

 def test_claude_structured_output_recovers_from_exact_retained_events(self):
  self.as_claude();self.assertEqual(K.recovered_answer(self.runtime,self.directory,self.journal)[0],self.answer)

 def test_claude_output_changed_before_answer_seal_is_refused(self):
  self.as_claude();self.write(self.directory/'result.json',dict(self.answer,owner_question='Unrelated substituted scope'))
  with self.assertRaises((ValueError,RuntimeError)):K.recovered_answer(self.runtime,self.directory,self.journal)

 def test_file_completion_before_database_commit_is_reconciled_without_rewriting_receipt(self):
  (self.provider/'result.json').unlink()
  with mock.patch.object(self.manager,'_database',side_effect=RuntimeError('after file before database')):
   with self.assertRaises(RuntimeError):self.manager.record_result(self.identity,'',0)
  original=(self.provider/'result.json').read_bytes();self.assertIsNone(self.database_result(self.identity))
  with mock.patch.object(K.CP,'CloudPolicy',return_value=self.manager),mock.patch.object(self.manager,'record_result',side_effect=AssertionError('do not replace completion')):
   self.assertEqual(K.recovered_answer(self.runtime,self.directory,self.journal)[0],self.answer)
   self.assertEqual(K.recovered_answer(self.runtime,self.directory,self.journal)[0],self.answer)
  self.assertIsNotNone(self.database_result(self.identity))
  self.assertEqual(json.loads(self.database_result(self.identity)),json.loads(original))
  self.assertEqual((self.provider/'result.json').read_bytes(),original)

 def test_conflicting_database_result_is_held_without_replacing_evidence(self):
  conflicting=dict(self.receipt,returncode=17)
  with self.manager._database() as con:con.execute('UPDATE cp_invocations SET result=? WHERE id=?',(json.dumps(conflicting),self.identity))
  before=(self.provider/'result.json').read_bytes()
  with self.assertRaises((ValueError,RuntimeError)):K.recovered_answer(self.runtime,self.directory,self.journal)
  self.assertEqual(json.loads(self.database_result(self.identity)),conflicting)
  self.assertEqual((self.provider/'result.json').read_bytes(),before)
  self.assertFalse((self.directory/'answer-receipt.json').exists())

 def test_missing_result_file_does_not_replace_conflicting_database_completion(self):
  conflicting=dict(self.receipt,returncode=17)
  with self.manager._database() as con:con.execute('UPDATE cp_invocations SET result=? WHERE id=?',(json.dumps(conflicting),self.identity))
  (self.provider/'result.json').unlink()
  with self.assertRaises((ValueError,RuntimeError)):K.recovered_answer(self.runtime,self.directory,self.journal)
  self.assertEqual(json.loads(self.database_result(self.identity)),conflicting)
  self.assertFalse((self.provider/'result.json').exists());self.assertFalse((self.directory/'answer-receipt.json').exists())

 def test_missing_result_file_restores_original_database_completion_timestamp(self):
  with self.manager._database() as con:con.execute('UPDATE cp_invocations SET result=? WHERE id=?',(json.dumps(self.receipt),self.identity))
  original=self.database_result(self.identity);(self.provider/'result.json').unlink()
  self.assertEqual(K.recovered_answer(self.runtime,self.directory,self.journal)[0],self.answer)
  self.assertEqual(self.database_result(self.identity),original)
  self.assertEqual(json.loads((self.provider/'result.json').read_text()),self.receipt)

 def test_dangling_result_symlink_is_held_without_replacement(self):
  path=self.provider/'result.json';path.unlink();target=self.runtime/'missing-target.json';path.symlink_to(target)
  with self.assertRaises((ValueError,RuntimeError)):K.recovered_answer(self.runtime,self.directory,self.journal)
  self.assertTrue(path.is_symlink());self.assertFalse(target.exists())
  self.assertIsNone(self.database_result(self.identity));self.assertFalse((self.directory/'answer-receipt.json').exists())

 def fallback_completion(self,mismatch):
  self.receipt['profile_mismatch']=mismatch;self.write(self.provider/'result.json',self.receipt)
  identity='c'*32;provider=self.runtime/'cloud-invocations'/identity;provider.mkdir()
  descriptor=dict(self.descriptor,backend='claude');self.seed_invocation(identity,descriptor)
  request=dict(self.request,invocation_id=identity,requested=descriptor);self.write(provider/'requested.json',request)
  self.write(provider/'result.json',dict(request,returncode=0,completed='2026-11-13T00:01:00+00:00',failure=None,profile_mismatch=False))
  call=dict(self.call,invocation_id=identity,provider='claude',descriptor=descriptor,
            refs=[R.ref(provider/'requested.json'),R.ref(self.directory/'input.txt'),R.ref(self.directory/'schema.json')])
  self.write(self.directory/'provider-call-1.json',call)
  events=self.directory/'events-1.jsonl'
  events.write_text(json.dumps({'type':'system','subtype':'init','claude_code_version':'fixture','tools':['StructuredOutput'],'mcp_servers':[]})+'\n'+json.dumps({'type':'result','structured_output':self.answer})+'\n')
  self.write(self.directory/'provider-completion-1.json',{'call':R.ref(self.directory/'provider-call-1.json'),'result':{'code':0,'reason':'exited'},'events':R.ref(events),'output':None})

 def test_completed_fallback_after_profile_mismatch_is_recovered_with_failure_provenance(self):
  self.fallback_completion(True)
  try:answer,outcome=K.recovered_answer(self.runtime,self.directory,self.journal)
  except RuntimeError as exc:self.fail('Valid completed fallback must recover: '+str(exc))
  self.assertEqual(answer,self.answer);self.assertEqual(outcome['provider'],'claude')
  self.assertEqual(outcome['attempts'][0]['failure']['kind'],'profile_mismatch')
  self.assertEqual(outcome['attempts'][1]['fallback_of'],self.identity)

 def test_completed_fallback_after_true_success_remains_refused(self):
  self.fallback_completion(False)
  with self.assertRaisesRegex(RuntimeError,'successful provider'):K.recovered_answer(self.runtime,self.directory,self.journal)


class ProductionAskTests(unittest.TestCase):
 setUp=fixtures.CoordinatorRunTests.setUp
 run_=fixtures.CoordinatorRunTests.run_

 def provider(self):
  (self.root/'projects.json').write_text(json.dumps(self.registry))
  manager=mock.Mock();identity='b'*32;descriptor={'backend':'codex','model':'fixture-cloud','profile_hash':'a'*64}
  def chain(directory,purpose,output,execute,**kwargs):
   folder=self.root/'runtime/cloud-invocations'/identity;folder.mkdir(parents=True,exist_ok=True)
   request={'invocation_id':identity,'purpose':purpose,'cwd':str(directory),'output_path':str(output),'requested':descriptor}
   path=folder/'requested.json';path.write_text(json.dumps(request))
   invocation={'invocation_id':identity,'descriptor':descriptor,'command':['fixture-provider',str(output)],'env':{},'receipt_path':str(path)}
   result,events=execute(invocation,0)
   receipt=dict(request,returncode=result['code'],completed='2026-11-13T00:00:00+00:00',failure=None,profile_mismatch=False)
   (folder/'result.json').write_text(json.dumps(receipt))
   return {'ok':True,'provider':'codex','attempts':[{'provider':'codex','invocation_id':identity}]}
  manager.run_with_fallback.side_effect=chain
  def command(args,**kwargs):
   from pathlib import Path
   path=Path(args[1]);journal=json.loads((path.parent/'journal.json').read_text())
   self.assertEqual(journal['call'],'started')
   call=json.loads((path.parent/'provider-call-0.json').read_text());self.assertEqual(call['invocation_id'],identity)
   path.write_text(json.dumps(self.answers['R005F']))
   return subprocess.CompletedProcess(args,0,'{"type":"turn.completed"}\n','')
  return manager,command

 def test_real_ask_journals_before_call_and_recovers_after_provider_completion(self):
  manager,command=self.provider()
  with mock.patch.object(K,'ask',side_effect=REAL_ASK),mock.patch.object(K.CP,'CloudPolicy',return_value=manager),mock.patch.object(K.PR,'resource',return_value=nullcontext()),mock.patch.object(K.subprocess,'run',side_effect=command) as execute:
   with mock.patch.object(K,'recover_provider_completion',side_effect=RuntimeError('after provider completion')):self.run_('live',limit=1)
   self.backend.store.offer_coordinator.assert_not_called()
   self.run_('live',limit=1)
   self.assertEqual(manager.run_with_fallback.call_count,1);self.assertEqual(execute.call_count,1)
   self.assertEqual(self.backend.store.offer_coordinator.call_count,1)

 def test_project_pause_during_resource_wait_prevents_provider_spawn(self):
  manager,command=self.provider()
  @contextmanager
  def resource(*args,**kwargs):
   (self.state_dir/'PAUSED').touch();yield
  with mock.patch.object(K,'ask',side_effect=REAL_ASK),mock.patch.object(K.CP,'CloudPolicy',return_value=manager),mock.patch.object(K.PR,'resource',side_effect=resource),mock.patch.object(K.subprocess,'run',side_effect=command) as execute:
   self.run_('live',limit=1);self.run_('live',limit=1)
   execute.assert_not_called();self.assertEqual(manager.run_with_fallback.call_count,1)
   self.backend.store.offer_coordinator.assert_not_called()

 def actual_provider_run(self,first_mismatch=False):
  (self.root/'projects.json').write_text(json.dumps(self.registry))
  manager=K.CP.CloudPolicy(self.root/'runtime');manager.select('codex')
  seen=[]
  def command(args,**kwargs):
   paths=list((self.root/'runtime/cloud-invocations').glob('*/requested.json'))
   path=next(p for p in paths if p.parent.name not in seen);seen.append(path.parent.name)
   request=json.loads(path.read_text());model=request['requested']['model'];answer=self.answers['R005F']
   if request.get('backend')=='claude':
    events=[{'type':'system','subtype':'init','claude_code_version':'fixture','model':model,'tools':['StructuredOutput'],'mcp_servers':[]},
            {'type':'result','structured_output':answer}]
   else:
    Path(request['output_path']).write_text(json.dumps(answer))
    events=[{'type':'response.created','response':{'model':'wrong-model' if first_mismatch else model,
             'reasoning_effort':request['requested']['reasoning_effort']}},{'type':'turn.completed'}]
   return subprocess.CompletedProcess(args,0,'\n'.join(json.dumps(e) for e in events)+'\n','')
  with mock.patch.object(K,'ask',side_effect=REAL_ASK),mock.patch.object(K.PR,'resource',return_value=nullcontext()),mock.patch.object(K.subprocess,'run',side_effect=command):
   result=self.run_('live',limit=1)
  self.assertNotIn('error',result['decided'][0]);self.assertEqual(self.backend.store.offer_coordinator.call_count,1)
  with manager._database() as con:
   rows=con.execute('SELECT id,result FROM cp_invocations').fetchall()
   self.assertEqual(len(rows),2 if first_mismatch else 1)
   for row in rows:self.assertEqual(json.loads(row['result']),json.loads((manager.runtime/'cloud-invocations'/row['id']/'result.json').read_text()))
  return result

 def test_actual_provider_ledger_with_exposed_profile_metadata_recovers(self):
  self.actual_provider_run()

 def test_actual_selected_fallback_after_profile_mismatch_recovers(self):
  result=self.actual_provider_run(first_mismatch=True)
  self.assertTrue(result['decided'][0]['fallback_used']);self.assertEqual(result['decided'][0]['provider'],'claude')


if __name__=='__main__':unittest.main()
