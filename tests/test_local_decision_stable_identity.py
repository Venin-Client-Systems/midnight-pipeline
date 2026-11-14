"""Canary promotion drift must not orphan a completed local decision.

Regression: the local-decision key fingerprinted the full dispatch descriptor,
including the volatile canary promotion_generation. When it changed, a
completed decision no longer matched and its single charge looked permanently
spent, holding the project for six days.
"""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import project_backend as B
import quality_monitor as Q
from action_store import Actions

REVIEW = {'decision': 'accept', 'summary': 'Supplied change is consistent with its scoped tests.', 'issues': [],
          'confidence': 'medium', 'correctness': 4, 'security': 4, 'maintainability': 4, 'tests': 4}


def descriptor(generation, digest='d' * 64):
    return {'model': 'fixture', 'context': 32768, 'digest': digest, 'promotion_generation': generation}


class StableDecisionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.backend = B.load_backend(self.root, 'fixture'); self.backend.STATE = self.root / 'state'
        self.backend.CLOUD_RUNTIME = self.root / 'runtime'
        self.directory = self.backend.STATE / 'review'; self.calls = []

    def run_process(self, *args, **kw):
        self.calls.append(kw); args[6]()
        self.backend.write(self.directory / 'result.json', REVIEW)
        self.backend.write(self.directory / 'local-metrics.json', {'model': 'fixture'})
        (self.directory / 'events.jsonl').write_text('{}\n')
        return {'code': 0, 'reason': 'exited'}

    def decide(self, model, state):
        return self.backend.local_decision('Exact review', Q.RATED_REVIEW_SCHEMA, self.directory, None, state,
                                           {'worker_model': model})

    def test_completed_decision_survives_promotion_generation_drift(self):
        state = {}
        with mock.patch.object(self.backend, 'git', return_value='a' * 40), \
             mock.patch.object(self.backend, 'charged_run', side_effect=self.run_process), \
             mock.patch.object(self.backend, 'journal_charge'):
            self.assertEqual(self.decide(descriptor('gen-1'), state), REVIEW)
            self.assertEqual(self.decide(descriptor(None), state), REVIEW)
            self.assertEqual(self.decide(descriptor('gen-2'), state), REVIEW)
        self.assertEqual(len(self.calls), 1)

    def test_different_weights_are_still_a_different_input(self):
        state = {}
        with mock.patch.object(self.backend, 'git', return_value='a' * 40), \
             mock.patch.object(self.backend, 'charged_run', side_effect=self.run_process), \
             mock.patch.object(self.backend, 'journal_charge'):
            self.decide(descriptor('gen-1'), state)
            self.decide(descriptor('gen-1', digest='e' * 64), state)
        self.assertEqual(len(self.calls), 2)

    def test_real_charge_journal_keeps_one_key_across_drift(self):
        store = Actions(self.backend.CLOUD_RUNTIME); job = store.submit('fixture', {'session': 's'}, time.time() + 600)
        store.claim(job['id'], {'pid': 1}); state = {}
        with mock.patch.dict('os.environ', {'MIDNIGHT_ACTION_ID': job['id']}), \
             mock.patch.object(self.backend, 'git', return_value='a' * 40), \
             mock.patch.object(self.backend, 'charged_run', side_effect=self.run_process):
            self.decide(descriptor('gen-1'), state)
            self.decide(descriptor('gen-2'), state)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(Actions(self.backend.CLOUD_RUNTIME).session_charges('s', 'fixture'), {'local_supervision': 1})


if __name__ == '__main__':
    unittest.main()
