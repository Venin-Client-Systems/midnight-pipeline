"""Fixed broker trust boundary. No inference, live state or account access."""
import json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest import mock
import worker_probe_runtime as P
import structured_worker as S
import core_repair as C
import worker_probe_client as Client

class ProbeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.case=self.root/'label';self.case.mkdir();self.work=self.case/'work';self.work.mkdir()
  self.manifest={'version':1,'workspace':str(self.work),'task_id':'typed-worker-qualification-label','allowed_files':['solution.py'],'test_command':S.syntax_command(['solution.py'],'/opt/homebrew/bin:/usr/bin:/bin'),'timeout_seconds':30}
  (self.case/'worker-manifest.json').write_text(json.dumps(self.manifest));self.runner=mock.Mock(return_value={'code':0,'reason':'exited','command':self.manifest['test_command']})
  self.broker=P.Broker(self.case,self.manifest,self.runner)
 def write(self,value):
  path=self.broker.root/('a'*32+'.request');path.write_text(json.dumps(value));return path
 def value(self):return {'case':'label','test':'test-1','manifest_sha256':self.broker.manifest_hash}
 def test_host_selects_exact_fixed_command_and_one_result(self):
  path=self.write(self.value());self.broker.step(time.monotonic()+10);self.broker.step(time.monotonic()+10)
  self.runner.assert_called_once();args=self.runner.call_args.args
  self.assertEqual(args[:3],(self.work,self.case/'worker-tools/test-1',self.manifest));self.assertTrue(path.with_suffix('.response').is_file())
 def test_request_cannot_choose_command_path_case_or_changed_manifest(self):
  for value in (dict(self.value(),command=['/bin/sh']),dict(self.value(),test='../../private'),dict(self.value(),case='product')):
   self.write(value)
   with self.assertRaises(ValueError):self.broker.step(time.monotonic()+10)
  self.write(self.value());(self.case/'worker-manifest.json').write_text('{}')
  with self.assertRaises(ValueError):self.broker.step(time.monotonic()+10)
  self.runner.assert_not_called()
 def test_product_worker_cannot_use_disposable_broker(self):
  with mock.patch.dict(os.environ,{'MIDNIGHT_WORKER_PROBE_BROKER':str(self.broker.root)}),self.assertRaises(ValueError):P.request(self.work,self.case/'worker-tools/test-1',dict(self.manifest,task_id='product'),time.monotonic()+2)
  self.assertEqual(list(self.broker.root.glob('*.request')),[])
 def test_confinement_proxy_rejects_product_manifest_before_exec(self):
  (self.case/'worker-manifest.json').write_text(json.dumps(dict(self.manifest,task_id='product')))
  with mock.patch.dict(os.environ,{'MIDNIGHT_PROBE_SOURCE':str(self.root),'MIDNIGHT_LOCAL_WORKER_MANIFEST':str(self.case/'worker-manifest.json'),'MIDNIGHT_WORKER_PROBE_BROKER':str(self.broker.root)}),mock.patch.object(Client.os,'execve') as execute:
   with self.assertRaises(ValueError):Client.main()
   execute.assert_not_called()
 def test_probe_oracles_cannot_be_changed_by_automatic_maintenance(self):
  for name in ('worker_probe.py','worker_probe_runtime.py','worker_probe_client.py','worker_recovery.py','verification_sandbox.py','ralphy/run.sh'):
   with mock.patch.object(C.G,'git',return_value='tests/test_recovery_new.py\n'+name),self.assertRaisesRegex(ValueError,'Protected maintenance path'):C.allowed_patch(self.root,'base')
 def test_installed_publisher_imports_in_isolated_interpreter(self):
  result=subprocess.run([sys.executable,'-I','-B',str(Path(C.__file__).with_name('progress_recovery.py')),'--help'],capture_output=True,text=True,timeout=10)
  self.assertEqual(result.returncode,0,result.stderr);self.assertIn('--publish-maintenance',result.stdout)
 def test_maintenance_controls_use_frozen_job_request_not_mutable_incident(self):
  self.root.joinpath('projects.json').write_text(json.dumps({'projects':[{'id':'alpha','state_dir':str(self.root/'alpha')},{'id':'beta','state_dir':str(self.root/'beta')}]}))
  frozen={'id':'request-a','scope':{'project':'alpha'}};job={'payload':{'maintenance':'repair','incident_binding':{'payload':{'progress_request':frozen}}}}
  with mock.patch('progress_recovery.harness_request_current',return_value=True) as validate,mock.patch.object(C.Actions,'get_incident',side_effect=AssertionError('Mutable incident was reread')):
   self.assertTrue(C.maintenance_current(self.root,job));validate.assert_called_once_with(self.root,frozen)

if __name__=='__main__':unittest.main()
