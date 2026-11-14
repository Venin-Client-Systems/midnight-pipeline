import copy
from pathlib import Path
import tempfile
import unittest

from quality_cycle import QualityCycle, QualityGateError


def profile():
    return {'schema_version': 1, 'id': 'synthetic', 'version': 1, 'owner': 'coordinator',
            'capabilities': [{'id': 'shipped', 'status': 'shipped'}],
            'contracts': [{'id': 'stable', 'path': 'CONTRACT.md'}],
            'checks': [{'id': 'regression', 'required': True, 'command': ['fake']}],
            'workflows': [{'id': 'operator', 'check_ids': ['regression']}], 'metrics': [],
            'freshness_seconds': 86400}


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name)
        self.now = 1000000
        self.cycle = QualityCycle(self.runtime, 'alpha', profile(), clock=lambda: self.now)

    def assess(self, kind='baseline', head='head-a', outcome='passed'):
        return self.cycle.record_assessment(head, kind, outcome, {'receipt': 'host-check.json'},
            [{'id': 'regression', 'status': 'passed' if outcome in ('passed', 'no_change') else 'failed', 'evidence': 'full.log'}])

    def finding(self, fid='bug'):
        return self.cycle.upsert_finding({'id': fid, 'title': 'Broken shipped contract',
            'material': True, 'evidence': {'receipt': 'failure.json'}})

    def test_read_only_missing_baseline_and_missing_checks(self):
        self.assertFalse(self.cycle.status('head-a')['feature_allowed'])
        self.assertFalse((self.runtime / 'history.sqlite3').exists())
        with self.assertRaises(QualityGateError):
            self.cycle.record_assessment('head-a', 'baseline', 'passed', 'claim', [])
        self.assertEqual(self.cycle.next_decision('head-a')['action'], 'assess')

    def test_two_fake_projects_are_isolated(self):
        self.assess()
        other = QualityCycle(self.runtime, 'beta', profile(), clock=lambda: self.now)
        other.record_assessment('head-b', 'baseline', 'passed', 'b.json',
                                [{'id': 'regression', 'status': 'passed', 'evidence': 'b.log'}])
        self.finding()
        self.assertFalse(self.cycle.status('head-a')['feature_allowed'])
        self.assertTrue(other.status('head-b')['feature_allowed'])
        ticket = other.issue_feature_ticket('head-b', 'T1')
        self.assertFalse(self.cycle.revalidate_ticket(ticket, 'head-b', 'T1')['valid'])
        self.assertEqual(other.findings(), [])

    def test_exhausted_repair_blocks_every_feature_and_independent_repair_runs(self):
        self.finding('stuck'); self.finding('independent'); self.assess()
        tasks = [{'id': 'R1', 'kind': 'repair', 'finding_ids': ['stuck'], 'exhausted': True},
                 {'id': 'F1', 'kind': 'feature'},
                 {'id': 'R2', 'kind': 'repair', 'finding_ids': ['independent']}]
        choice = self.cycle.next_decision('head-a', tasks)
        self.assertEqual(choice['task']['id'], 'R2')
        self.assertEqual(choice['action'], 'implement_locally')
        self.assertEqual(self.cycle.next_decision('head-a', tasks[:2])['action'], 'needs_manager')
        with self.assertRaises(QualityGateError):
            self.cycle.issue_feature_ticket('head-a', 'F1')

    def test_stale_unavailable_and_failed_assessments_never_admit_features(self):
        self.assess(); self.now += 86400
        self.assertIn('assessment_stale', self.cycle.status('head-a')['reasons'])
        self.assess('daily', outcome='unavailable')
        self.assertFalse(self.cycle.status('head-a')['feature_allowed'])
        self.assess('daily', outcome='failed')
        self.assertFalse(self.cycle.status('head-a')['feature_allowed'])
        self.assess('daily', outcome='no_change')
        self.assertTrue(self.cycle.status('head-a')['feature_allowed'])

    def test_ticket_exact_source_profile_findings_and_one_use(self):
        self.assess()
        ticket = self.cycle.issue_feature_ticket('head-a', 'F1')
        self.assertTrue(self.cycle.revalidate_ticket(ticket, 'head-a', 'F1')['valid'])
        self.assertFalse(self.cycle.revalidate_ticket(ticket, 'head-b', 'F1')['valid'])
        self.assertFalse(self.cycle.revalidate_ticket(ticket, 'head-a', 'F2')['valid'])
        tampered = dict(ticket, task_id='F2')
        self.assertIn('ticket_tampered', self.cycle.revalidate_ticket(tampered, 'head-a', 'F1')['reasons'])
        different = profile(); different['version'] = 2
        updated = QualityCycle(self.runtime, 'alpha', different, clock=lambda: self.now)
        self.assertIn('profile_changed', updated.revalidate_ticket(ticket, 'head-a', 'F1')['reasons'])
        self.cycle.consume_ticket(ticket, 'head-a', 'F1')
        with self.assertRaises(QualityGateError):
            self.cycle.consume_ticket(ticket, 'head-a', 'F1')

    def test_new_finding_invalidates_existing_ticket_even_after_resolution(self):
        self.assess(); ticket = self.cycle.issue_feature_ticket('head-a', 'F1')
        self.finding()
        self.cycle.resolve_finding('bug', {'accepted_commit': 'commit-fix', 'checks_passed': True, 'review_approved': True})
        self.assess('after_accepted')
        self.assertTrue(self.cycle.status('head-a')['feature_allowed'])
        self.assertIn('findings_changed', self.cycle.revalidate_ticket(ticket, 'head-a', 'F1')['reasons'])

    def test_finding_close_and_recurrence_require_evidence(self):
        self.finding()
        with self.assertRaises(QualityGateError):
            self.cycle.resolve_finding('bug', {'claim': 'fixed'})
        with self.assertRaises(QualityGateError):
            self.cycle.reopen_finding('bug', {'head': 'new', 'regression': 'log'})
        self.cycle.resolve_finding('bug', {'accepted_commit': 'fixed', 'checks_passed': True, 'review_approved': True})
        with self.assertRaises(QualityGateError):
            self.finding()
        again = self.cycle.reopen_finding('bug', {'head': 'regression', 'regression': 'reproduced.log'})
        self.assertEqual(again['episode_id'], 'bug:2')
        self.assertEqual(again['previous_episode_id'], 'bug:1')

    def test_assessment_cannot_downgrade_open_material_debt_without_fix_evidence(self):
        first = self.finding()
        result = self.cycle.upsert_finding({'id': 'bug', 'material': False, 'severity': 'minor',
            'title': 'Renamed as minor', 'evidence': {'receipt': 'later-opinion.json'}})
        self.assertTrue(result['material'])
        self.assertEqual(result['severity'], first['severity'])
        self.assertEqual(result['episode_id'], first['episode_id'])
        self.assess()
        status = self.cycle.status('head-a')
        self.assertEqual(status['material_finding_count'], 1)
        self.assertFalse(status['feature_allowed'])
        with self.assertRaises(QualityGateError):
            self.cycle.issue_feature_ticket('head-a', 'F1')
        self.cycle.resolve_finding('bug', {'accepted_commit': 'fix', 'checks_passed': True, 'review_approved': True})
        self.assess('after_accepted')
        self.assertTrue(self.cycle.status('head-a')['feature_allowed'])

    def test_rename_and_split_preserve_episode_and_cannot_reset_attempt_lineage(self):
        self.finding()
        first = self.cycle.admit_task({'id': 'R1', 'kind': 'repair', 'finding_ids': ['bug']})
        renamed = self.cycle.admit_task({'id': 'R2', 'kind': 'repair', 'retry_of': {'task_id': 'R1'}})
        split = self.cycle.admit_task({'id': 'R3', 'kind': 'repair'}, source_task='R1')
        self.assertEqual(first['episode_id'], renamed['episode_id'])
        self.assertEqual(first['episode_id'], split['episode_id'])
        with self.assertRaises(QualityGateError):
            self.cycle.admit_task(dict(renamed, episode_id='bug:2'))

    def test_assessment_due_windows_are_rolling_7_and_30_days(self):
        self.assess(); self.now += 7 * 86400
        self.assess('daily')
        self.assertIn('weekly', self.cycle.status('head-a')['assessment_due'])
        self.assess('weekly'); self.now += 23 * 86400
        self.assess('daily'); self.assess('weekly')
        self.assertEqual(self.cycle.status('head-a')['assessment_due'], ['monthly'])
        self.assess('monthly')
        self.assertTrue(self.cycle.status('head-a')['feature_allowed'])

    def test_no_work_does_not_create_an_accepted_achievement(self):
        self.assess()
        choice = self.cycle.next_decision('head-a', [])
        self.assertEqual(choice['action'], 'wait')
        self.assertFalse(choice['accepted_achievement'])
        self.assertEqual(self.cycle.store.counters('alpha')['accepted'], 0)
        self.assertEqual(self.cycle.store.query('alpha', kind='idle_assessment')[0]['payload']['outcome'], 'no_useful_work')

    def test_assessment_replay_does_not_refresh_health_or_mutate_lineage(self):
        checks = [{'id': 'regression', 'status': 'passed', 'evidence': 'full.log'}]
        first = self.cycle.record_assessment('head-a', 'baseline', 'passed', 'receipt.json', checks, assessment_id='once')
        self.now += 90000
        replay = self.cycle.record_assessment('head-a', 'baseline', 'passed', 'receipt.json', checks, assessment_id='once')
        self.assertEqual(first, replay)
        self.assertFalse(self.cycle.status('head-a')['feature_allowed'])
        with self.assertRaises(ValueError):
            self.cycle.record_assessment('head-a', 'baseline', 'failed', 'receipt.json', checks, assessment_id='once')

    def test_repair_can_run_without_baseline_and_cloud_needs_explicit_eligibility(self):
        self.finding()
        repair = {'id': 'R1', 'kind': 'repair', 'finding_ids': ['bug']}
        self.assertEqual(self.cycle.next_decision('head-a', [repair])['action'], 'implement_locally')
        repair['exhausted'] = True
        self.assertEqual(self.cycle.next_decision('head-a', [repair], cloud_available=True)['action'], 'assess')
        repair['rescue_eligible'] = True
        self.assertEqual(self.cycle.next_decision('head-a', [repair], cloud_available=True)['action'], 'cloud_rescue')

    def test_bound_maintenance_and_independent_research_do_not_hide_material_debt(self):
        self.finding()
        maintenance = {'id': 'M1', 'kind': 'maintenance', 'finding_ids': ['bug']}
        self.assertEqual(self.cycle.next_decision('head-a', [maintenance])['task']['id'], 'M1')
        self.assess()
        tasks = [{'id': 'F1', 'kind': 'feature'}, {'id': 'S1', 'kind': 'research'}]
        self.assertEqual(self.cycle.next_decision('head-a', tasks)['task']['id'], 'S1')
        self.assertFalse(self.cycle.status('head-a')['feature_allowed'])

    def test_failed_feature_rescue_requires_manager_without_blocking_independent_tasks(self):
        self.assess()
        held = {'id': 'F1', 'kind': 'feature', 'exhausted': True, 'needs_manager': True}
        self.assertEqual(self.cycle.next_decision('head-a', [held])['action'], 'needs_manager')
        choice = self.cycle.next_decision('head-a', [held, {'id': 'F2', 'kind': 'feature'}])
        self.assertEqual(choice['action'], 'implement_locally')
        self.assertEqual(choice['task']['id'], 'F2')

    def test_canonical_advance_requires_reassessment_and_expired_ticket_fails(self):
        self.assess()
        ticket = self.cycle.issue_feature_ticket('head-a', 'F1', ttl_seconds=10)
        self.assertFalse(self.cycle.status('head-b')['feature_allowed'])
        self.assess('after_accepted', 'head-b')
        self.assertTrue(self.cycle.status('head-b')['feature_allowed'])
        self.now += 10
        self.assertIn('ticket_expired', self.cycle.revalidate_ticket(ticket, 'head-a', 'F1')['reasons'])


if __name__ == '__main__':
    unittest.main()
