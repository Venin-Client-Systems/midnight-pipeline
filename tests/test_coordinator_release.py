"""Independent release-gate checks: real temporary Git, entirely fake GitHub."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import coordinator_release as C
import git_delivery as G


class CoordinatorReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = self.folder / 'midnight'
        self.runtime = self.folder / 'runtime'
        self.root.mkdir()
        self.runtime.mkdir()
        self.real_git = G.git
        self.run_git('init', '--quiet', '--initial-branch=main')
        self.run_git('config', 'user.name', 'Codex')
        self.run_git('config', 'user.email', 'codex@localhost')
        (self.root / 'source.txt').write_text('original\n')
        self.run_git('add', '.')
        self.run_git('commit', '--quiet', '-m', 'base')
        self.base = self.run_git('rev-parse', 'HEAD')
        (self.root / 'source.txt').write_text('reviewed release\n')
        self.run_git('commit', '--quiet', '-am', 'reviewed coordinator release')
        self.head = self.run_git('rev-parse', 'HEAD')
        self.tree = self.run_git('rev-parse', 'HEAD^{tree}')
        self.merged = self.run_git('commit-tree', self.tree, '-p', self.base, '-m', 'confirmed remote squash')
        self.repository = G.ORGANISATION + '/midnight'
        self.endpoint = 'repos/' + self.repository
        self.path = self.runtime / 'coordinator-releases.json'
        self.check_path = self.runtime / 'checks.json'
        self.review_path = self.runtime / 'review.json'
        self.checks = {'schema_version': 1, 'passed': True, 'head': self.head, 'tree': self.tree,
                       'commands': [{'command': ['python3', '-m', 'unittest', 'discover', '-s', 'tests'],
                                     'returncode': 0, 'log_sha256': 'a' * 64}]}
        self.review = {'schema_version': 1, 'decision': 'accept', 'issues': [], 'head': self.head,
                       'tree': self.tree, 'reviewer': 'Codex independent cross-review',
                       'evidence': ['Independent review of this exact source tree completed.']}
        self.item = {'repository': self.repository, 'canonical': str(self.root), 'base': self.base,
                     'head': self.head, 'tree': self.tree, 'pr': 12, 'title': 'Reviewed coordinator release',
                     'status': 'pending', 'checks_path': str(self.check_path), 'review_path': str(self.review_path)}
        self.write_evidence()
        self.protection = {'required_status_checks': {'strict': True, 'contexts': list(G.CONTEXTS)},
                           'enforce_admins': {'enabled': True},
                           'required_pull_request_reviews': {'required_approving_review_count': 0, 'require_code_owner_reviews': False},
                           'allow_force_pushes': {'enabled': False}, 'allow_deletions': {'enabled': False}}
        self.pr = {'number': 12, 'state': 'open', 'head': {'sha': self.head},
                   'base': {'sha': self.base, 'ref': 'main', 'repo': {'full_name': self.repository}},
                   'merged': False, 'merge_commit_sha': None}
        self.calls = []
        self.git_calls = []
        self.unknown_merge_once = False
        self.protection_error = None
        self.git_failure = None
        self.before_cas = None
        self.after_fetch = None
        self.merge_intent_seen = False
        self.remote_merged_seen_before_fetch = False
        self.git_patch = mock.patch.object(G, 'git', side_effect=self.fake_git)
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)

    def run_git(self, *args):
        return self.real_git(self.root, *args)

    def write_evidence(self):
        self.check_path.write_text(json.dumps(self.checks, sort_keys=True))
        self.review_path.write_text(json.dumps(self.review, sort_keys=True))
        self.item.update(checks_sha256=hashlib.sha256(self.check_path.read_bytes()).hexdigest(),
                         review_sha256=hashlib.sha256(self.review_path.read_bytes()).hexdigest())
        G.save(self.path, {'status': 'pending', 'releases': [copy.deepcopy(self.item)]})

    def fake_git(self, root, *args):
        self.git_calls.append(tuple(args))
        if self.git_failure and args[0] == self.git_failure:
            raise subprocess.CalledProcessError(128, ['git', *args], stderr=b'private credential-bearing diagnostic')
        if args[0] == 'fetch':
            # Simulate fetch object availability without contacting a network.
            journal = G.read(self.path)['releases'][0]
            self.remote_merged_seen_before_fetch = (journal['status'] == 'remote_merged' and journal.get('merge_commit') == self.merged)
            result = self.real_git(root, 'fetch', '--quiet', str(root), self.merged)
            if self.after_fetch:
                callback, self.after_fetch = self.after_fetch, None
                callback()
            return result
        if args[:2] == ('update-ref', 'refs/heads/main') and self.before_cas:
            callback, self.before_cas = self.before_cas, None
            callback()
        return self.real_git(root, *args)

    def api(self, endpoint, method='GET', payload=None):
        self.calls.append((endpoint, method, payload))
        if endpoint == 'user':
            return {'login': G.IDENTITY}
        if endpoint == self.endpoint:
            return {'full_name': self.repository, 'private': True, 'permissions': {'push': True}}
        if endpoint.endswith('/protection'):
            if self.protection_error:
                raise self.protection_error
            return copy.deepcopy(self.protection)
        if endpoint == self.endpoint + '/pulls/12' and method == 'GET':
            return copy.deepcopy(self.pr)
        if endpoint == self.endpoint + '/statuses/' + self.head and method == 'POST':
            return {'state': 'success'}
        if endpoint == self.endpoint + '/commits/' + self.head + '/status':
            return {'state': 'success', 'statuses': [{'context': context, 'state': 'success'} for context in G.CONTEXTS]}
        if endpoint == self.endpoint + '/pulls/12/merge' and method == 'PUT':
            self.merge_intent_seen = G.read(self.path)['releases'][0]['status'] == 'merge_intent'
            self.assertEqual(payload['sha'], self.head)
            self.assertEqual(payload['merge_method'], 'squash')
            self.assertIn('Midnight automation', payload['commit_message'])
            self.pr.update(merged=True, state='closed', merge_commit_sha=self.merged)
            if self.unknown_merge_once:
                self.unknown_merge_once = False
                raise RuntimeError('Merge response unavailable after server committed it')
            return {'merged': True, 'sha': self.merged}
        self.fail('Unexpected external operation: ' + method + ' ' + endpoint)

    def reconcile(self, **kwargs):
        return C.reconcile(self.runtime, api=self.api, **kwargs)

    def assert_no_external_writes(self):
        self.assertTrue(all(method == 'GET' for _, method, _ in self.calls), self.calls)

    def test_success_journals_before_merge_and_confirmation_then_adopts_identical_tree(self):
        original_bytes = (self.root / 'source.txt').read_bytes()
        result = self.reconcile()
        self.assertEqual(result['status'], 'complete')
        self.assertTrue(self.merge_intent_seen)
        self.assertTrue(self.remote_merged_seen_before_fetch)
        self.assertEqual(self.run_git('rev-parse', 'HEAD'), self.merged)
        self.assertEqual(self.run_git('rev-parse', 'refs/midnight/coordinator-backup/' + self.head), self.head)
        self.assertEqual((self.root / 'source.txt').read_bytes(), original_bytes)
        self.assertEqual(self.run_git('status', '--porcelain'), '')
        # A complete release is not merged or fetched a second time.
        self.calls.clear()
        self.git_calls.clear()
        self.assertEqual(self.reconcile()['status'], 'complete')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.git_calls, [])

    def test_wrong_pr_head_or_base_or_base_branch_never_attests_or_merges(self):
        variants = [('head', 'sha', 'b' * 40), ('base', 'sha', 'b' * 40), ('base', 'ref', 'other-branch')]
        for section, field, value in variants:
            with self.subTest(section=section, field=field):
                self.write_evidence()
                original = self.pr[section][field]
                self.pr[section][field] = value
                self.calls.clear()
                result = self.reconcile()
                self.assertNotEqual(result['status'], 'complete')
                self.assert_no_external_writes()
                self.assertEqual(self.run_git('rev-parse', 'HEAD'), self.head)
                self.pr[section][field] = original

    def test_dirty_or_wrong_local_branch_never_attests_or_merges(self):
        (self.root / 'untracked.txt').write_text('preserve my change')
        result = self.reconcile()
        self.assertNotEqual(result['status'], 'complete')
        self.assert_no_external_writes()
        (self.root / 'untracked.txt').unlink()
        self.calls.clear()
        self.run_git('checkout', '--quiet', '-b', 'other')
        result = self.reconcile()
        self.assertNotEqual(result['status'], 'complete')
        self.assert_no_external_writes()
        self.assertEqual(self.run_git('symbolic-ref', 'HEAD'), 'refs/heads/other')

    def test_failed_missing_or_unbound_checks_never_post_success(self):
        originals = copy.deepcopy(self.checks)
        variants = [dict(originals, passed=False), dict(originals, head='c' * 40),
                    dict(originals, tree='c' * 40), dict(originals, commands=[]),
                    dict(originals, commands=[dict(originals['commands'][0], returncode=1)]),
                    dict(originals, commands=[dict(originals['commands'][0], log_sha256='not-a-hash')]),
                    dict(originals, commands=[dict(originals['commands'][0], command='not-an-argument-list')]),
                    dict(originals, commands=[dict(originals['commands'][0], command=[None])]),
                    {'passed': True}]
        for receipt in variants:
            with self.subTest(receipt=receipt):
                self.checks = receipt
                self.write_evidence()
                self.calls.clear()
                self.assertNotEqual(self.reconcile()['status'], 'complete')
                self.assert_no_external_writes()

    def test_unbound_or_nonindependent_review_never_post_success(self):
        original = copy.deepcopy(self.review)
        variants = [dict(original, head='d' * 40), dict(original, tree='d' * 40),
                    dict(original, decision='revise'), dict(original, issues=['Unresolved defect']),
                    dict(original, reviewer='implementation worker'), dict(original, evidence=[]),
                    {'decision': 'accept', 'issues': []}]
        for receipt in variants:
            with self.subTest(receipt=receipt):
                self.review = receipt
                self.write_evidence()
                self.calls.clear()
                self.assertNotEqual(self.reconcile()['status'], 'complete')
                self.assert_no_external_writes()

    def test_modified_evidence_fails_closed_without_writes(self):
        self.check_path.write_text('changed after attestation')
        self.assertNotEqual(self.reconcile()['status'], 'complete')
        self.assert_no_external_writes()

    def test_unknown_merge_response_is_reconciled_without_second_merge(self):
        self.unknown_merge_once = True
        first = self.reconcile()
        self.assertEqual(first['status'], 'pending')
        self.assertEqual(G.read(self.path)['releases'][0]['status'], 'merge_intent')
        self.assertEqual(self.run_git('rev-parse', 'HEAD'), self.head)
        second = self.reconcile()
        self.assertEqual(second['status'], 'complete')
        self.assertEqual(sum(method == 'PUT' for _, method, _ in self.calls), 1)
        self.assertEqual(self.run_git('rev-parse', 'HEAD'), self.merged)

    def test_different_remote_merge_tree_is_never_adopted(self):
        wrong_tree = self.run_git('rev-parse', self.base + '^{tree}')
        self.merged = self.run_git('commit-tree', wrong_tree, '-p', self.base, '-m', 'wrong remote content')
        result = self.reconcile()
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['releases'][0]['status'], 'remote_merged')
        self.assertEqual(self.run_git('rev-parse', 'HEAD'), self.head)
        self.assertEqual((self.root / 'source.txt').read_text(), 'reviewed release\n')
        self.assertFalse(any(args[:2] == ('update-ref', 'refs/heads/main') for args in self.git_calls))

    def test_fetch_failure_is_journalled_without_exposing_subprocess_diagnostics(self):
        self.git_failure = 'fetch'
        result = self.reconcile()
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['releases'][0]['status'], 'remote_merged')
        self.assertNotIn('private credential', json.dumps(result))
        self.assertEqual(self.run_git('rev-parse', 'HEAD'), self.head)
        self.git_failure = None
        self.assertEqual(self.reconcile()['status'], 'complete')
        self.assertEqual(sum(method == 'PUT' for _, method, _ in self.calls), 1)

    def test_compare_and_swap_ref_update_preserves_concurrent_head_change(self):
        newer = self.run_git('commit-tree', self.tree, '-p', self.head, '-m', 'concurrent coordinator change')
        self.before_cas = lambda: self.real_git(self.root, 'update-ref', 'refs/heads/main', newer, self.head)
        result = self.reconcile()
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(self.run_git('rev-parse', 'HEAD'), newer)
        self.assertEqual(self.run_git('rev-parse', 'refs/midnight/coordinator-backup/' + self.head), self.head)
        self.assertEqual((self.root / 'source.txt').read_text(), 'reviewed release\n')

    def test_already_adopted_recovery_does_not_hide_concurrent_head_change(self):
        self.item.update(status='remote_merged', merge_commit=self.merged)
        self.write_evidence()
        self.pr.update(merged=True, state='closed', merge_commit_sha=self.merged)
        self.run_git('update-ref', 'refs/midnight/coordinator-backup/' + self.head, self.head)
        self.run_git('update-ref', 'refs/heads/main', self.merged, self.head)
        newer = self.run_git('commit-tree', self.tree, '-p', self.merged, '-m', 'concurrent change after prior adoption')
        self.after_fetch = lambda: self.real_git(self.root, 'update-ref', 'refs/heads/main', newer, self.merged)
        result = self.reconcile()
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(self.run_git('rev-parse', 'HEAD'), newer)
        self.assert_no_external_writes()

    def test_advanced_local_head_is_held_before_any_external_write(self):
        newer = self.run_git('commit-tree', self.tree, '-p', self.head, '-m', 'new coordinator work')
        self.run_git('update-ref', 'refs/heads/main', newer, self.head)
        self.assertEqual(self.reconcile()['status'], 'pending')
        self.assertEqual(self.run_git('rev-parse', 'HEAD'), newer)
        self.assert_no_external_writes()

    def test_internally_consistent_but_wrong_tree_attestation_never_merges(self):
        self.item['tree'] = self.checks['tree'] = self.review['tree'] = 'e' * 40
        self.write_evidence()
        self.assertEqual(self.reconcile()['status'], 'pending')
        self.assert_no_external_writes()
        self.assertEqual(self.run_git('rev-parse', 'HEAD'), self.head)

    def test_malformed_receipt_shape_is_held_without_external_writes(self):
        self.checks = []
        self.write_evidence()
        self.assertEqual(self.reconcile()['status'], 'pending')
        self.assert_no_external_writes()

    def test_private_protection_unavailable_never_buys_plan_or_weakens_policy(self):
        self.protection_error = RuntimeError('GitHub API unavailable HTTP 403')
        result = self.reconcile()
        self.assertEqual(result['status'], 'pending')
        self.assert_no_external_writes()
        self.assertEqual(self.run_git('rev-parse', 'HEAD'), self.head)
        self.assertFalse(any('/merge' in endpoint for endpoint, _, _ in self.calls))
        self.assertFalse(any('billing' in endpoint or 'subscription' in endpoint for endpoint, _, _ in self.calls))

    def test_weak_protection_is_not_bypassed(self):
        for patch in [{'enforce_admins': {'enabled': False}},
                      {'required_status_checks': {'strict': False, 'contexts': list(G.CONTEXTS)}},
                      {'required_pull_request_reviews': {'required_approving_review_count': 1}}]:
            with self.subTest(patch=patch):
                original = copy.deepcopy(self.protection)
                self.protection.update(patch)
                self.calls.clear()
                self.assertEqual(self.reconcile()['status'], 'pending')
                self.assert_no_external_writes()
                self.protection = original

    def test_paused_or_expired_window_cannot_merge(self):
        (self.runtime / 'PAUSED').write_text('owner pause')
        self.assertEqual(self.reconcile()['status'], 'pending')
        self.assertFalse(any(method == 'PUT' for _, method, _ in self.calls))
        (self.runtime / 'PAUSED').unlink()
        self.calls.clear()
        expired = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        self.assertEqual(self.reconcile(cutoff=expired)['status'], 'pending')
        self.assertFalse(any(method == 'PUT' for _, method, _ in self.calls))


if __name__ == '__main__':
    unittest.main()
