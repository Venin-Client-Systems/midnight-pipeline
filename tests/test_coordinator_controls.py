import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import midnight as M
import coordinator as K

class CoordinatorControls(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.state=self.root/'runtime';self.state.mkdir()
        for name,value in [('ROOT',self.root),('STATE',self.state)]:
            patch=mock.patch.object(M,name,value);patch.start();self.addCleanup(patch.stop)

    def test_detached_spawn_is_cadenced_and_preserves_other_maintenance(self):
        M.write(self.state/'maintenance-cadence.json',{'models':123})
        with mock.patch.object(M.subprocess,'Popen') as spawn:
            self.assertTrue(M.schedule_coordinator());self.assertFalse(M.schedule_coordinator())
        self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        self.assertEqual(spawn.call_args.args[0][-2:],['--root',str(self.root)])
        self.assertEqual(M.read(self.state/'maintenance-cadence.json')['models'],123)
        for name in ('PAUSED','INSTALLING','WEEKDAY_ACTIVATION_PENDING'):
            (self.state/name).touch()
            with mock.patch.object(M.subprocess,'Popen') as spawn:
                self.assertFalse(M.schedule_coordinator());spawn.assert_not_called()
            (self.state/name).unlink()

    def test_cli_changes_are_logged_and_status_is_read_only(self):
        args=argparse.Namespace(mode='off',overrule='abc',run=True)
        with mock.patch.object(K,'overrule',return_value={'decision_id':'abc'}) as over, mock.patch.object(K,'run') as run:
            view=M.coordinator_command(args,{'projects':[]})
        self.assertEqual(view['mode'],'off');over.assert_called_once();run.assert_called_once()
        rows=[json.loads(x) for x in (self.state/'owner-actions.jsonl').read_text().splitlines()]
        self.assertEqual([x['action'] for x in rows],['coordinator_mode','overrule_decision','coordinator_run'])
        before=(self.state/'owner-actions.jsonl').read_bytes()
        M.coordinator_command(argparse.Namespace(mode=None,overrule=None,run=False),{'projects':[]})
        self.assertEqual(before,(self.state/'owner-actions.jsonl').read_bytes())
