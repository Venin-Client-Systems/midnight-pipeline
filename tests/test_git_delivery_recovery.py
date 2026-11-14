"""Local Git plus a stateful fake GitHub API: never contacts a remote service."""
import copy
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import git_delivery as G
import github_setup


def git(root, *args):
    return subprocess.check_output(['git', *map(str, args)], cwd=root, text=True, stderr=subprocess.PIPE).strip()


class FakeGitHub:
    def __init__(self, root, remote, base):
        self.root, self.remote, self.base = root, remote, base
        self.prefix = 'repos/' + G.ORGANISATION + '/exampleapp'
        self.pr = None
        self.posts, self.merges, self.statuses = 0, 0, {}
        self.uncertain_create = self.uncertain_merge = False
        self.stale_head = self.stale_base = False
        self.calls = []

    def __call__(self, endpoint, method='GET', payload=None):
        self.calls.append((endpoint, method, payload))
        if endpoint == 'user':
            return {'login': G.IDENTITY}
        if endpoint == self.prefix:
            return {'full_name': G.ORGANISATION + '/exampleapp', 'private': True, 'permissions': {'push': True, 'admin': True}}
        if endpoint.endswith('/protection'):
            return {'required_status_checks': {'strict': True, 'contexts': list(G.CONTEXTS)},
                    'enforce_admins': {'enabled': True},
                    'required_pull_request_reviews': {'required_approving_review_count': 0},
                    'allow_force_pushes': {'enabled': False}, 'allow_deletions': {'enabled': False}}
        if '/pulls?' in endpoint:
            if not self.pr or ('state=open' in endpoint and self.pr['state'] != 'open'):
                return []
            return [copy.deepcopy(self.pr)]
        if endpoint.endswith('/git/ref/heads/main'):
            return {'object': {'sha': 'b' * 40 if self.stale_base else self.base}}
        if endpoint.endswith('/pulls') and method == 'POST':
            self.posts += 1
            head = git(self.remote, 'rev-parse', 'refs/heads/' + payload['head'])
            self.pr = {'number': 1, 'html_url': 'https://github.invalid/exampleapp/pull/1',
                       'head': {'sha': head, 'ref': payload['head']}, 'base': {'sha': self.base, 'ref': 'main'},
                       'state': 'open', 'merged': False, 'merge_commit_sha': None}
            if self.uncertain_create:
                self.uncertain_create = False
                raise RuntimeError('Connection lost after PR creation')
            return copy.deepcopy(self.pr)
        if endpoint.endswith('/pulls/1'):
            result = copy.deepcopy(self.pr)
            if self.stale_head:
                result['head']['sha'] = 'f' * 40
            if self.stale_base:
                result['base']['sha'] = 'b' * 40
            return result
        if '/statuses/' in endpoint and method == 'POST':
            self.statuses[payload['context']] = payload['state']
            return {}
        if endpoint.endswith('/status'):
            return {'state': 'success', 'statuses': [{'context': key, 'state': value} for key, value in self.statuses.items()]}
        if endpoint.endswith('/pulls/1/merge') and method == 'PUT':
            self.merges += 1
            if self.stale_base or self.stale_head or payload['sha'] != self.pr['head']['sha']:
                raise RuntimeError('Strict source protection rejected stale merge')
            tree = git(self.remote, 'rev-parse', self.pr['head']['sha'] + '^{tree}')
            merged = git(self.remote, '-c', 'user.name=Codex', '-c', 'user.email=codex@localhost',
                         'commit-tree', tree, '-p', self.base, '-m', 'Synthetic squash merge')
            git(self.remote, 'update-ref', 'refs/heads/main', merged)
            self.pr.update(merged=True, state='closed', merge_commit_sha=merged)
            self.base = merged
            if self.uncertain_merge:
                self.uncertain_merge = False
                raise RuntimeError('Connection lost after remote merge')
            return {'merged': True, 'sha': merged}
        raise AssertionError((endpoint, method, payload))


class DeliveryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.canonical = self.root / 'canonical'
        self.canonical.mkdir()
        git(self.canonical, 'init', '--quiet', '-b', 'main')
        git(self.canonical, 'config', 'user.name', 'Test')
        git(self.canonical, 'config', 'user.email', 'test@localhost')
        (self.canonical / 'value.txt').write_text('before\n')
        git(self.canonical, 'add', '.')
        git(self.canonical, 'commit', '--quiet', '-m', 'Baseline')
        self.base = git(self.canonical, 'rev-parse', 'HEAD')
        self.remote = self.root / 'fake-remote.git'
        subprocess.run(['git', 'clone', '--bare', '--quiet', str(self.canonical), str(self.remote)], check=True, capture_output=True)
        self.run_id = '20260101T000000Z-F1-abcdef'
        self.directory = self.root / 'runtime' / 'runs' / self.run_id
        self.directory.mkdir(parents=True)
        (self.canonical / 'value.txt').write_text('after\n')
        patch = git(self.canonical, 'diff', '--binary') + '\n'
        git(self.canonical, 'checkout', '--', 'value.txt')
        (self.directory / 'candidate.patch').write_text(patch)
        self.digest = hashlib.sha256(patch.encode()).hexdigest()
        self.api = FakeGitHub(self.root, self.remote, self.base)
        self.delivery = G.GitDelivery(self.root / 'runtime', 'exampleapp',
            {'repository': G.ORGANISATION + '/exampleapp', 'base': 'main'}, api=self.api)
        self.task = {'id': 'F1', 'title': 'Change the value'}
        self.review = {'decision': 'accept', 'issues': []}
        self.checks = [{'code': 0, 'reason': 'exited'}]
        original = G.git
        def local_git(root, *args):
            args = list(args)
            if args[0] in ('fetch', 'push'):
                # Replace only the remote URL, preserving the actual Git operation.
                args[1] = str(self.remote)
            return original(root, *args)
        self.patcher = mock.patch.object(G, 'git', side_effect=local_git)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def deliver(self, **kwargs):
        return self.delivery.deliver(self.canonical, self.task, self.run_id, self.base,
                                     self.review, self.checks, canonical=self.canonical,
                                     directory=self.directory, patch_sha256=self.digest, **kwargs)

    def test_exact_head_merge_requires_host_gate_and_synchronizes_canonical(self):
        gate = mock.Mock()
        receipt = self.deliver(pre_merge=gate)
        self.assertEqual(receipt['status'], 'merged')
        gate.assert_called_once_with()
        self.assertEqual(git(self.canonical, 'rev-parse', 'HEAD'), receipt['commit'])
        self.assertEqual((self.canonical / 'value.txt').read_text(), 'after\n')
        self.assertEqual(self.api.posts, 1)
        self.assertEqual(self.api.merges, 1)

    def test_uncertain_pr_creation_reconciles_without_duplicate_pr(self):
        self.api.uncertain_create = True
        self.assertEqual(self.deliver()['status'], 'pending')
        self.assertEqual(self.deliver()['status'], 'merged')
        self.assertEqual(self.api.posts, 1)
        self.assertEqual(self.api.merges, 1)

    def test_uncertain_remote_merge_reconciles_without_reusing_promotion_authority(self):
        self.api.uncertain_merge = True
        gate = mock.Mock()
        self.assertEqual(self.deliver(pre_merge=gate)['status'], 'pending')
        receipt = self.deliver(pre_merge=gate)
        self.assertEqual(receipt['status'], 'merged')
        gate.assert_called_once_with()
        self.assertEqual(self.api.merges, 1)
        self.assertEqual(self.api.posts, 1)

    def test_changed_remote_base_never_creates_or_merges(self):
        self.api.stale_base = True
        self.assertEqual(self.deliver()['status'], 'blocked')
        self.assertEqual(self.api.posts, 0)
        self.assertEqual(self.api.merges, 0)

    def test_changed_pr_head_blocks_before_attestation_or_merge(self):
        self.api.stale_head = True
        self.assertEqual(self.deliver()['status'], 'blocked')
        self.assertEqual(self.api.statuses, {})
        self.assertEqual(self.api.merges, 0)

    def test_dirty_canonical_preserves_remote_acceptance_for_later_recovery(self):
        def owner_edit():
            (self.canonical / 'owner.txt').write_text('preserve this edit\n')
        first = self.deliver(pre_merge=owner_edit)
        self.assertEqual(first['status'], 'blocked')
        self.assertTrue(self.api.pr['merged'])
        self.assertEqual((self.canonical / 'owner.txt').read_text(), 'preserve this edit\n')
        (self.canonical / 'owner.txt').unlink()
        self.assertEqual(self.deliver()['status'], 'merged')
        self.assertEqual(self.api.merges, 1)

    def test_failed_quality_recheck_never_calls_remote_merge(self):
        gate = mock.Mock(side_effect=ValueError('New material finding blocks feature'))
        self.assertEqual(self.deliver(pre_merge=gate)['status'], 'blocked')
        self.assertEqual(self.api.merges, 0)

    def test_recovered_clone_marker_cannot_substitute_different_candidate_bytes(self):
        delivery_work = self.directory / 'delivery-work'
        subprocess.run(['git', 'clone', '--quiet', str(self.canonical), str(delivery_work)], check=True, capture_output=True)
        git(delivery_work, 'checkout', '--quiet', '-b', 'midnight/' + self.run_id, self.base)
        (delivery_work / 'value.txt').write_text('different unreviewed content\n')
        git(delivery_work, 'add', '.')
        git(delivery_work, '-c', 'user.name=Codex', '-c', 'user.email=codex@localhost',
            'commit', '--quiet', '-m', 'Misleading marker\n\nMidnight-Run: ' + self.run_id)
        self.assertEqual(self.deliver()['status'], 'blocked')
        self.assertEqual(self.api.posts, 0)
        self.assertEqual(self.api.merges, 0)

    def test_owner_commit_after_preparation_blocks_remote_merge_before_ticket_consumption(self):
        # Leave an exact prepared PR, then advance only the local canonical repo.
        self.api.uncertain_create = True
        self.assertEqual(self.deliver()['status'], 'pending')
        (self.canonical / 'owner.txt').write_text('independent owner commit\n')
        git(self.canonical, 'add', '.')
        git(self.canonical, 'commit', '--quiet', '-m', 'Owner change')
        gate = mock.Mock()
        receipt = self.deliver(pre_merge=gate)
        self.assertEqual(receipt['status'], 'blocked')
        self.assertEqual(self.api.merges, 0)
        gate.assert_not_called()

    def test_unknown_repository_setup_does_not_mutate_before_admission(self):
        calls = []
        repository = G.ORGANISATION + '/unadmitted'
        def api(endpoint, method='GET', payload=None):
            calls.append((endpoint, method))
            if endpoint == 'user': return {'login': G.IDENTITY}
            if endpoint.startswith('orgs/'): return {'plan': {'name': 'team'}}
            if endpoint.startswith('repos/') and '/branches/' not in endpoint:
                return {'full_name': endpoint[6:], 'private': True, 'permissions': {'push': True, 'admin': True}}
            if endpoint.endswith('/protection') and method == 'GET':
                return self.api(self.api.prefix + '/branches/main/protection')
            return {}
        project = {'id': 'unknown', 'state_dir': str(self.root / 'unknown'),
                   'github': {'enabled': True, 'repository': repository}}
        result = github_setup.reconcile(self.root / 'setup', [project], api=api, force=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertNotIn(('repos/' + repository, 'PATCH'), calls)


if __name__ == '__main__':
    unittest.main()
