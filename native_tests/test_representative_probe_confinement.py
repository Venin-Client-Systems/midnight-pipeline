"""Additional host contracts stay read-only in the actual macOS sandbox."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from worker_probe_runtime import confined_worker


class RepresentativeConfinement(unittest.TestCase):
    def test_candidate_cannot_rewrite_new_host_contracts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / 'source'; source.mkdir()
            case = root / 'summary_creation'; case.mkdir()
            work = case / 'work'; work.mkdir()
            names = ('contracts.md', 'fixtures.json', 'reference.py')
            for name in names:
                (work / name).write_text('protected fixture')
            code = '''import json,pathlib,sys
blocked=[]
for name in sys.argv[1:]:
 try:pathlib.Path(name).write_text('changed');blocked.append(False)
 except PermissionError:blocked.append(True)
print(json.dumps(blocked))
'''
            command, env = confined_worker(source, case,
                [sys.executable, '-I', '-B', '-c', code, *[str(work / n) for n in names]])
            result = subprocess.run(command, cwd=case, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [True, True, True])
            self.assertEqual([(work / n).read_text() for n in names], ['protected fixture'] * 3)


if __name__ == '__main__':
    unittest.main()
