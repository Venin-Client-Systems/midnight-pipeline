"""Failure facts survive the worker, wrapper, classification and read-only reports."""
import contextlib, datetime as dt, hashlib, io, json, os, subprocess, tempfile, time, unittest
from pathlib import Path
from unittest import mock
import structured_worker as S
import worker_quality as Q
import progress_recovery as R
import coordinator as K
import history_maintenance as H
import performance as F
import project_backend as B
from history_store import HistoryStore


class FailureEvidenceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  self.run='20260101T000000Z-T1-abcdef';self.directory=self.root/'runs'/self.run;self.directory.mkdir(parents=True)
  self.model={'model':'fixture','context':32768,'digest':'a'*64}
  self.save('task.json',{'id':'T1','title':'One exact repair'})

 def save(self,name,value):
  path=self.directory/name;path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(value));return path

 def failure(self,*,phase='edit',legacy=False):
  self.save('worker-model.json',self.model);self.save('worker-process.json',{'code':0,'reason':'exited'})
  self.save('worker-completion.json',{'contract':S.CONTRACT,'finished':False})
  self.save('outcome.json',{'outcome':'failed','summary':'Unfinished'})
  value={'status':'failed','phase':phase,'completion_reason':'length','metrics':{'prompt_eval_count':16000,'eval_count':4096},'partial_text_applied':False}
  if not legacy:value.update(schema_version=2,requested_budget=4096,worker_contract=S.CONTRACT,model=self.model,inference_policy={'version':S.INFERENCE_POLICY,'thinking_capable':True})
  receipt=self.save('worker-tools/'+phase+'-11.json',value)
  (self.directory/'worker-events.jsonl').write_text(json.dumps({'type':'worker.client_result','schema_version':1,'exit_code':17})+'\n')
  return receipt

 def test_truncated_edit_survives_exit_zero_classification_and_readonly_reports(self):
  receipt=self.failure();actual=R.classify(self.directory)
  self.assertEqual(actual['kind'],'inference_truncated');failure=actual['worker_failure']
  self.assertEqual(failure['phase'],'edit');self.assertEqual(failure['requested_budget'],4096)
  self.assertEqual(failure['metrics']['eval_count'],4096);self.assertEqual(failure['completion_reason'],'length')
  self.assertEqual(failure['client_result']['exit_code'],17);self.assertEqual(failure['wrapper_result']['code'],0)
  self.assertEqual(failure['model'],self.model);self.assertEqual(failure['inference_policy']['version'],S.INFERENCE_POLICY)
  self.assertIn(R.ref(receipt),actual['refs']);self.assertIn(R.ref(self.directory/'worker-events.jsonl'),actual['refs'])
  before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
  project={'id':'fixture','state_dir':str(self.root)}
  payload=K.evidence(project,mock.Mock(),{'task':{'id':'T1'},'state':'needs_harness_repair','diagnosis':actual,'launches':1})
  self.assertEqual(payload['escalation']['worker_failure'],failure)
  self.assertEqual(payload['recent_attempts'][0]['worker_failure'],failure)
  source={'case.py':{'sha256':'a'*64,'content':'value=0\n','truncated':False}}
  with mock.patch.object(B,'ROOT',self.root),mock.patch.object(B,'STATE',self.root):
   capsule=B.recovery_model_context({'id':'T1','files':['case.py'],'acceptance':['Keep the exact criterion']},None,actual,{}, {'source':source},None,[])
  self.assertEqual(capsule['failure']['worker_failure'],failure)
  self.assertEqual(H.observe_run(self.root,self.run)['worker_failure'],failure)
  since=dt.datetime(2025,12,31,tzinfo=dt.timezone.utc);until=dt.datetime(2026,1,2,tzinfo=dt.timezone.utc)
  self.assertEqual(F.legacy_collect([project],since,until)['projects'][0]['details'][0]['worker_failure'],failure)
  self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()})
  message=Q.worker_diagnostic(self.directory,'Worker unfinished')
  for word in ('edit','length','4096','17'):self.assertIn(word,message)
  self.assertEqual(Q.read(self.directory/'worker-diagnostic.json')['worker_failure'],failure)
  row=H.observe_run(self.root,self.run);HistoryStore(self.root).record_run('fixture',self.run,row,created_at=row['started_at'])
  self.assertEqual(F.collect([project],since,until)['projects'][0]['details'][0]['worker_failure'],failure)

 def test_legacy_missing_fields_remain_unknown_and_receipts_order_numerically(self):
  self.failure(legacy=True);self.save('worker-tools/decision-2.json',{'status':'failed','phase':'decision','completion_reason':'missing','metrics':{}})
  failure=R.classify(self.directory)['worker_failure'];self.assertEqual(failure['phase'],'edit')
  self.assertIsNone(failure['requested_budget']);self.assertIsNone(failure['inference_policy'])
  (self.directory/'worker-events.jsonl').unlink();self.assertIsNone(R.classify(self.directory)['worker_failure']['client_result'])

 def test_completed_handoff_is_not_reclassified_by_earlier_inference_failure(self):
  self.failure();self.save('worker-completion.json',{'finished':True,'contract':S.CONTRACT})
  self.assertEqual(R.classify(self.directory)['kind'],'verification')

 def test_inference_receipt_keeps_phase_budget_policy_and_observed_channel_bytes(self):
  tools=self.directory/'worker-tools';tools.mkdir()
  event={'model':'fixture','done':True,'done_reason':'length','prompt_eval_count':16000,'eval_count':1024,
         'message':{'role':'assistant','thinking':'analysis','content':'{}'}}
  def open(request,**kwargs):
   return io.BytesIO(json.dumps({'capabilities':['thinking']}).encode() if request.full_url.endswith('/api/show') else (json.dumps(event)+'\n').encode())
  opener=mock.Mock();opener.open.side_effect=open
  with mock.patch.object(S.urllib.request,'build_opener',return_value=opener),S.inference_session(self.model),contextlib.redirect_stdout(io.StringIO()),self.assertRaises(S.IncompleteInference):
   S.recorded_infer(tools,4,'decision',self.model,[],time.monotonic()+10,schema=S.ACTION_SCHEMA,predict=1024)
  value=json.loads((tools/'decision-4.json').read_text());self.assertEqual(value['schema_version'],2)
  self.assertEqual(value['requested_budget'],1024);self.assertEqual(value['model'],self.model)
  self.assertEqual(value['inference_policy']['version'],S.INFERENCE_POLICY);self.assertEqual(value['worker_contract'],S.CONTRACT)
  self.assertEqual(value['metrics']['thinking_bytes'],8);self.assertEqual(value['metrics']['final_text_bytes'],2)
  self.assertFalse(value['partial_text_applied'])

 def test_interrupted_stream_retains_partial_byte_counts_and_is_terminal(self):
  folder=self.directory/'worker-tools';folder.mkdir()
  first={'model':'fixture','done':False,'message':{'role':'assistant','thinking':'analysis','content':'{"old_text":'}}
  response=mock.MagicMock();response.__enter__.return_value=response
  response.readline.side_effect=[(json.dumps(first)+'\n').encode(),ConnectionResetError('connection reset')]
  opener=mock.Mock();opener.open.return_value=response
  with mock.patch.object(S.urllib.request,'build_opener',return_value=opener),contextlib.redirect_stdout(io.StringIO()),self.assertRaises(S.WorkerInferenceError):
   S.recorded_infer(folder,4,'edit',self.model,[],time.monotonic()+10,schema=S.EDIT_SCHEMA,predict=4096)
  receipt=json.loads((folder/'edit-4.json').read_text());self.assertEqual(receipt['error_class'],'ConnectionResetError')
  self.assertEqual(receipt['completion_reason'],'stream_error');self.assertEqual(receipt['metrics']['thinking_bytes'],8)
  self.assertEqual(receipt['metrics']['final_text_bytes'],12);self.assertIsNone(receipt['metrics']['eval_count'])
  self.assertEqual(receipt['requested_budget'],4096);self.assertFalse(receipt['partial_text_applied']);opener.open.assert_called_once()

 def test_rejected_terminal_role_or_tools_cannot_erase_observed_completion_facts(self):
  folder=self.directory/'worker-tools';folder.mkdir()
  for index,changes in enumerate(({'role':'user'},{'tool_calls':[{'function':{'name':'unrequested'}}]})):
   with self.subTest(changes=changes):
    event={'model':'fixture','done':True,'done_reason':'length','prompt_eval_count':16000,'eval_count':1024,
           'message':dict({'role':'assistant','thinking':'analysis','content':'{}'},**changes)}
    opener=mock.Mock();opener.open.return_value=io.BytesIO((json.dumps(event)+'\n').encode())
    with mock.patch.object(S.urllib.request,'build_opener',return_value=opener),contextlib.redirect_stdout(io.StringIO()),self.assertRaises(S.WorkerInferenceError):
     S.recorded_infer(folder,index,'decision',self.model,[],time.monotonic()+10,schema=S.ACTION_SCHEMA,predict=1024)
    receipt=json.loads((folder/('decision-'+str(index)+'.json')).read_text())
    self.assertEqual(receipt['completion_reason'],'length');self.assertEqual(receipt['metrics']['eval_count'],1024)
    self.assertEqual(receipt['metrics']['prompt_eval_count'],16000)
    self.assertEqual(receipt['metrics']['thinking_bytes'],8);self.assertEqual(receipt['metrics']['final_text_bytes'],2)
    self.assertFalse(receipt['partial_text_applied']);self.assertEqual(receipt['status'],'failed')

 def test_wrapper_preserves_actual_client_exit_even_when_loop_returns_success(self):
  work=self.root/'work';work.mkdir();subprocess.run(['git','init','-q',str(work)],check=True)
  (work/'PRD.md').write_text('# Task\n- [ ] Repair the scoped value\n');(work/'AGENTS.md').write_text('No external actions.\n')
  subprocess.run(['git','-C',str(work),'add','.'],check=True)
  subprocess.run(['git','-C',str(work),'-c','user.name=Test','-c','user.email=test@localhost','-c','commit.gpgsign=false','commit','-qm','fixture'],check=True)
  client=self.root/'client';client.write_text('#!/bin/sh\nprintf \'{"type":"error","error":{"message":"fixture failed"}}\\n\'\nexit 17\n');client.chmod(0o700)
  events=self.directory/'worker-events.jsonl';env=dict(os.environ,MIDNIGHT_LOCAL_CODE_BIN=str(client),EXAMPLEAPP_AGENT_EVENTS=str(events),EXAMPLEAPP_RALPHY_STATE_DIR=str(self.root/'ralphy-state'))
  process=subprocess.run([str(Path(S.__file__).parent/'ralphy/run.sh'),'--prd','PRD.md'],cwd=work,env=env,capture_output=True,text=True,timeout=30)
  rows=[json.loads(line) for line in events.read_text().splitlines()]
  results=[row for row in rows if row.get('type')=='worker.client_result']
  self.assertEqual(len(results),1,process.stdout+process.stderr);self.assertEqual(results[0]['exit_code'],17)
  self.assertEqual(results[0]['schema_version'],1)


if __name__=='__main__':unittest.main()
