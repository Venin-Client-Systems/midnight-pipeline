import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import active_state as A
import durability as D
from history_store import HistoryStore


class ActiveStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name)

    def receipt(self, identity, index=0):
        return {'id': identity, 'title': 'Accepted ' + identity, 'run': 'run-' + identity,
                'commit': 'commit-' + identity, 'at': dt.datetime.fromtimestamp(1_000_000 + index, dt.timezone.utc).isoformat()}

    def state(self, count):
        identities = ['T' + str(i) for i in range(count)]
        return {'done': identities, 'accepted': [self.receipt(identity, i) for i, identity in enumerate(identities)],
                'tries': {identity: 2 for identity in identities}, 'feedback': {identity: 'First attempt failed' for identity in identities},
                'planned': [{'id': identity, 'description': 'Concrete task ' + identity} for identity in identities]}

    def test_read_only_lookups_and_empty_compaction_do_not_create_state(self):
        self.assertFalse(A.task_done(self.runtime, 'p', 'missing'))
        self.assertEqual(A.accepted_since(self.runtime, 'p', 0), [])
        state = {'done': [], 'accepted': [], 'tries': {}, 'planned': []}
        self.assertIs(A.compact(self.runtime, 'p', state, []), state)
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_closed_tasks_are_durable_and_dependent_tasks_retain_done_lookup(self):
        state = self.state(60)
        state['planned'].append({'id': 'dependent', 'depends_on': ['T0']})
        result = A.compact(self.runtime, 'p', state, [])
        self.assertEqual(len(result['accepted']), 50)
        self.assertEqual(result['done'], [])
        self.assertEqual(result['tries'], {})
        self.assertEqual(result['feedback'], {})
        self.assertEqual(result['planned'], [{'id': 'dependent', 'depends_on': ['T0']}])
        self.assertTrue(A.task_done(self.runtime, 'p', 'T0'))
        self.assertFalse(A.task_done(self.runtime, 'other', 'T0'))
        self.assertFalse(A.task_done(self.runtime, 'p', 'dependent'))
        self.assertEqual(A.local_attempt_total(result), 120)
        self.assertEqual(len(state['done']), 60)  # Original checkpoint remains untouched.
        archive = self.runtime / result['state_compaction']['archive_path']
        raw = gzip.decompress(archive.read_bytes())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), result['state_compaction']['snapshot_sha256'])
        self.assertEqual(json.loads(raw)['state'], state)
        with HistoryStore(self.runtime).reader() as con:
            facts = json.loads(con.execute('SELECT payload FROM qc_terminal_tasks WHERE task_id=?', ('T0',)).fetchone()[0])
        self.assertEqual(facts['task_state']['feedback'], 'First attempt failed')
        self.assertEqual(facts['task']['description'], 'Concrete task T0')

    def test_unresolved_retry_candidate_pending_and_original_ancestors_remain(self):
        state = self.state(4)
        state['planned'].append({'id': 'repair', 'retry_of': {'task_id': 'T0', 'task_sha256': 'exact'}})
        state['tries']['repair'] = 4
        state['feedback']['repair'] = 'Still failing'
        state['retry_candidates'] = {'T1': {'run': 'draft', 'patch_sha256': 'unique'}}
        state['pending'] = {'task': {'id': 'T2'}, 'run': 'pending-review'}
        state['active_attempt'] = {'task': {'id': 'T3'}, 'run': 'worker'}
        result = A.compact(self.runtime, 'p', state, [])
        self.assertIs(result, state)
        self.assertFalse(A.task_done(self.runtime, 'p', 'T0'))
        self.assertEqual(result['retry_candidates']['T1']['patch_sha256'], 'unique')
        self.assertEqual(result['feedback']['repair'], 'Still failing')

    def test_unresolved_findings_and_promotion_intent_pin_task_facts(self):
        state = self.state(3)
        state['planned'][0]['finding_ids'] = ['F-open']
        with HistoryStore(self.runtime).transaction() as con:
            con.execute('INSERT INTO qc_findings VALUES(?,?,?,?,?,?,?)', ('p', 'F-open', 1, 'open', 1, 0, '{}'))
        D.save_json(self.runtime / 'promotion-intent.json', {'task': {'id': 'T1'}, 'run': 'promoting'})
        result = A.compact(self.runtime, 'p', state, [])
        self.assertEqual(set(result['done']), {'T0', 'T1'})
        self.assertFalse(A.task_done(self.runtime, 'p', 'T0'))
        self.assertFalse(A.task_done(self.runtime, 'p', 'T1'))
        self.assertTrue(A.task_done(self.runtime, 'p', 'T2'))

    def test_superseded_chain_closes_only_after_successor_and_preserves_episode_proof(self):
        state = self.state(1)
        state['planned'] += [{'id': 'failed-source'}, {'id': 'failed-middle', 'retry_of': {'task_id': 'failed-source'}}]
        state['superseded_tasks'] = {'failed-source': 'failed-middle', 'failed-middle': 'T0'}
        state['tries'].update({'failed-source': 2, 'failed-middle': 2})
        state['issue_episodes'] = {identity: {'episode': 'same-episode', 'task_sha256': identity} for identity in ['failed-source', 'failed-middle', 'T0']}
        state['issue_guidance'] = {'same-episode': {'steps': ['Exact correction']}}
        state['episode_failure_imports'] = {'same-episode': True}
        state['rescue_decisions'] = {'same-episode': {'decision': 'narrow_rescue'}}
        result = A.compact(self.runtime, 'p', state, [])
        self.assertEqual(result['planned'], [])
        self.assertEqual(result['issue_guidance'], {})
        self.assertEqual(result['superseded_tasks'], {})
        self.assertTrue(A.task_done(self.runtime, 'p', 'failed-source'))
        self.assertEqual(len(A.accepted_since(self.runtime, 'p', 0)), 1)
        self.assertEqual(A.accepted_count(self.runtime, 'p', result), 1)
        self.assertEqual(A.local_attempt_total(result), 6)
        with HistoryStore(self.runtime).reader() as con:
            facts = json.loads(con.execute('SELECT payload FROM qc_terminal_tasks WHERE task_id=?', ('failed-source',)).fetchone()[0])
        self.assertEqual(facts['disposition'], 'superseded')
        self.assertFalse(facts['accepted'])
        self.assertEqual(facts['episode_state']['issue_guidance']['steps'], ['Exact correction'])

    def test_closed_superseded_drafts_archive_with_verified_terminal_proof(self):
        state = self.state(1)
        state['planned'] += [{'id': 'source'}, {'id': 'middle', 'retry_of': {'task_id': 'source'}}]
        state['superseded_tasks'] = {'source': 'middle', 'middle': 'T0'}
        state['retry_candidates'] = {'source': {'run': 'source-run', 'patch_sha256': 'original-draft'},
                                     'middle': {'run': 'middle-run', 'patch_sha256': 'guided-draft'}}
        result = A.compact(self.runtime, 'p', state, [])
        self.assertEqual(result['retry_candidates'], {})
        self.assertEqual(result['superseded_tasks'], {})
        self.assertEqual(result['planned'], [])
        with HistoryStore(self.runtime).reader() as con:
            fact = json.loads(con.execute('SELECT payload FROM qc_terminal_tasks WHERE task_id=?', ('source',)).fetchone()[0])
        self.assertEqual(fact['task_state']['retry_candidates']['patch_sha256'], 'original-draft')
        self.assertEqual(fact['terminal_proof'], {'kind': 'accepted_successor_chain', 'direct_successor': 'middle',
                         'accepted_task': 'T0', 'accepted_run': 'run-T0', 'accepted_commit': 'commit-T0'})
        original = json.loads(gzip.decompress((self.runtime / result['state_compaction']['archive_path']).read_bytes()))
        self.assertEqual(original['state']['retry_candidates'], state['retry_candidates'])
        self.assertEqual(A.accepted_count(self.runtime, 'p', result), 1)

    def test_done_without_accepted_successor_cannot_release_old_draft(self):
        state = self.state(1)
        state['accepted'] = []
        state['planned'].append({'id': 'source'})
        state['superseded_tasks'] = {'source': 'T0'}
        state['retry_candidates'] = {'source': {'run': 'source-run', 'patch_sha256': 'unproven'}}
        result = A.compact(self.runtime, 'p', state, [])
        self.assertIn('source', result['retry_candidates'])
        self.assertFalse(A.task_done(self.runtime, 'p', 'source'))
        # Mere archived done status is still not accepted successor evidence.
        again = A.compact(self.runtime, 'p', result, [])
        self.assertIn('source', again['retry_candidates'])

    def test_live_descendant_or_pending_source_overrides_completed_chain_proof(self):
        for active in ('pending', 'descendant'):
            with self.subTest(active=active):
                state = self.state(1)
                state['planned'].append({'id': 'source'})
                state['superseded_tasks'] = {'source': 'T0'}
                state['retry_candidates'] = {'source': {'run': 'source-run', 'patch_sha256': 'still-useful'}}
                if active == 'pending':
                    state['pending'] = {'task': {'id': 'source'}, 'run': 'pending-source'}
                else:
                    state['planned'].append({'id': 'unfinished', 'retry_of': {'task_id': 'source'}})
                result = A.compact(self.runtime, 'p', state, [])
                self.assertIn('source', result['retry_candidates'])
                self.assertFalse(A.task_done(self.runtime, 'p', 'source'))

    def test_archived_accepted_successor_proves_old_draft_can_retire(self):
        result = A.compact(self.runtime, 'p', self.state(1), [])
        result['accepted'] = []  # It has aged out of the bounded summary.
        result['planned'] = [{'id': 'source'}]
        result['superseded_tasks'] = {'source': 'T0'}
        result['retry_candidates'] = {'source': {'run': 'source-run', 'patch_sha256': 'saved'}}
        retired = A.compact(self.runtime, 'p', result, [])
        self.assertEqual(retired['retry_candidates'], {})
        self.assertTrue(A.task_done(self.runtime, 'p', 'source'))
        self.assertEqual(A.accepted_count(self.runtime, 'p', retired), 1)

    def test_10000_closed_retry_drafts_do_not_accumulate_in_hot_state(self):
        state = self.state(10_000)
        state['accepted'] = state['accepted'][-1:]
        state['done'] = ['T9999']
        state['superseded_tasks'] = {'T' + str(i): 'T9999' for i in range(9999)}
        state['retry_candidates'] = {'T' + str(i): {'run': 'failed-' + str(i), 'patch_sha256': 'unique-' + str(i)}
                                     for i in range(9999)}
        result = A.compact(self.runtime, 'p', state, [])
        self.assertEqual(result['retry_candidates'], {})
        self.assertEqual(result['planned'], [])
        self.assertEqual(result['feedback'], {})
        self.assertLess(len(json.dumps(result)), 2_000)
        self.assertEqual(A.local_attempt_total(result), 20_000)
        self.assertEqual(A.accepted_count(self.runtime, 'p', result), 1)

    def test_accepted_pagination_is_indexed_and_does_not_double_count_recent_overlap(self):
        result = A.compact(self.runtime, 'p', self.state(70), [])
        first = A.accepted_since(self.runtime, 'p', 1_000_010, limit=17)
        second = A.accepted_since(self.runtime, 'p', 1_000_010, limit=100, cursor=(first[-1]['at'], first[-1]['run']))
        self.assertEqual(len(first + second), 60)
        union = {row['run']: row for row in first + second + result['accepted']}
        self.assertEqual(len(union), 60)
        self.assertEqual(A.accepted_count(self.runtime, 'p', result), 70)
        result['accepted'].append(self.receipt('new-run', 71))
        self.assertEqual(A.accepted_count(self.runtime, 'p', result), 71)
        with HistoryStore(self.runtime).reader() as con:
            plan = ' '.join(str(row[3]) for row in con.execute('EXPLAIN QUERY PLAN SELECT * FROM qc_terminal_acceptances WHERE project_id=? AND accepted_at>=? ORDER BY accepted_at,run_id LIMIT 50', ('p', 0)))
        self.assertIn('qc_terminal_acceptances_time', plan)

    def test_replaying_original_checkpoint_is_safe_and_repeat_is_noop(self):
        state = self.state(60)
        first = A.compact(self.runtime, 'p', state, [])
        again = A.compact(self.runtime, 'p', state, [])
        self.assertEqual(first, again)
        count = len(list((self.runtime / 'state-archives').iterdir()))
        self.assertIs(A.compact(self.runtime, 'p', first, []), first)
        self.assertEqual(len(list((self.runtime / 'state-archives').iterdir())), count)
        self.assertEqual(len(A.accepted_since(self.runtime, 'p', 0)), 60)
        self.assertEqual(A.local_attempt_total(again), 120)

    def test_archived_backlog_tasks_are_not_treated_as_unresolved_ancestors(self):
        state = self.state(2)
        state['planned'][1]['retry_of'] = {'task_id': 'T0'}
        seed = [dict(state['planned'][1])]
        result = A.compact(self.runtime, 'p', state, seed)
        # The unchanged seed backlog still contains T1 after its done ID retires.
        result['done'] = ['T0']
        result['tries']['T0'] = 0
        result['planned'] = [{'id': 'T0', 'description': 'Concrete task T0'}]
        again = A.compact(self.runtime, 'p', result, seed)
        self.assertEqual(again['done'], [])
        self.assertEqual(again['planned'], [])
        self.assertEqual(A.local_attempt_total(again), 4)

    def test_duplicate_accepted_summaries_cannot_grow_hot_state(self):
        state = self.state(1)
        state['accepted'] = state['accepted'] * 1000
        result = A.compact(self.runtime, 'p', state, [])
        self.assertEqual(len(result['accepted']), 1)
        self.assertEqual(A.accepted_count(self.runtime, 'p', result), 1)
        original = json.loads(gzip.decompress((self.runtime / result['state_compaction']['archive_path']).read_bytes()))
        self.assertEqual(len(original['state']['accepted']), 1000)

    def test_snapshot_write_or_transaction_failure_never_mutates_source(self):
        state = self.state(3)
        original = json.dumps(state, sort_keys=True)
        with mock.patch.object(A.D, 'atomic_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                A.compact(self.runtime, 'p', state, [])
        self.assertEqual(json.dumps(state, sort_keys=True), original)
        self.assertFalse(A.task_done(self.runtime, 'p', 'T0'))
        with mock.patch.object(A.HistoryStore, 'transaction', side_effect=OSError('database unavailable')):
            with self.assertRaises(OSError):
                A.compact(self.runtime, 'p', state, [])
        self.assertEqual(json.dumps(state, sort_keys=True), original)
        self.assertTrue(list((self.runtime / 'state-archives').iterdir()))

    def test_100000_closed_tasks_leave_bounded_active_checkpoint(self):
        state = self.state(100_000)
        result = A.compact(self.runtime, 'p', state, [])
        self.assertEqual(len(result['accepted']), 50)
        self.assertEqual(result['done'], [])
        self.assertEqual(result['planned'], [])
        self.assertEqual(result['tries'], {})
        self.assertEqual(result['feedback'], {})
        self.assertLess(len(json.dumps(result)), 15_000)
        self.assertEqual(A.local_attempt_total(result), 200_000)
        self.assertTrue(A.task_done(self.runtime, 'p', 'T99999'))
        self.assertTrue(A.task_done(self.runtime, 'p', 'T0'))
        with HistoryStore(self.runtime).reader() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM qc_terminal_tasks').fetchone()[0], 100_000)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM qc_terminal_acceptances').fetchone()[0], 100_000)
        self.assertEqual(len(A.accepted_since(self.runtime, 'p', 0, limit=50)), 50)


if __name__ == '__main__':
    unittest.main()
