"""PR-only trusted checks: real temporary Git, retained logs, fake GitHub."""
import copy
import datetime as dt
import fcntl
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

import coordinator_attestation as A
import coordinator_release as C
import git_delivery as G
import test_coordinator_release as Fixtures


class PrAttestationTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixtures.CoordinatorReleaseTests('test_dirty_or_wrong_local_branch_never_attests_or_merges')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.run_git('checkout', '--quiet', '-b', 'codex/reviewed-pr')
        self.f.pr['head']['repo'] = {'full_name': A.REPOSITORY}
        self.root = self.f.runtime / 'coordinator-release-evidence' / 'reviewed'
        self.root.mkdir(parents=True)
        self.source = {'base': self.f.base, 'head': self.f.head, 'tree': self.f.tree}
        self.log = self.root / 'suite.log'
        self.log.write_text('test_required_behaviour ... ok\n\n----------------------------------------------------------------------\nRan 1 test in 0.123s\n\nOK\n')
        self.checks = {'schema_version': 2, 'source': self.source, 'passed': True,
            'commands': [{'command': ['python3', *A.SUITE], 'returncode': 0, 'tests': 1, 'skips': 0,
                'log': self.descriptor(self.log)}]}
        self.review = {'schema_version': 2, 'source': self.source, 'decision': 'accept', 'issues': [],
            'reviewer': A.REVIEWER, 'evidence': ['Independent review of source and complete check log.']}
        self.request = {'schema_version': 1, 'kind': 'codex-pr-attestation', 'repository': A.REPOSITORY,
            'pr': 12, 'source_root': str(self.f.root), **self.source}
        self.request_path = self.root / 'request.json'
        self.write_bundle()
        self.rows = []; self.posts = []; self.calls = []
        self.uncertain = False; self.drop_post = False; self.read_hook = None

    def descriptor(self, path):
        return {'path': path.relative_to(self.root).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def write_bundle(self):
        path = self.root / 'checks.json'; path.write_text(json.dumps(self.checks))
        self.request['checks'] = self.descriptor(path)
        self.review['checks_sha256'] = self.request['checks']['sha256']
        path = self.root / 'review.json'; path.write_text(json.dumps(self.review))
        self.request['review'] = self.descriptor(path)
        self.request_path.write_text(json.dumps(self.request))
        self.request_sha = hashlib.sha256(self.request_path.read_bytes()).hexdigest()

    def journal(self):
        return self.f.runtime / 'coordinator-attestations' / (self.request_sha + '.json')

    def api(self, endpoint, method='GET', payload=None):
        self.calls.append((endpoint, method, copy.deepcopy(payload)))
        if method == 'GET' and self.read_hook:
            callback, self.read_hook = self.read_hook, None
            callback(endpoint)
        if endpoint == self.f.endpoint + '/git/ref/heads/main':
            return {'object': {'sha': self.f.base}}
        if endpoint == self.f.endpoint + '/git/commits/' + self.f.head:
            return {'tree': {'sha': self.f.tree}}
        if '/statuses?per_page=100&page=' in endpoint:
            page = int(endpoint.rsplit('=', 1)[1])
            return copy.deepcopy(self.rows[(page - 1) * 100:page * 100])
        if endpoint == self.f.endpoint + '/statuses/' + self.f.head and method == 'POST':
            self.assertEqual(G.read(self.journal())['effects'][payload['context']]['state'], 'intent')
            self.posts.append(copy.deepcopy(payload))
            if self.drop_post: raise RuntimeError('Uncertain request with no remote result')
            row = dict(payload, id=len(self.rows) + 1, creator={'login': G.IDENTITY})
            self.rows.insert(0, row)
            if self.uncertain:
                self.uncertain = False
                raise RuntimeError('Response lost after remote publication')
            return copy.deepcopy(row)
        return self.f.api(endpoint, method, payload)

    def attest(self, **kwargs):
        return C.attest_pr(self.f.runtime, self.request_path, request_sha256=self.request_sha, api=self.api, **kwargs)

    def assert_held_without_posts(self):
        result = self.attest()
        self.assertEqual(result['status'], 'held', result)
        self.assertEqual(self.posts, [])
        return result

    def test_exact_reviewed_pr_attests_once_without_merge_or_adoption(self):
        before = self.f.run_git('rev-parse', 'HEAD')
        result = self.attest(); self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(len(self.posts), 2)
        self.assertEqual(self.attest()['status'], 'complete'); self.assertEqual(len(self.posts), 2)
        self.assertEqual(self.f.run_git('rev-parse', 'HEAD'), before)
        self.assertEqual(self.f.run_git('symbolic-ref', 'HEAD'), 'refs/heads/codex/reviewed-pr')
        self.assertTrue(all(method == 'GET' or '/statuses/' in endpoint for endpoint, method, _ in self.calls))
        self.assertFalse(any(args[0] in ('fetch', 'update-ref', 'merge', 'push') for args in self.f.git_calls))

    def test_uncertain_completed_status_reconciles_without_second_post(self):
        self.uncertain = True
        self.assertEqual(self.attest()['status'], 'held'); self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.attest()['status'], 'complete'); self.assertEqual(len(self.posts), 2)
        self.assertEqual(len([row for row in self.posts if row['context'] == G.CONTEXTS[0]]), 1)

    def test_uncertain_missing_status_never_retries_or_refunds_intent(self):
        self.drop_post = True
        self.assertEqual(self.attest()['status'], 'held')
        before = G.read(self.journal())['effects']
        self.drop_post = False
        self.assertEqual(self.attest()['status'], 'held'); self.assertEqual(len(self.posts), 1)
        self.assertEqual(G.read(self.journal())['effects'], before)

    def test_changed_receipt_or_log_fails_before_publication(self):
        for name in ('checks.json', 'review.json', 'suite.log'):
            with self.subTest(name=name):
                path = self.root / name; original = path.read_bytes(); path.write_bytes(original + b' ')
                self.assert_held_without_posts(); path.write_bytes(original)

    def test_stale_source_base_tree_and_nonindependent_review_fail_closed(self):
        original_checks = copy.deepcopy(self.checks); original_review = copy.deepcopy(self.review)
        changes = [('checks', 'source', dict(self.source, base='a' * 40)),
            ('checks', 'source', dict(self.source, head='a' * 40)),
            ('checks', 'source', dict(self.source, tree='a' * 40)),
            ('review', 'source', dict(self.source, base='a' * 40)),
            ('review', 'decision', 'revise'), ('review', 'issues', ['Unresolved']),
            ('review', 'reviewer', 'implementation worker'), ('review', 'evidence', [])]
        for owner, field, value in changes:
            with self.subTest(owner=owner, field=field, value=value):
                self.checks = copy.deepcopy(original_checks); self.review = copy.deepcopy(original_review)
                getattr(self, owner)[field] = value; self.write_bundle(); self.assert_held_without_posts()

    def test_incomplete_failed_skipped_and_unhashed_checks_fail_closed(self):
        original = copy.deepcopy(self.checks)
        variants = [dict(original, passed=False), dict(original, commands=[])]
        for field, value in [('command', ['python3', '-m', 'unittest', 'one_test']), ('returncode', 1),
                ('returncode', False), ('tests', 2), ('skips', 1), ('log', {'path': 'suite.log', 'sha256': 'a' * 64})]:
            command = dict(original['commands'][0]); command[field] = value
            variants.append(dict(original, commands=[command]))
        for checks in variants:
            with self.subTest(checks=checks):
                self.checks = checks; self.write_bundle(); self.assert_held_without_posts()

    def test_missing_terminal_or_expected_failure_summary_cannot_attest(self):
        for summary in ('Ran 1 test in 0.1s\n\nFAILED (failures=1)\n',
                'Ran 1 test in 0.1s\n\nOK (skipped=1)\n', 'Ran 1 test in 0.1s\n\nOK (expected failures=1)\n', ''):
            with self.subTest(summary=summary):
                self.log.write_text('partial output\n' + summary)
                self.checks['commands'][0]['log'] = self.descriptor(self.log)
                self.write_bundle(); self.assert_held_without_posts()

    def test_remote_pr_drift_fork_or_closed_state_prevents_statuses(self):
        original = copy.deepcopy(self.f.pr)
        variants = [('head', 'sha', 'a' * 40), ('base', 'sha', 'a' * 40), ('base', 'ref', 'staging'),
            ('head', 'repo', {'full_name': 'other/midnight'})]
        for section, key, value in variants:
            with self.subTest(section=section, key=key):
                self.f.pr = copy.deepcopy(original); self.f.pr[section][key] = value
                self.assert_held_without_posts()
        self.f.pr = copy.deepcopy(original); self.f.pr['state'] = 'closed'; self.assert_held_without_posts()

    def test_identity_protection_remote_tree_and_main_are_checked(self):
        original = self.api
        variants = [('user', {'login': 'wrong'}), (self.f.endpoint, {'full_name': A.REPOSITORY, 'private': False}),
            (self.f.endpoint + '/branches/main/protection', {}),
            (self.f.endpoint + '/git/ref/heads/main', {'object': {'sha': 'a' * 40}}),
            (self.f.endpoint + '/git/commits/' + self.f.head, {'tree': {'sha': 'a' * 40}})]
        for changed, response in variants:
            with self.subTest(endpoint=changed):
                def altered(endpoint, method='GET', payload=None):
                    return response if endpoint == changed else original(endpoint, method, payload)
                with mock.patch.object(self, 'api', side_effect=altered): self.assert_held_without_posts()

    def test_dirty_or_advanced_checkout_prevents_statuses(self):
        path = self.f.root / 'source.txt'; original = path.read_text()
        path.write_text('concurrent owner edit'); self.assert_held_without_posts(); path.write_text(original)
        self.f.run_git('commit', '--allow-empty', '--quiet', '-m', 'advanced source')
        self.assert_held_without_posts()

    def test_pause_cutoff_and_change_during_read_are_rechecked(self):
        (self.f.runtime / 'PAUSED').touch(); self.assert_held_without_posts()
        (self.f.runtime / 'PAUSED').unlink()
        self.assertEqual(self.attest(cutoff=dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1))['status'], 'held')
        self.assertEqual(self.posts, [])
        self.read_hook = lambda _: self.log.write_text('changed during remote observation')
        self.assert_held_without_posts()

    def test_pause_after_intent_prevents_external_effect(self):
        save = G.save
        def paused(path, value):
            save(path, value)
            if path == self.journal() and value.get('status') == 'publishing':
                (self.f.runtime / 'PAUSED').touch()
        with mock.patch.object(G, 'save', side_effect=paused): self.assert_held_without_posts()
        self.assertEqual(G.read(self.journal())['effects'][G.CONTEXTS[0]]['state'], 'intent')

    def test_exclusive_writer_holds_concurrent_attempt(self):
        folder = self.f.runtime / 'coordinator-attestations'; folder.mkdir()
        with (folder / 'writer.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_held_without_posts()

    def test_paginated_status_reconciliation_checks_all_pages(self):
        self.uncertain = True; self.attest()
        self.rows[:0] = [{'id': 1000 + i, 'context': 'unrelated'} for i in range(100)]
        self.assertEqual(self.attest()['status'], 'complete')
        self.assertEqual(len(self.posts), 2)
        self.assertTrue(any(endpoint.endswith('page=2') for endpoint, _, _ in self.calls))

    def test_newer_failure_supersedes_old_matching_success(self):
        self.assertEqual(self.attest()['status'], 'complete')
        self.rows.insert(0, {'id': 999, 'context': G.CONTEXTS[0].upper(), 'state': 'failure'})
        self.assertEqual(self.attest()['status'], 'held'); self.assertEqual(len(self.posts), 2)

    def test_unsafe_and_malformed_evidence_is_held(self):
        path = self.root / 'review.json'; original = path.read_bytes()
        for data in (b'null', b'[]', b'{', b'\xff', b'{"schema_version":2,"schema_version":2}'):
            with self.subTest(data=data):
                path.write_bytes(data); self.request['review'] = self.descriptor(path)
                self.request_path.write_text(json.dumps(self.request)); self.request_sha = hashlib.sha256(self.request_path.read_bytes()).hexdigest()
                self.assert_held_without_posts()
        path.write_bytes(original); self.write_bundle()
        saved = self.root / 'saved-review.json'; path.rename(saved); path.symlink_to(saved)
        self.assert_held_without_posts()

    def test_request_digest_and_path_are_mandatory(self):
        with self.assertRaises(ValueError):
            C.attest_pr(self.f.runtime, self.request_path, request_sha256='0' * 64, api=self.api)
        with self.assertRaises(ValueError):
            C.attest_pr(self.f.runtime, self.f.runtime / 'outside.json', request_sha256=self.request_sha, api=self.api)
        self.assertEqual(self.posts, [])

    def test_crash_after_remote_success_before_confirmation_recovers_once(self):
        save = G.save
        def crash(path, value):
            if path == self.journal() and any(effect.get('state') == 'confirmed' for effect in value.get('effects', {}).values()):
                raise InterruptedError('Crash before confirmation journal')
            save(path, value)
        with mock.patch.object(G, 'save', side_effect=crash), self.assertRaises(InterruptedError):
            self.attest()
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(G.read(self.journal())['effects'][G.CONTEXTS[0]]['state'], 'intent')
        self.assertEqual(self.attest()['status'], 'complete'); self.assertEqual(len(self.posts), 2)

    def test_changed_request_during_external_reads_prevents_effect(self):
        self.read_hook = lambda _: self.request_path.write_text(self.request_path.read_text() + ' ')
        self.assert_held_without_posts()

    def test_receipt_change_after_first_status_holds_second_effect(self):
        original = self.api
        def changed(endpoint, method='GET', payload=None):
            result = original(endpoint, method, payload)
            if method == 'POST': self.log.write_text('changed after first publication')
            return result
        with mock.patch.object(self, 'api', side_effect=changed):
            self.assertEqual(self.attest()['status'], 'held')
        self.assertEqual(len(self.posts), 1)

    def test_malformed_remote_status_response_is_held(self):
        original = self.api
        def malformed(endpoint, method='GET', payload=None):
            if '/statuses?' in endpoint: return {'error': 'partial status response'}
            return original(endpoint, method, payload)
        with mock.patch.object(self, 'api', side_effect=malformed): self.assert_held_without_posts()

    def test_hardlink_oversize_and_directory_traversal_are_rejected(self):
        import os
        path = self.root / 'review.json'; copy_path = self.root / 'linked-review.json'
        os.link(path, copy_path); self.assert_held_without_posts(); copy_path.unlink()
        path.write_bytes(b'x' * (1024 * 1024 + 1))
        self.request['review'] = self.descriptor(path)
        self.request_path.write_text(json.dumps(self.request)); self.request_sha = hashlib.sha256(self.request_path.read_bytes()).hexdigest()
        self.assert_held_without_posts()
        self.request['review']['path'] = '../review.json'
        self.request_path.write_text(json.dumps(self.request)); self.request_sha = hashlib.sha256(self.request_path.read_bytes()).hexdigest()
        self.assert_held_without_posts()

    def test_invalid_present_journals_preserve_uncertainty_and_original_bytes(self):
        self.drop_post = True; self.assertEqual(self.attest()['status'], 'held'); self.drop_post = False
        original = self.journal().read_bytes(); valid = json.loads(original)
        variants = [b'null\n', b'[]\n', b'{}\n', b'{', b'\xff', b'x' * (65536 + 1)]
        for value in (dict(valid, schema_version=999), dict(valid, schema_version=True),
                dict(valid, effects={G.CONTEXTS[0]: None}), dict(valid, effects={G.CONTEXTS[0]: {}}),
                dict(valid, effects={G.CONTEXTS[0]: {'state': 'invented'}}),
                dict(valid, effects={G.CONTEXTS[0]: {'state': 'confirmed', 'id': False}})):
            variants.append(json.dumps(value).encode())
        for data in variants:
            with self.subTest(data=data[:100]):
                self.journal().write_bytes(data); before = len(self.posts)
                try: result = self.attest()
                except Exception as exc: self.fail('Malformed journal must return held: ' + repr(exc))
                self.assertEqual(result['status'], 'held'); self.assertEqual(len(self.posts), before)
                self.assertEqual(self.journal().read_bytes(), data)
        self.journal().write_bytes(original)

    def test_dangling_journal_and_pause_links_are_preserved_and_hold(self):
        self.drop_post = True; self.attest(); self.drop_post = False
        self.journal().unlink(); self.journal().symlink_to(self.f.runtime / 'missing-journal')
        before = len(self.posts); result = self.attest()
        self.assertEqual(result['status'], 'held'); self.assertEqual(len(self.posts), before)
        self.assertTrue(self.journal().is_symlink())
        self.journal().unlink()
        pause = self.f.runtime / 'PAUSED'; pause.symlink_to(self.f.runtime / 'missing-pause')
        result = self.attest(); self.assertEqual(result['status'], 'held'); self.assertEqual(len(self.posts), before)
        self.assertTrue(pause.is_symlink())

    def test_symlinked_writer_and_journal_folder_never_publish(self):
        folder = self.f.runtime / 'coordinator-attestations'; folder.mkdir()
        target = self.f.runtime / 'external-lock'; target.write_text('preserve lock target')
        (folder / 'writer.lock').symlink_to(target)
        self.assert_held_without_posts(); self.assertEqual(target.read_text(), 'preserve lock target')
        (folder / 'writer.lock').unlink(); folder.rmdir()
        target_dir = self.f.runtime / 'other-journals'; target_dir.mkdir(); folder.symlink_to(target_dir)
        self.assert_held_without_posts(); self.assertEqual(list(target_dir.iterdir()), [])

    def test_hidden_git_flags_cannot_conceal_changed_source(self):
        path = self.f.root / 'source.txt'; original = path.read_bytes()
        for flag in ('assume-unchanged', 'skip-worktree'):
            with self.subTest(flag=flag):
                self.f.run_git('update-index', '--' + flag, 'source.txt')
                path.write_text('unreviewed hidden modification\n')
                self.assertEqual(self.f.run_git('status', '--porcelain'), '')
                self.assert_held_without_posts()
                self.f.run_git('update-index', '--no-' + flag, 'source.txt'); path.write_bytes(original)

    def test_hidden_source_change_after_intent_stops_publication(self):
        save = G.save
        def changed(path, value):
            save(path, value)
            if path == self.journal() and value.get('status') == 'publishing':
                self.f.run_git('update-index', '--assume-unchanged', 'source.txt')
                (self.f.root / 'source.txt').write_text('changed after intent\n')
        with mock.patch.object(G, 'save', side_effect=changed): self.assert_held_without_posts()

    def test_pause_metadata_access_failure_is_held(self):
        original = Path.lstat
        def inaccessible(path):
            if path == self.f.runtime / 'PAUSED': raise PermissionError('Uncertain pause metadata')
            return original(path)
        with mock.patch.object(Path, 'lstat', inaccessible): self.assert_held_without_posts()

    def test_actual_source_bytes_modes_and_empty_tracked_files_are_verified(self):
        path = self.f.root / 'source.txt'; original = path.read_bytes(); mode = path.stat().st_mode
        A.checked_checkout(self.f.root, self.f.head)
        path.write_text('different tracked content\n')
        with self.assertRaisesRegex(ValueError, 'tracked bytes'): A.checked_checkout(self.f.root, self.f.head)
        path.write_bytes(original); path.chmod(mode | 0o111)
        with self.assertRaisesRegex(ValueError, 'tracked mode'): A.checked_checkout(self.f.root, self.f.head)
        path.chmod(mode); (self.f.root / 'empty.py').touch()
        self.f.run_git('add', 'empty.py'); self.f.run_git('commit', '--quiet', '-m', 'empty tracked source')
        A.checked_checkout(self.f.root, self.f.run_git('rev-parse', 'HEAD'))

    def journal_race(self, variant):
        original_api = self.api; save = G.save; fired = []
        def change():
            if fired: return
            fired.append(True)
            if variant == 'writer':
                lock = self.journal().parent / 'writer.lock'; lock.unlink(); lock.write_text('replacement writer')
            else: self.journal().write_text('null\n')
        def persist(path, value):
            save(path, value)
            if variant == 'after_intent' and path == self.journal() and value.get('status') == 'publishing': change()
        def remote(endpoint, method='GET', payload=None):
            if method == 'POST':
                # A real remote service cannot validate our private journal.
                self.posts.append(copy.deepcopy(payload)); row = dict(payload, id=1, creator={'login': G.IDENTITY})
                self.rows.insert(0, row); return row
            if (variant != 'after_intent' and endpoint.endswith('/git/commits/' + self.f.head)
                    and self.journal().exists() and not fired): change()
            return original_api(endpoint, method, payload)
        with mock.patch.object(self, 'api', side_effect=remote), mock.patch.object(G, 'save', side_effect=persist):
            try: result = self.attest()
            except Exception as exc: self.fail('Conflict must return held: ' + repr(exc))
        self.assertEqual(fired, [True]); self.assertEqual(result['status'], 'held'); self.assertEqual(self.posts, [])
        if variant == 'writer':
            self.assertEqual((self.journal().parent / 'writer.lock').read_text(), 'replacement writer')
            self.assertEqual(json.loads(self.journal().read_text())['effects'][G.CONTEXTS[0]], {'state': 'intent'})
        else: self.assertEqual(self.journal().read_text(), 'null\n')

    def test_changed_journal_after_intent_prevents_post_and_preserves_conflict(self):
        self.journal_race('after_intent')

    def test_changed_journal_during_final_read_prevents_post_and_preserves_conflict(self):
        self.journal_race('during_read')

    def test_replaced_writer_during_final_read_prevents_post_and_preserves_conflict(self):
        self.journal_race('writer')

    def test_local_status_config_cannot_hide_untracked_source(self):
        self.f.run_git('config', 'status.showUntrackedFiles', 'no')
        (self.f.root / 'unreviewed.py').write_text('unreviewed = True\n')
        self.assertEqual(self.f.run_git('status', '--porcelain'), '')
        self.assert_held_without_posts()


if __name__ == '__main__': unittest.main()
