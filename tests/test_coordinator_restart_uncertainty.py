"""An unacknowledged kickstart remains spent until explicitly reconciled."""
import subprocess
import unittest
from unittest import mock

import coordinator_install as C
import test_recovery_coordinator_serialization as F


class CoordinatorRestartUncertaintyTests(unittest.TestCase):
    def setUp(self):
        self.fixture=F.CoordinatorSerializationTests()
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.effects=[];self.launch_error=None
        # Exercise the production restart_service subprocess boundary, while
        # every launchctl effect and health observation stays disposable.
        def native_call(args,timeout=45):
            self.assertEqual(args[:3],['/bin/launchctl','kickstart','-k'])
            self.assertEqual(timeout,15);self.effects.append(tuple(args))
            health=self.fixture.f.actions.meta('install_health')
            health.update(state='verified',verified_restart_epoch=health['restart_epoch'])
            self.fixture.f.actions.meta('install_health',health,write=True)
            if self.launch_error is not None:raise self.launch_error
            return ''
        patch=mock.patch.object(C,'call',side_effect=native_call)
        patch.start();self.addCleanup(patch.stop)
        self.fixture.options['restart']=C.restart_service

    def assert_repeated_resume_holds(self):
        f=self.fixture
        intents={path.name:path.read_bytes() for path in f.directory.glob('service-restart-intent-*.json')}
        health=f.f.actions.meta('install_health');effects=list(self.effects)
        receipt=C.D.read_bytes(f.directory/'service-restarts.json',None)
        self.assertTrue(intents);self.assertTrue(effects)
        self.launch_error=None
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError,'Uncertain service restart'):
                f.finish(deadline=0.01)
            self.assertEqual(self.effects,effects)
            self.assertEqual(f.f.actions.meta('install_health'),health)
            self.assertEqual({path.name:path.read_bytes() for path in f.directory.glob('service-restart-intent-*.json')},intents)
            self.assertEqual(C.D.read_bytes(f.directory/'service-restarts.json',None),receipt)
            self.assertEqual((f.root/'runtime/INSTALLING').read_bytes(),f.expected)
            self.assertEqual(f.calls['activate'],0)
            self.assertFalse((f.directory/'installation-complete.json').exists())

    def test_successful_restart_missing_receipt_is_not_reissued(self):
        def checkpoint(stage):
            if stage=='service_restart_requested':raise OSError('receipt write interrupted after successful effect')
        with self.assertRaisesRegex(OSError,'receipt write interrupted'):
            self.fixture.finish(checkpoint=checkpoint)
        self.assertEqual(len(self.effects),1)
        self.assert_repeated_resume_holds()

    def test_timeout_after_native_effect_does_not_authorise_retry(self):
        self.launch_error=subprocess.TimeoutExpired(['/bin/launchctl','kickstart'],15)
        with self.assertRaises(subprocess.TimeoutExpired):self.fixture.finish()
        self.assertEqual(len(self.effects),1)
        self.assert_repeated_resume_holds()

    def test_interrupted_subprocess_completion_does_not_authorise_retry(self):
        self.launch_error=InterruptedError('native effect completion is unknown')
        with self.assertRaises(InterruptedError):self.fixture.finish()
        self.assertEqual(len(self.effects),1)
        self.assert_repeated_resume_holds()

    def test_confirmed_receipt_resumes_without_repeating_service(self):
        def checkpoint(stage):
            if stage=='service_restarted':raise OSError('after confirmed receipt')
        with self.assertRaisesRegex(OSError,'after confirmed receipt'):
            self.fixture.finish(checkpoint=checkpoint)
        result=self.fixture.finish()
        self.assertEqual(result['state'],'complete')
        self.assertEqual(len(self.effects),2)
        self.assertEqual(len({command[-1] for command in self.effects}),2)
        self.assertEqual(self.fixture.calls['activate'],1)
        self.assertFalse((self.fixture.root/'runtime/INSTALLING').exists())


if __name__=='__main__':unittest.main()
