"""Bound observation I/O without reusing its state for actual launch authority."""
import json,tempfile,unittest
from pathlib import Path
from unittest import mock
import active_state as A
import durability as D
import midnight as M
import progress_recovery as P
from history_store import HistoryStore

class ObservationIOTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def recovery(self):
  r=P.Recovery(self.root,'notes');model={'model':'fixture'};scopes=[]
  for index in range(6):
   task={'id':'T'+str(index),'files':['tests/value.py'],'acceptance':['Exact behavior']};scope=P.scope('notes','issue:'+str(index),task,'a'*40,'profile');r.enroll(scope,task,model);scopes.append(scope)
  return r,model,scopes
 def test_recovery_snapshot_reads_candidate_state_and_components_once(self):
  r,_,_=self.recovery()
  with mock.patch.object(D,'read_json',wraps=D.read_json) as read,mock.patch.object(P,'components',wraps=P.components) as source:
   snapshot=r.snapshot();self.assertTrue(all(s['admission']['allowed'] for s in snapshot['scopes']));self.assertEqual(read.call_count,1);self.assertEqual(source.call_count,1)
 def test_observation_snapshot_never_replaces_fresh_launch_validation(self):
  r,model,scopes=self.recovery();self.assertTrue(all(s['admission']['allowed'] for s in r.snapshot()['scopes']))
  D.save_json(self.root/'state.json',{'retry_candidates':{'T0':{'task_sha256':scopes[0]['task_hash'],'run':'new-candidate'}}})
  with self.assertRaises(P.RecoveryHeld):r.reserve(scopes[0],'must-not-launch',model)
  self.assertFalse(next(s for s in r.snapshot()['scopes'] if s['scope']['id']==scopes[0]['id'])['admission']['allowed'])
  self.assertEqual(next(s for s in r.snapshot()['scopes'] if s['scope']['id']==scopes[0]['id'])['historical_launches'],0)
 def test_bulk_terminal_lookup_uses_one_read_connection_and_preserves_project_scope(self):
  store=HistoryStore(self.root)
  with store.transaction() as con:
   for statement in A.SCHEMA:con.execute(statement)
   for project,task in [('notes','T0'),('notes','T999'),('other','T42')]:con.execute('INSERT INTO qc_terminal_tasks VALUES(?,?,?,?,?,?)',(project,task,'superseded',0,'a'*64,'{}'))
  calls=[];original=HistoryStore.connect
  def connect(target,*args,**kwargs):calls.append(target.path);return original(target,*args,**kwargs)
  before=store.path.read_bytes()
  with mock.patch.object(HistoryStore,'connect',connect):self.assertEqual(A.tasks_done(self.root,'notes',['T'+str(n) for n in range(1200)]),{'T0','T999'})
  self.assertEqual(len(calls),1);self.assertEqual(before,store.path.read_bytes())
  missing=self.root/'missing';self.assertEqual(A.tasks_done(missing,'notes',[]),set());self.assertEqual(A.tasks_done(missing,'notes',['unknown']),set());self.assertFalse(missing.exists())
 def test_project_queue_uses_one_bulk_lookup(self):
  tasks=[{'id':'T'+str(n),'title':'Concrete task '+str(n),'depends_on':[]} for n in range(40)];(self.root/'backlog.json').write_text(json.dumps(tasks));project={'id':'notes','path':str(self.root),'backlog':'backlog.json','state_dir':str(self.root/'runtime')}
  with mock.patch.object(A,'tasks_done',return_value={'T0'}) as bulk,mock.patch.object(A,'task_done',side_effect=AssertionError('Per-task database opens are forbidden in a snapshot')):
   queue=M.queue(project,{});bulk.assert_called_once();self.assertEqual(queue['completed'],1);self.assertNotIn('T0',[t['id'] for t in queue['next']])

if __name__=='__main__':unittest.main()
