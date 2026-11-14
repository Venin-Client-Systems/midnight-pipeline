"""Guardian scheduling survives publication without changing its runtime contract."""
import hashlib
import io
from pathlib import Path
import plistlib
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

import install_guardian as I
import maintenance_authority as A


class GuardianSchedulingTests(unittest.TestCase):
    def test_publication_keeps_standard_policy_for_both_install_modes(self):
        names = (
            'guardian.py core_repair.py recovery_policy.json action_store.py phase_runtime.py '
            'phase_guard.py project_action.py history_store.py durability.py cloud_policy.py '
            'cloud_models.json git_delivery.py verification_sandbox.py process_observer.py '
            'verification_runner.py tests/test_models.py tests/test_maintenance_sandbox.py '
            'model_manager.py model_bench.py model_cases.py models.json colibri_provider.py '
            'maintenance_authority.py guardian_bootstrap.py'
        ).split()
        body = b'# disposable publication fixture\n'
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            for name in names:
                member = tarfile.TarInfo(name)
                member.size = len(body)
                archive.addfile(member, io.BytesIO(body))
        for load in (False, True):
            with self.subTest(load=load), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve() / 'midnight'
                root.mkdir()
                home = Path(temp) / 'home'
                (home / 'Library/LaunchAgents').mkdir(parents=True)
                (root / 'guardian_bootstrap.py').write_bytes(body)
                head = 'a' * 40
                calls = []

                def native(command, **kwargs):
                    calls.append(command)
                    if command[1] == 'bootstrap':
                        published = plistlib.loads(Path(command[-1]).read_bytes())
                        self.assertEqual(published['ProcessType'], 'Standard')
                    return mock.Mock(returncode=0)

                with mock.patch.object(I.Path, 'home', return_value=home), \
                     mock.patch.object(I.subprocess, 'check_output', side_effect=['', head, stream.getvalue()]), \
                     mock.patch.object(I.subprocess, 'run', side_effect=native), \
                     mock.patch.object(A, 'seal') as seal:
                    manifest = I.install(root, load=load)

                path = home / 'Library/LaunchAgents/space.venin.midnight-guardian.plist'
                definition = plistlib.loads(path.read_bytes())
                self.assertEqual(definition['ProcessType'], 'Standard')
                self.assertEqual(definition['StartInterval'], 60)
                self.assertTrue(definition['RunAtLoad'])
                self.assertEqual(definition['ProgramArguments'],
                                 [sys.executable, '-B', str(root / 'runtime/resilience/guardian_bootstrap.py')])
                self.assertEqual(definition['WorkingDirectory'], str(root / 'runtime/resilience'))
                self.assertEqual(definition['StandardOutPath'], str(root / 'runtime/guardian.out.log'))
                self.assertEqual(definition['StandardErrorPath'], str(root / 'runtime/guardian.err.log'))
                self.assertEqual(definition['EnvironmentVariables']['TZ'], 'Australia/Adelaide')
                self.assertEqual((definition['ExitTimeOut'], definition['ThrottleInterval']), (30, 30))
                self.assertEqual(manifest['source'], head)
                self.assertEqual(manifest['files']['colibri_provider.py'], hashlib.sha256(body).hexdigest())
                seal.assert_called_once_with(root)
                self.assertEqual([command[1] for command in calls], ['bootout', 'bootstrap'] if load else [])


if __name__ == '__main__':
    unittest.main()
