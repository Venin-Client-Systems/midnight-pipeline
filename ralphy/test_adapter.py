"""Offline integration tests for the vendored Ralphy/local-code boundary."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


RUNTIME = Path(__file__).resolve().parent
RUNNER = RUNTIME / "run.sh"
BASH = "/opt/homebrew/bin/bash"


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="exampleapp-ralphy-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "candidate"
        self.repo.mkdir()
        self.events = self.root / "events.jsonl"
        self.result = self.root / "result.txt"
        self.fake = self.root / "local-code"
        self.env = os.environ.copy()
        self.env.update(
            EXAMPLEAPP_LOCAL_CODE_BIN=str(self.fake),
            EXAMPLEAPP_RALPHY_STATE_DIR=str(self.root / "state"),
            EXAMPLEAPP_AGENT_EVENTS=str(self.events),
            EXAMPLEAPP_AGENT_RESULT=str(self.result),
            LOCAL_CODE_EVALUATION_LOCK_HELD="1",
        )
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "PRD.md").write_text("- [ ] T001 Implement a fixture change\n")
        (self.repo / "progress.txt").write_text("")
        (self.repo / "fixture.py").write_text("VALUE = 0\n")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Adapter Test", "-c", "user.email=adapter@example.invalid",
             "commit", "-qm", "fixture baseline"], cwd=self.repo, check=True,
        )

    def install_fake(self, fail=False):
        self.fake.write_text("""#!/opt/homebrew/bin/python3
import json, os, pathlib, sys
args=sys.argv[1:]
print(json.dumps({'type':'adapter_test','args':args[:-1],
 'lock_inherited':'LOCAL_CODE_EVALUATION_LOCK_HELD' in os.environ},separators=(',',':')),flush=True)
""" + (
            "sys.exit(7)\n" if fail else """
pathlib.Path('fixture.py').write_text('VALUE = 1\\n')
p=pathlib.Path('PRD.md')
p.write_text(p.read_text().replace('- [ ]','- [x]'))
pathlib.Path(args[args.index('--output-last-message')+1]).write_text('Fixture candidate produced.\\n')
print('{"type":"tool_use","name":"apply_patch"}',flush=True)
print('{"type":"turn.completed"}',flush=True)
"""
        ))
        self.fake.chmod(0o755)

    def invoke(self, *args):
        return subprocess.run(
            [BASH, str(RUNNER), *args], cwd=self.repo, env=self.env,
            capture_output=True, text=True, timeout=30,
        )

    def test_vendor_integrity(self):
        provenance = json.loads((RUNTIME / "PROVENANCE.json").read_text())
        self.assertEqual(len(provenance["source_head"]), 40)
        for entry in provenance["files"]:
            with self.subTest(file=entry["path"]):
                self.assertEqual(
                    hashlib.sha256((RUNTIME / entry["path"]).read_bytes()).hexdigest(),
                    entry["sha256"],
                )

    def test_help_does_not_invoke_model(self):
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("one Autoissue/Ralphy task", result.stdout)
        self.assertFalse(self.events.exists())

    def test_external_and_unbounded_modes_rejected(self):
        for args in [("--github", "owner/repo"), ("--fallback", "claude"),
                     ("--parallel",), ("--max-iterations", "100")]:
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.events.exists())

    def test_real_ralphy_loop_invokes_local_adapter_and_preserves_evidence(self):
        self.install_fake()
        result = self.invoke("--prd", "PRD.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        records = [json.loads(line) for line in self.events.read_text().splitlines()]
        self.assertEqual(records[0]["args"][:5], ["-a", "never", "exec", "--ephemeral", "--json"])
        self.assertIn("features.goals=false", records[0]["args"])
        self.assertFalse(records[0]["lock_inherited"])
        self.assertEqual((self.repo / "fixture.py").read_text(), "VALUE = 1\n")
        self.assertEqual(self.result.read_text(), "Fixture candidate produced.\n")
        self.assertIn("Host validation determines task acceptance", result.stdout)
        self.assertNotIn("PRD complete!", result.stdout)

    def test_shared_midnight_worker_entry_takes_precedence(self):
        self.install_fake()
        self.env['MIDNIGHT_LOCAL_CODE_BIN']=self.env['EXAMPLEAPP_LOCAL_CODE_BIN']
        self.env['EXAMPLEAPP_LOCAL_CODE_BIN']='/usr/bin/false'
        result=self.invoke('--prd','PRD.md')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue(self.events.exists())

    def test_events_only_result_uses_durable_events_sidecar(self):
        self.install_fake()
        del self.env['EXAMPLEAPP_AGENT_RESULT']
        run = self.invoke('--prd', 'PRD.md')
        expected = Path(str(self.events) + '.last')
        records = [json.loads(line) for line in self.events.read_text().splitlines()]
        args = records[0]['args']
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(args[args.index('--output-last-message') + 1], str(expected))
        self.assertEqual(expected.read_text(), 'Fixture candidate produced.\n')

    def test_no_result_or_events_preserves_temporary_parser_fallback(self):
        self.install_fake()
        del self.env['EXAMPLEAPP_AGENT_RESULT']
        del self.env['EXAMPLEAPP_AGENT_EVENTS']
        self.env.pop('EXAMPLEAPP_AGENT_LOG', None)
        run = self.invoke('--prd', 'PRD.md')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual((self.repo / 'fixture.py').read_text(), 'VALUE = 1\n')
        self.assertFalse(self.result.exists())
        self.assertFalse(self.events.exists())
        self.assertIn('Fixture candidate produced.', run.stdout)

    def test_existing_host_result_is_preserved_and_client_is_not_replayed(self):
        self.install_fake()
        self.result.write_text('Retained earlier result.\n')
        run = self.invoke('--prd', 'PRD.md')
        records = [json.loads(line) for line in self.events.read_text().splitlines()]
        self.assertEqual([r['type'] for r in records], ['error'])
        self.assertIn('already exists', records[0]['error']['message'])
        self.assertEqual(self.result.read_text(), 'Retained earlier result.\n')
        self.assertEqual((self.repo / 'fixture.py').read_text(), 'VALUE = 0\n')
        self.assertIn('- [ ]', (self.repo / 'PRD.md').read_text())
        self.assertIn('[ERROR]', run.stderr)

    def test_explicit_host_result_is_client_destination_and_survives_cleanup(self):
        self.install_fake()
        run = self.invoke('--prd', 'PRD.md')
        records = [json.loads(line) for line in self.events.read_text().splitlines()]
        args = records[0]['args']
        self.assertEqual(args[args.index('--output-last-message') + 1], str(self.result))
        self.assertEqual(self.result.read_text(), 'Fixture candidate produced.\n')
        self.assertNotIn('identical', run.stderr)
        self.assertEqual(records[-1], {'type': 'worker.client_result',
                                      'schema_version': 1, 'exit_code': 0})

    def test_rejected_parser_collision_survives_full_wrapper_cleanup(self):
        self.install_fake()
        stdout = self.root / 'fresh-stdout'
        parser = Path(str(stdout) + '.parser-last')
        parser.write_text('Retained parser evidence.\n')
        commands = self.root / 'commands'; commands.mkdir()
        mktemp = commands / 'mktemp'
        mktemp.write_text('#!/opt/homebrew/bin/python3\nimport pathlib\np=pathlib.Path(' +
                         repr(str(stdout)) + ')\np.open("x").close()\nprint(p)\n')
        mktemp.chmod(0o700)
        self.env['PATH'] = str(commands) + ':' + self.env['PATH']
        self.invoke('--prd', 'PRD.md')
        records = [json.loads(line) for line in self.events.read_text().splitlines()]
        self.assertEqual([r['type'] for r in records], ['error'])
        self.assertTrue(parser.exists(), 'Refused parser evidence was deleted by cleanup')
        self.assertEqual(parser.read_text(), 'Retained parser evidence.\n')
        self.assertEqual((self.repo / 'fixture.py').read_text(), 'VALUE = 0\n')

    def test_child_failure_is_logged_as_error_and_task_remains_open(self):
        self.install_fake(fail=True)
        result = self.invoke("--prd", "PRD.md")
        records = [json.loads(line) for line in self.events.read_text().splitlines()]
        self.assertEqual(records[-1]["type"], "error")
        self.assertIn("exited 7", records[-1]["error"]["message"])
        self.assertIn("- [ ]", (self.repo / "PRD.md").read_text())
        self.assertEqual((self.repo / "fixture.py").read_text(), "VALUE = 0\n")
        self.assertIn("API error", result.stderr)


if __name__ == "__main__":
    unittest.main()
