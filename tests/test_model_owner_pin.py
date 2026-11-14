"""An owner-pinned worker cannot be replaced by speed-biased auto-qualification.

Design decision: Qwen3.8 was chosen for quality over speed; a
faster candidate passing qualification must not silently revert it.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import model_manager as M


class OwnerPinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        p = mock.patch.object(M, 'RUNTIME', Path(self.temp.name)); p.start(); self.addCleanup(p.stop)
        self.active = {'model': 'local-qwen38:context32', 'digest': 'a' * 64, 'context': 32768, 'bytes': 10, 'parameters_sha256': 'b' * 64}
        M.save({'schema_version': 1, 'active': self.active, 'previous': dict(self.active, model='local-coder:context32', digest='c' * 64),
                'evaluated': {}, 'history': [],
                'canary': {'observed_runs': [], 'failures': 0, 'generation': 'g' * 32, 'started_at': '2026-11-11T03:48:17+00:00'},
                'comparison': {'id': 'cmp', 'challenger': {'model': 'devstral'}, 'results': []}})
        for target, name, kwargs in [(M, 'active', {'return_value': self.active}), (M.GP, 'active', {'return_value': None}),
                                     (M, 'CONTROL_CHECK', {})]:
            q = mock.patch.object(target, name, **kwargs); q.start(); self.addCleanup(q.stop)

    def test_pin_abandons_comparison_and_retires_canary(self):
        pin = M.pin('owner chose quality over speed')
        state = M.load()
        self.assertEqual(pin['model'], 'local-qwen38:context32'); self.assertEqual(pin['digest'], 'a' * 64)
        self.assertNotIn('comparison', state); self.assertNotIn('canary', state)
        kinds = [h['kind'] for h in state['history']]
        self.assertEqual(kinds, ['comparison_abandoned', 'canary_retired', 'owner_pinned'])

    def test_pinned_step_never_discovers_or_benchmarks(self):
        M.pin()
        with mock.patch.object(M, 'config', return_value={'enabled': True, 'discovery_hours': 1}), \
             mock.patch.object(M, 'discover', side_effect=AssertionError('No discovery while pinned')), \
             mock.patch.object(M, 'install', side_effect=AssertionError('No acquisition while pinned')), \
             mock.patch.object(M.B, 'recover_probe'):
            result = M._step()
        self.assertEqual(result, {'status': 'owner_pinned', 'model': 'local-qwen38:context32', 'matches_active': True})

    def test_dispatch_descriptor_is_stable_once_pinned(self):
        M.pin()
        self.assertIsNone(M.dispatch_descriptor()['promotion_generation'])

    def test_canary_cannot_roll_back_a_pinned_worker(self):
        M.pin()
        M._observe([])  # no canary remains, so no automatic rollback path exists
        self.assertEqual(M.load()['active']['model'], 'local-qwen38:context32')

    def test_snapshot_reports_owner_pin(self):
        M.pin()
        view = M.snapshot()
        self.assertEqual(view['selection_kind'], 'owner_pinned'); self.assertEqual(view['owner_pin']['model'], 'local-qwen38:context32')

    def test_unpin_resumes_qualification(self):
        M.pin(); self.assertEqual(M.unpin(), {'status': 'unpinned'})
        self.assertNotIn('owner_pin', M.load()); self.assertEqual(M.unpin(), {'status': 'not_pinned'})

    def test_pin_refuses_a_changed_model(self):
        with mock.patch.object(M, 'active', return_value=dict(self.active, digest='f' * 64)):
            with self.assertRaises(ValueError):M.pin()

    def test_pin_refuses_while_glm_provider_selected(self):
        with mock.patch.object(M.GP, 'active', return_value={'model': 'glm'}):
            with self.assertRaises(ValueError):M.pin()


if __name__ == '__main__':
    unittest.main()
