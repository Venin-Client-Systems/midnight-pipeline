from pathlib import Path
import tempfile
import unittest
from action_store import Actions


class ActionsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'runtime';self.time=1000
        self.store=Actions(self.root,clock=lambda:self.time)

    def test_read_paths_create_nothing(self):
        self.assertEqual(self.store.jobs(),[]);self.assertEqual(self.store.phases(),[])
        self.assertEqual(self.store.incidents(),[]);self.assertEqual(self.store.events(),[])
        self.assertFalse(self.root.exists())

    def test_read_only_observation_reports_corruption_without_resetting_history(self):
        import action_store as A
        self.root.mkdir();path=self.root/'workflow.sqlite3';payload=b'preserve corrupt bytes';path.write_bytes(payload)
        self.assertFalse(A.observe(self.root)['available']);self.assertEqual(path.read_bytes(),payload)

    def test_launch_charges_are_unique_and_belong_to_the_original_session(self):
        job=self.store.submit('notes',{'session':'old'},2000);self.store.claim(job['id'],{'pid':1})
        self.store.charge(job['id'],'run','local');self.store.charge(job['id'],'run','local')
        self.assertEqual(self.store.session_charges('old','notes'),{'local':1})
        self.assertEqual(self.store.session_charges('new','notes'),{})

    def test_submission_and_claim_allow_one_owned_candidate_per_project(self):
        one=self.store.submit('notes',{'exact':'input'},2000)
        two=self.store.submit('notes',{'different':'input'},2000)
        self.assertEqual(one['id'],two['id']);self.assertEqual(two['payload'],{'exact':'input'})
        lease={'pid':123,'stamp':'owned','boot_id':'boot'}
        self.assertTrue(self.store.claim(one['id'],lease));self.assertFalse(self.store.claim(one['id'],lease))
        with self.assertRaises(ValueError):self.store.finish(one['id'],'succeeded',{},lease={'pid':999})
        self.store.finish(one['id'],'succeeded',{'returncode':0},lease=lease)
        self.store.finish(one['id'],'failed',{'late':True},lease=lease)
        self.assertEqual(self.store.get(one['id'])['state'],'succeeded')
        self.assertNotEqual(self.store.submit('notes',{},2000)['id'],one['id'])

    def test_phase_identity_and_history_survive_action_restart(self):
        job=self.store.submit('notes',{},2000)
        one=self.store.phase(job['id'],'notes','local','same-input',1500,{'log':'private'})
        self.assertEqual(one['id'],self.store.phase(job['id'],'notes','local','same-input',1500,{})['id'])
        self.store.phase_update(one['id'],'running',lease={'pid':1},progress=True,deadline=1600)
        self.time+=30;self.store.phase_update(one['id'],'succeeded',result={'code':0},progress=True)
        self.store.finish(job['id'],'interrupted',{'reason':'controller_restart'})
        second=self.store.submit('notes',{},2000)
        two=self.store.phase(second['id'],'notes','local','same-input',1700,{})
        self.assertNotEqual(one['id'],two['id'])
        old=next(p for p in self.store.phases(active=False) if p['id']==one['id'])
        self.assertEqual(old['state'],'succeeded');self.assertEqual(old['result']['code'],0)

    def test_incident_and_four_attempt_limit_cannot_reset_on_evidence_or_restart(self):
        case=self.store.incident('midnight','transition','stable-root','evidence-one',{'reason':'actual defect'})
        for number in range(1,5):
            repair=self.store.reserve_repair(case,{'attempt':number})
            self.assertEqual(self.store.repair(repair)['number'],number)
            self.store.repair(repair,state='failed',payload={'tests':'failed'})
        self.assertIsNone(self.store.reserve_repair(case,{}))
        restarted=Actions(self.root,clock=lambda:self.time)
        self.assertEqual(restarted.incident('midnight','transition','stable-root','new-evidence',{}),case)
        current=restarted.incidents()[0]
        self.assertEqual(current['attempts'],4);self.assertEqual(current['state'],'held')
        with self.assertRaises(ValueError):restarted.reserve_repair(case,{})

    def test_unknown_launch_remains_charged_and_holds_single_writer_lane(self):
        case=self.store.incident('midnight','error','root','evidence',{})
        repair=self.store.reserve_repair(case,{})
        self.store.repair(repair,state='unknown')
        with self.assertRaises(ValueError):self.store.reserve_repair(case,{})
        self.assertEqual(self.store.incidents()[0]['attempts'],1)

    def test_stale_repair_cannot_revive_or_replace_a_new_writer(self):
        case=self.store.incident('midnight','error','root','one',{})
        first=self.store.reserve_repair(case,{})
        owner={'pid':1,'stamp':'first'}
        self.store.repair(first,state='running',lease=owner)
        self.store.repair(first,state='failed',expected_lease=owner)
        second=self.store.reserve_repair(case,{})
        self.store.repair(second,state='running',lease={'pid':2,'stamp':'second'})
        with self.assertRaises(ValueError):self.store.repair(first,state='running',lease=owner)
        self.assertEqual([r['id'] for r in self.store.repairs()],[second])
        with self.assertRaises(ValueError):self.store.repair(second,state='running',lease={'pid':3})

    def test_terminal_phase_rejects_delayed_owner_and_new_result(self):
        job=self.store.submit('notes',{},2000)
        phase=self.store.phase(job['id'],'notes','host','check',1500,{})
        owner={'pid':1};self.store.phase_update(phase['id'],'running',lease=owner)
        self.store.phase_update(phase['id'],'succeeded',expected_lease=owner,result={'code':0})
        for state in ('running','failed'):
            with self.assertRaises(ValueError):self.store.phase_update(phase['id'],state,lease={'pid':99})

    def test_recurring_incident_reopens_without_refunding_budget(self):
        case=self.store.incident('midnight','error','root','one',{})
        repair=self.store.reserve_repair(case,{})
        self.store.repair(repair,state='failed')
        self.store.incident_state(case,'resolved','Evidence recovered')
        self.assertEqual(self.store.incident('midnight','error','root','two',{}),case)
        current=self.store.incidents()[0]
        self.assertEqual((current['state'],current['attempts']),('open',1))


if __name__=='__main__':unittest.main()
