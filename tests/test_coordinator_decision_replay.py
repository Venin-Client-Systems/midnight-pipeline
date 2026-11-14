"""A restart never renews a charged coordinator call or repeats completed effects."""
import datetime as dt,json,unittest
from unittest import mock
import coordinator as K
import progress_recovery as R
import test_coordinator as fixtures


class RestartTests(unittest.TestCase):
 setUp=fixtures.CoordinatorRunTests.setUp
 run_=fixtures.CoordinatorRunTests.run_

 def test_uncertain_provider_call_is_not_repeated_or_refunded(self):
  self.mocks['ask'].side_effect=RuntimeError('provider result unknown after interruption')
  self.run_('live',limit=1)
  K.set_mode(self.root/'runtime','live')
  path=self.root/'runtime/coordinator/config.json';config=json.loads(path.read_text());config['max_per_day']=1;path.write_text(json.dumps(config))
  self.run_('live',limit=1)
  self.assertEqual(self.mocks['ask'].call_count,1)
  self.backend.store.offer_coordinator.assert_not_called()

 def test_saved_answer_resumes_after_effect_failure_without_another_provider_call(self):
  with mock.patch.object(K,'apply',side_effect=RuntimeError('interrupted before effect')):self.run_('live',limit=1)
  self.run_('live',limit=1)
  self.assertEqual(self.mocks['ask'].call_count,1)
  self.assertEqual(self.backend.store.offer_coordinator.call_count,1)

 def test_completed_effect_is_not_repeated_when_event_emission_is_interrupted(self):
  events=[]
  def emit(kind,**fields):
   if not events:events.append('interrupted');raise RuntimeError('before event write')
   events.append({'event':kind,**fields})
  self.backend.event.side_effect=emit
  self.run_('live',limit=1);self.run_('live',limit=1)
  self.assertEqual(self.mocks['ask'].call_count,1)
  self.assertEqual(self.backend.store.offer_coordinator.call_count,1)
  self.assertEqual(len([e for e in events if isinstance(e,dict)]),1)

 def test_uncertain_notification_is_not_sent_twice(self):
  self.mocks['notify'].side_effect=RuntimeError('notification may already be visible')
  self.run_('live',limit=1);self.run_('live',limit=1)
  self.assertEqual(self.mocks['notify'].call_count,1)
  self.assertEqual(self.mocks['ask'].call_count,1)
  self.assertEqual(self.backend.store.offer_coordinator.call_count,1)

 def test_terminal_history_write_failure_does_not_repeat_effects(self):
  with mock.patch.object(K,'append',side_effect=RuntimeError('history append interrupted')):
   try:self.run_('live',limit=1)
   except RuntimeError:pass
  self.run_('live',limit=1)
  self.assertEqual(self.mocks['ask'].call_count,1)
  self.assertEqual(self.backend.store.offer_coordinator.call_count,1)
  self.assertEqual(self.mocks['notify'].call_count,1)

 def test_unjournaled_legacy_result_is_retained_and_never_requeried(self):
  escalation=K.escalations(self.registry['projects'][0],self.backend)[0]
  identity=K.fingerprint({'escalation':escalation['id'],'mode':'live'})[:32]
  directory=self.root/'runtime/coordinator/runs'/identity;directory.mkdir(parents=True)
  result=directory/'result.json';result.write_text(json.dumps(self.answers['R005F']));before=result.read_bytes()
  self.run_('live',limit=1)
  self.mocks['ask'].assert_not_called();self.backend.store.offer_coordinator.assert_not_called()
  self.assertEqual(result.read_bytes(),before)

 def test_committed_advice_recovers_when_its_effect_receipt_was_interrupted(self):
  store=R.Recovery(self.state_dir,'exampleapp');task=self.backend.tasks[0]
  scope=R.scope('exampleapp','original',task,'a'*40,'profile')
  store.enroll(scope,task,fixtures.MODEL,legacy_tries=1)
  run=self.state_dir/'runs/retained';run.mkdir(parents=True)
  (run/'worker-completion.json').write_text(json.dumps({'finished':True}))
  (run/'checks.json').write_text(json.dumps([{'code':1,'reason':'exited','command':['fixed-test']}]))
  store.observe(scope,'retained',run);store.wait(scope,'needs_codex','Original semantic failure needs diagnosis')
  self.backend.store=store;original=K.apply
  def interrupted(*args,**kwargs):original(*args,**kwargs);raise RuntimeError('effect committed before receipt')
  with mock.patch.object(K,'apply',side_effect=interrupted):self.run_('live',limit=1)
  self.assertEqual(K.escalations(self.registry['projects'][0],self.backend),[])
  with mock.patch.object(K,'apply',side_effect=AssertionError('must recover existing effect')):self.run_('live',limit=1)
  self.assertEqual(self.mocks['ask'].call_count,1);self.assertEqual(self.backend.event.call_count,1)
  self.assertEqual(self.mocks['notify'].call_count,1)
  with store.store.reader() as con:
   self.assertEqual(con.execute('SELECT COUNT(*) FROM pr_coordinator_advice').fetchone()[0],1)
   self.assertEqual(con.execute('SELECT COUNT(*) FROM pr_launches').fetchone()[0],0)

 def test_prepared_unlaunched_decision_still_obeys_current_daily_quota(self):
  K.set_mode(self.root/'runtime','live')
  with mock.patch.object(K,'continue_decision',side_effect=RuntimeError('after preparation before call intent')):
   with self.assertRaises(RuntimeError):self.run_('live',limit=1)
  K.append(self.root/'runtime',{'decision_id':'e'*32,'mode':'live','at':K.now().isoformat(),'escalation':'other','provider_charge':'spent'})
  path=self.root/'runtime/coordinator/config.json';value=json.loads(path.read_text());value['max_per_day']=1;path.write_text(json.dumps(value))
  self.run_('live',limit=1);self.mocks['ask'].assert_not_called()

 def test_prepared_yesterday_call_is_charged_today(self):
  K.set_mode(self.root/'runtime','live')
  path=self.root/'runtime/coordinator/config.json';config=json.loads(path.read_text());config['max_per_day']=1;path.write_text(json.dumps(config))
  yesterday=dt.datetime(2026,11,12,12,tzinfo=dt.timezone.utc);today=yesterday+dt.timedelta(days=1)
  with mock.patch.object(K,'now',return_value=yesterday),mock.patch.object(K,'continue_decision',side_effect=RuntimeError('before call intent')):
   with self.assertRaises(RuntimeError):self.run_('live',limit=1)
  with mock.patch.object(K,'now',return_value=today):
   self.run_('live',limit=1);self.run_('live',limit=1)
  self.assertEqual(self.mocks['ask'].call_count,1)
  record=K.decisions(self.root/'runtime',None)[-1]
  self.assertEqual(record['at'],yesterday.isoformat());self.assertEqual(record['charged_at'],today.isoformat())

 def test_uncertain_held_decision_does_not_starve_other_eligible_work(self):
  original=self.mocks['ask'].side_effect
  self.mocks['ask'].side_effect=RuntimeError('uncertain provider completion')
  self.run_('live',limit=1);self.mocks['ask'].side_effect=original
  for _ in range(3):self.run_('live',limit=1)
  self.assertEqual(self.mocks['ask'].call_count,2)
  self.assertEqual(self.asked,['OLD']);self.assertIn('OLD',self.backend.coordinator_retired())

 def test_bounded_reconciliation_rotates_past_held_journal_to_saved_answer(self):
  with mock.patch.object(K,'apply',side_effect=RuntimeError('before effects')):self.run_('live',limit=2)
  original=K.apply
  def apply(project,backend,escalation,*args,**kwargs):
   if escalation['task']['id']=='R005F':raise RuntimeError('retained hold')
   return original(project,backend,escalation,*args,**kwargs)
  with mock.patch.object(K,'apply',side_effect=apply) as effects:
   for _ in range(2):self.run_('live',limit=1)
   self.assertEqual(effects.call_count,2)
  self.assertIn('OLD',self.backend.coordinator_retired());self.assertEqual(self.mocks['ask'].call_count,2)
  self.backend.store.offer_coordinator.assert_not_called();self.assertEqual(self.mocks['notify'].call_count,1)

 def test_event_written_before_phase_receipt_is_detected_without_duplicate(self):
  path=self.state_dir/'events.jsonl'
  def emit(kind,**fields):
   with path.open('a') as stream:stream.write(json.dumps({'event':kind,**fields})+'\n')
   raise RuntimeError('after committed event before phase receipt')
  self.backend.event.side_effect=emit
  self.run_('live',limit=1);self.run_('live',limit=1)
  self.assertEqual(self.backend.event.call_count,1);self.assertEqual(len(path.read_text().splitlines()),1)
  self.assertEqual(self.backend.store.offer_coordinator.call_count,1);self.assertEqual(self.mocks['notify'].call_count,1)


if __name__=='__main__':unittest.main()
