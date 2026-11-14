import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import profile_manager as P
from quality_cycle import QualityCycle, QualityGateError, profile_hash


def static_profile():
    return {'schema_version': 1, 'id': 'synthetic', 'version': 2, 'owner': 'coordinator',
            'capabilities': [{'id': 'original', 'status': 'shipped'}],
            'contracts': [{'id': 'original', 'path': 'src/original.py', 'requirements': ['Preserve original behaviour']}],
            'checks': [{'id': 'full', 'required': True, 'command': ['python3', '-m', 'unittest', 'discover', '-v']},
                       {'id': 'cli', 'required': True, 'command': ['python3', '-m', 'tool', '--help']}],
            'workflows': [{'id': 'original', 'check_ids': ['full']}], 'metrics': []}


def task(identity='F1', source='src/feature.py', test='tests/test_feature.py'):
    return {'id': identity, 'kind': 'feature', 'title': 'Accepted feature', 'files': [source, test],
            'description': 'Implement a bounded useful product behaviour with meaningful regressions'}


def proposal(identity='feature', source='src/feature.py', test='tests/test_feature.py'):
    return {'capability': {'id': identity, 'title': 'Accepted feature',
                          'description': 'Concrete accepted behaviour covered by the committed regression test', 'source_paths': [source]},
            'contracts': [{'id': identity, 'path': source, 'requirements': ['Preserve the accepted valid and invalid input behaviour']}],
            'workflow': {'id': identity, 'check_ids': ['full']},
            'coverage': [{'source_path': source, 'test_path': test, 'check_id': 'full',
                          'explanation': 'The accepted regression asserts valid and invalid results of the new public function'}]}


class ProfileManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name)
        self.static = static_profile()
        self.commit = 'a' * 40

    def queue(self, identity='p'):
        return P.queue_update(self.runtime, identity, task(), self.commit)

    def apply(self, pending, value=None, identity='p', verify=None):
        return P.apply_update(self.runtime, identity, self.static, pending['id'], value or proposal(),
                              verify or (lambda commit, path: commit == self.commit and path in task()['files'] + ['tests/__init__.py']))

    def assess(self, profile=None):
        cycle = QualityCycle(self.runtime, 'p', profile or self.static)
        cycle.record_assessment(self.commit, 'baseline', 'passed', 'host.json',
            [{'id': cid, 'status': 'passed', 'evidence': cid + '.log'} for cid in ('full', 'cli')])
        return cycle

    def test_read_only_initial_status_and_effective_profile_create_nothing(self):
        self.assertEqual(P.effective_profile(self.runtime, 'p', self.static), self.static)
        self.assertFalse(P.status(self.runtime, 'p')['feature_hold'])
        self.assertEqual(P.pending_updates(self.runtime, 'p'), [])
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_only_new_complete_proof_reconsiders_missing_evidence_once(self):
        pending=self.queue();identity=pending['id']
        P.mark_needs_manager(self.runtime,'p',identity,'The supplied test source is truncated; complete accepted test source is required')
        held=P.provide_evidence(self.runtime,'p',identity,'1'*64,complete=False)
        self.assertEqual(held['state'],'needs_manager')
        ready=P.provide_evidence(self.runtime,'p',identity,'2'*64,complete=True)
        self.assertEqual(ready['state'],'pending');self.assertEqual(ready['commit'],self.commit)
        P.mark_needs_manager(self.runtime,'p',identity,'Complete accepted test source still needs execution evidence')
        self.assertEqual(P.provide_evidence(self.runtime,'p',identity,'2'*64,complete=True)['state'],'needs_manager')

    def test_source_context_cannot_override_a_scope_or_owner_decision(self):
        pending=self.queue();P.mark_needs_manager(self.runtime,'p',pending['id'],'Owner must decide the product scope before adoption')
        self.assertEqual(P.provide_evidence(self.runtime,'p',pending['id'],'3'*64,complete=True)['state'],'needs_manager')

    def test_additive_growth_preserves_checks_and_requires_fresh_profile_baseline(self):
        original = copy.deepcopy(self.static)
        cycle = self.assess()
        pending = self.queue()
        self.assertIn('profile_update_required', cycle.status(self.commit)['reasons'])
        updated = self.apply(pending)
        self.assertEqual(self.static, original)
        self.assertEqual(updated['version'], 3)
        self.assertEqual(updated['checks'], original['checks'])
        self.assertEqual(updated['capabilities'][:1], original['capabilities'])
        self.assertEqual(updated['contracts'][:1], original['contracts'])
        self.assertEqual(updated['workflows'][:1], original['workflows'])
        self.assertEqual(updated['profile_manager']['static_hash'], profile_hash(original))
        self.assertFalse(P.status(self.runtime, 'p')['feature_hold'])
        self.assertIn('effective_profile_changed', cycle.status(self.commit)['reasons'])
        current = QualityCycle(self.runtime, 'p', P.effective_profile(self.runtime, 'p', self.static))
        self.assertFalse(current.status(self.commit)['feature_allowed'])
        current = self.assess(updated)
        self.assertTrue(current.status(self.commit)['feature_allowed'])

    def test_pending_update_invalidates_ticket_but_independent_repair_runs(self):
        cycle = self.assess()
        ticket = cycle.issue_feature_ticket(self.commit, 'F-next')
        self.queue()
        self.assertFalse(cycle.revalidate_ticket(ticket, self.commit, 'F-next')['valid'])
        cycle.upsert_finding({'id': 'bug', 'evidence': 'reproduced.json', 'material': True})
        decision = cycle.next_decision(self.commit, [{'id': 'repair', 'kind': 'repair', 'finding_ids': ['bug']}])
        self.assertEqual(decision['action'], 'implement_locally')
        self.assertEqual(decision['task']['id'], 'repair')

    def test_cross_project_isolation_and_idempotent_queue_apply(self):
        pending = self.queue()
        self.assertEqual(self.queue(), pending)
        self.assertFalse(P.status(self.runtime, 'other')['feature_hold'])
        updated = self.apply(pending)
        self.assertEqual(self.apply(pending), updated)
        self.assertEqual(P.effective_profile(self.runtime, 'other', self.static), self.static)
        self.assertEqual(P.status(self.runtime, 'p')['applied_count'], 1)
        with self.assertRaises(P.ProfileError):
            P.queue_update(self.runtime, 'p', dict(task(), title='Rewritten contract'), self.commit)
        changed = proposal(); changed['capability']['title'] = 'Different claim'
        with self.assertRaises(P.ProfileError):
            self.apply(pending, changed)

    def test_new_commands_or_overwritten_existing_profile_are_rejected(self):
        pending = self.queue()
        bad = proposal(); bad['checks'] = []
        with self.assertRaises(P.ProfileError): self.apply(pending, bad)
        for section in ('capability', 'workflow'):
            bad = proposal(); bad[section]['id'] = 'original'
            with self.subTest(section=section), self.assertRaises(P.ProfileError): self.apply(pending, bad)
        bad = proposal(); bad['contracts'][0]['id'] = 'original'
        with self.assertRaises(P.ProfileError): self.apply(pending, bad)
        bad = proposal(); bad['workflow']['check_ids'] = ['new-command']
        with self.assertRaises(P.ProfileError): self.apply(pending, bad)
        self.assertEqual(P.effective_profile(self.runtime, 'p', self.static), self.static)
        self.assertTrue(P.status(self.runtime, 'p')['feature_hold'])

    def test_regression_must_be_accepted_discovered_and_verified_at_exact_commit(self):
        pending = self.queue()
        bad = proposal(); bad['coverage'][0]['test_path'] = 'tests/test_unchanged.py'
        with self.assertRaises(P.ProfileError): self.apply(pending, bad)
        bad = proposal(); bad['workflow']['check_ids'] = ['cli']; bad['coverage'][0]['check_id'] = 'cli'
        with self.assertRaises(P.ProfileError): self.apply(pending, bad)
        with self.assertRaises(P.ProfileError): self.apply(pending, verify=lambda commit, path: False)
        bad = proposal(); bad['capability']['source_paths'] = ['../other.py']
        with self.assertRaises(P.ProfileError): self.apply(pending, bad)
        self.assertTrue(P.status(self.runtime, 'p')['feature_hold'])

    def test_every_accepted_python_source_requires_regression_coverage(self):
        accepted = task(); accepted['files'].append('src/other.py')
        pending = P.queue_update(self.runtime, 'p', accepted, self.commit)
        with self.assertRaises(P.ProfileError): self.apply(pending)

    def test_discovery_pattern_limits_are_respected(self):
        check = {'command': ['python3', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_unit*.py']}
        self.assertTrue(P._check_covers(check, 'tests/test_unit_feature.py'))
        self.assertFalse(P._check_covers(check, 'tests/test_lab_feature.py'))
        self.assertFalse(P._check_covers(check, 'other/test_unit_feature.py'))
        check['command'] += ['-k', 'one_case']
        self.assertFalse(P._check_covers(check, 'tests/test_unit_feature.py'))

    def test_narrow_class_filters_and_positional_discovery_cannot_claim_whole_file(self):
        for suffix in (['tests.test_feature.ExistingCase'], ['tests.test_feature', '-kExistingCase'],
                       ['discover', 'tests/old'], ['discover', '-kExistingCase'],
                       ['discover', '-ptest_old*.py']):
            check = {'command': ['python3', '-m', 'unittest', *suffix]}
            with self.subTest(command=check):
                self.assertFalse(P._check_covers(check, 'tests/test_feature.py'))
        self.assertTrue(P._check_covers({'command': ['python3', '-m', 'unittest', 'tests.test_feature', '-v']}, 'tests/test_feature.py'))

    def test_sequential_updates_preserve_prior_capability_and_immutable_version_journal(self):
        first = self.apply(self.queue())
        other_task = task('F2', 'src/two.py', 'tests/test_two.py')
        second = P.queue_update(self.runtime, 'p', other_task, 'b' * 40)
        result = self.apply(second, proposal('second', 'src/two.py', 'tests/test_two.py'), verify=lambda commit, path: commit == 'b' * 40 and path in other_task['files'] + ['tests/__init__.py'])
        self.assertEqual(result['version'], 4)
        self.assertEqual(result['capabilities'][:2], first['capabilities'])
        self.assertEqual(P.pending_updates(self.runtime, 'p'), [])
        with P.HistoryStore(self.runtime).reader() as con:
            versions = con.execute('SELECT version,static_hash FROM qc_profile_versions ORDER BY version').fetchall()
        self.assertEqual([row['version'] for row in versions], [3, 4])
        self.assertEqual({row['static_hash'] for row in versions}, {profile_hash(self.static)})

    def test_failed_mirror_write_retains_authoritative_committed_profile(self):
        pending = self.queue()
        with mock.patch.object(P.D, 'save_json', side_effect=OSError('simulated interrupted mirror')):
            with self.assertRaises(OSError): self.apply(pending)
        self.assertFalse(P.status(self.runtime, 'p')['feature_hold'])
        effective = P.effective_profile(self.runtime, 'p', self.static)
        self.assertEqual(effective['version'], 3)
        self.assertEqual(self.apply(pending), effective)
        self.assertTrue((self.runtime / 'effective-quality-profile.json').exists())

    def test_manager_hold_and_changed_static_profile_fail_closed(self):
        pending = self.queue()
        P.mark_needs_manager(self.runtime, 'p', pending['id'], 'New protected command is needed for regression coverage')
        state = P.status(self.runtime, 'p')
        self.assertTrue(state['feature_hold']); self.assertEqual(state['needs_manager_count'], 1)
        self.apply(pending)
        changed = copy.deepcopy(self.static); changed['version'] += 1
        with self.assertRaises(P.ProfileError): P.effective_profile(self.runtime, 'p', changed)

    def test_nested_discovery_requires_committed_package_ancestors(self):
        accepted = task(test='tests/nested/test_feature.py')
        pending = P.queue_update(self.runtime, 'p', accepted, self.commit)
        value = proposal(test='tests/nested/test_feature.py')
        with self.assertRaises(P.ProfileError):
            self.apply(pending, value, verify=lambda commit, path: path in accepted['files'] + ['tests/__init__.py'])
        result = self.apply(pending, value, verify=lambda commit, path: path in accepted['files'] + ['tests/__init__.py', 'tests/nested/__init__.py'])
        self.assertEqual(result['version'], 3)

    def test_unknown_profile_schema_is_not_silently_adopted_by_readers(self):
        self.queue()
        with P.HistoryStore(self.runtime).transaction() as con:
            con.execute("UPDATE qc_meta SET value='2' WHERE key='profile_manager_schema'")
        with self.assertRaises(P.ProfileError): P.status(self.runtime, 'p')
        with self.assertRaises(P.ProfileError): P.effective_profile(self.runtime, 'p', self.static)


if __name__ == '__main__': unittest.main()
