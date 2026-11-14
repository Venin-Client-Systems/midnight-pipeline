from contextlib import ExitStack
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
import phase_runtime as P


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def test_one_gpu_and_host_but_two_cloud_invocations(self):
        for kind,capacity in P.CAPACITY.items():
            with self.subTest(kind=kind),ExitStack() as stack:
                for _ in range(capacity):stack.enter_context(P.resource(self.root,kind,time.time()+1,pressure=lambda:False))
                with self.assertRaises(P.PhaseInterrupted):
                    with P.resource(self.root,kind,time.time()+.02,poll=.005,pressure=lambda:False):pass
            with P.resource(self.root,kind,time.time()+1,pressure=lambda:False):pass

    def test_pause_and_critical_memory_never_launch_a_phase(self):
        with self.assertRaises(P.PhaseInterrupted):
            with P.resource(self.root,'local',time.time()+1,stopped=lambda:True,pressure=lambda:False):pass
        reasons=[]
        with self.assertRaises(P.PhaseInterrupted):
            with P.resource(self.root,'host',time.time()+.02,poll=.005,pressure=lambda:True,waiting=reasons.append):pass
        self.assertEqual(reasons,['critical_memory_pressure'])

    def test_resource_lock_symlink_is_rejected(self):
        (self.root/'resources').mkdir();outside=self.root/'outside';outside.write_text('preserve')
        (self.root/'resources/local-0.lock').symlink_to(outside)
        with self.assertRaises(OSError):
            with P.resource(self.root,'local',time.time()+1,pressure=lambda:False):pass
        self.assertEqual(outside.read_text(),'preserve')

    def test_source_lock_wait_has_an_absolute_bound(self):
        with P.source_lock(self.root):
            with self.assertRaises(P.PhaseInterrupted):
                with P.source_lock(self.root,deadline=time.time()+.05):pass
        with P.source_lock(self.root):pass

    def test_process_output_remains_captured_outside_confined_mode(self):
        import process_observer as O
        with mock.patch.dict(__import__('os').environ,{'MIDNIGHT_CONFINED_TESTS':'0'}):
            self.assertEqual(O.check_output(['/usr/bin/true'],text=True),'')

    def test_process_birth_microseconds_reject_recycled_identity(self):
        lease=P.lease()
        if 'birth_us' in lease:
            self.assertTrue(P.alive(lease));lease['birth_us']+=1;self.assertFalse(P.alive(lease))


if __name__=='__main__':unittest.main()
