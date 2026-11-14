"""Interrupted workers retain evidence of the source actually admitted at launch."""
import json,tempfile,unittest
from pathlib import Path
from unittest import mock
import progress_recovery as P

class LaunchProvenanceTests(unittest.TestCase):
 def fixture(self,root):
  task={'id':'repair','acceptance':['Preserve exact original behavior'],'files':['test_value.py']};model={'model':'fixture','digest':'a'*64}
  identity=P.scope('one','behavior:1',task,'a'*40,'profile','micro-1');recovery=P.Recovery(root,'one');old={k:'b'*64 for k in P.components()}
  with mock.patch.object(P,'components',return_value=old):recovery.enroll(identity,task,model);reservation=recovery.reserve(identity,'actual-run',model)
  run=root/'actual-run';run.mkdir();(run/'worker-model.json').write_text(json.dumps(model));(run/'outcome.json').write_text(json.dumps({'outcome':'interrupted'}))
  return recovery,identity,model,old,reservation,run
 def test_interruption_recovers_launch_components_without_refunding_or_claiming_completion(self):
  with tempfile.TemporaryDirectory() as name:
   recovery,identity,model,old,reservation,run=self.fixture(Path(name));value=recovery.observe(identity,'actual-run',run)
   self.assertEqual(value['components'],old);self.assertEqual(value['kind'],'interrupted');self.assertFalse(value['observed_finished'])
   self.assertEqual(value['source_provenance'],'consumed_host_proof');self.assertFalse(recovery.admission(identity,model)['allowed'])
   self.assertEqual(recovery.reserve(identity,'actual-run',model),reservation);self.assertEqual(recovery.observe(identity,'actual-run',run),value)
   self.assertEqual(P.classify(run)['refs'],value['refs'])
   with recovery.store.reader() as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM pr_launches').fetchone()[0],1)
 def test_mismatched_model_or_corrupt_proof_does_not_invent_launch_provenance(self):
  for problem in ('model','proof'):
   with self.subTest(problem=problem),tempfile.TemporaryDirectory() as name:
    recovery,identity,model,old,reservation,run=self.fixture(Path(name))
    if problem=='model':(run/'worker-model.json').write_text(json.dumps({'model':'different'}))
    else:
     with recovery.store.transaction() as con:
      row=con.execute('SELECT payload FROM pr_proofs WHERE id=?',(reservation['proof'],)).fetchone();value=json.loads(row[0]);value['components']['structured_worker.py']='c'*64
      con.execute('UPDATE pr_proofs SET payload=? WHERE id=?',(P.encoded(value),reservation['proof']))
    with self.assertRaises(P.RecoveryHeld):recovery.observe(identity,'actual-run',run)
    self.assertFalse((run/'reservation-provenance.json').exists())
    with recovery.store.reader() as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM pr_launches').fetchone()[0],1)

if __name__=='__main__':unittest.main()
