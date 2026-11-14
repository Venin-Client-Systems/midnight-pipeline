import copy
from pathlib import Path
import tempfile
import unittest
import github_setup as S
import git_delivery as G


class SetupTests(unittest.TestCase):
    def test_live_schema_uses_one_required_check_form_and_preserves_app_bindings(self):
        with tempfile.TemporaryDirectory() as root:
            rules = {}; writes = []
            old = {'required_status_checks': {'contexts': ['existing/legacy'],
                                            'checks': [{'context': 'existing/app', 'app_id': 1234}]}}
            def api(endpoint, method='GET', payload=None):
                if endpoint == 'user': return {'login': G.IDENTITY}
                if endpoint.startswith('orgs/'): return {'plan': {'name': 'team'}}
                if endpoint.endswith('/protection'):
                    if method == 'GET': return copy.deepcopy(rules.get(endpoint, old))
                    checks = payload['required_status_checks']
                    # GitHub rejected the previous request with both forms:422.
                    self.assertNotIn('contexts', checks); self.assertIn('checks', checks)
                    self.assertIn({'context': 'existing/app', 'app_id': 1234}, checks['checks'])
                    self.assertIn({'context': 'existing/legacy'}, checks['checks'])
                    self.assertEqual({x['context'] for x in checks['checks']}, set(G.CONTEXTS) | {'existing/app', 'existing/legacy'})
                    rules[endpoint] = copy.deepcopy(payload); rules[endpoint]['enforce_admins'] = {'enabled': True}
                    for key in ('allow_force_pushes', 'allow_deletions'):
                        rules[endpoint][key] = {'enabled': False}
                    writes.append(endpoint); return rules[endpoint]
                return {'full_name': endpoint[6:], 'private': True, 'permissions': {'push': True, 'admin': True}}
            project = {'id': 'notes', 'state_dir': str(Path(root)/'project'),
                       'github': {'enabled': True, 'repository': G.ORGANISATION+'/exampleapp'}}
            result = S.reconcile(Path(root), [project], api=api, force=True)
            self.assertEqual(result['status'], 'configured'); self.assertEqual(len(writes), 2)


if __name__ == '__main__': unittest.main()
