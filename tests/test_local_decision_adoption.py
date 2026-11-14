"""A completed decision for the exact same input is reused from another directory.

Regression: diagnosis directories are bound to source
components, so a Midnight code update moved a re-run of an identical input to
a new directory. Its completed result was orphaned and the single charge
looked permanently spent (LocalDecisionHeld), holding exampleapp again.
"""
import json
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
MODEL = {'model': 'fixture', 'context': 32768, 'digest': 'd' * 64}


class DecisionAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.backend = B.load_backend(root, 'fixture'); self.backend.STATE = root / 'state'
        self.backend.CLOUD_RUNTIME = root / 'runtime'
        recovery = self.backend.STATE / 'progress-recovery' / 'scope'
        self.first, self.second = recovery / 'binding-1' / 'proposal', recovery / 'binding-2' / 'proposal'
        self.calls = []
        store = Actions(self.backend.CLOUD_RUNTIME); job = store.submit('fixture', {'session': 's'}, time.time() + 600)
        store.claim(job['id'], {'pid': 1})
        for p in (mock.patch.dict('os.environ', {'MIDNIGHT_ACTION_ID': job['id']}),
                  mock.patch.object(self.backend, 'git', return_value='a' * 40),
                  mock.patch.object(self.backend, 'charged_run', side_effect=self.run_process)):
            p.start(); self.addCleanup(p.stop)

    def run_process(self, *args, **kw):
        directory = args[1]; self.calls.append(directory); args[6]()
        self.backend.write(directory / 'result.json', REVIEW)
        self.backend.write(directory / 'local-metrics.json', {'model': 'fixture'})
        (directory / 'events.jsonl').write_text('{}\n')
        return {'code': 0, 'reason': 'exited'}

    def decide(self, directory, prompt='Exact diagnosis input', role='diagnosis'):
        workspace = mock.Mock(); workspace.context.return_value = {}
        with mock.patch.object(self.backend, 'local_role_context', return_value={}), \
             mock.patch.object(self.backend.CW, 'Workspace', return_value=workspace):
            return self.backend.local_decision(prompt, Q.RATED_REVIEW_SCHEMA, directory, None, {}, {'worker_model': MODEL}, role=role)

    def test_identical_input_in_new_directory_reuses_completed_result(self):
        self.assertEqual(self.decide(self.first), REVIEW)
        self.assertEqual(self.decide(self.second), REVIEW)
        self.assertEqual(len(self.calls), 1)
        adopted = json.loads((self.second / 'local-decision-adopted.json').read_text())
        self.assertTrue(adopted['source'].endswith('binding-1/proposal'))
        self.assertEqual(self.decide(self.second), REVIEW)  # now an ordinary cached receipt
        self.assertEqual(len(self.calls), 1)

    def test_pre_index_results_are_found_by_scanning_recovery_evidence(self):
        self.decide(self.first)
        for entry in (self.backend.STATE / 'local-decision-index').iterdir():entry.unlink()
        self.assertEqual(self.decide(self.second), REVIEW); self.assertEqual(len(self.calls), 1)

    def test_tampered_source_is_not_adopted_and_spent_charge_still_holds(self):
        self.decide(self.first)
        (self.first / 'result.json').write_text(json.dumps(dict(REVIEW, summary='Tampered after receipt.')))
        with self.assertRaises(self.backend.LocalDecisionHeld):self.decide(self.second)
        self.assertEqual(len(self.calls), 1)

    def test_different_input_is_never_adopted(self):
        self.decide(self.first)
        self.decide(self.second, prompt='A different diagnosis input')
        self.assertEqual(len(self.calls), 2)
        self.assertFalse((self.second / 'local-decision-adopted.json').exists())

    def test_planning_keeps_its_existing_spent_semantics(self):
        # Planning re-asks with a changed input after its wait; an identical
        # input elsewhere keeps the pre-existing spent-charge behaviour.
        self.decide(self.first, role='planning')
        with self.assertRaises(self.backend.LocalDecisionHeld):self.decide(self.second, role='planning')
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.backend.STATE / 'local-decision-index').exists())
        self.assertFalse((self.second / 'local-decision-adopted.json').exists())

    def test_source_outside_state_is_rejected(self):
        outside = Path(self.tmp.name) / 'outside'; outside.mkdir()
        self.assertIsNone(self.backend.verified_decision_source('k' * 64, outside, 'x'))


if __name__ == '__main__':
    unittest.main()
