import contextlib
import datetime as dt
import io
import subprocess
import unittest
from unittest import mock

import midnight as M
import readiness as R
from operation_status import describe


class LaunchReadinessTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 11, 7, 10, tzinfo=dt.timezone.utc)
        self.sample = dict(at=self.now.timestamp(), total_bytes=32_000_000_000,
                           free_bytes=10_000_000_000, inactive_bytes=6_000_000_000,
                           swap_out_counter=123, boot_id='test-boot', pressure='normal')
        self.models = {'selected_next': {'model': 'colibri/glm-5.3'}}
        self.view = {'phase': 'waiting_for_midnight', 'service': {'loaded': True, 'running': False},
                     'next_start': '2026-11-09T00:00:00+10:30', 'schedule':
                     {'start': '00:00', 'end': '09:00', 'weekdays': ['mon', 'tue', 'wed', 'thu', 'fri']},
                     'timezone': 'Australia/Adelaide', 'checked_at': self.now.isoformat(),
                     'projects': [], 'override_active': False, 'ledger': 'test ledger'}

    def observe(self, **kwargs):
        return R.observe(self.models, {}, self.now, profile=lambda model: {'version': 2},
                         probe=lambda: self.sample, clock=lambda: self.now, **kwargs)

    def test_scheduled_but_memory_blocked_never_claims_ready(self):
        self.view['readiness'] = self.observe()
        result = describe(self.view)
        self.assertNotIn('is ready', result['happening'])
        self.assertIn('blocked now', result['happening'])
        self.assertEqual(result['next_run_label'], 'Next eligible window')
        self.assertEqual(result['next_run_at'], self.view['next_start'])
        self.assertFalse(result['work_active'])

    def test_low_memory_reports_decimal_headroom_and_deficit(self):
        result = self.observe()
        self.assertEqual(result['state'], 'blocked_now')
        self.assertEqual(result['memory']['deficit_bytes'], 8_000_000_000)
        self.assertIn('16.0 GB', result['summary'])
        self.assertFalse(result['admission_verified'])

    def test_initial_good_sample_is_never_continuous_admission(self):
        self.sample['free_bytes'] = 20_000_000_000
        result = self.observe()
        self.assertEqual(result['state'], 'quiet_window_not_observed')
        self.assertFalse(result['memory']['admission_verified'])
        self.assertEqual(result['memory']['quiet_window_seconds'], 120)

    def test_pressure_blocks_even_with_sufficient_headroom(self):
        self.sample.update(free_bytes=20_000_000_000, pressure='warning')
        result = self.observe()
        self.assertEqual(result['state'], 'blocked_now')
        self.assertEqual(result['memory']['reason'], 'memory_pressure')

    def test_stale_future_and_invalid_samples_are_unknown(self):
        for sample in [None, {}, dict(self.sample, at=self.now.timestamp()-8),
                       dict(self.sample, at=self.now.timestamp()+1),
                       dict(self.sample, free_bytes=float('nan'))]:
            with self.subTest(sample=sample):
                result = R.memory_observation(sample, self.now)
                self.assertEqual(result['state'], 'unknown')
                self.assertFalse(result['admission_verified'])

    def test_native_probe_timeout_is_unavailable_not_failed_status(self):
        with mock.patch.object(R.MA, 'sample', side_effect=subprocess.TimeoutExpired('vm_stat', 3)):
            result = R.observe(self.models, {}, self.now, profile=lambda model: {'version': 2})
        self.assertEqual(result['state'], 'unknown')

    def test_native_sample_is_compared_with_end_of_probe_time(self):
        # A real sample is newer than the outer status collection's start time.
        self.sample['at'] += 2
        result = R.observe(self.models, {}, self.now, profile=lambda model: {'version': 2},
                           probe=lambda: self.sample, clock=lambda: self.now + dt.timedelta(seconds=2))
        self.assertEqual(result['state'], 'blocked_now')

    def test_real_profile_path_checks_bindings_then_configuration_version(self):
        with mock.patch('colibri_provider.phase_profile', return_value={'worker_timeout_seconds': 32400}) as verified, \
             mock.patch('colibri_provider.config', return_value={'version': 2}):
            result = R.observe(self.models, {}, self.now, probe=lambda: self.sample, clock=lambda: self.now)
        verified.assert_called_once_with(self.models['selected_next'])
        self.assertEqual(result['state'], 'blocked_now')

    def test_profile_failure_does_not_sample_or_start_anything(self):
        probe = mock.Mock()
        with mock.patch('colibri_provider.phase_profile', side_effect=ValueError('binding changed')):
            result = R.observe(self.models, {}, self.now, probe=probe)
        probe.assert_not_called()
        self.assertEqual(result['checks'][0]['id'], 'model_profile')
        self.assertEqual(result['state'], 'blocked_now')

    def test_pause_and_running_work_take_priority_over_next_launch_observation(self):
        self.view['readiness'] = self.observe()
        self.view['phase'] = 'paused'
        self.assertEqual(describe(self.view)['state'], 'paused')
        self.view.update(phase='working', service={'loaded': True, 'running': True})
        result = describe(self.view)
        self.assertTrue(result['work_active'])
        self.assertNotIn('blocked now', result['happening'])

    def test_cli_shows_saved_weekdays_and_launch_observation(self):
        self.view['readiness'] = self.observe()
        output = io.StringIO()
        with contextlib.redirect_stdout(output): M.render(self.view)
        self.assertIn('Mon, Tue, Wed, Thu, Fri', output.getvalue())
        self.assertIn('Next launch observation:', output.getvalue())
        self.assertNotIn(', daily', output.getvalue())

    def test_expiry_does_not_hide_the_failed_commissioning_outcome(self):
        result = R.observe(self.models, {'state': 'expired', 'job_state': 'failed'}, self.now,
                           profile=lambda model: {'version': 2}, probe=lambda: self.sample,
                           clock=lambda: self.now)
        self.assertTrue(any('did not complete successfully' in note for note in result['notes']))


if __name__ == '__main__': unittest.main()
