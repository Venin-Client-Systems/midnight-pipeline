"""Daily advice must not replay duplicated full recovery contracts to the model."""
import copy
import datetime as dt
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
import active_state as AS
import agent_discussion as A
import agent_workspace as C
import midnight as M
import progress_recovery as R
import project_backend as B
from action_store import Actions


class DiscussionFactsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.project={'id':'one','name':'One','path':str(self.root/'project'),'state_dir':str(self.root/'project-state'),'on_schedule':True}
  self.instant=dt.datetime.now(dt.timezone.utc)
  self.snapshot={'available':True,'version':1,'local_counts_are_permission':False,'scopes':[
   {'scope':{'id':'scope-'+str(i),'project':'one','stage':'micro-3'},'task_id':'original-'+str(i),'legacy_launches':4,'historical_launches':7,
    'admission':{'allowed':False,'state':'needs_codex','reason':'current_source_quote_rejected','proof':None,
       'diagnosis':{'original_contract':'Keep every frozen condition. '*1000}},
    'diagnosis':{'kind':'semantic','next_action':'Use the current writable method','retained_contract':'Keep the final gates. '*1000},
    'legacy_runs':['spent-'+str(n) for n in range(40)]} for i in range(6)]}

 def facts(self):
  with mock.patch.object(R,'Recovery') as recovery,mock.patch.object(AS,'accepted_since',return_value=[]):
   recovery.return_value.snapshot.return_value=self.snapshot
   return A.project_facts(self.project,self.instant)

 def test_existing_project_facts_path_is_bounded_and_preserves_current_state_and_spent_counts(self):
  before=copy.deepcopy(self.snapshot);facts=self.facts();recovery=facts['recovery']
  self.assertLess(len(json.dumps(facts).encode()),16000)
  self.assertEqual(recovery['scopes'][0]['admission']['reason'],'current_source_quote_rejected')
  self.assertEqual(recovery['scopes'][0]['legacy_launches'],4);self.assertEqual(recovery['scopes'][0]['historical_launches'],7)
  self.assertEqual(recovery['scopes'][0]['diagnosis']['next_action'],'Use the current writable method')
  self.assertFalse(recovery['local_counts_are_permission']);self.assertTrue(recovery['historical_details_omitted'])
  self.assertEqual(recovery['scopes_observed'],6);self.assertEqual(recovery['omitted_scopes'],2)
  self.assertEqual(recovery['snapshot_sha256'],hashlib.sha256(json.dumps(before,sort_keys=True,ensure_ascii=False).encode()).hexdigest())
  self.assertEqual(self.snapshot,before);self.assertIn('human release gates',facts['publication'])

 def test_real_discussion_path_compacts_legacy_meeting_facts_without_rewriting_history(self):
  runtime=self.root/'runtime';clock=[self.instant.timestamp()];workspace=C.Workspace(runtime/'agent-workspace',clock=lambda:clock[0])
  workspace.bootstrap([self.project]);workspace.ensure_scrum([self.project],self.instant)
  clock[0]+=86400;meeting=workspace.ensure_scrum([self.project],dt.datetime.fromtimestamp(clock[0],dt.timezone.utc))
  old={'project':'one','host_observed_at':'original-opening-time','recovery':copy.deepcopy(self.snapshot),
       'publication':'Original human publication hold remains.'}
  saved=workspace.scrum_facts(meeting['day'],'one',old);work=workspace.pending_work()[0]
  store=Actions(runtime);head='a'*40;model={'model':'fixture'}
  job=store.submit('midnight',{'kind':'agent_discussion','work':work['id'],'source_head':head,'worker_model':model},time.time()+600)
  backend=mock.Mock();captured=[]
  def decision(prompt,schema,directory,*args,**kwargs):
   captured.append(prompt);self.assertLess(len(prompt.encode()),48000)
   facts=json.loads(prompt.split('\n',1)[1])['facts'];opening=facts['meeting_open_observations']
   self.assertEqual(opening['host_observed_at'],'original-opening-time')
   self.assertEqual(opening['publication'],'Original human publication hold remains.')
   self.assertEqual(opening['recovery']['scopes'][0]['historical_launches'],7)
   (directory/'local-decision-receipt.json').write_text('{}')
   return {'body':'Advice only; original gates and charged work remain.','needs_principal':False}
  backend.local_decision.side_effect=decision
  timing={'active':True,'cutoff':dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=1)}
  with mock.patch.object(A,'ROOT',self.root),mock.patch.object(A,'BACKEND',None),mock.patch.object(A,'STOP',False),mock.patch.object(A.PA,'source_head',return_value=head),mock.patch.object(A.PA,'drain',return_value=True),mock.patch.object(M,'registry',return_value={'projects':[self.project]}),mock.patch.object(M,'window',return_value=timing),mock.patch.object(B,'load_backend',return_value=backend),mock.patch.object(A,'project_facts',return_value=self.facts()),mock.patch.dict(os.environ):
   self.assertEqual(A.execute(runtime,job['id']),0)
  self.assertEqual(len(captured),1)
  recorded=next(m for m in workspace.messages(meeting['room'])['messages'] if m['id']==saved['id'])
  self.assertEqual(recorded['context']['facts'],old)
  self.assertEqual(store.get(job['id'])['state'],'succeeded')


if __name__=='__main__':unittest.main()
