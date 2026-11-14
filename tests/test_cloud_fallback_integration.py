"""project_backend.cloud() completes on the fallback when the primary provider fails."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import project_backend as B
from test_cloud_provider_fallback import claude_stream

GUIDANCE = {'summary': 'Create the missing test module first.', 'steps': ['Add tests/test_unit_workspace.py', 'Run the focused unittest']}


class CloudFallbackIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.backend = B.load_backend(root, 'fixture'); self.backend.STATE = root / 'state'; self.backend.STATE.mkdir()
        self.backend.CLOUD_RUNTIME = root / 'shared'; self.directory = root / 'guidance'; self.calls = []
        self.state = {'cloud_calls_today': 0}; self.config = {'max_cloud_calls_per_night': None, 'routine_provider': 'local'}

    def run_process(self, command, cwd, log, seconds, cutoff, state, charge, **kwargs):
        charge(); self.calls.append(command[0])
        if command[0] == 'claude':
            log.write_text(claude_stream(error='Claude usage limit reached; resets 5am'))
            return {'code': 1, 'reason': 'exited', 'seconds': 1}
        Path(command[command.index('--output-last-message') + 1]).write_text(json.dumps(GUIDANCE))
        log.write_text('{"type":"turn.completed"}\n')
        return {'code': 0, 'reason': 'exited', 'seconds': 1}

    def test_guidance_completes_on_codex_after_claude_limit(self):
        cutoff = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)
        with mock.patch.object(self.backend, 'git', return_value='a' * 40), mock.patch.object(self.backend, 'journal_charge'), \
             mock.patch.object(self.backend, 'charged_run', side_effect=self.run_process):
            output = self.backend.cloud('Guide the repair', self.backend.GUIDANCE_SCHEMA, self.directory, cutoff, self.state, self.config)
        self.assertEqual(output, GUIDANCE); self.assertEqual(self.calls, ['claude', 'codex'])
        attempts = json.loads((self.directory / 'cloud-attempts.json').read_text())
        self.assertEqual([a['provider'] for a in attempts], ['claude', 'codex']); self.assertEqual(attempts[0]['failure']['kind'], 'limit')
        self.assertTrue((self.directory / 'events-attempt-0.jsonl').exists())
        self.assertEqual(self.state['cloud_calls_today'], 2)
        events = [json.loads(l)['event'] for l in (self.backend.STATE / 'events.jsonl').read_text().splitlines()]
        self.assertIn('cloud_fallback', events)

    def test_both_providers_failing_raises_with_both_reasons(self):
        def fail(command, cwd, log, *a, **k):log.write_text(''); return {'code': 1, 'reason': 'exited', 'seconds': 1}
        cutoff = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)
        with mock.patch.object(self.backend, 'git', return_value='a' * 40), mock.patch.object(self.backend, 'journal_charge'), \
             mock.patch.object(self.backend, 'charged_run', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'claude .*codex'):
                self.backend.cloud('Guide the repair', self.backend.GUIDANCE_SCHEMA, self.directory, cutoff, self.state, self.config)


if __name__ == '__main__':
    unittest.main()
