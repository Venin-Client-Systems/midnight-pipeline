import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock
import midnight as M
from dashboard_details import inspect_request
from dashboard_events import ActivityHub,notify


class DetailAndActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.projects=[]
        for identity in ('inventory','notes'):
            path=self.root/identity;path.mkdir();runtime=path/'runtime';runtime.mkdir();(runtime/'runs').mkdir()
            (path/'backlog.json').write_text(json.dumps([{'id':'T1','title':'Fixture','description':'Keep records','acceptance':['Preserve existing records'],'files':['src/fixture.py']}]))
            (path/'config.json').write_text('{"cloud_model":"fixture-review"}')
            M.write(runtime/'state.json',{'done':[],'tries':{'T1':2},'feedback':{'T1':'Needs repair'}})
            self.projects.append({'id':identity,'name':identity,'path':str(path),'state_dir':str(runtime),'backlog':'backlog.json','config':'config.json','local_model':'fixture-local','backend_protocol':2})
        patch=mock.patch.object(M,'registry',return_value={'projects':self.projects});patch.start();self.addCleanup(patch.stop)

    def test_project_inspection_uses_registered_project_and_retains_failed_tasks(self):
        for identity in ('inventory','notes'):
            result=inspect_request('/api/project?project='+identity)
            self.assertEqual(result['project'],identity);self.assertEqual(result['tasks'][0]['status'],'exhausted')
            self.assertEqual(result['tasks'][0]['attempts'],2)
        for url in ('/api/project?project=../escape','/api/project?project=notes&project=inventory','/api/run?project=notes&run=../../escape'):
            with self.assertRaises(ValueError):inspect_request(url)

    def test_archived_accepted_dependency_remains_satisfied_in_project_inspection(self):
        tasks=[{'id':'prerequisite','title':'Accepted prerequisite'},
               {'id':'dependent','title':'Next change','depends_on':['prerequisite']},
               {'id':'still-blocked','title':'Needs another change','depends_on':['prerequisite','unfinished']}]
        for project in self.projects:
            (Path(project['path'])/'backlog.json').write_text(json.dumps(tasks))
            M.write(Path(project['state_dir'])/'state.json',{'done':[],'accepted':[],'tries':{}})
        project=self.projects[0];runtime=Path(project['state_dir'])
        original={'done':['prerequisite'],'tries':{'prerequisite':1},'planned':[],
                  'accepted':[{'id':'prerequisite','title':'Accepted prerequisite','run':'accepted-prerequisite',
                               'commit':'a'*40,'at':'2026-11-01T00:00:00+00:00'}]}
        compacted=M.AS.compact(runtime,project['id'],original,tasks)
        self.assertNotIn('prerequisite',compacted['done'])
        self.assertTrue(M.AS.task_done(runtime,project['id'],'prerequisite'))
        M.write(runtime/'state.json',compacted)
        before={path:(path.stat().st_mtime_ns,path.read_bytes()) for path in runtime.rglob('*') if path.is_file()}
        with mock.patch.object(M,'STATE',self.root/'controller'),mock.patch.object(M,'improvement_snapshot',return_value={}):
            result=inspect_request('/api/project?project=inventory')
            other=inspect_request('/api/project?project=notes')
        self.assertEqual({task['id']:task['status'] for task in result['tasks']},
                         {'prerequisite':'accepted','dependent':'queued','still-blocked':'blocked'})
        self.assertEqual(next(task['status'] for task in other['tasks'] if task['id']=='dependent'),'blocked')
        self.assertEqual(before,{path:(path.stat().st_mtime_ns,path.read_bytes()) for path in runtime.rglob('*') if path.is_file()})

    def test_run_receipts_and_bounded_check_logs_refuse_symlinks(self):
        folder=Path(self.projects[0]['state_dir'])/'runs/20261031T100000Z-T1-fixture';folder.mkdir()
        (folder/'task.json').write_text('{"id":"T1"}');(folder/'check-0.log').write_text('x'*15000)
        outside=self.root/'private';outside.write_text('PRIVATE OUTSIDE CONTENT');(folder/'outcome.json').symlink_to(outside)
        result=inspect_request('/api/run?project=inventory&run='+folder.name)
        self.assertEqual(result['records']['task.json']['id'],'T1')
        self.assertTrue(result['check_logs'][0]['truncated']);self.assertEqual(len(result['check_logs'][0]['text']),12000)
        self.assertNotIn('PRIVATE OUTSIDE CONTENT',json.dumps(result));self.assertTrue(result['errors'])

    def test_native_activity_survives_restart_and_does_not_notify_historical_rows(self):
        calls=[];config=lambda:{'projects':self.projects}
        eventfile=Path(self.projects[0]['state_dir'])/'events.jsonl'
        eventfile.write_text(json.dumps({'event':'accepted','run':'old','title':'Historical'})+'\n')
        hub=ActivityHub(config,self.root/'dashboard',lambda event:calls.append(event) or {'helper_exit':0})
        hub.scan();self.assertEqual(calls,[])
        value={'at':'2026-10-31T10:00:00+00:00','event':'accepted','run':'new','title':'Accepted fixture'}
        with eventfile.open('a') as f:f.write(json.dumps(value)+'\n')
        hub.scan();hub.scan();hub.deliver_one();self.assertEqual(len(calls),1)
        restarted=ActivityHub(config,self.root/'dashboard',lambda event:calls.append(event))
        restarted.scan();self.assertEqual(len(calls),1);self.assertEqual(restarted.view()['events'][0]['run'],'new')
        with eventfile.open('a') as f:f.write('{"event":"attempt_finished",')
        restarted.scan();self.assertEqual(len(calls),1)
        with eventfile.open('a') as f:f.write('"run":"failed","outcome":"failed","summary":"Needs repair"}\n')
        restarted.scan();restarted.deliver_one();self.assertEqual(len(calls),2)

    def test_notification_content_is_passed_as_data_without_shell_execution(self):
        with mock.patch('dashboard_events.subprocess.run',return_value=mock.Mock(returncode=0)) as run:
            result=notify({'project':'fixture','kind':'accepted','summary':'$(touch file) "quoted"'})
        argv=run.call_args.args[0];self.assertIn('--',argv);self.assertEqual(argv[-2],'$(touch file) "quoted"')
        self.assertNotIn('shell',run.call_args.kwargs);self.assertEqual(result['helper_exit'],0)
        self.assertEqual(result['visible_delivery'],'not_independently_verified')

    def test_controller_observations_do_not_repeat_alerts_after_restart(self):
        calls=[];value={'phase':'working','override_active':True,'override_expires':'2026-11-01T13:30:00+00:00',
            'recovery':'healthy','projects':[{'id':'inventory','on_schedule':True}],
            'checked_at':'2026-10-31T10:00:00+00:00','controller':{},'service':{'running':True}}
        folder=self.root/'observer'
        hub=ActivityHub(lambda:{'projects':[]},folder,lambda e:calls.append(e),lambda:value)
        hub.observe();hub.observe();self.assertEqual(calls,[])
        restarted=ActivityHub(lambda:{'projects':[]},folder,lambda e:calls.append(e),lambda:value)
        restarted.observe();self.assertEqual(calls,[])
        value['recovery']='needs_attention';restarted.observe();restarted.observe()
        restarted.deliver_one()
        self.assertEqual(len(calls),1);self.assertEqual(calls[0]['kind'],'recovery_changed')

    def test_bad_notification_checkpoint_keeps_viewer_available_and_preserves_evidence(self):
        folder=self.root/'corrupt';folder.mkdir();db=folder/'checkpoints.sqlite3';db.write_bytes(b'broken checkpoint')
        hub=ActivityHub(lambda:{'projects':[]},folder,lambda e:self.fail('No delivery from corrupt state'))
        hub.scan();self.assertTrue(hub.disabled);self.assertTrue(hub.view()['error'])
        self.assertEqual(db.read_bytes(),b'broken checkpoint')

    def test_schema_invalid_checkpoints_disable_notifications_without_breaking_view(self):
        import durability as D
        cases=[None,[],{'offsets':{},'events':None},{'offsets':{'notes':True},'events':[]},
               {'offsets':{},'events':[{}]},{'offsets':{},'events':[],'observed':None}]
        for state in ([],{}):
            event={'id':'f'*64,'at':None,'project':'inventory','kind':'accepted','run':None,'summary':'Fixture','notification':{'state':state}}
            cases.append({'offsets':{},'events':[event]})
        for index,value in enumerate(cases):
            folder=self.root/('bad-schema-'+str(index));folder.mkdir();path=folder/'notification-state.json'
            D.save_json(path,value);before=path.read_bytes()
            hub=ActivityHub(lambda:{'projects':[]},folder)
            self.assertTrue(hub.disabled);self.assertEqual(hub.view()['events'],[])
            self.assertEqual(path.read_bytes(),before)

    def test_first_event_from_new_project_is_delivered(self):
        calls=[];hub=ActivityHub(lambda:{'projects':self.projects},self.root/'new-log',lambda e:calls.append(e) or {'helper_exit':0})
        hub.scan()
        path=Path(self.projects[1]['state_dir'])/'events.jsonl'
        path.write_text(json.dumps({'event':'accepted','run':'first','title':'First feature'})+'\n')
        hub.scan();hub.deliver_one()
        self.assertEqual([e['run'] for e in calls],['first'])

    def test_stalled_notifier_does_not_block_live_readers(self):
        started=threading.Event();release=threading.Event()
        def stalled(event):started.set();release.wait(3);return {'helper_exit':0}
        hub=ActivityHub(lambda:{'projects':self.projects},self.root/'stalled',stalled);hub.scan()
        path=Path(self.projects[0]['state_dir'])/'events.jsonl';path.write_text('{"event":"accepted","run":"fixture","title":"Fixture"}\n')
        hub.scan();thread=threading.Thread(target=hub.deliver_one,daemon=True);thread.start()
        try:
            self.assertTrue(started.wait(2));before=time.monotonic();view=hub.view()
            self.assertLess(time.monotonic()-before,.2);self.assertEqual(view['events'][0]['notification']['state'],'delivering')
        finally:release.set();thread.join(2)


if __name__=='__main__':unittest.main()
