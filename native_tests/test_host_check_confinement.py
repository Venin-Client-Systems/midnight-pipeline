"""Native evidence that code run by the host checks stays inside its sandbox."""
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import project_backend as B

HOSTILE = '''
import json, os, signal, socket, subprocess
results = {}
def attempt(name, action):
    try:
        action(); results[name] = 'allowed'
    except BaseException as problem:
        results[name] = 'denied'
def run(command):
    if subprocess.run(command, capture_output=True, timeout=20).returncode != 0:
        raise PermissionError(command[0])
attempt('signal_another_process', lambda: os.kill(VICTIM, signal.SIGTERM))
attempt('change_workspace_git', lambda: open('.git/config', 'a').write('\\n'))
attempt('add_workspace_git_hook', lambda: open('.git/hooks/post-checkout', 'w').write('#!/bin/sh\\n'))
attempt('write_outside', lambda: open(OUTSIDE, 'w').write('x'))
attempt('read_home', lambda: os.listdir(HOME))
attempt('start_application', lambda: run(['/usr/bin/open', '-g', '-a', 'Calculator']))
attempt('read_clipboard', lambda: run(['/usr/bin/pbpaste']))
attempt('internet', lambda: socket.create_connection(('1.1.1.1', 443), timeout=3).close())
attempt('local_service', lambda: socket.create_connection(('127.0.0.1', PORT), timeout=3).close())
attempt('write_workspace_file', lambda: open('scratch.txt', 'w').write('x'))
attempt('run_child_process', lambda: run(['/bin/echo', 'child']))
attempt('resolve_own_path', lambda: [os.lstat(parent) for parent in __import__('pathlib').Path(os.getcwd()).parents])
print('RESULTS ' + json.dumps(results))
'''


class HostCheckConfinementTests(unittest.TestCase):
    def test_hostile_test_code_cannot_reach_outside_the_workspace(self):
        temporary = tempfile.TemporaryDirectory(dir=str(Path.home() / 'Library/Caches')); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        (root / 'source').mkdir()
        backend = B.load_backend(root / 'source', 'notes', settings={'source_dirs': ['src', 'tests'], 'product_dirs': ['src']})
        backend.STATE = root / 'runtime'; backend.STATE.mkdir()
        work = root / 'work'; (work / 'tests').mkdir(parents=True)
        subprocess.run(['/usr/bin/git', 'init', '-q', str(work)], check=True)
        victim = subprocess.Popen(['/bin/sleep', '60']); self.addCleanup(victim.kill)
        self.addCleanup(subprocess.run, ['/usr/bin/pkill', '-x', 'Calculator'], capture_output=True)
        listener = __import__('socket').socket(); listener.bind(('127.0.0.1', 0)); listener.listen(1); self.addCleanup(listener.close)
        prelude = f'VICTIM={victim.pid}\nOUTSIDE={str(root / "outside")!r}\nHOME={str(Path.home())!r}\nPORT={listener.getsockname()[1]}\n'
        (work / 'tests' / 'hostile.py').write_text(prelude + HOSTILE)
        output = {}

        def real(command, cwd, log, seconds, cutoff, state, *, env=None, **_):
            done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
            output['text'] = done.stdout + done.stderr
            return {'code': done.returncode, 'reason': 'exited', 'seconds': 0}
        directory = root / 'check'; directory.mkdir()
        with mock.patch.object(backend, 'run', side_effect=real):
            backend.host_checks(work, directory, None, {}, [['/opt/homebrew/bin/python3', '-B', 'tests/hostile.py']])
        line = [text for text in output['text'].splitlines() if text.startswith('RESULTS ')]
        self.assertTrue(line, output['text'][-800:])
        results = json.loads(line[0][8:])
        allowed = {name for name, outcome in results.items() if outcome == 'allowed'}
        self.assertEqual(allowed, {'write_workspace_file', 'run_child_process', 'resolve_own_path'}, results)
        time.sleep(0.5)
        self.assertIsNone(victim.poll())
        self.assertFalse((root / 'outside').exists())
        self.assertEqual(subprocess.run(['/usr/bin/pkill', '-x', 'Calculator'], capture_output=True).returncode, 1)


if __name__ == '__main__':
    unittest.main()
