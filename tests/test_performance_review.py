import hashlib
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import performance_review as R


@contextmanager
def sql(path):
    con = sqlite3.connect(path)
    try:
        with con:
            yield con
    finally:
        con.close()


class PerformanceReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve(); self.runtime = self.root / 'runtime'
        self.directory = self.runtime / 'performance-reviews' / 'fixture-week'
        self.start = 1_900_000_000.; self.commit = 'a' * 40
        self.model = {'model': 'colibri/glm-5.3', 'digest': 'b' * 64, 'context': 32768,
                      'bytes': 123, 'parameters_sha256': 'c' * 64}
        projects = []
        for name in sorted(R.PROJECTS):
            state = self.runtime / 'projects' / name; state.mkdir(parents=True)
            repo = self.root / name; repo.mkdir()
            projects.append({'id': name, 'state_dir': str(state), 'path': str(repo)})
            with sql(state / 'history.sqlite3') as con:
                con.executescript('CREATE TABLE qc_runs(project_id,run_id,created_at,outcome,accepted_commit,payload);'
                    'CREATE TABLE qc_events(sequence INTEGER PRIMARY KEY,project_id,kind,occurred_at,payload);'
                    'CREATE TABLE cr_reservations(token,project,created,state);')
        self.save(self.root / 'projects.json', {'projects': projects})
        with sql(self.runtime / 'workflow.sqlite3') as con:
            con.executescript('CREATE TABLE wf_events(sequence INTEGER PRIMARY KEY,at,project,kind,reference,payload);'
                'CREATE TABLE wf_jobs(id,project,state,created,updated,payload);'
                'CREATE TABLE wf_phases(id,job,project,kind,state,created,updated,lease,payload,result);'
                'CREATE TABLE wf_charges(job,key,project,kind,created);')
        with sql(self.runtime / 'history.sqlite3') as con:
            con.execute('CREATE TABLE cp_invocations(id,purpose,created,result)')
        proof = self.root / 'activation.json'
        self.save(proof, {'installed': self.commit, 'source_clean': True, 'selected_model': self.model,
            'observed_at': R.iso(self.start), 'fence_present': False,
            'generation_health': {'state': 'verified', 'installed': self.commit}})
        self.config = {'activation_at': R.iso(self.start), 'expected_source': self.commit,
            'expected_model': self.model, 'activation_proof': str(proof),
            'activation_proof_sha256': R.digest(proof.read_bytes()), 'projects': ['secondapp', 'exampleapp']}
        self.review = R.Review(self.root, self.directory)
        self.git = mock.patch.object(R, 'git', return_value=self.commit).start(); self.addCleanup(mock.patch.stopall)
        mock.patch.object(self.review, 'telemetry', return_value={'available': False}).start()

    def save(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value))

    def initialise(self):
        return self.review.initialise(self.config, self.start)

    def accepted(self, run, at, *, project='exampleapp', commit='d' * 40, started=None, valid=True, model=None):
        state = self.runtime / 'projects' / project; folder = state / 'runs' / run
        self.save(folder / 'accepted.json', {'run': run, 'commit': commit, 'at': R.iso(at), 'delivery': 'local_branch'})
        self.save(folder / 'checks.json', [{'code': 0 if valid else 1}])
        self.save(folder / 'review/result.json', {'decision': 'accept', 'issues': []})
        self.save(folder / 'worker-model.json', self.model if model is None else model)
        with sql(state / 'history.sqlite3') as con:
            con.execute('INSERT INTO qc_runs VALUES(?,?,?,?,?,?)', (project, run, started or at - 20, 'accepted', commit, '{}'))
        return folder

    def phase(self, name, at, *, state='succeeded', kind='local', created=None, receipt=True, identity='1' * 64, model=None):
        model = self.model if model is None else model
        folder = self.runtime / 'projects/exampleapp/decisions' / name
        self.save(folder / 'events.jsonl', {})
        if receipt:
            for file, value in {'result.json': {'answer': True}, 'schema.json': {'type': 'object', 'properties': {'answer': {'type': 'boolean'}}, 'required': ['answer'], 'additionalProperties': False},
                'process.json': {'code': 0, 'reason': 'exited'}, 'supervision-model.json': dict(model, kind='local', role='diagnosis'),
                'local-metrics.json': {}, 'local-request.json': {'model': model}}.items():
                self.save(folder / file, value)
            (folder / 'input.txt').write_text('SYNTHETIC PRIVATE PROMPT')
            self.save(folder / 'local-decision-receipt.json', {'input_hash': identity,
                'files': {file: R.digest((folder / file).read_bytes()) for file in R.DECISION_FILES}})
        with sql(self.runtime / 'workflow.sqlite3') as con:
            con.execute('INSERT INTO wf_jobs VALUES(?,?,?,?,?,?)', ('job-' + name, 'exampleapp', 'succeeded', created or at - 10, at, json.dumps({'worker_model': model})))
            con.execute('INSERT INTO wf_phases VALUES(?,?,?,?,?,?,?,?,?,?)', (name, 'job-' + name, 'exampleapp', kind, state,
                created or at - 10, at, '{}', json.dumps({'log': str(folder / 'events.jsonl'), 'role': 'diagnosis'}), json.dumps({'code': 0, 'reason': 'exited'})))
        return folder

    def report(self, day):
        return R.unseal(self.directory / 'reports' / f'day-{day:02}.json')

    def test_baseline_excludes_historical_eleven_and_two_acceptances(self):
        for project, count in [('exampleapp', 11), ('secondapp', 2)]:
            for number in range(count):
                self.accepted('old-' + str(number), self.start - 10, project=project)
        baseline = self.initialise()
        self.review.tick(self.start + R.DAY)
        self.assertEqual(len(baseline['projects']['exampleapp']['accepted']), 11)
        self.assertEqual(len(baseline['projects']['secondapp']['accepted']), 2)
        self.assertEqual(self.report(1)['accepted'], [])

    def test_acceptance_completion_boundary_and_carryover_are_not_run_start(self):
        self.initialise()
        self.accepted('carry', self.start + 5, started=self.start - 100)
        self.accepted('edge', self.start + R.DAY, commit='e' * 40)
        self.review.tick(self.start + 2 * R.DAY)
        self.assertEqual([r['run'] for r in self.report(1)['accepted']], ['carry'])
        self.assertTrue(self.report(1)['accepted'][0]['carryover'])
        self.assertEqual([r['run'] for r in self.report(2)['accepted']], ['edge'])

    def test_new_completion_between_activation_and_baseline_capture_is_not_lost(self):
        self.accepted('just-completed', self.start + 1)
        self.review.initialise(self.config, self.start + 5)
        self.review.tick(self.start + R.DAY)
        self.assertEqual([r['run'] for r in self.report(1)['accepted']], ['just-completed'])

    def test_new_run_id_cannot_recount_a_baseline_commit(self):
        self.accepted('old', self.start - 10); self.initialise()
        self.accepted('replayed', self.start + 10)
        self.review.tick(self.start + R.DAY)
        self.assertEqual(self.report(1)['accepted'], [])

    def test_undated_baseline_identity_cannot_become_new_via_duplicate(self):
        folder = self.accepted('old-undated', self.start - 10)
        receipt = folder / 'accepted.json'; original = receipt.read_bytes(); receipt.unlink()
        baseline = self.initialise()
        self.assertEqual(len(baseline['projects']['exampleapp']['accepted_without_timestamp']), 1)
        self.accepted('renamed-duplicate', self.start + 20)
        self.review.tick(self.start + R.DAY)
        self.assertEqual(self.report(1)['accepted'], [])
        self.assertEqual(self.report(1)['accepted_unverified'][0]['classification'], 'baseline_acceptance_timestamp_unresolved')
        receipt.write_bytes(original)
        self.accepted('another-duplicate', self.start + R.DAY + 20)
        self.review.tick(self.start + 2 * R.DAY)
        self.assertEqual(self.report(2)['accepted'], [])
        self.assertEqual(self.report(2)['accepted_unverified'], [])

    def test_original_post_activation_timestamp_can_resolve_baseline_uncertainty(self):
        folder = self.accepted('new-undated', self.start + 1)
        receipt = folder / 'accepted.json'; original = receipt.read_bytes(); receipt.unlink()
        self.review.initialise(self.config, self.start + 5)
        receipt.write_bytes(original)
        self.review.tick(self.start + R.DAY)
        self.assertEqual([r['run'] for r in self.report(1)['accepted']], ['new-undated'])

    def test_late_verification_is_an_append_only_correction_not_todays_output(self):
        self.initialise(); folder = self.accepted('late', self.start + 10, valid=False)
        self.review.tick(self.start + R.DAY)
        original = (self.directory / 'reports/day-01.json').read_bytes()
        self.save(folder / 'checks.json', [{'code': 0}])
        self.review.tick(self.start + 2 * R.DAY)
        self.assertEqual(self.report(2)['accepted'], [])
        self.assertEqual(self.report(2)['late_verified_acceptances'][0]['original_day'], 1)
        self.assertEqual((self.directory / 'reports/day-01.json').read_bytes(), original)

    def test_collecting_does_not_mutate_source_ledgers_or_product_files(self):
        self.initialise()
        paths = [self.runtime / 'workflow.sqlite3', self.runtime / 'history.sqlite3',
                 self.runtime / 'projects/exampleapp/history.sqlite3', self.root / 'projects.json']
        before = {p: p.read_bytes() for p in paths}
        self.review.tick(self.start + R.DAY)
        self.assertEqual(before, {p: p.read_bytes() for p in paths})

    def test_late_typed_receipt_is_a_correction_with_original_interval(self):
        self.initialise(); folder = self.phase('late-decision', self.start + 10)
        receipt = folder / 'local-decision-receipt.json'; original_receipt = receipt.read_bytes(); receipt.unlink()
        self.review.tick(self.start + R.DAY)
        original_report = (self.directory / 'reports/day-01.json').read_bytes()
        receipt.write_bytes(original_receipt)
        self.review.tick(self.start + 2 * R.DAY)
        self.assertEqual(self.report(2)['typed_decisions'], [])
        self.assertEqual(self.report(2)['late_verified_decisions'][0]['original_day'], 1)
        self.assertEqual((self.directory / 'reports/day-01.json').read_bytes(), original_report)

    def test_later_output_symlink_is_refused_without_product_writes(self):
        self.initialise()
        (self.directory / 'reports').symlink_to(self.root / 'exampleapp', target_is_directory=True)
        with self.assertRaises(R.Unavailable):self.review.tick(self.start + R.DAY)
        self.assertEqual(list((self.root / 'exampleapp').iterdir()), [])

    def test_history_bound_leaves_report_pending(self):
        self.initialise(); self.phase('one', self.start + 1); self.phase('two', self.start + 2)
        with mock.patch.object(R, 'ROW_LIMIT', 1), self.assertRaises(R.Unavailable):
            self.review.tick(self.start + R.DAY)
        self.assertFalse((self.directory / 'reports/day-01.json').exists())

    def test_commit_deduplication_crosses_daily_windows(self):
        self.initialise()
        self.accepted('first', self.start + 5)
        self.accepted('duplicate', self.start + R.DAY + 5)
        self.review.tick(self.start + 2 * R.DAY)
        self.assertEqual(len(self.report(1)['accepted']), 1)
        self.assertEqual(self.report(2)['accepted'], [])

    def test_closed_report_deduplicates_when_original_receipts_disappear(self):
        self.initialise()
        first_acceptance = self.accepted('first-commit', self.start + 5)
        first_decision = self.phase('first-decision', self.start + 6)
        self.review.tick(self.start + R.DAY)
        closed = (self.directory / 'reports/day-01.json').read_bytes()
        (first_acceptance / 'accepted.json').unlink()
        (first_decision / 'local-decision-receipt.json').unlink()
        self.accepted('later-commit', self.start + R.DAY + 5)
        self.phase('later-decision', self.start + R.DAY + 6)
        self.review.tick(self.start + 2 * R.DAY)
        self.assertEqual(self.report(2)['accepted'], [])
        self.assertEqual(self.report(2)['typed_decisions'], [])
        self.assertEqual((self.directory / 'reports/day-01.json').read_bytes(), closed)

    def test_partial_acceptance_does_not_become_correct_code(self):
        self.initialise(); self.accepted('bad-checks', self.start + 5, valid=False)
        folder = self.accepted('missing-review', self.start + 6, commit='f' * 40)
        (folder / 'review/result.json').unlink()
        self.review.tick(self.start + R.DAY)
        self.assertEqual(self.report(1)['accepted'], [])
        self.assertEqual(len(self.report(1)['accepted_unverified']), 2)

    def test_git_commit_proof_is_required(self):
        self.initialise(); self.accepted('missing-commit', self.start + 5)
        def check(root, *args):
            if args[0] == 'cat-file':raise R.Unavailable('Commit missing')
            return self.commit
        self.git.side_effect = check
        self.review.tick(self.start + R.DAY)
        self.assertEqual(self.report(1)['accepted'], [])

    def test_host_decision_events_and_successful_phases_are_not_typed_decisions(self):
        self.initialise()
        self.phase('missing-receipt', self.start + 5, receipt=False)
        self.phase('host-pass', self.start + 6, kind='host')
        with sql(self.runtime / 'projects/exampleapp/history.sqlite3') as con:
            con.execute('INSERT INTO qc_events VALUES(?,?,?,?,?)', (1, 'exampleapp', 'decision', self.start + 10, '{}'))
        self.review.tick(self.start + R.DAY)
        self.assertEqual(self.report(1)['typed_decisions'], [])
        self.assertEqual(len(self.report(1)['typed_decisions_unverified']), 1)

    def test_verified_decisions_deduplicate_and_do_not_disclose_prompts(self):
        self.initialise(); self.phase('one', self.start + 5)
        self.phase('retry', self.start + R.DAY + 5)
        self.review.tick(self.start + 2 * R.DAY)
        self.assertEqual(len(self.report(1)['typed_decisions']), 1)
        self.assertEqual(self.report(2)['typed_decisions'], [])
        self.assertNotIn('SYNTHETIC PRIVATE PROMPT', (self.directory / 'reports/day-01.json').read_text())

    def test_changed_receipt_or_model_is_unverified(self):
        self.initialise(); folder = self.phase('changed', self.start + 5)
        self.save(folder / 'result.json', {'answer': False})
        self.review.tick(self.start + R.DAY)
        self.assertEqual(self.report(1)['typed_decisions'], [])
        self.assertEqual(len(self.report(1)['typed_decisions_unverified']), 1)

    def test_missed_runs_catch_up_original_windows_and_restart_is_idempotent(self):
        self.initialise(); self.review.tick(self.start + 7 * R.DAY + 200)
        before = {p: p.read_bytes() for p in (self.directory / 'reports').iterdir()}
        replacement = R.Review(self.root, self.directory)
        self.assertEqual(replacement.tick(self.start + 8 * R.DAY)['closed_days'], 7)
        self.assertEqual(before, {p: p.read_bytes() for p in (self.directory / 'reports').iterdir()})
        self.assertEqual(self.report(7)['end'], self.start + 7 * R.DAY)
        self.assertTrue(replacement.status()['complete'])
        self.assertTrue((self.directory / 'week-summary.txt').exists())

    def test_missing_samples_are_unknown_not_zero_pressure_or_idle(self):
        self.initialise(); self.review.tick(self.start + R.DAY)
        value = self.report(1)['resources']
        self.assertEqual(value['telemetry_covered_seconds'], 0)
        self.assertEqual(value['telemetry_unobserved_seconds'], R.DAY)
        self.assertIsNone(value['peak_sampled_charged_bytes'])
        self.assertEqual(value['sampled_continuous_active_seconds'], {})

    def test_sample_coverage_counts_only_adjacent_short_gaps(self):
        self.initialise()
        for offset in (0, 60, 600):
            value = {'at': self.start + offset, 'slot': offset // 60, 'active_available': True, 'active': [],
                     'telemetry': {'available': True, 'pressure': 'warning', 'charged_bytes': [123]}, 'source': self.commit}
            R.write_once(self.directory / 'samples' / f'{offset // 60:05}.json', value)
        value = self.review.resources(1, self.start, self.start + R.DAY)
        self.assertEqual(value['telemetry_covered_seconds'], 60)
        self.assertEqual(value['pressure_samples']['warning'], 3)

    def test_tampered_closed_report_is_rejected_without_rewriting(self):
        self.initialise(); self.review.tick(self.start + R.DAY)
        path = self.directory / 'reports/day-01.json'; value = json.loads(path.read_text())
        value['value']['accepted'] = [{'commit': 'invented'}]; self.save(path, value)
        before = path.read_bytes()
        with self.assertRaises(R.Unavailable):self.review.tick(self.start + 2 * R.DAY)
        self.assertEqual(path.read_bytes(), before)

    def test_unavailable_ledger_leaves_due_report_pending(self):
        self.initialise(); (self.runtime / 'history.sqlite3').unlink()
        with self.assertRaises(R.Unavailable):self.review.tick(self.start + R.DAY)
        self.assertFalse((self.directory / 'reports/day-01.json').exists())

    def test_charge_history_is_not_refunded_or_reported_as_currency(self):
        self.initialise()
        with sql(self.runtime / 'workflow.sqlite3') as con:
            con.executemany('INSERT INTO wf_charges VALUES(?,?,?,?,?)', [('old','key','exampleapp','cloud',self.start-1),('new','key','exampleapp','cloud',self.start+1)])
        with sql(self.runtime / 'history.sqlite3') as con:
            con.execute('INSERT INTO cp_invocations VALUES(?,?,?,?)', ('unknown','review',self.start+1,None))
        self.review.tick(self.start + R.DAY); value = self.report(1)
        self.assertEqual(len(value['new_charges']), 1)
        self.assertTrue(value['cloud_invocations'][0]['unresolved'])
        self.assertNotIn('cost', value)

    def test_initialisation_rejects_stale_or_mismatched_activation(self):
        with self.assertRaises(R.Unavailable):self.review.initialise(self.config, self.start + 301)
        changed = dict(self.config, expected_source='e' * 40)
        with self.assertRaises(R.Unavailable):self.review.initialise(changed, self.start)
        self.assertFalse((self.directory / 'baseline.json').exists())

    def test_output_path_cannot_target_product_or_other_runtime_records(self):
        with self.assertRaises(R.Unavailable):R.Review(self.root, self.root / 'exampleapp')
        with self.assertRaises(R.Unavailable):R.Review(self.root, self.runtime / 'performance-reviews')

    def test_parent_traversal_and_symlink_alias_cannot_escape_review_folder(self):
        unsafe = self.runtime / 'performance-reviews/../projects/exampleapp'
        product_before = {p: p.read_bytes() for p in (self.runtime / 'projects/exampleapp').iterdir() if p.is_file()}
        with self.assertRaises(R.Unavailable):R.Review(self.root, unsafe).initialise(self.config, self.start)
        self.assertFalse((self.runtime / 'projects/exampleapp/baseline.json').exists())
        self.assertEqual(product_before, {p: p.read_bytes() for p in product_before})
        alias = self.runtime / 'performance-reviews'; alias.symlink_to(self.runtime / 'projects', target_is_directory=True)
        with self.assertRaises(R.Unavailable):R.Review(self.root, alias / 'exampleapp')

    def test_cloud_summary_contains_only_bounded_numeric_fact_leaves(self):
        self.initialise(); self.review.tick(self.start + R.DAY)
        def check(value):
            if isinstance(value, dict):
                for item in value.values():check(item)
            else:self.assertTrue(value is None or type(value) in (int, float, bool))
        check(self.report(1)['cloud_summary'])

    def test_model_attribution_uses_receipt_profile_not_current_selection(self):
        self.initialise()
        for index, (name, model) in enumerate([
            ('current', dict(self.model, promotion_generation=999)),
            ('other-glm', dict(self.model, context=65536)),
            ('qwen', dict(self.model, model='local-coder:context32')),
            ('unknown', {'model': 'colibri/glm-5.3'})]):
            self.accepted(name, self.start + 10 + index, commit=hex(index + 10)[2:] * 40, model=model)
            self.phase(name, self.start + 20 + index, identity=str(index) * 64, model=model)
        self.review.tick(self.start + R.DAY)
        report = self.report(1); counts = report['cloud_summary']['projects']['exampleapp']
        self.assertEqual(counts['accepted_commits_all_models'], 4)
        self.assertEqual(counts['typed_decisions_all_models'], 4)
        for group in ('glm_current_profile', 'glm_other_profile', 'other_model', 'unknown'):
            self.assertEqual(counts['by_model'][group]['accepted_commits'], 1)
            self.assertEqual(counts['by_model'][group]['typed_decisions'], 1)
        exact = next(r for r in report['accepted'] if r['run'] == 'current')
        self.assertEqual(exact['model_descriptor_sha256'], R.model_facts(self.model)['model_descriptor_sha256'])

    def test_late_model_correction_preserves_other_profile_attribution(self):
        self.initialise()
        changed = dict(self.model, parameters_sha256='e' * 64)
        folder = self.accepted('late-profile', self.start + 10, valid=False, model=changed)
        self.review.tick(self.start + R.DAY)
        self.save(folder / 'checks.json', [{'code': 0}])
        self.review.tick(self.start + 2 * R.DAY)
        counts = self.report(2)['cloud_summary']['projects']['exampleapp']['by_model']
        self.assertEqual(counts['glm_current_profile']['late_verified_acceptances'], 0)
        self.assertEqual(counts['glm_other_profile']['late_verified_acceptances'], 1)
        self.assertEqual(counts['glm_other_profile']['accepted_commits'], 0)


if __name__ == '__main__':unittest.main()
