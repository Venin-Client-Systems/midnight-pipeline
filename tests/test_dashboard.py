import csv
import http.client
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

import dashboard as D
import midnight as M


class DashboardTests(unittest.TestCase):
    def test_top_level_user_navigation_works_without_allowing_cross_site_api_reads(self):
        import urllib.request,urllib.error
        base='http://127.0.0.1:'+str(self.server.server_port)
        headers={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document','Sec-Fetch-User':'?1'}
        with urllib.request.urlopen(urllib.request.Request(base+'/',headers=headers)) as response:self.assertEqual(response.status,200)
        for route,extra in [('/api/status',{}),('/',{'Sec-Fetch-Dest':'iframe'}),('/',{'Origin':'https://example.invalid'})]:
            with self.assertRaises(urllib.error.HTTPError) as result:urllib.request.urlopen(urllib.request.Request(base+route,headers={**headers,**extra}))
            self.assertEqual(result.exception.code,403)

    def setUp(self):
        self.calls=0
        def provider():
            self.calls+=1
            return {'phase':'working','value':'<script>untrusted</script>'}
        self.server=D.DashboardServer(int(os.environ.get('MIDNIGHT_VALIDATION_PORT','0')),provider,lambda:b'date,summary\n2026-10-31,fixture\n')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    def request(self,path='/',method='GET',headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        conn.request(method,path,headers=headers or {})
        result=conn.getresponse();body=result.read();response=(result.status,dict(result.getheaders()),body);conn.close();return response

    def test_read_only_data_cached_and_static_assets_available(self):
        for _ in range(2):
            code,headers,body=self.request('/api/status')
            self.assertEqual(code,200);self.assertEqual(json.loads(body)['phase'],'working')
            self.assertEqual(headers['Cache-Control'],'no-store')
            self.assertNotIn('Access-Control-Allow-Origin',headers)
        self.assertEqual(self.calls,1)
        for path in ('/','/app.js','/style.css','/telemetry.js'):
            code,headers,body=self.request(path)
            self.assertEqual(code,200);self.assertTrue(body)
            self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])

    def test_cross_origin_rebinding_and_write_requests_rejected(self):
        for headers in ({'Host':'evil.example'}, {'Origin':'https://evil.example'}, {'Sec-Fetch-Site':'cross-site'}):
            self.assertEqual(self.request('/api/status',headers=headers)[0],403)
        for method in ('POST','PUT','PATCH','DELETE'):
            self.assertEqual(self.request('/api/status',method=method)[0],403)
        for path in ('/../projects.json','/runtime/sessions.csv','/api/start'):
            self.assertEqual(self.request(path)[0],404)
        self.assertEqual(self.calls,0)

    def test_telemetry_reads_cached_sampler_and_survives_unavailable_project_status(self):
        sampler=mock.Mock();sampler.view.return_value={'available':True,'history':[{'cpu_percent':42}],'sampled_at':'fixture'}
        self.server.telemetry=sampler;self.server.provider=mock.Mock(side_effect=ValueError('Project unavailable'))
        for _ in range(2):
            code,_,body=self.request('/api/telemetry');self.assertEqual(code,200)
            self.assertEqual(json.loads(body)['history'][0]['cpu_percent'],42)
        self.server.provider.assert_not_called()
        self.assertEqual(self.request('/api/telemetry',headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/telemetry',method='POST')[0],403)
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        conn.request('GET','/api/telemetry/events');response=conn.getresponse()
        self.assertEqual(response.readline(),b'event: telemetry\n')
        self.assertIn(b'"available": true',response.readline());conn.close()

    def test_unavailable_observation_is_not_returned_as_healthy(self):
        self.server.provider=mock.Mock(side_effect=ValueError('private detail'))
        code,_,body=self.request('/api/status')
        self.assertEqual(code,503);self.assertNotIn(b'private detail',body)

    def test_service_health_is_independent_of_project_collection_and_has_loaded_generation(self):
        self.server.provider=mock.Mock(side_effect=ValueError('Project state unavailable'))
        code,_,body=self.request('/api/health');self.assertEqual(code,200)
        value=json.loads(body);self.assertEqual(value['service'],'midnight-dashboard')
        self.assertEqual(value['code_sha256'],D.LOADED_CODE_SHA256);self.assertIn('boot_id',value['process'])
        self.server.provider.assert_not_called()
        self.assertEqual(self.request('/api/health',headers={'Origin':'https://evil.example'})[0],403)

    def test_collector_includes_multiple_registered_projects_without_product_assumptions(self):
        import performance
        projects=[];stats=[]
        for identity in ('inventory','notes'):
            projects.append({'id':identity,'name':identity.title(),'on_schedule':True,'why':'Fixture','phase':'running',
                'observed_at':M.now().isoformat(),'active_task':{'id':'task','title':'Fixture task'},'queue':{},
                'attempts_today':1,'failures_today':0,'cloud_calls_today':1,'interruptions_today':0,
                'pending_review':None,'errors':[],'accepted':[],'recent_feedback':{}})
            stats.append({'project':identity,**{k:0 for k in ('accepted','failed','interrupted','in_progress','awaiting_review','acceptance_rate','worker_seconds_total','review_seconds_total')}})
        view={'pipeline':'Midnight','checked_at':M.now().isoformat(),'phase':'working','service':{'loaded':True},
            'schedule':{'start':'00:00','end':'09:00'},'timezone':'Australia/Adelaide','override':None,
            'override_active':False,'next_start':M.now().isoformat(),'projects':projects,'recent_sessions':[]}
        with mock.patch.object(M,'registry',return_value={'projects':projects}),mock.patch.object(M,'snapshot',return_value=view), \
             mock.patch.object(M,'doctor',return_value={'status':'healthy','cold_boot_requirement':'Login required'}), \
             mock.patch.object(performance,'collect',return_value={'projects':stats}):
            result=D.collect()
            view.update(run_mode='continuous',override={'mode':'continuous','created_at':M.now().isoformat(),'expires_at':None})
            projects[0].update(quality_monitor={'local_reviews':[{'accepted_commit':'a'*40,'review':{'correctness':4}}]},routine_provider='local',local_supervision_calls_today=3)
            continuous=D.collect()
        self.assertEqual([p['id'] for p in result['projects']],['inventory','notes'])
        self.assertEqual(continuous['run_mode'],'continuous');self.assertIsNone(continuous['override_expires'])
        self.assertEqual(continuous['projects'][0]['quality_monitor'],projects[0]['quality_monitor'])
        self.assertEqual(continuous['projects'][0]['local_supervision_calls_today'],3)

    def test_csv_download_and_formula_safe_export_preserve_original_rows(self):
        code,headers,body=self.request('/api/sessions.csv')
        self.assertEqual(code,200);self.assertIn('attachment',headers['Content-Disposition'])
        rows=[{'date':'2026-10-31','summary':'=CMD()','why':'  @external','project':'fixture'}]
        with mock.patch.object(M,'ledger_rows',return_value=rows):
            exported=list(csv.DictReader(io.StringIO(D.sessions_csv().decode())))
        self.assertEqual(exported[0]['summary'],"'=CMD()")
        self.assertEqual(exported[0]['why'],"'  @external")
        self.assertEqual(rows[0]['summary'],'=CMD()')

    def test_dashboard_cli_handles_sigterm_independently_of_controller_stop_flag(self):
        with tempfile.TemporaryDirectory() as name:
            script="""import os,dashboard as D,midnight as M,datetime as dt
from pathlib import Path
M.STATE=Path(__import__('sys').argv[1]);M.registry=lambda:{'projects':[]}
D.collect=lambda:{'phase':'paused','override_active':False,'override_expires':None,'recovery':'healthy','projects':[],'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'controller':{},'service':{'running':False}}
D.Telemetry=lambda *args: type('FakeTelemetry',(),{'start':lambda self:None,'stop':lambda self:None,'view':lambda self:{'available':False,'history':[]}})()
D.serve(int(os.environ.get('MIDNIGHT_VALIDATION_CHILD_PORT','0')))
"""
            env=dict(os.environ,PYTHONPATH=str(Path(D.__file__).parent))
            with subprocess.Popen([sys.executable,'-B','-u','-c',script,name],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env) as process:
                try:
                    line=process.stdout.readline();self.assertIn('Midnight dashboard:',line)
                    process.send_signal(signal.SIGTERM);out,err=process.communicate(timeout=5)
                    self.assertEqual(process.returncode,0,err)
                finally:
                    if process.poll() is None:process.kill();process.communicate(timeout=3)


if __name__=='__main__':unittest.main()
