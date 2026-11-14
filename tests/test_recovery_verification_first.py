"""Regressions from the first retained production candidate after adoption."""
import datetime as dt,json,subprocess,tempfile,unittest
from pathlib import Path
from unittest import mock
import evidence_context as E
import guardian as G
import core_repair as C
from action_store import Actions

class VerificationFirstTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def test_frozen_parent_cannot_hide_actual_writable_repair_source(self):
  (self.root/'production.py').write_text('frozen = True\n'*1000);(self.root/'test_value.py').write_text('#' * 1100+'\ndef TARGET_SAVED_TEST():\n    assert 1 == 1\n')
  task={'files':['production.py','test_value.py'],'local_worker_files':['test_value.py']}
  context=E.source_context(self.root,task,[],{},max_bytes=2048)
  self.assertIn('TARGET_SAVED_TEST',json.dumps(context));self.assertLessEqual(len(json.dumps(context).encode()),2048)
  full=E.source_context(self.root,task,[],{},max_bytes=32000);self.assertIn('production.py',full);self.assertIn('test_value.py',full)
 def test_narrowing_cannot_attribute_an_error_assertion_to_an_already_correct_method(self):
  import project_backend as B
  (self.root/'test_value.py').write_text('class Test:\n def success(self):\n  self.assertEqual(result.status,200)\n def failure(self):\n  with self.assertRaises(TransportError): pass\n')
  task={'files':['frozen.py','test_value.py'],'local_worker_files':['test_value.py']}
  quote='with self.assertRaises(TransportError): pass';proposal={'path':'test_value.py','symbol':'Test.success','evidence_quote':quote}
  with self.assertRaises(B.RP.RecoveryHeld):B.narrowing_anchor(self.root,task,proposal)
  proposal['symbol']='Test.failure';self.assertEqual(B.narrowing_anchor(self.root,task,proposal)['symbol'],'Test.failure')
  proposal['path']='frozen.py'
  with self.assertRaises(B.RP.RecoveryHeld):B.narrowing_anchor(self.root,task,proposal)
 def test_recovered_dashboard_does_not_reserve_an_old_cloud_incident(self):
  store=Actions(self.root/'runtime');incident=store.incident('midnight','dashboard_unresponsive','dashboard','old',{'reason':'Earlier unavailable endpoint'})
  with mock.patch.object(G,'dashboard_probe',return_value={'available':True}),mock.patch.object(G.subprocess,'check_output',return_value='a'*40),mock.patch.object(G,'repair_running',return_value=False):
   self.assertIsNone(G.launch_repair(self.root,store,incident,spawn=lambda *_a,**_k:None))
  self.assertEqual(store.repairs(active=False),[]);self.assertEqual(store.get_incident(incident)['state'],'resolved')
 def test_existing_dashboard_maintenance_stops_when_its_premise_recovers(self):
  job={'payload':{'maintenance':'charged','incident_binding':{'code':'dashboard_unresponsive','payload':{'reason':'old'}}}}
  with mock.patch.object(C.GU,'dashboard_probe',return_value={'available':True}):self.assertFalse(C.maintenance_current(self.root,job))

if __name__=='__main__':unittest.main()
