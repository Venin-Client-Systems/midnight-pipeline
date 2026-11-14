"""Exercise the real Ralphy adapter and fixed macOS worker sandbox offline."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

from worker_probe_runtime import confined_worker


SOURCE = Path(__file__).resolve().parents[1]


class ProbeFinalMessageTests(unittest.TestCase):
    def test_confined_client_publishes_to_host_result_before_success(self):
        self.exercise()

    def test_confined_result_symlink_cannot_disclose_host_only_fixture(self):
        self.exercise(unsafe=True)

    def exercise(self, unsafe=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / 'source'; source.mkdir()
            case = root / 'label'; case.mkdir()
            work = case / 'work'; work.mkdir()
            (case / 'probe-broker').mkdir()
            manifest = case / 'worker-manifest.json'
            manifest.write_text(json.dumps({'task_id': 'typed-worker-qualification-label',
                'workspace': str(work), 'allowed_files': ['solution.py']}))
            (work / 'PRD.md').write_text('- [ ] T001 Produce a fixed offline result\n')
            (work / 'AGENTS.md').write_text('Fixed offline client; no inference.\n')
            (work / 'progress.txt').write_text('')
            (work / 'solution.py').write_text('VALUE = 0\n')
            subprocess.run(['git', 'init', '-q', str(work)], check=True)
            subprocess.run(['git', 'add', '.'], cwd=work, check=True)
            subprocess.run(['git', '-c', 'user.name=Native Test', '-c',
                'user.email=native@example.invalid', 'commit', '-qm', 'fixture'], cwd=work, check=True)
            client = source / 'local_worker.py'
            client.write_text('#!' + sys.executable + '\n' + '''import json,os,pathlib,sys,tempfile
args=sys.argv[1:]; target=pathlib.Path(args[args.index('--output-last-message')+1])
print(json.dumps({'type':'offline.final_path','path':str(target)}),flush=True)
with tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as stream:
 stream.write(b'Fixed confined final message.\\n'); stream.flush(); os.fsync(stream.fileno())
 pending=stream.name
os.replace(pending,target)
print('{"type":"turn.completed"}',flush=True)
''')
            client.chmod(0o700)
            if unsafe:
                private = Path.home() / ('midnight-fake-handoff-' + uuid.uuid4().hex)
                private.write_text('HOST_ONLY_FAKE_DATA')
                self.addCleanup(private.unlink, missing_ok=True)
                client.write_text('#!' + sys.executable + '\n' + '''import json,pathlib,sys
args=sys.argv[1:];target=pathlib.Path(args[args.index('--output-last-message')+1])
private=pathlib.Path(''' + repr(str(private)) + ''')
try:private.read_text();denied=False
except PermissionError:denied=True
print(json.dumps({'type':'offline.private_read','denied':denied}),flush=True)
target.symlink_to(private)
print('{"type":"tool_use","name":"apply_patch"}',flush=True)
print('{"type":"turn.completed"}',flush=True)
''')
            _, env = confined_worker(source, case, [str(client)])
            result_path = case / 'worker-result.txt'
            events_path = case / 'worker-events.jsonl'
            env.update(MIDNIGHT_PROBE_SOURCE=str(source),
                MIDNIGHT_LOCAL_WORKER_MANIFEST=str(manifest),
                MIDNIGHT_WORKER_PROBE_BROKER=str(case / 'probe-broker'),
                MIDNIGHT_LOCAL_CODE_BIN=str(SOURCE / 'worker_probe_client.py'),
                EXAMPLEAPP_RALPHY_STATE_DIR=str(case / 'ralphy-state'),
                EXAMPLEAPP_AGENT_RESULT=str(result_path),
                EXAMPLEAPP_AGENT_EVENTS=str(events_path))
            run = subprocess.run(['/opt/homebrew/bin/bash', str(SOURCE / 'ralphy/run.sh'),
                '--prd', 'PRD.md'], cwd=work, env=env, capture_output=True, text=True, timeout=30)
            records = [json.loads(line) for line in events_path.read_text().splitlines()
                       if line.startswith('{')]
            clients = [r for r in records if r.get('type') == 'worker.client_result']
            self.assertEqual(clients, [{'type': 'worker.client_result', 'schema_version': 1,
                                      'exit_code': 0}], run.stdout + run.stderr + events_path.read_text())
            self.assertEqual(run.returncode, 0, run.stderr)
            if unsafe:
                self.assertEqual(records[0], {'type': 'offline.private_read', 'denied': True})
                self.assertNotIn('HOST_ONLY_FAKE_DATA', run.stdout + run.stderr + events_path.read_text())
                self.assertTrue(any(r.get('type') == 'error' for r in records), records)
                self.assertIn({'type': 'worker.result_handoff', 'schema_version': 1,
                               'status': 'failed'}, records)
                self.assertTrue(result_path.is_symlink())
                return
            self.assertEqual(records[0]['path'], str(result_path))
            self.assertEqual(result_path.read_text(), 'Fixed confined final message.\n')
            self.assertEqual([r['type'] for r in records],
                ['offline.final_path', 'turn.completed', 'worker.result_handoff', 'worker.client_result'])
            self.assertEqual(records[2]['status'], 'completed')
            self.assertEqual((work / 'solution.py').read_text(), 'VALUE = 0\n')
            self.assertIn('- [ ]', (work / 'PRD.md').read_text())


if __name__ == '__main__':
    unittest.main()
