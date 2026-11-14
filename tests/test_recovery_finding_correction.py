import hashlib
import tempfile
from pathlib import Path
import unittest
from quality_cycle import QualityCycle,QualityGateError
from history_store import encoded
from tests.test_quality_cycle import profile


class HostFindingCorrectionTests(unittest.TestCase):
    def test_correction_is_explicit_cas_bound_audited_and_is_not_an_achievement(self):
        with tempfile.TemporaryDirectory() as tmp:
            q=QualityCycle(Path(tmp),'notes',profile())
            finding=q.upsert_finding({'id':'host-check-regression','evidence':['retained.log'],'material':True})
            digest=hashlib.sha256(encoded(finding['evidence']).encode()).hexdigest()
            proof={'classification':'host_orchestration_error','independent_review_approved':True,
                'all_episode_observations_checked':True,'observations':[{'phase':'verified_timeout','hash':'a'*64}],
                'same_source_pass':{'head':'source','profile_hash':q.profile_hash,'receipt':'b'*64},'review_receipt_sha256':'c'*64,
                'observation_stamp':q.finding_observation_stamp(finding['id'])}
            with self.assertRaises(QualityGateError):q.invalidate_host_finding(finding['id'],'wrong:1',digest,proof)
            with self.assertRaises(QualityGateError):q.invalidate_host_finding(finding['id'],finding['episode_id'],'d'*64,proof)
            bad=dict(proof);bad.pop('all_episode_observations_checked')
            with self.assertRaises(QualityGateError):q.invalidate_host_finding(finding['id'],finding['episode_id'],digest,bad)
            result=q.invalidate_host_finding(finding['id'],finding['episode_id'],digest,proof)
            self.assertEqual(result['state'],'invalidated');self.assertEqual(result['episode_id'],finding['episode_id'])
            self.assertNotIn('resolution',result);self.assertNotIn('accepted_commit',result)
            self.assertEqual(q.status('source')['material_finding_count'],0)
            self.assertEqual(q.store.counters('notes')['accepted'],0)
            events=q.store.query('notes',kind='finding_observed');self.assertEqual(len(events),1)
            self.assertEqual(q.invalidate_host_finding(finding['id'],finding['episode_id'],digest,proof),result)
            with self.assertRaises(QualityGateError):q.upsert_finding({'id':finding['id'],'evidence':['guess.log']})
            reopened=q.reopen_finding(finding['id'],{'head':'later','regression':['actual-assertion.log']})
            self.assertEqual(reopened['episode'],2);self.assertNotIn('invalidation',reopened)

    def test_an_old_contract_cannot_own_a_later_regression_even_without_a_closed_tick(self):
        import project_backend as B
        with tempfile.TemporaryDirectory() as tmp:
            q=QualityCycle(Path(tmp),'notes',profile())
            q.upsert_finding({'id':'bug','evidence':['original.log']})
            task={'id':'original','kind':'repair','finding_ids':['bug'],'issue_episode':'bug:1'}
            state={};self.assertTrue(B.current_repair_contract(task,state,q))
            q.resolve_finding('bug',{'accepted_commit':'source','checks_passed':True,'review_approved':True})
            q.reopen_finding('bug',{'head':'new-source','regression':['actual-failure.log']})
            self.assertFalse(B.current_repair_contract(task,state,q))
            self.assertIn('original',state['retired_repair_contracts'])
            later=dict(task,id='later',issue_episode='bug:2')
            self.assertTrue(B.current_repair_contract(later,state,q))

    def test_actual_product_defect_cannot_use_host_correction_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            q=QualityCycle(Path(tmp),'notes',profile())
            q.upsert_finding({'id':'product-contract','evidence':['failed.log']})
            with self.assertRaises(QualityGateError):q.invalidate_host_finding('product-contract','product-contract:1','a'*64,{})

    def test_new_observation_then_repeated_old_evidence_cannot_escape_snapshot_cas(self):
        with tempfile.TemporaryDirectory() as tmp:
            q=QualityCycle(Path(tmp),'notes',profile())
            finding=q.upsert_finding({'id':'host-check-regression','evidence':['timeout.log']})
            digest=hashlib.sha256(encoded(finding['evidence']).encode()).hexdigest()
            proof={'classification':'host_orchestration_error','independent_review_approved':True,
                'all_episode_observations_checked':True,'observations':['reviewed timeout'],
                'same_source_pass':{'receipt':'pass'},'review_receipt_sha256':'c'*64,
                'observation_stamp':q.finding_observation_stamp(finding['id'])}
            q.upsert_finding({'id':finding['id'],'evidence':['actual-assertion-failure.log']})
            q.upsert_finding({'id':finding['id'],'evidence':['timeout.log']})
            with self.assertRaises(QualityGateError):q.invalidate_host_finding(finding['id'],finding['episode_id'],digest,proof)
            self.assertEqual(q.get_finding(finding['id'])['state'],'open')


if __name__=='__main__':unittest.main()
