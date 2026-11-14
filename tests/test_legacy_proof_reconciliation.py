"""Legacy proof migration preserves immutable charges and requires adopted repairs."""
import concurrent.futures,json,tempfile,unittest
from pathlib import Path
from unittest import mock
import progress_recovery as R
import worker_recovery as W
import action_store as A
import git_delivery as G


class LegacyProofTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
  self.task={'id':'retained','files':['solution.py'],'acceptance':['Preserve the original contract']}
  self.model={'model':'fixture','digest':'a'*64}
  self.scope=R.scope('one','original',self.task,'a'*40,'profile');self.store=R.Recovery(self.root,'one')
  self.store.enroll(self.scope,self.task,self.model,legacy_tries=5,legacy_runs=['legacy-unknown'])
  self.proofs=[]
  with self.store.store.transaction() as con:
   for index in range(3):
    proof=self.store._offer(con,self.scope,'coordinator_guidance','coordinator-'+str(index)*32,self.model,{'instruction':'Retained legacy advice','refs':[]});self.proofs.append(proof)
    if index<2:
     run=('completed','unknown-after-crash')[index]
     launch={'scope':self.scope,'run':run,'proof':proof,'kind':'coordinator_guidance','model':self.model,'charged':True}
     con.execute('INSERT INTO pr_launches VALUES(?,?,?,?,?)',(run,self.scope['id'],proof,R.encoded(launch),1))
   con.execute('DROP TABLE pr_proof_holds');con.execute('DROP TABLE pr_coordinator_advice')

 def rows(self,table):
  with self.store.store.reader() as con:return [dict(row) for row in con.execute('SELECT * FROM '+table+' ORDER BY rowid')]

 def test_readonly_inventory_lists_legacy_proofs_without_migration(self):
  before=self.rows('qc_events');inventory=self.store.coordinator_proof_inventory()
  self.assertEqual(inventory['total'],3);self.assertEqual(inventory['consumed'],2);self.assertEqual(inventory['unconsumed'],1)
  self.assertEqual(inventory['held'],0);self.assertEqual(self.rows('qc_events'),before)
  with self.store.store.reader() as con:self.assertIsNone(con.execute("SELECT 1 FROM sqlite_master WHERE name='pr_proof_holds'").fetchone())

 def test_reconciliation_preserves_all_original_rows_and_is_idempotent(self):
  tables=('pr_scopes','pr_proofs','pr_launches','pr_results','pr_diagnoses');before={t:self.rows(t) for t in tables};events=self.rows('qc_events')
  result=self.store.reconcile_coordinator_proofs()
  self.assertEqual(result['new_holds'],[self.proofs[2]])
  self.assertEqual(result['inventory']['held'],1);self.assertEqual(result['inventory']['consumed'],2)
  self.assertEqual(self.store.reconcile_coordinator_proofs()['new_holds'],[])
  self.assertEqual({t:self.rows(t) for t in tables},before)
  current=self.rows('qc_events');self.assertEqual(current[:len(events)],events);self.assertEqual(len(current),len(events)+1)
  self.assertEqual(self.rows('pr_proof_holds')[0]['reason'],'legacy_coordinator_requires_review')
  self.assertFalse(self.store.admission(self.scope,self.model)['allowed'])
  with self.assertRaises(R.RecoveryHeld):self.store.reserve(self.scope,'repeat',self.model)

 def test_crash_before_journal_commit_rolls_back_the_hold(self):
  with mock.patch.object(self.store.store,'append',side_effect=RuntimeError('simulated interruption')):
   with self.assertRaisesRegex(RuntimeError,'simulated interruption'):self.store.reconcile_coordinator_proofs()
  self.assertEqual(self.store.coordinator_proof_inventory()['held'],0)
  self.assertEqual(self.store.reconcile_coordinator_proofs()['new_holds'],[self.proofs[2]])

 def test_reconciliation_does_not_cross_project_or_create_a_missing_store(self):
  other=R.Recovery(self.root,'two');scope=R.scope('two','separate',self.task,'b'*40,'profile')
  other.enroll(scope,self.task,self.model,legacy_tries=1)
  with other.store.transaction() as con:proof=other._offer(con,scope,'coordinator_guidance','other',self.model,{'refs':[]})
  self.store.reconcile_coordinator_proofs()
  self.assertEqual(other.coordinator_proof_inventory()['held'],0)
  self.assertNotIn(proof,[row['proof'] for row in self.rows('pr_proof_holds')])
  missing=R.Recovery(self.root/'missing','one');self.assertEqual(missing.reconcile_coordinator_proofs()['new_holds'],[])
  self.assertFalse((self.root/'missing').exists())

 def test_concurrent_reconciliation_adds_each_hold_and_event_once(self):
  before=self.rows('qc_events')
  with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
   results=list(pool.map(lambda _:self.store.reconcile_coordinator_proofs(),range(2)))
  self.assertEqual(sum(len(value['new_holds']) for value in results),1)
  self.assertEqual(len(self.rows('qc_events')),len(before)+1)
  self.assertEqual(len(self.rows('pr_launches')),2)

 def test_launch_committed_before_migration_transaction_remains_consumed(self):
  inventory=self.store.coordinator_proof_inventory()
  def older_inflight_launch():
   with self.store.store.transaction() as con:
    value={'scope':self.scope,'run':'legacy-inflight','proof':self.proofs[2],'kind':'coordinator_guidance','model':self.model,'charged':True}
    con.execute('INSERT INTO pr_launches VALUES(?,?,?,?,?)',('legacy-inflight',self.scope['id'],self.proofs[2],R.encoded(value),2))
   return inventory
  with mock.patch.object(self.store,'coordinator_proof_inventory',side_effect=older_inflight_launch):result=self.store.reconcile_coordinator_proofs()
  self.assertEqual(result['new_holds'],[]);self.assertEqual(result['inventory']['consumed'],3)
  self.assertEqual(self.rows('pr_proof_holds'),[])


class RepairCertificateTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
  self.model={'model':'fixture','digest':'a'*64};self.path=self.root/'qualification.json';self.path.write_text('{"fixture":"qualification verifier is separately tested"}')
  self.qualified={'path':str(self.path),'sha256':R.ref(self.path)['sha256'],'model':self.model,'worker_sources':{n:R.components()[n] for n in W.SOURCES},'contract':'fixture'}
  self.certificate={'kind':'verified_harness_fix','project':'one','model':self.model,'worker_sources':self.qualified['worker_sources'],
   'from_components':{n:'f'*64 for n in R.components()},'failure_kinds':['inference_truncated'],
   'qualification':self.qualified,'refs':[R.ref(self.path)],'review':{'decision':'accept','issues':[]},
   'installed_source':'a'*40,'coordinator_runtime':str(self.root),'instruction':'Apply the qualified repair under the unchanged contract.'}
  self.health={'state':'verified','installed':'a'*40,'tree':'b'*40}
  self.sign()
  patch=mock.patch.object(W,'qualification',return_value=self.qualified);patch.start();self.addCleanup(patch.stop)
  patch=mock.patch.object(A.Actions,'meta',side_effect=lambda key:self.health);patch.start();self.addCleanup(patch.stop)
  patch=mock.patch.object(G,'git',side_effect=lambda root,*args:'b'*40 if args[-1]=='HEAD^{tree}' else 'a'*40);patch.start();self.addCleanup(patch.stop)

 def sign(self):self.certificate['id']=R.fingerprint({k:v for k,v in self.certificate.items() if k!='id'})

 def test_exact_healthy_registered_certificate_validates(self):R.validate_qualification(self.certificate)

 def test_unknown_generation_or_changed_registration_cannot_admit(self):
  original=dict(self.certificate)
  for key,value in [('installed_source',None),('coordinator_runtime',None),('id','0'*64),('from_components',R.components()),('worker_sources',{'wrong':'e'*64})]:
   with self.subTest(key=key):
    self.certificate=dict(original);self.certificate[key]=value
    if key!='id':self.sign()
    with self.assertRaises((R.RecoveryHeld,ValueError)):R.validate_qualification(self.certificate)

 def test_stale_or_unverified_installed_tree_cannot_admit(self):
  for health in ({'state':'verifying','installed':'a'*40,'tree':'b'*40},{'state':'verified','installed':'c'*40,'tree':'b'*40},{'state':'verified','installed':'a'*40,'tree':'c'*40},{'state':'verified','installed':'a'*40}):
   with self.subTest(health=health):
    self.health=health
    with self.assertRaises((R.RecoveryHeld,ValueError)):R.validate_qualification(self.certificate)

 def test_publisher_can_retain_a_pending_certificate_without_admitting_it(self):
  self.certificate.pop('id');self.health={'state':'verifying','installed':'a'*40,'tree':'b'*40}
  saved=R.register_certificate(self.root,self.certificate)
  self.assertEqual(saved['id'],R.fingerprint(self.certificate))
  self.assertTrue((self.root/'progress-certificates'/(saved['id']+'.json')).is_file())
  self.assertEqual(R.certificates(self.root,'inference_truncated',self.model),[])

 def test_malformed_legacy_certificate_files_stay_held_without_reader_writes(self):
  folder=self.root/'progress-certificates';folder.mkdir()
  for index,value in enumerate((None,[],{'id':'x','failure_kinds':None},dict(self.certificate,failure_kinds='inference_truncated'))):
   with self.subTest(value=value):
    path=folder/(str(index)+'.json');path.write_text(json.dumps(value));before={p.name:p.read_bytes() for p in folder.iterdir()}
    self.assertEqual(R.certificates(self.root,'inference_truncated',self.model),[])
    self.assertEqual({p.name:p.read_bytes() for p in folder.iterdir()},before)


if __name__=='__main__':unittest.main()
