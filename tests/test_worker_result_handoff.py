"""Real adapter error propagation with bounded fake clients, never inference."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('ralphy_adapter_fixture',
    Path(__file__).resolve().parents[1] / 'ralphy/test_adapter.py')
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)


class ResultHandoffTests(unittest.TestCase):
    def setUp(self):
        self.f = A.AdapterTests('test_help_does_not_invoke_model')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def client(self, body):
        self.f.fake.write_text('''#!/opt/homebrew/bin/python3
import os,pathlib,sys
args=sys.argv[1:];target=pathlib.Path(args[args.index('--output-last-message')+1])
''' + body + '''
print('{"type":"tool_use","name":"apply_patch"}',flush=True)
print('{"type":"turn.completed"}',flush=True)
''')
        self.f.fake.chmod(0o700)

    def rejected(self, body):
        self.client(body)
        run = self.f.invoke('--prd', 'PRD.md')
        records = [json.loads(line) for line in self.f.events.read_text().splitlines()]
        self.assertEqual([r for r in records if r['type'] == 'worker.client_result'],
            [{'type': 'worker.client_result', 'schema_version': 1, 'exit_code': 0}])
        self.assertTrue(any(r['type'] == 'error' and 'handoff' in r['error']['message']
                            for r in records), records)
        self.assertIn({'type': 'worker.result_handoff', 'schema_version': 1,
                       'status': 'failed'}, records)
        self.assertNotIn('HOST_ONLY_FAKE_DATA', run.stdout + run.stderr)
        self.assertIn('- [ ]', (self.f.repo / 'PRD.md').read_text())

    def test_unreadable_result_keeps_client_exit_but_fails_handoff(self):
        self.rejected("target.write_text('fresh');target.chmod(0)")

    def test_missing_result_keeps_client_exit_but_fails_handoff(self):
        self.rejected('pass')

    def test_special_file_is_rejected_without_blocking(self):
        self.rejected('os.mkfifo(target)')

    def test_oversized_result_is_rejected(self):
        self.rejected("target.write_bytes(b'x' * 65537)")

    def test_hard_link_is_rejected_before_reading(self):
        fixture = self.f.root / 'host-only'; fixture.write_text('HOST_ONLY_FAKE_DATA')
        self.rejected('os.link(' + repr(str(fixture)) + ',target)')

    def test_replaced_result_parent_is_rejected(self):
        parent = self.f.root / 'run'; parent.mkdir()
        other = self.f.root / 'other'; other.mkdir()
        (other / 'result.txt').write_text('HOST_ONLY_FAKE_DATA')
        self.f.env['EXAMPLEAPP_AGENT_RESULT'] = str(parent / 'result.txt')
        self.rejected('target.parent.rmdir();target.parent.symlink_to(' + repr(str(other)) + ',target_is_directory=True)')


class HostSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'result'; self.target = self.root / 'parser'
        # Execute the exact source-bound embedded host helper, not a test copy.
        self.code = A.RUNNER.read_text().split("<<'MIDNIGHT_RESULT_PY'\n", 1)[1].split('\nMIDNIGHT_RESULT_PY', 1)[0]
        prepared = self.call('prepare', str(self.source), str(self.target))
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.binding = prepared.stdout

    def call(self, *args):
        return subprocess.run([sys.executable, '-I', '-B', '-c', self.code, *args],
                              capture_output=True, text=True, timeout=5)

    def test_exact_output_bound_is_preserved_in_regular_snapshot(self):
        self.source.write_bytes(b'x' * 65536)
        result = self.call('publish', self.binding)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.read_bytes(), b'x' * 65536)
        self.assertEqual(self.source.read_bytes(), b'x' * 65536)

    def test_empty_and_invalid_utf8_results_do_not_publish(self):
        for value in (b'', b'\xff'):
            with self.subTest(value=value):
                self.source.write_bytes(value)
                self.assertNotEqual(self.call('publish', self.binding).returncode, 0)
                self.assertFalse(self.target.exists())

    def test_parser_collision_preserves_existing_evidence(self):
        self.source.write_text('new'); self.target.write_text('retained')
        self.assertNotEqual(self.call('publish', self.binding).returncode, 0)
        self.assertEqual(self.target.read_text(), 'retained')

    def test_parser_symlink_cannot_redirect_host_write(self):
        other = self.root / 'other'; other.write_text('retained')
        self.source.write_text('new'); self.target.symlink_to(other)
        self.assertNotEqual(self.call('publish', self.binding).returncode, 0)
        self.assertEqual(other.read_text(), 'retained')


if __name__ == '__main__':
    unittest.main()
