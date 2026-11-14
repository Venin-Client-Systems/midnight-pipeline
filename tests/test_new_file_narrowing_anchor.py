"""A diagnosis may anchor a repair that must create a declared, absent file.

Regression: R005F's correct diagnosis ("Create
tests/test_unit_workspace.py ...", symbol __file__) was rejected with
"Narrowing source is unavailable" because anchors required an existing file.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import progress_recovery as RP
import project_backend as B

QUOTE = "ModuleNotFoundError: No module named 'tests.test_unit_workspace'. Did you mean: 'tests.test_unit_transport'?"


class NewFileAnchorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); self.work = root / 'work'; (self.work / 'tests').mkdir(parents=True)
        (self.work / 'pkg').mkdir(); (self.work / 'pkg' / 'workspace.py').write_text('def load():\n    return {}\n')
        self.log = root / 'run' / 'check-0.log'; self.log.parent.mkdir(); self.log.write_text('ERROR\n' + QUOTE + '\nFAILED (errors=1)\n')
        self.task = {'files': ['pkg/workspace.py', 'tests/test_unit_workspace.py'], 'acceptance': ['tests exist']}
        self.proposal = {'action': 'narrow', 'obligation': 0, 'instruction': 'Create the test module.',
                         'path': 'tests/test_unit_workspace.py', 'symbol': '__file__', 'evidence_quote': QUOTE}

    def test_absent_declared_file_anchors_on_failed_check_evidence(self):
        anchor = RP.new_file_anchor(self.work, self.task, self.proposal, [self.log])
        self.assertEqual(anchor['kind'], 'new_file'); self.assertEqual(anchor['source']['path'], str(self.log.resolve()))
        self.assertEqual(anchor['unit_id'], RP.fingerprint({'path': 'tests/test_unit_workspace.py', 'syntax': RP.NEW_FILE_SYNTAX}))

    def test_narrowing_anchor_routes_absent_file_to_new_file_anchor(self):
        anchor = B.narrowing_anchor(self.work, self.task, self.proposal, [self.log])
        self.assertEqual(anchor['kind'], 'new_file')

    def test_existing_file_keeps_source_quote_rules(self):
        proposal = dict(self.proposal, path='pkg/workspace.py')
        with self.assertRaises(RP.RecoveryHeld):B.narrowing_anchor(self.work, self.task, proposal, [self.log])

    def test_quote_must_come_from_retained_failure_evidence(self):
        with self.assertRaisesRegex(RP.RecoveryHeld, 'retained failed checks'):
            RP.new_file_anchor(self.work, self.task, dict(self.proposal, evidence_quote='An invented failure that never happened.'), [self.log])

    def test_undeclared_path_is_refused(self):
        with self.assertRaises(RP.RecoveryHeld):
            RP.new_file_anchor(self.work, self.task, dict(self.proposal, path='tests/test_other.py'), [self.log])

    def test_symlinked_parent_is_refused(self):
        outside = Path(self.tmp.name) / 'outside'; outside.mkdir()
        (self.work / 'linked').symlink_to(outside, target_is_directory=True)
        task = dict(self.task, files=self.task['files'] + ['linked/test_x.py'])
        with self.assertRaisesRegex(RP.RecoveryHeld, 'unsafe'):
            RP.new_file_anchor(self.work, task, dict(self.proposal, path='linked/test_x.py'), [self.log])

    def test_pinned_anchor_reverifies_and_detects_tampering(self):
        anchor = RP.new_file_anchor(self.work, self.task, self.proposal, [self.log])
        store = RP.Recovery(Path(self.tmp.name) / 'state', 'fixture', evidence_roots=[Path(self.tmp.name)])
        self.assertEqual(store._verified_anchor(anchor)['unit_id'], anchor['unit_id'])
        with self.assertRaises(RP.RecoveryHeld):store._verified_anchor(dict(anchor, unit_id='0' * 64))
        self.log.write_text('rewritten log\n')
        with self.assertRaises(RP.RecoveryHeld):store._verified_anchor(anchor)


if __name__ == '__main__':
    unittest.main()
