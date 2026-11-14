"""An open review keeps a project that was archived and removed from the register."""
import json
import tempfile
import unittest
from pathlib import Path

import performance_review as R


class RetiredProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); runtime = self.root / "runtime"
        for name in ('exampleapp', 'secondapp'):(runtime / 'projects' / name).mkdir(parents=True)
        self.entry = lambda name, path: {'id': name, 'state_dir': str(runtime / 'projects' / name), 'path': path}
        (self.root / 'projects.json').write_text(json.dumps({'projects': [self.entry('exampleapp', '/repo/exampleapp')]}))
        self.review = R.Review(self.root, runtime / 'performance-reviews' / 'fixture')

    def test_missing_registered_project_still_fails_without_a_retirement_record(self):
        with self.assertRaisesRegex(R.Unavailable, 'Both admitted projects'):self.review.projects()

    def test_retirement_record_supplies_the_archived_project(self):
        (self.root / 'runtime' / 'retired-projects.json').write_text(json.dumps({'projects': [self.entry('secondapp', '/archive/secondapp')]}))
        projects = self.review.projects()
        self.assertEqual(set(projects), {'exampleapp', 'secondapp'}); self.assertEqual(str(projects['secondapp']['repo']), '/archive/secondapp')

    def test_registered_entry_wins_over_a_retirement_record(self):
        (self.root / 'projects.json').write_text(json.dumps({'projects': [self.entry('exampleapp', '/repo/exampleapp'), self.entry('secondapp', '/live/secondapp')]}))
        (self.root / 'runtime' / 'retired-projects.json').write_text(json.dumps({'projects': [self.entry('secondapp', '/archive/secondapp')]}))
        self.assertEqual(str(self.review.projects()['secondapp']['repo']), '/live/secondapp')


if __name__ == '__main__':
    unittest.main()
