"""Current evidence replaces stale instruction noise without reducing the contract."""
import json,os,tempfile,unittest
from pathlib import Path
from unittest import mock
import agent_workspace as W
import project_backend as B

class CompactRecoveryTests(unittest.TestCase):
 def workspace(self,root):
  workspace=W.Workspace(root/'workspace');workspace.bootstrap([{'id':p,'name':p,'on_schedule':True,'state_dir':str(root/p)} for p in ('one','two')]);return workspace
 def test_superseded_guidance_leaves_model_context_but_remains_in_history(self):
  with tempfile.TemporaryDirectory() as name:
   w=self.workspace(Path(name));old=w.decide('one','Obsolete unit selection','guidance');hold=w.decide('one','Retain this delivery hold','hold_delivery')
   new=w.decide('one','Choose the current unmet unit','guidance',supersedes=[old['id']],event_key='current-unit')
   current=w.context('planner:one');ids={d['id'] for d in current['principal_decisions']}
   self.assertNotIn(old['id'],ids);self.assertIn(new['id'],ids);self.assertIn(hold['id'],ids)
   self.assertNotIn(old['id'],{m['id'] for m in current['recent']})
   self.assertIn(old['id'],{m['id'] for m in w.messages('architecture',limit=50)['messages']})
   self.assertEqual(w.delivery_holds('one')[0]['message'],hold['id'])
   self.assertEqual(w.decide('one','Choose the current unmet unit','guidance',supersedes=[old['id']],event_key='current-unit')['id'],new['id'])
 def test_supersession_cannot_release_holds_cross_scope_or_impersonate_operator(self):
  with tempfile.TemporaryDirectory() as name:
   w=self.workspace(Path(name));old=w.decide('one','Old guidance','guidance');hold=w.decide('one','Hold','hold_delivery')
   for scope,items in [('two',[old['id']]),('one',[hold['id']]),('one',['m-missing'])]:
    with self.assertRaises(ValueError):w.decide(scope,'Invalid replacement','guidance',supersedes=items)
   with self.assertRaises(ValueError):w.decide('one','Invalid release','release',releases=hold['id'],supersedes=[old['id']])
   with mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':'fixture'}),self.assertRaises(ValueError):w.decide('one','Pretend Codex','guidance',supersedes=[old['id']])
   self.assertIn(old['id'],{d['id'] for d in w.context('planner:one')['principal_decisions']});self.assertEqual(len(w.delivery_holds('one')),1)
 def test_legacy_context_reads_do_not_migrate_but_operator_replacement_can(self):
  with tempfile.TemporaryDirectory() as name:
   w=self.workspace(Path(name));old=w.decide('one','Legacy advice','guidance')
   with w.store.transaction() as con:con.execute('DROP TABLE aw_superseded_guidance')
   self.assertEqual(w.full_principal_decisions('planner:one')[0]['id'],old['id'])
   with w.store.reader() as con:self.assertIsNone(con.execute("SELECT 1 FROM sqlite_master WHERE name='aw_superseded_guidance'").fetchone())
   new=w.decide('one','Current advice','guidance',supersedes=[old['id']])
   self.assertEqual([x['id'] for x in w.context('planner:one')['principal_decisions']],[new['id']])
 def test_full_current_direction_is_not_lost_at_display_excerpt_boundary(self):
  with tempfile.TemporaryDirectory() as name:
   w=self.workspace(Path(name));body='Context. '*100+'CURRENT REQUIRED UNIT AT END';decision=w.decide('one',body,'guidance')
   displayed=w.context('planner:one');self.assertTrue(displayed['principal_decisions'][0]['excerpted'])
   focused=B.local_role_context(w,'planner:one',displayed,'diagnosis')
   self.assertEqual(focused['principal_decisions'][0]['body'],body);self.assertFalse(focused['principal_decisions'][0]['excerpted'])
 def test_existing_local_decision_path_omits_chat_noise_and_retains_direction_and_contract(self):
  with tempfile.TemporaryDirectory() as name:
   root=Path(name);backend=B.load_backend(root,'one');backend.STATE=root/'state';backend.CLOUD_RUNTIME=root/'shared'
   workspace=mock.Mock();workspace.context.return_value={'principal_decisions':[{'id':'fixed','body':'Keep the original contract'}],'recent':[{'body':'OBSOLETE CHAT CLAIM'}]}
   directory=root/'decision';answer={'decision':'accept','summary':'Supported small plan','issues':[]}
   def run(*args,**kwargs):
    prompt=(directory/'input.txt').read_text();self.assertIn('ALL ORIGINAL CRITERIA',prompt);self.assertIn('Keep the original contract',prompt);self.assertNotIn('OBSOLETE CHAT CLAIM',prompt)
    args[6]();backend.write(directory/'result.json',answer);backend.write(directory/'local-metrics.json',{'model':'fixture'});(directory/'events.jsonl').write_text('{}\n');return {'code':0,'reason':'exited'}
   with mock.patch.object(backend.PR,'enabled',return_value=True),mock.patch.object(backend.CW,'Workspace',return_value=workspace),mock.patch.object(backend,'git',return_value='a'*40),mock.patch.object(backend,'journal_charge'),mock.patch.object(backend,'charged_run',side_effect=run):
    self.assertEqual(backend.local_decision('ALL ORIGINAL CRITERIA',backend.REVIEW_SCHEMA,directory,None,{}, {'worker_model':{'model':'fixture'}},role='diagnosis'),answer)
   self.assertIn('OBSOLETE CHAT CLAIM',(directory/'chat-context-full.json').read_text())
 def test_capsule_retains_every_obligation_source_current_step_and_audit(self):
  acceptance=['Original obligation '+str(i) for i in range(8)];source={'one.py':{'sha256':'a'*64,'content':'actual current code','truncated':False}}
  host={'source':source,'host':{'old_review':'STALE CLAIM '*10000},'checks':{'checks':[{'code':0,'reason':'exited','combined_stdout_stderr':'success noise'*1000},{'code':1,'reason':'exited','combined_stdout_stderr':'failure context'*1000,'receipt':'pinned'}]},'principal_direction':[{'body':'Current direction'}]}
  value=B.recovery_model_context({'id':'task','description':'Full original scope and frozen production','acceptance':acceptance,'files':['one.py'],'local_worker_files':['one.py']},{'index':2,'acceptance':['Current step'], 'remaining':0},{'kind':'semantic','fingerprint':'b'*64},{'run':'retained'},host,{'guidance_id':'spent','guidance':{'constraint':'Keep saved Codex constraints'}},[{'anchor':{'unit_id':'c'*64,'path':'one.py'},'instruction':'STALE CLAIM'}],{'issues':['Preserve this audit requirement']})
  self.assertEqual(value['original_task']['acceptance'],acceptance);self.assertEqual(value['current_source'],source);self.assertEqual(value['current_microstep']['acceptance'],['Current step'])
  self.assertEqual(value['codex_findings'],{'issues':['Preserve this audit requirement']});self.assertNotIn('STALE CLAIM',json.dumps(value));self.assertLess(len(json.dumps(value)),6000)
  self.assertEqual(value['original_task']['description'],'Full original scope and frozen production');self.assertEqual(value['committed_codex_guidance']['guidance']['constraint'],'Keep saved Codex constraints')
  self.assertEqual(value['used_work_units'][0]['unit_id'],'c'*64);self.assertNotIn('combined_stdout_stderr',value['host_check_receipts']['checks'][0])
 def test_final_serialization_retains_long_and_many_requirements_or_refuses(self):
  requirements=['Original obligation '+str(i) for i in range(17)]+['x'*16001+' FINAL_REQUIRED_CONSTRAINT']
  value={'original_task':{'acceptance':requirements},'credential':{'API_KEY':'sensitive'},'current_step':{'acceptance':requirements[:2]}}
  actual=json.loads(B.recovery_model_json(value));self.assertEqual(actual['original_task']['acceptance'],requirements);self.assertEqual(actual['credential']['API_KEY'],'[REDACTED]')
  with self.assertRaises(B.ManagerHeld):B.recovery_model_json({'acceptance':['x'*48001]})
 def test_incomplete_principal_direction_prevents_charge_and_launch(self):
  with tempfile.TemporaryDirectory() as name:
   root=Path(name);backend=B.load_backend(root,'one');backend.STATE=root/'state';backend.CLOUD_RUNTIME=root/'shared';workspace=mock.Mock()
   workspace.context.return_value={'principal_decisions':[{'id':'current','body':'Excerpt','excerpted':True}]};workspace.full_principal_decisions.side_effect=ValueError('Full source unavailable')
   with mock.patch.object(backend.PR,'enabled',return_value=True),mock.patch.object(backend.CW,'Workspace',return_value=workspace),mock.patch.object(backend,'charged_run') as launch,mock.patch.object(backend,'journal_charge') as charge:
    with self.assertRaises(backend.ManagerHeld):backend.local_decision('All original criteria',backend.REVIEW_SCHEMA,root/'decision',None,{}, {'worker_model':{'model':'fixture'}},role='diagnosis_review')
    launch.assert_not_called();charge.assert_not_called()

if __name__=='__main__':unittest.main()
