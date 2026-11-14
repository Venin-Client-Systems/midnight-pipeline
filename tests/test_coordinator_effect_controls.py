"""Current owner controls and withdrawals win at coordinator effect boundaries."""
from contextlib import contextmanager
import concurrent.futures,json,threading,unittest
from unittest import mock
import coordinator as K
import test_coordinator as fixtures
import test_harness_failure_admission as admission_fixtures


class ControlTests(unittest.TestCase):
 setUp=fixtures.CoordinatorRunTests.setUp
 run_=fixtures.CoordinatorRunTests.run_

 def changed_answer(self,change):
  K.set_mode(self.root/'runtime','live');original=self.mocks['ask'].side_effect
  def ask(*args):
   result=original(*args);change();return result
  self.mocks['ask'].side_effect=ask

 def test_live_to_shadow_during_answer_withholds_effects(self):
  self.changed_answer(lambda:K.set_mode(self.root/'runtime','shadow'))
  self.run_('live',limit=1);self.backend.store.offer_coordinator.assert_not_called()

 def test_pause_resume_recovers_saved_answer_with_exactly_one_provider_call(self):
  self.changed_answer(lambda:(self.state_dir/'PAUSED').touch())
  self.run_('live',limit=1);self.backend.store.offer_coordinator.assert_not_called()
  self.run_('live',limit=1);self.backend.store.offer_coordinator.assert_not_called()
  self.assertEqual(self.mocks['ask'].call_count,1)
  (self.state_dir/'PAUSED').unlink();self.run_('live',limit=1)
  self.assertEqual(self.mocks['ask'].call_count,1)
  self.assertEqual(self.backend.store.offer_coordinator.call_count,1)

 def test_membership_removal_during_answer_withholds_effects(self):
  self.changed_answer(lambda:self.registry['projects'][0].update(on_schedule=False))
  self.run_('live',limit=1);self.backend.store.offer_coordinator.assert_not_called()

 def test_pause_while_waiting_for_effect_lock_withholds_effect(self):
  original=K.effect_lock
  @contextmanager
  def delayed(project):
   with original(project):
    (self.state_dir/'PAUSED').touch();yield
  with mock.patch.object(K,'effect_lock',side_effect=delayed):self.run_('live',limit=1)
  self.backend.store.offer_coordinator.assert_not_called()

 def test_worker_model_change_during_answer_withholds_effects(self):
  self.changed_answer(lambda:self.mocks['dispatch_descriptor'].configure_mock(return_value=dict(fixtures.MODEL,digest='e'*64)))
  self.run_('live',limit=1);self.backend.store.offer_coordinator.assert_not_called()

 def test_admission_source_change_during_answer_withholds_effects(self):
  original=K.RP.components();changed=[False]
  self.changed_answer(lambda:changed.__setitem__(0,True))
  with mock.patch.object(K.RP,'components',side_effect=lambda:dict(original,**({'structured_worker.py':'f'*64} if changed[0] else {}))):self.run_('live',limit=1)
  self.backend.store.offer_coordinator.assert_not_called()

 def test_owner_can_overrule_an_inflight_decision_before_effects(self):
  original=self.mocks['ask'].side_effect;withdrawn=[]
  def ask(runtime,directory,prompt,timeout):
   answer=original(runtime,directory,prompt,timeout)
   withdrawn.append(K.overrule(self.root,directory.name,self.registry));return answer
  self.mocks['ask'].side_effect=ask;self.run_('live',limit=1)
  self.assertEqual(len(withdrawn),1);self.backend.store.offer_coordinator.assert_not_called()

 def test_owner_restore_after_retirement_survives_restart(self):
  self.backend.scopes=[s for s in self.backend.scopes if s['task_id']=='OLD']
  self.backend.store.snapshot.return_value={'scopes':self.backend.scopes}
  self.backend.event.side_effect=RuntimeError('interrupted after retirement')
  self.run_('live',limit=1);K.unretire(self.registry['projects'][0],'OLD')
  self.backend.event.side_effect=None;self.run_('live',limit=1)
  self.assertNotIn('OLD',self.backend.coordinator_retired())
  self.assertEqual(self.mocks['ask'].call_count,1)

 def test_malformed_withdrawal_history_cannot_revive_retirement(self):
  self.backend.scopes=[s for s in self.backend.scopes if s['task_id']=='OLD']
  self.backend.store.snapshot.return_value={'scopes':self.backend.scopes}
  with mock.patch.object(K,'apply',side_effect=RuntimeError('before retirement')):result=self.run_('live',limit=1)
  K.overrule(self.root,result['decided'][0]['decision_id'],self.registry)
  path=self.state_dir/'coordinator-withdrawals.json';path.write_text('{incomplete owner record')
  result=self.run_('live',limit=1)
  self.assertNotIn('OLD',self.backend.coordinator_retired())
  self.assertIn('withdrawal',result['decided'][0]['error'].lower())
  self.assertEqual(path.read_text(),'{incomplete owner record')
  self.assertEqual(self.mocks['ask'].call_count,1)

 def test_invalid_withdrawal_history_is_never_overwritten(self):
  path=self.state_dir/'coordinator-withdrawals.json'
  for content in ('{incomplete','[]','null','{"old":null}'):
   with self.subTest(content=content):
    path.write_text(content)
    with self.assertRaises(Exception) as caught:
     K.withdraw(self.registry['projects'][0],'a'*32,'OLD','retire')
    self.assertIsInstance(caught.exception,(RuntimeError,ValueError))
    self.assertEqual(path.read_text(),content)

 def test_symlink_withdrawal_history_is_held_without_touching_target(self):
  target=self.root/'outside-withdrawal.json';target.write_text('{}')
  path=self.state_dir/'coordinator-withdrawals.json';path.symlink_to(target)
  with self.assertRaisesRegex(RuntimeError,'withdrawal'):
   K.withdraw(self.registry['projects'][0],'a'*32,'OLD','retire')
  self.assertTrue(path.is_symlink());self.assertEqual(target.read_text(),'{}')

 def test_overrule_of_an_old_decision_does_not_remove_a_newer_retirement(self):
  K.append(self.root/'runtime',{'decision_id':'a'*32,'mode':'live','project':'exampleapp','task':'OLD','action':'retire'})
  path=self.state_dir/'coordinator-retired.json';current={'OLD':{'decision_id':'b'*32,'rationale':'Separate later retirement'}};path.write_text(json.dumps(current))
  K.overrule(self.root,'a'*32,self.registry)
  self.assertEqual(json.loads(path.read_text()),current)

 def test_concurrent_overrule_completes_while_provider_is_waiting(self):
  entered=threading.Event();release=threading.Event();original=self.mocks['ask'].side_effect;identity=[]
  def ask(runtime,directory,prompt,timeout):
   identity.append(directory.name);entered.set()
   if not release.wait(3):raise RuntimeError('test provider was not released')
   return original(runtime,directory,prompt,timeout)
  self.mocks['ask'].side_effect=ask
  with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
   future=pool.submit(self.run_,'live',limit=1)
   try:
    self.assertTrue(entered.wait(2));K.overrule(self.root,identity[0],self.registry)
   finally:release.set()
   result=future.result(timeout=3)
  self.assertIn('withdrew',result['decided'][0]['error'])
  self.backend.store.offer_coordinator.assert_not_called();self.assertEqual(self.mocks['ask'].call_count,1)

 def test_withdrawal_intent_blocks_reservation_before_advice_row_is_revoked(self):
  case=admission_fixtures.HarnessAdmissionTests();case.setUp();self.addCleanup(case.doCleanups)
  kwargs=case.semantic_advice();case.store.offer_narrowing(case.scope,case.model,**kwargs)
  self.assertTrue(case.store.admission(case.scope,case.model)['allowed'])
  (case.root/'coordinator-withdrawals.json').write_text(json.dumps({'d'*32:{'action':'guidance'}}))
  self.assertFalse(case.store.admission(case.scope,case.model)['allowed'])
  with self.assertRaises(K.RP.RecoveryHeld):case.store.reserve(case.scope,'after-withdrawal-intent',case.model)
  with case.store.store.reader() as con:
   self.assertEqual(con.execute('SELECT state FROM pr_coordinator_advice WHERE id=?',('d'*32,)).fetchone()[0],'active')
   self.assertEqual(con.execute('SELECT COUNT(*) FROM pr_launches').fetchone()[0],0)


if __name__=='__main__':unittest.main()
