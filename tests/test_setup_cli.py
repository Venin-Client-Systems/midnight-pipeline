"""Setup, project connection and task entry against a temporary home.

Nothing here touches launchd, Ollama or the real checkout: the registry,
profile directory and home folder are redirected, and launchctl is a recorder.
"""
import json
import plistlib
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import setup_cli as S


def arguments(**values):
    return types.SimpleNamespace(**values)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'home'; self.home.mkdir()
        self.root = self.base / 'midnight'; (self.root / 'quality_profiles').mkdir(parents=True)
        for name, value in (('ROOT', self.root), ('REGISTRY', self.root / 'projects.json'), ('PROFILES', self.root / 'quality_profiles'),
                            ('RECEIPT', self.root / 'runtime' / 'setup.json')):
            patch = mock.patch.object(S, name, value); patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(S.Path, 'home', return_value=self.home); patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(S, 'say'); self.said = patch.start(); self.addCleanup(patch.stop)
        real = S.run
        def guarded(command, **kwargs):
            if command[0] == '/bin/launchctl':
                raise AssertionError('A test reached the real launchctl')
            return real(command, **kwargs)
        patch = mock.patch.object(S, 'run', side_effect=guarded); patch.start(); self.addCleanup(patch.stop)

    def output(self):
        return '\n'.join(str(call.args[0]) if call.args else '' for call in self.said.call_args_list)

    def repository(self, name='Example App'):
        path = self.base / name; (path / 'example').mkdir(parents=True); (path / 'tests').mkdir()
        (path / 'example' / '__init__.py').write_text(''); (path / 'tests' / '__init__.py').write_text('')
        for command in (['init', '-q', '-b', 'main'], ['add', '-A'],
                        ['-c', 'user.name=t', '-c', 'user.email=t@example.invalid', 'commit', '-q', '-m', 'first']):
            subprocess.run(['/usr/bin/git', '-C', str(path), *command], check=True)
        for key, value in (('user.name', 't'), ('user.email', 't@example.invalid')):
            subprocess.run(['/usr/bin/git', '-C', str(path), 'config', key, value], check=True)
        return path

    def registry(self):
        S.write(S.REGISTRY, S.new_registry('Australia/Adelaide', '00:00', '09:00', S.WEEKDAYS))

    def connect(self, path, **values):
        return S.init(arguments(**{'path': str(path), 'id': None, 'name': None, 'why': None, 'no_commit': False, **values}))


class SetupTests(Fixture):
    def run_setup(self, **values):
        options = {'model': None, 'timezone': 'Europe/London', 'start': '22:00', 'end': '06:00', 'every_day': False,
                   'port': 8766, 'no_services': False, 'yes': True, **values}
        self.launchctl = mock.Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
        manager = types.ModuleType('model_manager'); manager.adopt = mock.Mock()
        with mock.patch.object(S, 'prerequisites', return_value=[('everything', True, '')]), \
             mock.patch.object(S, 'local_models', return_value=[{'model': 'big:70b', 'bytes': 40 * 2**30}, {'model': 'fits:30b', 'bytes': 18 * 2**30}]), \
             mock.patch.object(S, 'memory_bytes', return_value=32 * 2**30), mock.patch.object(S, 'run', self.launchctl), \
             mock.patch.dict('sys.modules', {'model_manager': manager}):
            code = S.setup(arguments(**options))
        return code, manager

    def test_setup_writes_local_state_services_and_launcher(self):
        code, manager = self.run_setup()
        self.assertEqual(code, 0)
        data = json.loads(S.REGISTRY.read_text())
        self.assertEqual((data['timezone'], data['schedule'], data['projects']),
                         ('Europe/London', {'start': '22:00', 'end': '06:00', 'weekdays': S.WEEKDAYS}, []))
        manager.adopt.assert_called_once_with('fits:30b', reason='Chosen during setup')
        controller = plistlib.loads((self.home / 'Library/LaunchAgents/space.venin.midnight.plist').read_bytes())
        self.assertEqual(controller['ProgramArguments'], [S.PYTHON, str(self.root / 'midnight.py'), 'tick'])
        self.assertEqual(controller['EnvironmentVariables']['TZ'], 'Europe/London')
        dashboard = plistlib.loads((self.home / 'Library/LaunchAgents/space.venin.midnight-dashboard.plist').read_bytes())
        self.assertEqual(dashboard['ProgramArguments'][-2:], ['--port', '8766'])
        self.assertEqual([call.args[0][1] for call in self.launchctl.call_args_list], ['bootout', 'bootstrap', 'bootout', 'bootstrap'])
        launcher = self.home / '.local/bin/midnight'
        self.assertIn(str(self.root / 'midnight.py'), launcher.read_text()); self.assertTrue(launcher.stat().st_mode & 0o100)
        self.assertEqual(json.loads(S.RECEIPT.read_text())['model'], 'fits:30b')

    def test_setup_again_keeps_the_schedule_and_a_foreign_launcher(self):
        self.run_setup()
        launcher = self.home / '.local/bin/midnight'; launcher.write_text('#!/bin/sh\necho someone else\n')
        self.run_setup(timezone='Asia/Tokyo', no_services=True)
        self.assertEqual(json.loads(S.REGISTRY.read_text())['timezone'], 'Europe/London')
        self.assertEqual(launcher.read_text(), '#!/bin/sh\necho someone else\n')
        self.assertFalse(self.launchctl.called)

    def test_missing_prerequisite_stops_before_anything_is_written(self):
        with mock.patch.object(S, 'prerequisites', return_value=[('Ollama running', False, 'Install Ollama')]):
            with self.assertRaisesRegex(S.SetupError, 'MISSING'):
                S.setup(arguments(model=None))
        self.assertFalse(S.REGISTRY.exists()); self.assertIn('Install Ollama', self.output())

    def test_model_choice_prefers_the_largest_that_fits_and_explains_an_empty_machine(self):
        models = [{'model': 'big', 'bytes': 30 * 2**30}, {'model': 'mid', 'bytes': 18 * 2**30}, {'model': 'small', 'bytes': 9 * 2**30}]
        self.assertEqual(S.choose_model(models, 32 * 2**30), 'mid')
        self.assertEqual(S.choose_model(models, 8 * 2**30), 'small')
        self.assertEqual(S.choose_model(models, 32 * 2**30, 'big'), 'big')
        with self.assertRaisesRegex(S.SetupError, 'ollama pull absent'):
            S.choose_model(models, 32 * 2**30, 'absent')
        with self.assertRaisesRegex(S.SetupError, 'ollama pull'):
            S.choose_model([], 32 * 2**30)

    def test_uninstall_removes_only_what_setup_created(self):
        self.run_setup()
        foreign = self.home / 'Library/LaunchAgents/com.example.other.plist'; foreign.write_text('keep')
        with mock.patch.object(S, 'run', mock.Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))):
            S.uninstall(arguments(purge=False))
        self.assertEqual(sorted(p.name for p in (self.home / 'Library/LaunchAgents').iterdir()), ['com.example.other.plist'])
        self.assertFalse((self.home / '.local/bin/midnight').exists()); self.assertTrue(S.REGISTRY.exists())
        with mock.patch.object(S, 'run', mock.Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))):
            S.uninstall(arguments(purge=True))
        self.assertFalse(S.REGISTRY.exists()); self.assertFalse((self.root / 'runtime').exists())


class ProjectTests(Fixture):
    def setUp(self):
        super().setUp(); self.registry(); self.path = self.repository()

    def test_init_scaffolds_commits_and_registers_without_touching_tracked_midnight_files(self):
        self.assertEqual(self.connect(self.path), 0)
        project = json.loads(S.REGISTRY.read_text())['projects'][0]
        self.assertEqual((project['id'], project['path'], project['backend_protocol'], project['github']),
                         ('example-app', str(self.path), 5, {'enabled': False}))
        self.assertEqual(project['state_dir'], str(self.root / 'runtime/projects/example-app'))
        config = json.loads((self.path / 'orchestration/config.json').read_text())
        self.assertEqual(config['product_dirs'], ['example']); self.assertEqual(config['max_cloud_calls_per_night'], 0)
        self.assertIn(repr(str(self.root)), (self.path / 'orchestration/supervisor.py').read_text())
        import quality_cycle
        quality_cycle.load_profile(self.root / 'quality_profiles/example-app.json')
        self.assertEqual(S.git(self.path, 'status', '--porcelain'), '')
        self.assertEqual(S.git(self.path, 'log', '-1', '--format=%s'), 'Add Midnight orchestration files')

    def test_init_refuses_what_midnight_cannot_work_on(self):
        (self.path / 'scratch.txt').write_text('dirty')
        with self.assertRaisesRegex(S.SetupError, 'uncommitted'):
            self.connect(self.path)
        (self.path / 'scratch.txt').unlink()
        self.connect(self.path)
        with self.assertRaisesRegex(S.SetupError, 'already connected'):
            self.connect(self.path)
        plain = self.base / 'plain'; plain.mkdir()
        with self.assertRaisesRegex(S.SetupError, 'not a Git repository'):
            self.connect(plain)
        S.REGISTRY.unlink()
        with self.assertRaisesRegex(S.SetupError, 'midnight setup'):
            self.connect(self.path)

    def task(self, **values):
        return S.add_task(arguments(**{'project': 'example-app', 'title': 'Add greeting', 'description': 'Add greet(name).',
                                        'accept': ["greet('a') returns 'Hello, a'."], 'file': ['example/greeting.py', 'tests/test_unit_greeting.py'],
                                        'id': None, **values}))

    def test_add_task_appends_numbers_and_commits_a_task_the_backend_accepts(self):
        self.connect(self.path); self.task(); self.task(title='Second')
        tasks = json.loads((self.path / 'orchestration/backlog.json').read_text())
        self.assertEqual([t['id'] for t in tasks], ['T001', 'T002'])
        self.assertEqual(S.git(self.path, 'status', '--porcelain'), '')
        import project_backend
        backend = project_backend.load_backend(self.path, 'example-app')
        self.assertTrue(all(backend.task_valid(t) for t in tasks))

    def test_add_task_explains_each_rejected_task(self):
        self.connect(self.path)
        for values, message in (({'file': ['README.md', 'tests/test_unit_x.py']}, 'outside what the worker may edit'),
                                ({'file': ['example/a.py']}, 'test_unit'),
                                ({'file': ['example/a.py', 'example/b.py', 'example/c.py', 'example/d.py', 'tests/test_unit_x.py']}, 'one to four'),
                                ({'accept': ['  ']}, 'at least one --accept'), ({'project': 'nope'}, 'Unknown project')):
            with self.subTest(message=message), self.assertRaisesRegex(S.SetupError, message):
                self.task(**values)
        self.assertEqual(json.loads((self.path / 'orchestration/backlog.json').read_text()), [])

    def test_release_pauses_only_for_the_release_and_explains_a_refusal(self):
        self.connect(self.path)
        import progress_recovery
        marker = self.root / 'runtime/projects/example-app/PAUSED'
        seen = []
        with mock.patch.object(progress_recovery, 'owner_release', side_effect=lambda *a: seen.append((a, marker.exists()))):
            self.assertEqual(S.release(arguments(project='example-app', task='T001', reason='Model server restarted after a stall')), 0)
        self.assertEqual(seen, [((self.root, 'example-app', 'T001', 'Model server restarted after a stall'), True)])
        self.assertFalse(marker.exists())
        marker.touch()
        with mock.patch.object(progress_recovery, 'owner_release', side_effect=progress_recovery.RecoveryHeld('Recovery sources are unchanged')):
            with self.assertRaisesRegex(S.SetupError, 'unchanged.*repair-status'):
                S.release(arguments(project='example-app', task='T001', reason='Model server restarted after a stall'))
        self.assertTrue(marker.exists())

    def test_try_builds_a_demo_whose_task_is_valid_and_whose_tests_pass(self):
        target = self.base / 'demo'
        launch = mock.Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
        real = subprocess.run
        with mock.patch.object(S, 'run', side_effect=lambda command, **kw: launch(command) if command[0] in (S.PYTHON, '/bin/launchctl') else real(command, capture_output=True, text=True, **kw)):
            self.assertEqual(S.try_demo(arguments(path=str(target))), 0)
            self.assertEqual(S.try_demo(arguments(path=str(target))), 0)
        self.assertEqual([p['id'] for p in json.loads(S.REGISTRY.read_text())['projects']], ['midnight-demo'])
        self.assertEqual(json.loads((target / 'orchestration/backlog.json').read_text())[0]['files'], S.DEMO_TASK['files'])
        self.assertEqual([call.args[0][2] for call in launch.call_args_list if call.args[0][0] == S.PYTHON], ['override', 'override'])
        tests = subprocess.run([S.PYTHON, '-m', 'unittest', 'discover', '-s', 'tests'], cwd=target, capture_output=True, text=True)
        self.assertEqual(tests.returncode, 0, tests.stderr)


if __name__ == '__main__':
    unittest.main()
