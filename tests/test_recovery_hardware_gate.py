import hashlib,json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import durability as D
import hardware_release as H
import deferred_release as R
import tests.test_deferred_release as F


class HardwareRecoveryGateTests(unittest.TestCase):
    def test_only_complete_matching_source_proof_activates_native_readings(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime=Path(tmp);head='a'*40
            path=runtime/'commissioning/recovery/result.json';path.parent.mkdir(parents=True)
            for name in ('observer.json','monitor.py'):(path.parent/name).write_text('pinned '+name)
            D.save_json(runtime/'sensor-release-gate.json',{'expected_source':head,
                'proof_relative_path':'commissioning/recovery/result.json',
                'observer_sha256':hashlib.sha256((path.parent/'observer.json').read_bytes()).hexdigest(),
                'monitor_sha256':hashlib.sha256((path.parent/'monitor.py').read_bytes()).hexdigest()})
            proof={'state':'passed','continuous_healthy_seconds':14400,'required_seconds':14400,
                'maximum_resource_ownership':{'local':1,'host':1,'cloud':2},
                'latest':{'actual_commit':head,'expected_commit':head,'installed_commit':head,'installation':'verified','issues':[]}}
            self.assertFalse(H.ready(runtime,head));path.write_text(json.dumps(proof));self.assertTrue(H.ready(runtime,head))
            self.assertFalse(H.ready(runtime,'b'*40))
            (path.parent/'monitor.py').write_text('changed observer implementation')
            self.assertFalse(H.ready(runtime,head));(path.parent/'monitor.py').write_text('pinned monitor.py')
            for change in ({'continuous_healthy_seconds':14399},{'state':'observing'},
                           {'maximum_resource_ownership':{'local':2,'host':1,'cloud':2}}):
                path.write_text(json.dumps({**proof,**change}));self.assertFalse(H.ready(runtime,head))
            path.unlink();path.symlink_to(runtime/'sensor-release-gate.json');self.assertFalse(H.ready(runtime,head))

    def test_installed_observer_never_adopts_source_or_restarts_services(self):
        fixture=F.DeferredReleaseTests('test_validation_result_is_required_before_any_stop_or_source_mutation')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.value.update(mode='observe_installed',commit=fixture.before)
        fixture.proof_path.unlink()
        self.assertEqual(fixture.tick()['state'],'waiting_validation')
        R.subprocess.run.assert_not_called()
        fixture.proof_path.write_text(json.dumps(fixture.proof))
        with mock.patch.object(R,'sensor_health',return_value={'passed':True}):
            self.assertEqual(fixture.tick()['state'],'complete')
        self.assertEqual(fixture.head,fixture.before);R.subprocess.run.assert_not_called()
        self.assertNotIn(('merge','--ff-only',fixture.before),fixture.calls)


if __name__=='__main__':unittest.main()
