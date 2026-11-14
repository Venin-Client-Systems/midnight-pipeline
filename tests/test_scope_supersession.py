"""Runtime supersession is history, not fresh work that wakes idle scopes."""
import copy,datetime as dt,tempfile,unittest
from pathlib import Path
from unittest import mock
import midnight as M


class SupersessionTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);root=Path(self.temp.name)
  self.runtime=root/'runtime';self.runtime.mkdir();self.project={'id':'inventory','name':'Inventory','on_schedule':True,
      'path':str(root/'product'),'backlog':'backlog.json','config':'config.json','state_dir':str(self.runtime/'inventory'),
      'why':'Repair existing behavior','instruction':'Repair only the admitted scope'}
  self.product=root/'product';self.product.mkdir();Path(self.project['state_dir']).mkdir()
  self.patch=mock.patch.object(M,'STATE',self.runtime);self.patch.start();self.addCleanup(self.patch.stop)
  M.write(self.product/'config.json',{'max_attempts_per_night':24,'max_cloud_calls_per_night':16,'max_failures_per_night':8})
  self.tasks=[{'id':n,'title':n} for n in ('old','guided','replacement')]
  self.state={'done':['replacement'],'tries':{'old':0,'guided':2},'superseded_tasks':{'old':'guided','guided':'replacement'}}
  self.instant=M.now();key=M.AW.fingerprint({'why':self.project['why'],'instruction':self.project['instruction'],'source':M.Q.policy()['project_backend_sha256']})
  M.write(self.product/'backlog.json',self.tasks);M.write(Path(self.project['state_dir'])/'state.json',self.state)
  M.write(Path(self.project['state_dir'])/'status.json',{'phase':'quality_wait','scope_wait':{'until':(self.instant+dt.timedelta(minutes=5)).isoformat(),'key':key}})

 def test_replaced_chain_does_not_repeatedly_wake_clear_scope_and_preserves_charges(self):
  before=copy.deepcopy(self.state);queue=M.queue(self.project,self.state)
  self.assertEqual(queue['next'],[]);self.assertEqual(queue['remaining'],0)
  self.assertEqual(queue['superseded'],[{'id':'old','by':'guided'},{'id':'guided','by':'replacement'}])
  for _ in range(3):self.assertFalse(M.eligible(self.project,self.instant))
  self.assertEqual(self.state,before);self.assertTrue(M.eligible(self.project,self.instant+dt.timedelta(minutes=6)))

 def test_actual_new_task_or_owner_direction_still_wakes_scope_immediately(self):
  self.assertFalse(M.eligible(self.project,self.instant))
  M.write(self.product/'backlog.json',self.tasks+[{'id':'new','title':'New bounded repair'}])
  self.assertTrue(M.eligible(self.project,self.instant))
  M.write(self.product/'backlog.json',self.tasks)
  self.assertTrue(M.eligible(dict(self.project,instruction='New owner direction'),self.instant))


if __name__=='__main__':unittest.main()
