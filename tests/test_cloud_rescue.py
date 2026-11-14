from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest

import cloud_rescue as R


class RescueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'history.sqlite3'
        self.now = 2_000_000.
        self.store = R.RescueStore(self.path, clock=lambda: self.now)

    def failure(self, episode, attempt, *, kind='acceptance', verified=True, guidance=None):
        return self.store.record_local_failure('p', episode, 'task-' + attempt, attempt, kind,
                                               {'host': 'assertion failed'}, host_verified=verified,
                                               guidance_id=guidance)

    def ready(self, episode):
        self.failure(episode, episode + 'a')
        self.failure(episode, episode + 'b')
        self.store.record_guidance('p', episode, 'g-' + episode, 'Change the authorization check at the server boundary.')
        self.failure(episode, episode + 'c', guidance='g-' + episode)
        return self.failure(episode, episode + 'd', guidance='g-' + episode)

    def test_read_only_empty_status_does_not_initialize_database(self):
        self.assertFalse(self.store.eligibility('p', 'e')['allowed'])
        self.assertFalse(self.path.exists())

    def test_manager_evidence_contains_both_phases_and_excludes_provider_faults(self):
        self.ready('e')
        self.failure('e','provider',kind='provider_auth',guidance='g-e')
        proof=self.store.failure_evidence('p','e')
        self.assertEqual([v['event_id'] for v in proof['initial']],['ea','eb'])
        self.assertEqual([v['event_id'] for v in proof['guided']],['ec','ed'])
        self.assertTrue(all(v['evidence']['host']=='assertion failed' for v in proof['initial']+proof['guided']))
        self.assertEqual(self.store.failure_evidence('p','absent'),{'initial':[],'guided':[]})

    def test_correcting_host_only_attempt_preserves_history_and_requires_fresh_guidance(self):
        self.failure('e','real-first')
        self.failure('e','host-only')  # legacy classifier incorrectly counted this
        self.store.record_guidance('p','e','premature','Concrete guidance issued after the legacy miscount.')
        self.failure('e','actual-second',guidance='premature')
        proof={'run':'host-only','reason':'Host verified a saved draft without invoking a worker'}
        self.store.exclude_verification_failure('p','e','host-only',proof)
        self.store.exclude_verification_failure('p','e','host-only',proof)
        result=self.store.eligibility('p','e')
        self.assertEqual(result['status'],'guidance_required')
        self.assertEqual(result['local_failures'],2)
        self.assertFalse(result['allowed'])
        with sqlite3.connect(self.path) as con:
            self.assertEqual(con.execute("SELECT substantive FROM cr_events WHERE event_id='host-only'").fetchone()[0],0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM cr_events WHERE kind='guidance_invalidated'").fetchone()[0],1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM cr_events WHERE kind='verification_correction'").fetchone()[0],1)
        self.store.record_guidance('p','e','corrected','Concrete guidance after two actual implementation failures.')
        self.assertEqual(self.failure('e','third',guidance='corrected')['status'],'guided_retries_required')
        self.assertTrue(self.failure('e','fourth',guidance='corrected')['allowed'])

    def test_correction_never_refunds_reservation_or_rewrites_non_check_failure(self):
        self.ready('e');self.store.reserve('p','e','task')
        self.store.exclude_verification_failure('p','e','ea',{'host':'no worker invoked'})
        result=self.store.eligibility('p','e')
        self.assertEqual(result['status'],'episode_reserved');self.assertEqual(result['attempts_168h'],1)
        self.failure('other','review',kind='review')
        with self.assertRaises(R.RescueDenied):
            self.store.exclude_verification_failure('p','other','review',{'host':'wrong classification'})

    def test_requires_two_failures_guidance_then_two_guided_failures(self):
        self.assertEqual(self.failure('e', 'a')['status'], 'local_retries_required')
        self.assertEqual(self.failure('e', 'b')['status'], 'guidance_required')
        self.store.record_guidance('p', 'e', 'g', 'Enforce the token expiration check in validation.')
        self.assertEqual(self.failure('e', 'c', guidance='g')['status'], 'guided_retries_required')
        self.assertTrue(self.failure('e', 'd', guidance='g')['allowed'])

    def test_infrastructure_duplicate_and_unverified_failures_do_not_count(self):
        for i, kind in enumerate(['timeout', 'model_unavailable', 'disk_full', 'process_lost', 'auth', 'infrastructure']):
            self.failure('e', str(i), kind=kind)
        self.failure('e', 'unverified', verified=False)
        self.failure('e', 'one')
        self.failure('e', 'one')
        self.assertEqual(self.store.eligibility('p', 'e')['local_failures'], 1)
        self.assertEqual(self.store.task_failures('p', 'e', 'task-one'), 1)
        self.assertEqual(self.store.task_failures('p', 'e', 'task-unverified'), 0)
        with self.assertRaises(R.RescueDenied):
            self.store.record_guidance('p', 'e', 'g', 'Concrete enough guidance still requires two failures.')

    def test_guidance_must_precede_failures_and_be_delivered_to_attempt(self):
        for i in range(4):
            self.failure('e', str(i))
        self.store.record_guidance('p', 'e', 'g', 'A specific verified fix for the failing server check.')
        self.failure('e', 'not-guided')
        self.failure('e', 'wrong-guidance', guidance='different')
        self.assertEqual(self.store.eligibility('p', 'e')['guided_failures'], 0)
        self.store.record_guidance('p', 'e', 'g', 'A specific verified fix for the failing server check.')
        with self.assertRaises(R.RescueDenied):
            self.store.record_guidance('p', 'e', 'new', 'Cannot reset the boundary of guidance.')

    def test_drafts_require_exact_host_verification_before_rescue(self):
        self.ready('e')
        self.store.record_saved_draft('p', 'e', 'draft')
        self.assertEqual(self.store.eligibility('p', 'e')['status'], 'host_verification_required')
        with self.assertRaises(R.RescueDenied):
            self.store.record_host_verification('p', 'e', 'other', False, 'failed')
        self.store.record_host_verification('p', 'e', 'draft', False, 'host assert failed')
        self.assertTrue(self.store.eligibility('p', 'e')['allowed'])
        # Replay of the same saved candidate cannot erase its host verification.
        self.store.record_saved_draft('p', 'e', 'draft')
        self.assertTrue(self.store.eligibility('p', 'e')['allowed'])

    def test_only_accepted_and_reviewed_success_closes_episode(self):
        self.ready('e')
        self.store.record_saved_draft('p', 'e', 'draft')
        self.store.record_host_verification('p', 'e', 'draft', True, 'full acceptance passed')
        self.assertTrue(self.store.eligibility('p', 'e')['allowed'])
        self.store.resolve_episode('p', 'e', 'host acceptance plus independent review plus accepted commit receipt')
        self.store.resolve_episode('p', 'e', 'host acceptance plus independent review plus accepted commit receipt')
        with self.assertRaises(R.RescueDenied):
            self.store.resolve_episode('p', 'e', 'different accepted commit')
        self.assertEqual(self.store.eligibility('p', 'e')['status'], 'resolved')
        with self.assertRaises(R.RescueDenied):
            self.store.reserve('p', 'e', 'task')

    def test_crash_and_unknown_launch_count_and_cannot_retry_episode(self):
        self.ready('e')
        self.store.reserve('p', 'e', 'task')
        resumed = R.RescueStore(self.path, clock=lambda: self.now)
        self.assertEqual(resumed.eligibility('p', 'e')['attempts_168h'], 1)
        self.assertEqual(resumed.eligibility('p', 'e')['status'], 'episode_reserved')
        with self.assertRaises(R.RescueDenied):
            resumed.reserve('p', 'e', 'renamed-task')
        self.now += R.WINDOW_SECONDS + 1
        self.assertEqual(resumed.eligibility('p', 'e')['attempts_168h'], 0)
        with self.assertRaises(R.RescueDenied):
            resumed.reserve('p', 'e', 'still-same-episode')

    def test_failed_rescue_requires_manager_and_success_is_terminal(self):
        self.ready('e')
        token = self.store.reserve('p', 'e', 'task')['token']
        self.store.finish(token, False, 'acceptance failed')
        self.store.finish(token, False, 'idempotent replay')
        self.assertEqual(self.store.eligibility('p', 'e')['status'], 'manager_required')
        with self.assertRaises(R.RescueDenied):
            self.store.finish(token, True, 'cannot rewrite failed receipt')
        self.ready('next')
        token = self.store.reserve('p', 'next', 'task')['token']
        self.store.finish(token, True, 'accepted commit receipt')
        self.assertEqual(self.store.eligibility('p', 'next')['status'], 'resolved')

    def test_rolling_project_quota_and_boundary_are_inclusive(self):
        for i in range(3):
            episode = str(i)
            self.ready(episode)
            self.store.reserve('p', episode, 'task')
        self.ready('fourth')
        self.assertEqual(self.store.eligibility('p', 'fourth')['status'], 'project_quota_exhausted')
        self.now += R.WINDOW_SECONDS
        self.assertFalse(self.store.eligibility('p', 'fourth')['allowed'])
        self.now += .01
        self.assertTrue(self.store.eligibility('p', 'fourth')['allowed'])

    def test_full_cloud_quota_preserves_initial_guidance_and_guided_local_work(self):
        for i in range(3):
            episode='reserved-'+str(i);self.ready(episode);self.store.reserve('p',episode,'task')
        self.assertEqual(self.failure('local','first')['status'],'local_retries_required')
        self.assertEqual(self.failure('local','second')['status'],'guidance_required')
        self.store.record_guidance('p','local','local-guidance','Preserve the validated transport and remove the named out-of-scope scratch file.')
        self.assertEqual(self.failure('local','guided-first',guidance='local-guidance')['status'],'guided_retries_required')
        result=self.failure('local','guided-second',guidance='local-guidance')
        self.assertEqual(result['status'],'project_quota_exhausted');self.assertFalse(result['allowed'])
        with self.assertRaises(R.RescueDenied):self.store.reserve('p','local','task')
        self.assertEqual(self.store.eligibility('p','local')['attempts_168h'],3)

    def test_concurrent_reservations_cannot_exceed_budget(self):
        for i in range(5):
            self.ready(str(i))
        def reserve(i):
            try:
                return self.store.reserve('p', str(i), 'task')['token']
            except R.RescueDenied:
                return None
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(reserve, range(5)))
        self.assertEqual(sum(x is not None for x in results), 3)

    def test_task_rewording_cannot_reset_bound_episode(self):
        first = self.store.bind_episode('p', 'original behaviour fingerprint', 'original')
        self.assertEqual(self.store.bind_episode('p', 'original behaviour fingerprint', 'new-id'), first)
        self.assertNotEqual(self.store.bind_episode('other-project', 'original behaviour fingerprint'), first)

    def test_conflicting_replays_and_unrelated_database_are_rejected(self):
        self.failure('e', 'same')
        with self.assertRaises(R.RescueDenied):
            self.failure('another', 'same')
        self.failure('e', 'second')
        self.store.record_guidance('p', 'e', 'g', 'Enforce the expiration check at the authorization boundary.')
        with self.assertRaises(R.RescueDenied):
            self.store.record_guidance('p', 'e', 'g', 'Different guidance cannot borrow the same receipt identity.')
        other = self.path.with_name('unrelated.sqlite3')
        con = sqlite3.connect(other)
        con.execute('PRAGMA application_id=12345')
        con.close()
        with self.assertRaises(R.RescueDenied):
            R.RescueStore(other).bind_episode('p', 'fingerprint')

    def test_coexists_with_history_and_cloud_policy_tables(self):
        con = sqlite3.connect(self.path)
        con.execute('CREATE TABLE qc_events(id TEXT)')
        con.execute('CREATE TABLE cp_invocations(id TEXT)')
        con.commit()
        con.close()
        self.assertTrue(self.ready('e')['allowed'])


if __name__ == '__main__':
    unittest.main()
