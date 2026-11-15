"""Scheduled workers see the task's focused unit test; damaged quotes and cold model metadata recover."""
import contextlib,io,json,subprocess,tempfile,time,unittest
from pathlib import Path
from unittest import mock
import model_manager as M
import project_backend as B
import structured_worker as S


class FocusedWorkerCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.directory=Path(self.tmp.name).resolve()
        self.work=self.directory/'work';(self.work/'tests').mkdir(parents=True);(self.work/'PRD.md').write_text('One exact repair.')
        self.config={'worker_timeout_seconds':30,'managed_brief':'Host direction','typed_worker_required':True}
        self.env={'PATH':'/opt/homebrew/bin:/usr/bin:/bin'}

    def manifest(self,task,micro=None,config=None):
        backend=B.load_backend(self.work,'exampleapp',settings={})
        path=backend.typed_worker_manifest(dict(task,id='fixture'),self.work,self.directory,config or self.config,micro,self.env)
        return S.load_manifest(path,self.work),backend

    def test_managed_worker_is_offered_the_focused_test_the_host_runs_without_changing_the_handoff_check(self):
        manifest,backend=self.manifest({'files':['exampleapp/transport.py','tests/test_unit_transport_nonfinite.py']})
        self.assertEqual(manifest['check_kind'],'syntax');self.assertIn('ast.parse',manifest['test_command'][4])
        self.assertEqual(manifest['focused_command'],backend.focused_checks({'files':['tests/test_unit_transport_nonfinite.py']})[0])
        self.assertIn('focused_test',S.brief_messages(self.work,manifest)[0]['content'])

    def test_several_unit_test_files_become_one_fixed_command(self):
        manifest,_=self.manifest({'files':['tests/test_unit_a.py','tests/test_unit_b.py']})
        self.assertEqual(manifest['focused_command'][1:],['-m','unittest','tests.test_unit_a','tests.test_unit_b','-v'])

    def test_no_focused_command_without_a_worker_safe_writable_unit_test(self):
        for task in ({'files':['exampleapp/transport.py']},{'files':['exampleapp/transport.py','tests/test_lab_transport.py']},
                     {'files':['tests/test_unit_a.py','tests/test_lab_transport.py']},
                     {'files':['exampleapp/transport.py','tests/test_unit_a.py'],'local_worker_files':['exampleapp/transport.py']}):
            manifest,_=self.manifest(task)
            self.assertNotIn('focused_command',manifest)
            self.assertNotIn('focused_test',S.brief_messages(self.work,manifest)[0]['content'])

    def test_unmanaged_compatibility_manifest_is_unchanged(self):
        manifest,_=self.manifest({'files':['tests/test_unit_a.py']},config={'worker_timeout_seconds':30,'managed_brief':'Host direction'})
        self.assertEqual(manifest['check_kind'],'standalone_compatibility');self.assertNotIn('focused_command',manifest)

    def test_manifest_rejects_a_malformed_focused_command(self):
        manifest,_=self.manifest({'files':['tests/test_unit_a.py']});path=self.directory/'worker-manifest.json'
        for bad in ([],['python3','-m','unittest'],'python3',[1]):
            path.write_text(json.dumps(dict(manifest,focused_command=bad)))
            with self.assertRaises(ValueError):S.load_manifest(path,self.work)


class FocusedFeedbackWorkerTests(unittest.TestCase):
    FOCUSED=['/opt/homebrew/bin/python3','-m','unittest','tests.test_unit_value','-v']

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.directory=Path(self.tmp.name).resolve()
        self.work=self.directory/'work';self.work.mkdir();(self.work/'value.py').write_text('value=0\n')
        self.manifest={'version':1,'workspace':str(self.work),'task_id':'fixture','allowed_files':['value.py'],
            'test_command':['/opt/homebrew/bin/python3','-B','syntax.py'],'focused_command':self.FOCUSED,'timeout_seconds':30}
        path=self.directory/'worker-manifest.json';path.write_text(json.dumps(self.manifest));self.manifest['_sha256']=S.digest(path)
        control=self.directory/'worker-tools';control.mkdir();self.model={'model':'fixture','context':32768}
        self.worker=S.Worker(self.work,control,self.manifest,self.model);self.focused_code=1;self.commands=[]

    def fake_run(self,work,directory,manifest,deadline):
        self.commands.append(manifest['test_command']);focused=manifest['test_command']==self.FOCUSED
        self.loopback=getattr(self,'loopback',[])+[manifest.get('loopback')]
        directory.mkdir(parents=True,exist_ok=True);(directory/'check.log').write_bytes(b'log')
        return {'code':self.focused_code if focused else 0,'reason':'exited','seconds':0.0,'command':manifest['test_command'],
                'output':'TypeError: missing base_url' if focused and self.focused_code else 'ok','log_sha256':S.digest(directory/'check.log')}

    def action(self,name,path='',content=''):
        with contextlib.redirect_stdout(io.StringIO()),mock.patch.object(S,'run_tests',side_effect=self.fake_run),\
             mock.patch.object(S.M,'local_descriptor',return_value=self.model):
            return self.worker.action({'action':name,'path':path,'content':content,'summary':'Factual summary'},time.monotonic()+10)

    def test_failing_focused_test_is_reported_and_finish_is_refused_once_then_left_to_the_host(self):
        self.action('write_file','value.py','value=1\n');result=self.action('run_tests')
        self.assertEqual(result['code'],0);self.assertEqual(result['focused_test']['code'],1);self.assertIn('base_url',result['focused_test']['output'])
        self.assertEqual(self.loopback,[None,False])
        path=self.directory/'worker-manifest.json';path.write_text(json.dumps(dict(json.loads(path.read_text()),loopback=True)))
        self.assertNotIn('loopback',S.load_manifest(path,self.work))
        self.assertEqual(self.commands,[self.manifest['test_command'],self.FOCUSED])
        self.assertIn('focused unit test still fails',self.action('finish')['error'])
        self.assertFalse((self.work/'.midnight-ready.json').exists())
        self.assertTrue(self.action('finish')['finished'])
        receipt=json.loads((self.directory/'worker-tools/submission.json').read_text())
        self.assertEqual(receipt['host_test']['command'],self.manifest['test_command']);self.assertNotIn('focused_test',receipt['host_test'])

    def test_repaired_focused_test_allows_finish_and_the_receipt_stays_bound_to_the_fixed_check(self):
        self.action('write_file','value.py','value=1\n');self.action('run_tests')
        self.focused_code=0;self.action('write_file','value.py','value=2\n')
        self.assertEqual(self.action('run_tests')['focused_test']['code'],0)
        self.assertTrue(self.action('finish')['finished'])
        self.assertTrue(S.submission_complete(self.directory,self.work,'fixture',self.model))


class DamagedQuoteAnchorTests(unittest.TestCase):
    SOURCE='import unittest\n\n\nclass NonFiniteTests(unittest.TestCase):\n    def setUp(self):\n        self.transport = LocalTransport()\n\n    def test_value(self):\n        self.assertTrue(self.transport)\n'

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.work=Path(self.tmp.name).resolve()
        (self.work/'tests').mkdir();(self.work/'tests/test_unit_x.py').write_text(self.SOURCE)
        self.backend=B.load_backend(self.work,'exampleapp',settings={});self.task={'id':'fixture','files':['tests/test_unit_x.py']}

    def proposal(self,quote):
        return {'action':'narrow','obligation':0,'instruction':'Repair the fixture.','path':'tests/test_unit_x.py','symbol':'NonFiniteTests.setUp','evidence_quote':quote}

    def test_newline_damaged_quote_binds_to_its_longest_real_source_line(self):
        damaged='    def setUp(self):n        self.transport = LocalTransport()'
        with self.assertRaises(B.RP.RecoveryHeld):B.RP.source_anchor(self.work,self.task,self.proposal(damaged))
        anchor=self.backend.narrowing_anchor(self.work,self.task,self.proposal(damaged))
        exact=self.backend.narrowing_anchor(self.work,self.task,self.proposal('self.transport = LocalTransport()'))
        self.assertEqual(anchor['quote_normalization']['method'],'longest_verbatim_source_line')
        self.assertEqual(anchor['unit_id'],exact['unit_id'])

    def test_quote_with_no_real_source_line_is_still_rejected(self):
        with self.assertRaises(B.RP.RecoveryHeld):
            self.backend.narrowing_anchor(self.work,self.task,self.proposal('self.transport = SomethingInvented(base_url)'))

    def test_real_line_outside_the_named_symbol_is_still_rejected(self):
        with self.assertRaises(B.RP.RecoveryHeld):
            self.backend.narrowing_anchor(self.work,self.task,self.proposal('xx self.assertTrue(self.transport) yy'))


class ModelMetadataRetryTests(unittest.TestCase):
    def test_one_cold_listing_timeout_is_retried_with_a_longer_bound(self):
        calls=[]
        def api(path,body=None,timeout=20):
            calls.append((path,timeout))
            if len(calls)==1:raise TimeoutError('timed out')
            return {'models':[]}
        with mock.patch.object(M.B,'api',side_effect=api):self.assertEqual(M.metadata('/api/tags'),{'models':[]})
        self.assertEqual(calls,[('/api/tags',20),('/api/tags',40)])

    def test_a_second_timeout_names_the_failing_operation(self):
        with mock.patch.object(M.B,'api',side_effect=TimeoutError('timed out')),self.assertRaisesRegex(TimeoutError,'/api/tags'):
            M.metadata('/api/tags')



class FocusedLoopbackSandboxTests(unittest.TestCase):
    SERVER="""import http.server,threading,unittest,urllib.request
class T(unittest.TestCase):
    def test_loopback(self):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):self.send_response(200);self.end_headers();self.wfile.write(b'ok')
            def log_message(self,*a):pass
        s=http.server.HTTPServer(('127.0.0.1',0),H);threading.Thread(target=s.handle_request,daemon=True).start()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.assertEqual(opener.open('http://127.0.0.1:%d/'%s.server_port,timeout=5).read(),b'ok');s.server_close()
"""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.directory=Path(self.tmp.name).resolve()
        self.work=self.directory/'work';(self.work/'tests').mkdir(parents=True);(self.work/'tests/__init__.py').write_text('')
        (self.work/'tests/test_unit_loop.py').write_text(self.SERVER)
        self.manifest={'test_command':['/opt/homebrew/bin/python3','-B','-m','unittest','tests.test_unit_loop'],'timeout_seconds':30}

    def run_check(self,name,**extra):
        return S.run_tests(self.work,self.directory/name,dict(self.manifest,**extra),time.monotonic()+30)

    def test_focused_sandbox_permits_a_loopback_server_and_the_syntax_sandbox_does_not(self):
        allowed=self.run_check('focused',loopback=True)
        self.assertEqual((allowed['code'],allowed['reason']),(0,'exited'),allowed['output'])
        denied=self.run_check('syntax')
        self.assertNotEqual(denied['code'],0);self.assertIn('Operation not permitted',denied['output'])
        self.assertNotIn('localhost',(self.directory/'syntax/test.sb').read_text())

    def test_sandboxed_tests_cannot_read_shared_or_external_folders(self):
        (self.work/'tests/test_unit_loop.py').write_text("""import os,unittest
class T(unittest.TestCase):
    def test_read(self):
        for folder in ('/Users/Shared','/Volumes','/Users'):
            with self.assertRaises(PermissionError):os.listdir(folder)
""")
        result=self.run_check('reads')
        self.assertEqual((result['code'],result['reason']),(0,'exited'),result['output'])

    def test_sandboxed_tests_cannot_start_an_application_by_replacing_themselves(self):
        (self.work/'tests/test_unit_loop.py').write_text("""import os
os.execv('/usr/bin/open',['open','-g','-a','Calculator'])
""")
        result=self.run_check('exec')
        self.addCleanup(subprocess.run,['/usr/bin/pkill','-x','Calculator'],capture_output=True)
        time.sleep(1)
        self.assertNotEqual(result['code'],0,result['output'])
        self.assertEqual(subprocess.run(['/usr/bin/pkill','-x','Calculator'],capture_output=True).returncode,1)

    def test_without_loopback_the_focused_run_cannot_open_a_local_connection(self):
        denied=self.run_check('off',loopback=False)
        self.assertNotEqual(denied['code'],0);self.assertIn('Operation not permitted',denied['output'])
        self.assertNotIn('localhost',(self.directory/'off/test.sb').read_text())

if __name__=='__main__':unittest.main()
