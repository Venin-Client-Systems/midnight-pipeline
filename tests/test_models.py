import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import model_manager as M
import model_bench as B

class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patch=mock.patch.object(M,'RUNTIME',self.root);self.patch.start()
        self.cfg=M.config()
        self.inc={'model':'fixture:old','digest':'a'*64,'context':32768,'bytes':10,'parameters_sha256':'b'*64}
        self.new=dict(self.inc,model='fixture:new',digest='c'*64)
    def tearDown(self):self.patch.stop();self.temp.cleanup()

    def results(self):
        return [{'side':('incumbent','challenger')[i%2],'passed':True,'seconds':100 if i%2==0 else 80,
                 'stop_reason':None,'swap_growth_mib':0,'peak_pressure':1} for i in range(12)]

    def test_promotion_requires_complete_absolute_quality_and_paired_win(self):
        values=self.results();self.assertTrue(M.compare(values)['promote'])
        self.assertFalse(M.compare(values[:-1])['promote'])
        values[1]['passed']=False;self.assertFalse(M.compare(values)['promote'])
        values=self.results();values[1]['peak_pressure']=4;self.assertFalse(M.compare(values)['promote'])
        values=self.results()
        for v in values:v['seconds']=100
        self.assertFalse(M.compare(values)['promote'])
        values[0]['passed']=False;self.assertTrue(M.compare(values)['promote'])

    def test_discovery_checks_actual_license_and_weights_not_advertised_context(self):
        cfg=copy.deepcopy(self.cfg);cfg['publishers']=[];cfg['candidates']=cfg['candidates'][:1]
        manifest={'layers':[{'mediaType':'application/vnd.ollama.image.model','size':16_000_000_000}]}
        with mock.patch.object(M,'ram',return_value=32*1024**3),mock.patch.object(M,'public_json',side_effect=[({'cardData':{'license':'apache-2.0'}},'c'),(manifest,'d')]):
            self.assertTrue(M.discover(cfg)['candidates'][0]['eligible'])
        manifest['layers'][0]['size']=52_000_000_000
        with mock.patch.object(M,'ram',return_value=32*1024**3),mock.patch.object(M,'public_json',side_effect=[({'cardData':{'license':'apache-2.0'}},'c'),(manifest,'d')]):
            self.assertEqual(M.discover(cfg)['candidates'][0]['reason'],'weights_exceed_ram_fit')
        with mock.patch.object(M,'ram',return_value=32*1024**3),mock.patch.object(M,'public_json',return_value=({'cardData':{'license':'other'}},'c')):
            self.assertFalse(M.discover(cfg)['candidates'][0]['eligible'])

    def test_read_only_snapshot_never_calls_network_or_probe(self):
        with mock.patch.object(B,'api',side_effect=AssertionError('Network forbidden')),mock.patch.object(M,'discover',side_effect=AssertionError('Discovery forbidden')):
            self.assertEqual(M.snapshot()['active']['model'],self.cfg['baseline']['model'])

    def test_dispatch_refuses_model_drift(self):
        M.save({'active':self.inc})
        with mock.patch.object(M,'local_descriptor',return_value=self.new):
            with self.assertRaisesRegex(ValueError,'changed'):M.active(verify=True)

    def test_durable_selected_state_and_rollback_keep_evidence(self):
        M.save({'active':self.new,'previous':self.inc,'history':[]})
        M.rollback('test_regression');state=M.load()
        self.assertEqual(state['active'],self.inc)
        self.assertEqual(state['history'][-1]['rejected'],'fixture:new')
        self.assertTrue((self.root/'events.jsonl').exists())

    def test_step_resumes_one_probe_and_promotes_only_after_last_paired_result(self):
        candidate={'model':'candidate:tag','source_digest':'d'*64}
        results=self.results()[:-1]
        pending={'id':'comparison','key':'key','suite':M.SUITE,'incumbent':self.inc,'challenger':self.new,'source':candidate,'results':results}
        M.save({'active':self.inc,'history':[],'evaluated':{},'comparison':pending,'discovery':{'checked_at':M.now().isoformat(),'candidates':[]}})
        with (self.root/'gpu.lock').open('a'):pass
        with mock.patch.object(B,'GPU_LOCK',self.root/'gpu.lock'),mock.patch.object(M,'local_descriptor',return_value=self.new),mock.patch.object(M,'ram',return_value=32*1024**3),mock.patch.object(B,'api'),mock.patch.object(B,'probe',return_value=self.results()[-1]) as probe:
            value=M.step()
        self.assertEqual(probe.call_count,1);self.assertEqual(M.load()['active'],self.new)
        self.assertEqual(M.load()['previous'],self.inc)

    def test_changed_suite_abandons_partial_comparison_without_promoting(self):
        M.save({'active':self.inc,'history':[],'evaluated':{},'comparison':{'suite':'stale','incumbent':self.inc},'discovery':{'checked_at':M.now().isoformat(),'candidates':[]}})
        with mock.patch.object(B,'GPU_LOCK',self.root/'gpu.lock'),mock.patch.object(B,'probe') as probe:
            self.assertEqual(M.step()['status'],'restarting_comparison')
        probe.assert_not_called();self.assertEqual(M.load()['active'],self.inc)

    def test_untracked_release_is_reported_for_compatibility_assessment(self):
        cfg=copy.deepcopy(self.cfg);cfg['candidates']=[];cfg['publishers']=['Qwen']
        with mock.patch.object(M,'ram',return_value=32*1024**3),mock.patch.object(M,'public_json',return_value=([{'id':'Qwen/Future-Coder','pipeline_tag':'text-generation'}],'d')):
            result=M.discover(cfg)
        self.assertEqual(result['publisher_releases'][0]['status'],'requires_compatibility_assessment')

    def test_worker_keeps_local_provider_and_uses_explicit_selected_model(self):
        command=B.worker_command('fixture:new',Path('/tmp/catalog.json'),['-a','never','exec','task'])
        self.assertEqual(command[1:3],['-m','fixture:new'])
        self.assertNotIn('--profile',command)

    def test_cleanup_rejects_user_projects(self):
        with self.assertRaises(ValueError):B.cleanup(Path.home()/'Developer/real-projects/exampleapp',self.root)

    def test_gpu_busy_never_probes(self):
        import fcntl
        M.save({'active':self.inc,'history':[],'discovery':{'checked_at':M.now().isoformat(),'candidates':[]}})
        lock=self.root/'gpu.lock'
        with lock.open('a') as handle, mock.patch.object(B,'GPU_LOCK',lock),mock.patch.object(B,'probe') as probe:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(M.step()['status'],'gpu_busy')
        probe.assert_not_called()

    def test_critical_state_recovers_after_mirror_corruption(self):
        M.save({'active':self.inc});(self.root/'model-state.json').write_text('{broken')
        self.assertEqual(M.load()['active'],self.inc)

    def test_alias_identity_changes_with_context_or_sampling(self):
        candidate={'model':'fixture:tag','source_digest':'d'*64,'temperature':1.0}
        cfg={'context':32768};first=M.alias_name(candidate,cfg)
        candidate['temperature']=.2;self.assertNotEqual(first,M.alias_name(candidate,cfg))
        candidate['temperature']=1.0;cfg['context']=16384;self.assertNotEqual(first,M.alias_name(candidate,cfg))

    def test_qualifier_exposes_only_three_bounded_tools(self):
        self.assertEqual([x['function']['name'] for x in B.TOOLS],['read_file','write_file','run_tests'])

    def test_real_confined_oracle_accepts_correct_code_and_rejects_wrong_code(self):
        repo=self.root/'work';repo.mkdir();folder=self.root/'oracle';folder.mkdir()
        (repo/'solution.py').write_text("def redact(v):\n if isinstance(v,dict):return {k:('[REDACTED]' if k.lower() in ('password','token','authorization','api_key') else redact(x)) for k,x in v.items()}\n if isinstance(v,list):return [redact(x) for x in v]\n return v\n")
        self.assertTrue(B.oracle(folder,repo,'redact-nested',None))
        (repo/'solution.py').write_text('def redact(v):return v\n')
        with self.assertRaises(AssertionError):B.oracle(folder,repo,'redact-nested',None)

    def test_ordinary_set_methods_and_cache_storage_choices_are_not_false_failures(self):
        repo=self.root/'work';repo.mkdir();folder=self.root/'oracle';folder.mkdir()
        source="import math\nclass Cache:\n def __init__(self,clock):self.time_source=clock;self.entries={}\n def put(self,key,value,ttl):\n  if type(ttl) not in (int,float) or not math.isfinite(ttl) or ttl<0:raise ValueError('ttl')\n  self.entries[key]=(value,self.time_source()+ttl)\n def get(self,key,default=None):\n  if key not in self.entries:return default\n  value,expiry=self.entries[key]\n  if self.time_source()>=expiry:\n   del self.entries[key]\n   return default\n  return value\n"
        (repo/'solution.py').write_text(source)
        self.assertTrue(B.oracle(folder,repo,'expiry-boundary',None))
        (repo/'solution.py').write_text('def validate(v):\n seen=set()\n seen.add(1)\n return v\n')
        self.assertIn('seen.add',B.constrained_source(repo/'solution.py'))

    def test_discovery_cancellation_is_not_swallowed(self):
        with mock.patch.object(M,'ram',return_value=32*1024**3),mock.patch.object(M,'public_json',side_effect=InterruptedError('cancelled')):
            with self.assertRaises(InterruptedError):M.discover(self.cfg)

    def test_acquisition_checks_controller_cancellation_during_stream(self):
        response=mock.MagicMock();response.readline.return_value=b'{"status":"downloading"}\n'
        opener=mock.MagicMock();opener.__enter__.return_value=response
        candidate={'model':'fixture:tag','bytes':1}
        with mock.patch.object(M.urllib.request,'urlopen',return_value=opener),mock.patch.object(M,'CONTROL_CHECK',side_effect=[None,InterruptedError('controller stopped')]):
            with self.assertRaises(InterruptedError):M.install(candidate,self.cfg)

    def test_acquisition_deadline_interrupts_reads_without_progress(self):
        import time
        start=time.monotonic()
        with mock.patch.object(M,'CONTROL_CHECK'):
            with self.assertRaises(TimeoutError):
                with M.acquisition_window(.05):time.sleep(1)
        self.assertLess(time.monotonic()-start,.5)

    def test_syntax_error_is_a_failed_probe_not_an_endless_manager_retry(self):
        from model_cases import CASES
        tests='import unittest\nclass Tests(unittest.TestCase):\n def test_ok(self):self.assertTrue(True)\n'
        calls=[{'function':{'name':'write_file','arguments':{'path':'solution.py','content':'def broken('}}},{'function':{'name':'write_file','arguments':{'path':'test_solution.py','content':tests}}}]
        replies=[{'message':{'role':'assistant','tool_calls':calls}},{'message':{'role':'assistant','content':'Done'}}]
        with mock.patch.object(B,'sample',return_value={'pressure':1,'swap_mib':0,'model_bytes':0}),mock.patch.object(B,'chat',side_effect=replies):
            result=B.probe(self.root/'runs'/'syntax',self.inc,CASES[0],30,0,32*1024**3,lease_path=self.root/'model-probe.json')
        self.assertFalse(result['passed']);self.assertIn('SyntaxError',result['host_error'])

    def test_generated_code_cannot_intercept_the_trusted_rpc_bridge(self):
        path=self.root/'solution.py'
        path.write_text("import sys,json\ndef validate(x):return None\nfor line in sys.stdin:\n req=json.loads(line)\n print(json.dumps({'id':req['id'],'result':{'returned':req.get('value'),'original':req.get('value')}}))\n")
        with self.assertRaisesRegex(ValueError,'qualification_import_subset'):B.constrained_source(path)

    def test_task_selection_blocks_rollback_until_the_task_finishes(self):
        import threading
        M.save({'active':self.new,'previous':self.inc,'history':[]})
        started=threading.Event();finished=threading.Event()
        def change():started.set();M.rollback('test');finished.set()
        with mock.patch.object(M,'dispatch_descriptor',return_value=self.new),mock.patch.object(B,'recover_probe'):
            with M.task_selection():
                thread=threading.Thread(target=change);thread.start();self.assertTrue(started.wait(1))
                self.assertFalse(finished.wait(.05));self.assertEqual(M.load()['active'],self.new)
        thread.join(timeout=3);self.assertTrue(finished.is_set());self.assertEqual(M.load()['active'],self.inc)

    def test_self_comparison_cannot_destroy_the_previous_model(self):
        candidate={'model':'candidate:tag','source_digest':'d'*64,'eligible':True}
        M.save({'active':self.inc,'previous':self.new,'history':[],'evaluated':{},'discovery':{'checked_at':M.now().isoformat(),'candidates':[candidate]}})
        with mock.patch.object(B,'GPU_LOCK',self.root/'gpu.lock'),mock.patch.object(M,'install',return_value=self.inc):
            self.assertEqual(M.step()['status'],'same_configuration_skipped')
        self.assertEqual(M.load()['previous'],self.new)

    def test_same_manifest_with_different_alias_and_parameter_presentation_is_skipped(self):
        alias=dict(self.inc,model='alias:new',parameters_sha256='f'*64)
        self.assertTrue(M.same_configuration(alias,self.inc))
        self.assertFalse(M.same_configuration(dict(alias,context=16384),self.inc))
        candidate={'model':'candidate:tag','source_digest':'d'*64,'eligible':True}
        M.save({'active':self.inc,'previous':self.new,'history':[],'evaluated':{},'discovery':{'checked_at':M.now().isoformat(),'candidates':[candidate]}})
        with mock.patch.object(B,'GPU_LOCK',self.root/'gpu.lock'),mock.patch.object(M,'install',return_value=alias):
            self.assertEqual(M.step()['status'],'same_configuration_skipped')
        self.assertEqual(M.load()['previous'],self.new)

    def test_direct_manager_entry_without_controller_lease_cannot_run(self):
        import sys
        with mock.patch.object(sys,'argv',['model_manager.py']),mock.patch.object(M,'step') as step:
            self.assertEqual(M.main(),1)
        step.assert_not_called()

    def test_long_tool_list_does_not_extend_the_probe_deadline(self):
        import time
        from model_cases import CASES
        def tests(*args):time.sleep(.05);return {'code':0,'reason':'exited','stdout':'','stderr':''}
        calls=[{'function':{'name':'run_tests','arguments':{}}} for _ in range(100)]
        with mock.patch.object(B,'sample',return_value={'pressure':1,'swap_mib':0,'model_bytes':0}),mock.patch.object(B,'chat',return_value={'message':{'role':'assistant','tool_calls':calls}}),mock.patch.object(B,'unit_tests',side_effect=tests) as unit:
            start=time.monotonic();result=B.probe(self.root/'runs'/'deadline',self.inc,CASES[0],.2,0,32*1024**3,lease_path=self.root/'model-probe.json')
        self.assertLess(time.monotonic()-start,1);self.assertLess(unit.call_count,10);self.assertEqual(result['stop_reason'],'deadline')

    def test_qualifier_writes_host_result_from_tool_edits_without_live_model(self):
        from model_cases import CASES
        code="def redact(v):\n if isinstance(v,dict):return {k:('[REDACTED]' if k.lower() in ('password','token','authorization','api_key') else redact(x)) for k,x in v.items()}\n if isinstance(v,list):return [redact(x) for x in v]\n return v\n"
        test='import unittest,solution\nclass Tests(unittest.TestCase):\n def test_case(self):self.assertEqual(solution.redact({"token":"secret"}),{"token":"[REDACTED]"})\n'
        calls=[{'function':{'name':'write_file','arguments':{'path':'solution.py','content':code}}},{'function':{'name':'write_file','arguments':{'path':'test_solution.py','content':test}}}]
        replies=[{'message':{'role':'assistant','tool_calls':calls}},{'message':{'role':'assistant','content':'Done.'}}]
        folder=self.root/'runs'/'complete'
        with mock.patch.object(B,'sample',return_value={'pressure':1,'swap_mib':0,'model_bytes':0}),mock.patch.object(B,'chat',side_effect=replies):
            result=B.probe(folder,self.inc,CASES[2],30,0,32*1024**3,lease_path=self.root/'model-probe.json')
        self.assertTrue(result['passed'],result)
        self.assertEqual(result['tool_calls'],2)


    def test_candidate_acquisition_failure_does_not_block_next_candidate(self):
        candidates=[{'model':'bad:tag','source_digest':'d'*64,'eligible':True},
                    {'model':'good:tag','source_digest':'e'*64,'eligible':True}]
        M.save({'active':self.inc,'history':[],'evaluated':{},'discovery':{'checked_at':M.now().isoformat(),'candidates':candidates}})
        with mock.patch.object(B,'GPU_LOCK',self.root/'gpu.lock'),mock.patch.object(M,'install',side_effect=[ValueError('incompatible'),self.new]) as install,mock.patch.object(M,'active',return_value=self.inc):
            self.assertEqual(M.step()['status'],'candidate_acquisition_held')
            self.assertEqual(M.step()['status'],'acquired')
        self.assertEqual(install.call_args_list[1].args[0]['model'],'good:tag')

    def test_canary_uses_global_chronology_and_never_replays_seen_receipts(self):
        M.save({'active':self.new,'previous':self.inc,'history':[],'canary':{'observed_runs':[],'failures':0,'generation':'generation','started_at':'2026-10-31T09:00:00+00:00'}})
        projects=[]
        # Iteration order is fail, fail, success, fail. Actual order is success then 3 fails.
        for project,number,failed in [('one',2,True),('one',3,True),('two',1,False),('two',4,True)]:
            folder=self.root/project/'runs'/str(number);folder.mkdir(parents=True)
            (folder/'worker-model.json').write_text(json.dumps(dict(self.new,promotion_generation='generation')))
            (folder/'worker-completion.json').write_text(json.dumps({'response_observed':True,'finished':True}))
            (folder/'outcome.json').write_text(json.dumps({'at':f'2026-10-31T10:00:0{number}+00:00'}))
            (folder/'worker-diagnostic.json').write_text(json.dumps({'unfinished_tool_call':failed}))
        projects=[{'id':x,'state_dir':str(self.root/x)} for x in ['one','two']]
        M.observe(projects);self.assertEqual(M.load()['active'],self.inc)
        self.assertEqual(M.load()['history'][-1]['kind'],'rolled_back')

    def test_recovery_terminates_exact_leased_probe_after_manager_loss(self):
        import subprocess,time
        repo=self.root/'runs'/'owned'/'work';repo.mkdir(parents=True)
        proc=subprocess.Popen(['/bin/sleep','30'],cwd=repo,start_new_session=True)
        try:
            M.D.save_json(self.root/'model-probe.json',{'pid':proc.pid,'stamp':B.stamp(proc.pid),'boot_id':M.D.boot_id(),'repo':str(repo)})
            B.recover_probe(self.root);proc.wait(timeout=5)
            self.assertNotEqual(proc.returncode,0)
            self.assertEqual(M.D.read_json(self.root/'model-probe.json'),{})
        finally:
            if proc.poll() is None:proc.kill();proc.wait()

    def test_host_checks_bound_time_and_output(self):
        repo=self.root/'work';repo.mkdir();runner=self.root/'bounded.py'
        runner.write_text(B.LIMITS+"while True:print('x'*8000,flush=True)\n")
        result=B.host_check(['/opt/homebrew/bin/python3','-B',str(runner)],repo,B.profile(self.root,repo,runner))
        self.assertIn('host_output_bound',result['reason'])
        runner.write_text('import time;time.sleep(30)')
        result=B.host_check(['/opt/homebrew/bin/python3','-B',str(runner)],repo,B.profile(self.root,repo,runner),seconds=.1)
        self.assertEqual(result['reason'],'host_deadline')


    def test_early_exit_or_forged_completion_cannot_pass_host_oracle(self):
        repo=self.root/'work';repo.mkdir();folder=self.root/'case';folder.mkdir()
        for code in ["raise SystemExit(0)\n", "from pathlib import Path\nimport sys\nprint('MIDNIGHT_ASSERTIONS_COMPLETE_forged')\nraise SystemExit(0)\n"]:
            (repo/'solution.py').write_text(code)
            with self.assertRaises((ValueError,BrokenPipeError)):
                B.oracle(folder,repo,'strict-record',None)


    def test_host_sandbox_blocks_fork_and_private_reads(self):
        repo=self.root/'work';repo.mkdir();runner=self.root/'sandbox-check.py'
        runner.write_text("import subprocess\ntry:subprocess.Popen(['/bin/sleep','30'],cwd='/')\nexcept PermissionError:print('FORK_DENIED')\nelse:raise AssertionError('fork allowed')\n")
        result=B.host_check(['/opt/homebrew/bin/python3','-B',str(runner)],repo,B.profile(self.root,repo,runner))
        self.assertEqual(result['code'],0,result['stderr']);self.assertIn('FORK_DENIED',result['stdout'])


if __name__=='__main__':unittest.main()
