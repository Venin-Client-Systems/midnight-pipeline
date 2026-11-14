import copy
import unittest
from operation_status import describe


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.view = {'phase': 'working', 'service': {'loaded': True, 'running': True},
                     'checked_at': '2026-11-01T00:00:00+10:30', 'controller': {'updated_at': '2026-10-31T13:29:59+00:00'},
                     'next_start': '2026-11-02T00:00:00+10:30', 'effective_cutoff': '2026-11-01T09:00:00+10:30',
                     'override_active': False, 'projects': [{'name': 'Notes', 'active_task': {'title': 'Repair an existing feature'}}]}

    def test_running_task_and_continuous_override_have_distinct_next_time_labels(self):
        result = describe(self.view)
        self.assertEqual(result['answer'], 'YES'); self.assertTrue(result['work_active'])
        self.assertIn('Repair an existing feature', result['happening'])
        self.assertEqual(result['next_run_label'], 'Current run window ends')
        self.view.update(override_active=True, override_expires='2026-11-02T00:00:00+10:30')
        result = describe(self.view)
        self.assertEqual(result['next_run_label'], 'Running continuously until')
        self.assertEqual(result['next_run_at'], self.view['override_expires'])

    def test_pause_and_offline_do_not_promise_an_automatic_start(self):
        self.view['phase'] = 'paused'; result = describe(self.view)
        self.assertEqual(result['answer'], 'NO'); self.assertTrue(result['next_run_blocked'])
        self.assertFalse(result['running'])
        self.view['phase'] = 'working'; self.view['service']['loaded'] = False
        result = describe(self.view); self.assertEqual(result['state'], 'offline'); self.assertTrue(result['next_run_blocked'])

    def test_idle_loaded_scheduler_is_not_reported_as_active_work(self):
        self.view['phase'] = 'waiting_for_midnight'; self.view['service']['running'] = False
        result = describe(self.view)
        self.assertFalse(result['running']); self.assertEqual(result['state'], 'waiting')
        self.assertEqual(result['next_run_at'], self.view['next_start']); self.assertFalse(result['next_run_blocked'])

    def test_waiting_project_and_maintenance_are_on_but_not_coding(self):
        for phase in ['waiting_for_project', 'cloud_model_maintenance', 'model_maintenance']:
            self.view['phase'] = phase; result = describe(self.view)
            self.assertTrue(result['running']); self.assertFalse(result['work_active'])

    def test_cloud_and_host_activity_are_distinct_from_local_coding(self):
        p=self.view['projects'][0]
        for activity,label in [('cloud_implementation','Exceptional cloud implementation'),('host_checks','Running host checks'),('task_management','checking escalation')]:
            p['activity']=activity;result=describe(self.view)
            self.assertFalse(result['local_worker_active']);self.assertIn(label,result['happening'])
        p['activity']='local_worker'
        self.assertTrue(describe(self.view)['local_worker_active'])

    def test_backoff_shows_next_eligible_retry_without_extending_window(self):
        self.view['phase']='waiting_for_project'
        self.view['projects'][0]['retry_after']='2026-11-01T00:15:00+10:30'
        result=describe(self.view)
        self.assertFalse(result['work_active']);self.assertIn('failure backoff',result['happening'])
        self.assertEqual(result['next_work_at'],'2026-11-01T00:15:00+10:30')
        self.assertEqual(result['next_run_at'],self.view['effective_cutoff'])
        self.view['projects'].append({'name':'Another','on_schedule':True})
        self.assertNotIn('next_work_at',describe(self.view))

    def test_stale_or_dead_controller_requires_attention(self):
        self.view['controller']['updated_at'] = '2026-10-31T12:00:00+00:00'
        result = describe(self.view); self.assertEqual(result['answer'], 'CHECK'); self.assertFalse(result['running'])
        self.view['controller']['updated_at'] = 'invalid'
        self.assertEqual(describe(self.view)['state'], 'attention')
        self.view['controller']['updated_at'] = self.view['checked_at']; self.view['service']['running'] = False
        self.assertEqual(describe(self.view)['state'], 'attention')


if __name__ == '__main__': unittest.main()
