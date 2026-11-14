import unittest
from unittest.mock import patch

import memory_admission as M


class QuietHost(unittest.TestCase):
    def setUp(self):
        self.now=0
        self.value={'at':1000,'boot_id':'boot','total_bytes':32*1024**3,'free_bytes':20_000_000_000,
                    'inactive_bytes':4_000_000_000,'pressure':'normal','swap_out_counter':0}
        self.gate=M.QuietGate(probe=lambda:dict(self.value,at=1000+self.now),clock=lambda:self.now)

    def tick(self,seconds=5):
        self.now+=seconds
        return self.gate.observe()

    def test_full_quiet_window_including_exact_threshold_and_old_swap(self):
        self.value['swap_used_bytes']=20_000_000_000
        self.assertFalse(self.gate.observe()['ready'])
        for _ in range(23):self.assertFalse(self.tick()['ready'])
        result=self.tick()
        self.assertTrue(result['ready']);self.assertEqual(result['quiet_seconds'],120)
        self.assertEqual(result['minimum_free_inactive_bytes'],24_000_000_000)

    def test_warning_low_memory_missing_observation_and_counter_reset_restart_window(self):
        mutations=[('pressure','warning'),('inactive_bytes',None),('free_bytes',19_999_999_999),
                   ('swap_out_counter',-1),('boot_id','other')]
        for key,value in mutations:
            with self.subTest(key=key):
                self.setUp();self.gate.observe()
                for _ in range(24):result=self.tick()
                self.assertTrue(result['ready']);self.value[key]=value
                result=self.tick();self.assertFalse(result['ready']);self.assertEqual(result['quiet_seconds'],0)

    def test_swap_out_allowance_is_a_delta_not_lifetime_counter(self):
        self.value['swap_out_counter']=10**12;self.gate.observe()
        self.value['swap_out_counter']+=64*1024**2
        for _ in range(24):result=self.tick()
        self.assertTrue(result['ready'])
        self.value['swap_out_counter']+=64*1024**2+1
        self.assertEqual(self.tick()['reason'],'recent_swap_out')

    def test_gap_and_reboot_cannot_join_separate_quiet_intervals(self):
        self.gate.observe()
        for _ in range(23):self.tick()
        self.assertEqual(self.tick(8)['reason'],'memory_observation_gap')
        for _ in range(24):result=self.tick()
        self.assertTrue(result['ready'])
        self.value['boot_id']='new-boot'
        self.assertFalse(self.tick()['ready'])

    def test_exception_and_impossible_measurement_fail_closed(self):
        with patch.object(self.gate,'probe',side_effect=OSError('private detail')):
            result=self.gate.observe()
        self.assertEqual(result['reason'],'memory_observation_unavailable')
        self.assertNotIn('private detail',str(result))
        self.value['inactive_bytes']=32*1024**3
        self.assertFalse(self.tick()['ready'])

    def test_native_probe_uses_existing_parser_and_only_read_commands(self):
        vm='Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 100.\nPages inactive: 200.\nSwapouts: 3.\n'
        sysctl='hw.memsize: 34359738368\nkern.memorystatus_vm_pressure_level: 1\n'
        with patch.object(M.subprocess,'check_output',side_effect=[vm,sysctl]) as command,patch.object(M.D,'boot_id',return_value='boot'):
            value=M.sample()
        self.assertEqual(value['inactive_bytes'],200*16384)
        self.assertEqual([call.args[0][0] for call in command.call_args_list],['/usr/bin/vm_stat','/usr/sbin/sysctl'])


if __name__=='__main__':unittest.main()
