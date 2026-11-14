"""Bounded decomposition preserves issue lineage and never promotes partials."""
import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest import mock
import manager_recovery as R
import micro_recovery as M
import worker_quality as Q
import worker_recovery as W


class MicroTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.parent={'id':'old','kind':'repair','files':['src/value.py','tests/test_value.py'],'finding_ids':['value'],'acceptance':['Complete original integration contract.']}
  self.task=dict(self.parent,id='narrow',local_worker_files=['tests/test_value.py'],retry_of={'task_id':'old','task_sha256':Q.task_fingerprint(self.parent)})
  self.recovery=R.ManagerRecovery(self.root,'notes');self.store=M.MicroRecovery(self.root,'notes')
  self.review={'decision':'accept','issues':[],'receipt_sha256':'c'*64}
  self.recovery.approve(self.task,self.parent,'value:1','a'*40,'profile',{'task_sha256':Q.task_fingerprint(self.parent),'patch_sha256':'b'*64},
    {'src/value.py':'d'*64},self.review,{'reason':'Only regression tests remain.','contract_comparison':'Original full final criterion is retained.'})
  for run in ('failed1','failed2'):self.recovery.reserve(self.task,'value:1','a'*40,'profile',run)
  self.proposal={'decision':'decompose','reason':'Two smaller test groups.','steps':[
    {'title':'Transport boundaries','description':'Replace obsolete scaffolding with real valid and invalid transport cases.','acceptance':['Actual boundary cases are asserted.']},
    {'title':'Assessment and CLI','description':'Add downstream regressions with valid fixtures.','acceptance':['Full original contract and all final checks remain.']}]}
  self.checkpoint={'task_sha256':Q.task_fingerprint(self.task),'base':'a'*40,'patch_sha256':'e'*64,'run':'failed2'}

 def approve(self,**changes):
  args=dict(task=self.task,episode='value:1',head='a'*40,profile_hash='profile',proposal=self.proposal,checkpoint=self.checkpoint,review=self.review)
  args.update(changes);return self.store.approve(**args)

 def advance(self,run='step1'):
  self.store.reserve(self.task,'value:1','a'*40,'profile',run)
  self.store.complete_step(self.task,'value:1','a'*40,'profile',0,run,'f'*64,[{'code':0,'reason':'exited'}],self.review,{'receipt_sha256':'f'*64})

 def test_plan_is_once_per_issue_and_never_renews_by_renaming_or_rewording(self):
  original=self.approve();self.assertEqual(self.approve(),original)
  changed=copy.deepcopy(self.proposal);changed['steps'][0]['description']='Another plan'
  with self.assertRaises(M.MicroHeld):self.approve(proposal=changed)
  with self.assertRaises(M.MicroHeld):self.approve(task=dict(self.task,id='renamed'))
  self.assertEqual(self.store.get('value:1')['final_contract'],self.task['acceptance'])

 def test_each_step_has_two_irreversible_reservations_and_keeps_original_dispatches(self):
  self.approve()
  for run in ('one','two'):self.recovery.reserve(self.task,'value:1','a'*40,'profile',run)
  self.assertFalse(self.recovery.eligible(self.task,'value:1','a'*40,'profile'))
  with self.assertRaises(M.MicroHeld):self.recovery.reserve(self.task,'value:1','a'*40,'profile','third')
  self.store.complete_step(self.task,'value:1','a'*40,'profile',0,'two','f'*64,[{'code':0,'reason':'exited'}],self.review,{'receipt_sha256':'f'*64})
  self.assertTrue(self.recovery.eligible(self.task,'value:1','a'*40,'profile'))
  with self.store.store.reader() as con:
   self.assertEqual(con.execute('SELECT COUNT(*) FROM mr_dispatches').fetchone()[0],4)
  with self.assertRaises(M.MicroHeld):self.recovery.reserve(self.task,'value:1','a'*40,'profile','failed1')

 def test_only_checked_independently_reviewed_current_intermediate_step_can_advance(self):
  self.approve();self.store.reserve(self.task,'value:1','a'*40,'profile','one')
  args=(self.task,'value:1','a'*40,'profile',0,'one','f'*64)
  for checks,review in (([],self.review),([{'code':1,'reason':'exited'}],self.review),([{'code':0,'reason':'exited'}],{'decision':'revise','issues':['Incomplete']})):
   with self.assertRaises(M.MicroHeld):self.store.complete_step(*args,checks,review,{'proof':'retained'})
  self.assertEqual(self.store.current(self.task,'value:1','a'*40,'profile')['index'],0)

 def test_checkpoint_replay_advances_once_and_final_step_cannot_be_accepted_as_partial(self):
  self.approve();self.advance()
  self.store.complete_step(self.task,'value:1','a'*40,'profile',0,'step1','f'*64,[{'code':0,'reason':'exited'}],self.review,{'receipt_sha256':'f'*64})
  self.assertEqual(self.store.current(self.task,'value:1','a'*40,'profile')['index'],1)
  self.store.reserve(self.task,'value:1','a'*40,'profile','final')
  with self.assertRaises(M.MicroHeld):self.store.complete_step(self.task,'value:1','a'*40,'profile',1,'final','f'*64,[{'code':0,'reason':'exited'}],self.review,{'proof':'retained'})
  with self.store.store.reader() as con:
   payload=json.loads(con.execute('SELECT payload FROM micro_completed').fetchone()[0]);self.assertFalse(payload['accepted_product'])

 def test_source_profile_task_and_test_only_scope_are_bound(self):
  with self.assertRaises(M.MicroHeld):self.approve(task=dict(self.task,local_worker_files=['src/value.py']))
  self.approve()
  for task,head,profile in ((self.task,'b'*40,'profile'),(self.task,'a'*40,'changed'),(dict(self.task,acceptance=['weaker']),'a'*40,'profile')):
   with self.assertRaises(M.MicroHeld):self.store.current(task,'value:1',head,profile)

 def test_excessive_or_empty_steps_and_missing_review_are_rejected(self):
  for steps in ([],self.proposal['steps']*3,[dict(self.proposal['steps'][0],acceptance=[]),self.proposal['steps'][1]]):
   with self.assertRaises((ValueError,M.MicroHeld)):self.approve(proposal=dict(self.proposal,steps=steps))
  with self.assertRaises(M.MicroHeld):self.approve(review={'decision':'accept','issues':[]})

 def extension(self):
  self.proof={'path':'qualified-proof','sha256':'1'*64,'model':{'model':'fixture'}}
  self.owner={'path':'owner-proof','sha256':'2'*64,'instruction':'Explicit two further launches'}
  return self.store.extend_after_worker_fix(self.task,'value:1','a'*40,'profile',owner_path='owner-proof',qualification_path='qualified-proof',review=self.review)

 def test_owner_extension_adds_exactly_two_without_resetting_or_expanding_next_step(self):
  self.approve()
  for run in ('one','two'):self.store.reserve(self.task,'value:1','a'*40,'profile',run)
  proof={'path':'qualified-proof','sha256':'1'*64,'model':{'model':'fixture'}};owner={'path':'owner-proof','sha256':'2'*64,'instruction':'Explicit two further launches'}
  with mock.patch.object(W,'qualification',return_value=proof),mock.patch.object(W,'owner_direction',return_value=owner):
   value=self.extension();self.assertEqual(self.extension(),value)
   self.assertEqual(self.store.get('value:1')['attempts_per_step'],2)
   with self.assertRaises(M.MicroHeld):self.store.reserve(self.task,'value:1','a'*40,'profile','extra-without-model')
   for run in ('extra1','extra2'):
    self.recovery.reserve(self.task,'value:1','a'*40,'profile',run,proof['model'])
    self.recovery.reserve(self.task,'value:1','a'*40,'profile',run,proof['model'])
   self.assertEqual(self.store.current(self.task,'value:1','a'*40,'profile')['used'],4)
   with self.assertRaises(M.MicroHeld):self.recovery.reserve(self.task,'value:1','a'*40,'profile','extra3',proof['model'])
   with self.store.store.reader() as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM mr_dispatches').fetchone()[0],6)
   self.store.complete_step(self.task,'value:1','a'*40,'profile',0,'extra2','f'*64,[{'code':0,'reason':'exited'}],self.review,{'proof':'Retained'})
   step=self.store.current(self.task,'value:1','a'*40,'profile')
   self.assertEqual((step['index'],step['remaining'],step['owner_additional_dispatches']),(1,2,0))

 def test_extension_cannot_precede_two_failures_or_bypass_qualification_or_review(self):
  self.approve()
  with mock.patch.object(W,'qualification',return_value={}),mock.patch.object(W,'owner_direction',return_value={}):
   with self.assertRaises(M.MicroHeld):self.extension()
  for run in ('one','two'):self.store.reserve(self.task,'value:1','a'*40,'profile',run)
  with mock.patch.object(W,'qualification',side_effect=ValueError('Unqualified')):
   with self.assertRaises(ValueError):self.extension()
  with self.assertRaises(M.MicroHeld):self.store.extend_after_worker_fix(self.task,'value:1','a'*40,'profile',owner_path='owner',qualification_path='proof',review={})
  self.assertEqual(self.store.current(self.task,'value:1','a'*40,'profile')['remaining'],0)

 def test_changed_qualified_source_or_owner_proof_blocks_extra_dispatch_before_charge(self):
  self.approve()
  for run in ('one','two'):self.store.reserve(self.task,'value:1','a'*40,'profile',run)
  proof={'path':'qualified-proof','sha256':'1'*64,'model':{'model':'fixture'}};owner={'path':'owner-proof','sha256':'2'*64,'instruction':'Explicit two further launches'}
  with mock.patch.object(W,'qualification',return_value=proof),mock.patch.object(W,'owner_direction',return_value=owner):self.extension()
  for proof_result,owner_result in ((dict(proof,sha256='3'*64),owner),(proof,dict(owner,sha256='4'*64))):
   with mock.patch.object(W,'qualification',return_value=proof_result),mock.patch.object(W,'owner_direction',return_value=owner_result):
    with self.assertRaises(M.MicroHeld):self.store.reserve(self.task,'value:1','a'*40,'profile','extra',proof['model'])
  self.assertEqual(self.store.current(self.task,'value:1','a'*40,'profile')['used'],2)


if __name__=='__main__':unittest.main()
