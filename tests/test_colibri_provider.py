import datetime as dt
import errno
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import colibri_provider as G
import model_manager as M
import durability as D


class Provider(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.directory=self.root/'runtime/models/colibri'
        self.addCleanup(patch.stopall)
        patch.object(G,'ROOT',self.root).start();patch.object(G,'DIRECTORY',self.directory).start()
        engine=self.root/'engine';engine.mkdir();model=self.root/'model';model.mkdir()
        files=[engine/name for name in ('coli','colibri','openai_server.py')]
        files += [self.root/name for name in ('colibri_provider.py','local_worker.py','structured_worker.py','local_supervisor.py','model_manager.py','worker_quality.py','project_backend.py')]
        for path in files:path.write_text('fixed synthetic test source\n')
        data=model/'one.safetensors';data.write_bytes(b'x');status=data.stat()
        manifest=self.root/'manifest.json';manifest.write_text(json.dumps({'revision':'fixed','files':[{'path':data.name,'size':1}]}));files.append(manifest)
        acquisition=self.root/'acquisition.json';acquisition.write_text(json.dumps({'phase':'complete','manifest_sha256':G.digest(manifest),'revision':'fixed','verified_files':1}))
        self.value={'version':1,'context':32768,'worker_timeout_seconds':21600,'supervisor_timeout_seconds':7200,'worker_stall_seconds':1800,
            'weights_bytes':1,'charged_limit_bytes':22000000000,'revision':'fixed','model':str(model),'engine_cli':str(engine/'coli'),
            'manifest':str(manifest),'acquisition':str(acquisition),'source_sha256':{str(path):G.digest(path) for path in files},
            'file_metadata':{data.name:{'size':1,'mtime_ns':status.st_mtime_ns,'inode':status.st_ino}},
            'environment':{'DIRECT':'1','CAP_RAISE':'0','PIN_GB':'0','KVB_FLASH_MB':'128','KVB_TILE_MB':'64','COLI_RAM_OVERCOMMIT':'1','RSS_GUARD_GB':'20'}}
        G.configure(self.value)

    def enable(self):return G.enable(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=1))

    def test_trial_selects_exact_model_and_preserves_incumbent(self):
        baseline={'model':'qwen','context':32768}
        with patch.object(M,'load',return_value={'active':baseline}),patch.object(M,'config',return_value={'baseline':baseline}):
            self.assertEqual(M.active(),baseline)
            self.enable();selected=M.active()
            self.assertEqual(selected['model'],G.MODEL)
            self.assertEqual(selected['bytes'],1)
            G.disable('test')
            self.assertEqual(M.active(),baseline)
            self.assertEqual(G.descriptor(verify=True),selected)

    def test_expiry_and_profile_edit_do_not_silently_activate(self):
        with self.assertRaises(ValueError):G.enable(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=25))
        trial=self.enable();trial['expires_at']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=1)).isoformat()
        D.save_json(self.directory/'trial.json',trial)
        self.assertIsNone(G.active())
        self.enable();changed=dict(self.value,worker_timeout_seconds=12000)
        with self.assertRaises(ValueError):G.configure(changed)

    def test_disabled_trial_profile_cannot_replace_pending_or_running_actions(self):
        self.enable();G.disable();changed=dict(self.value,worker_timeout_seconds=12000)
        with patch('action_store.Actions') as store:
            store.return_value.jobs.return_value=[{'payload':{'worker_model':G.descriptor()}}]
            with self.assertRaises(ValueError):G.configure(changed)
        self.assertEqual(G.config(),self.value)

    def test_startup_keeps_verified_launch_profile_across_ollama_drain(self):
        import model_bench as B
        from unittest.mock import Mock
        descriptor=G.descriptor(verify=True);native=G.NativeSession(descriptor,time.time()+60)
        replacement=self.root/'replacement-engine';replacement.mkdir()
        changed=dict(self.value,engine_cli=str(replacement/'coli'),source_sha256=dict(self.value['source_sha256']))
        for name in ('coli','colibri','openai_server.py'):
            path=replacement/name;path.write_text('different synthetic native engine\n');changed['source_sha256'][str(path)]=G.digest(path)
        def api(path,*args,**kwargs):
            if path=='/api/generate':G.configure(changed)
            return {'models':[{'name':'cached'}]} if path=='/api/ps' and G.config()==self.value else {'models':[]}
        with patch.object(B,'api',side_effect=api),patch.object(G.subprocess,'Popen',return_value=Mock(pid=123)) as launch,patch.object(G.P,'lease',return_value={'pid':123}),patch.object(native,'track'),patch.object(native,'wait_ready'),patch.object(G.threading,'Thread'):
            native.start(time.monotonic()+60)
        self.assertEqual(G.config()['engine_cli'],changed['engine_cli'])
        self.assertIn(self.value['engine_cli'],launch.call_args.args[0])
        self.assertNotIn(changed['engine_cli'],launch.call_args.args[0])
        self.assertEqual(native.profile,self.value)

    def test_changed_source_and_checkpoint_are_rejected(self):
        path=Path(self.value['engine_cli']).parent/'openai_server.py';path.write_text('changed')
        with self.assertRaises(ValueError):G.descriptor(verify=True)
        path.write_text('fixed synthetic test source\n')
        (Path(self.value['model'])/'one.safetensors').write_bytes(b'y')
        with self.assertRaises(ValueError):G.descriptor(verify=True)

    def version_two(self):
        value=dict(self.value,version=2,charged_limit_bytes=26_000_000_000,
            environment=dict(self.value['environment'],COLI_RAM_OVERCOMMIT='0',RSS_GUARD_GB='24',COLI_PREFILL_CHUNK='1024'),
            source_sha256=dict(self.value['source_sha256']))
        for name in ('memory_admission.py','memory_runtime.py','telemetry.py','colibri_observation.py','memory_coordination.py','memory_recovery.py',
                     'backup_coordination.py','phase_runtime.py','phase_guard.py','project_action.py','commissioning.py','schedule_control.py'):
            path=self.root/name;path.write_text('fixed memory envelope fixture\n');value['source_sha256'][str(path)]=G.digest(path)
        return value

    def test_version_two_envelope_is_exact_and_old_profiles_remain_valid(self):
        value=self.version_two()
        self.assertEqual(G.validate_config(self.value,verify=True),self.value)
        self.assertEqual(G.validate_config(value,verify=True),value)
        for change in ({'charged_limit_bytes':28_000_000_000},{'environment':dict(value['environment'],RSS_GUARD_GB='26')},
                       {'environment':dict(value['environment'],COLI_RAM_OVERCOMMIT='1')},
                       {'environment':dict(value['environment'],COLI_PREFILL_CHUNK='512')}):
            with self.subTest(change=change),self.assertRaises(ValueError):G.validate_config(dict(value,**change))
        incomplete=dict(value,source_sha256=dict(self.value['source_sha256']))
        with self.assertRaises(ValueError):G.validate_config(incomplete,verify=True)
        missing_runtime=dict(value,source_sha256=dict(value['source_sha256']))
        del missing_runtime['source_sha256'][str(self.root/'memory_runtime.py')]
        with self.assertRaises(ValueError):G.validate_config(missing_runtime,verify=True)

    def test_version_two_launch_uses_24gb_native_and_preserves_routing(self):
        import model_bench as B
        import memory_runtime as MR
        from unittest.mock import Mock
        G.configure(self.version_two());native=G.NativeSession(G.descriptor(),time.time()+60)
        with patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':'fixture-job','MIDNIGHT_RESOURCE_INPUT_KEY':'fixture-key'}):native.begin_request('a'*32,{})
        with patch.object(MR,'validate_provider_start',return_value={'phase':'owned-fixture'}) as admission,patch.object(B,'api',return_value={'models':[]}),patch.object(G.subprocess,'Popen',return_value=Mock(pid=123)) as launch,patch.object(G.P,'lease',return_value={'pid':123}),patch.object(native,'track'),patch.object(native,'wait_ready'),patch.object(G.threading,'Thread'):
            native.start(time.monotonic()+60)
        self.assertEqual(admission.call_count,2)
        self.assertEqual(json.loads((native.directory/'admission.json').read_text()),{'phase':'owned-fixture'})
        argv=launch.call_args.args[0];env=launch.call_args.kwargs['env']
        self.assertEqual(argv[argv.index('--ram')+1],'24');self.assertEqual(argv[argv.index('--cap')+1],'1')
        self.assertEqual(argv[argv.index('--ctx')+1],'32768')
        self.assertEqual({k:env[k] for k in ('COLI_PREFILL_CHUNK','COLI_RAM_OVERCOMMIT','RSS_GUARD_GB','CAP_RAISE','PIN_GB')},
            {'COLI_PREFILL_CHUNK':'1024','COLI_RAM_OVERCOMMIT':'0','RSS_GUARD_GB':'24','CAP_RAISE':'0','PIN_GB':'0'})

    def test_version_two_unmanaged_start_is_denied_before_any_native_effect(self):
        import model_bench as B
        from unittest.mock import Mock
        G.configure(self.version_two());native=G.NativeSession(G.descriptor(),time.time()+60)
        with patch.dict(os.environ,{},clear=True),patch.object(B,'api',return_value={'models':[]}) as api,patch.object(G.subprocess,'Popen',return_value=Mock(pid=123)) as launch,patch.object(G.P,'lease',return_value={'pid':123}),patch.object(native,'track'),patch.object(native,'wait_ready'),patch.object(G.threading,'Thread'):
            with self.assertRaises(ValueError):native.start(time.monotonic()+60)
        api.assert_not_called();launch.assert_not_called()

    def test_version_two_rechecks_admission_after_cache_drain_before_native_launch(self):
        import model_bench as B
        import memory_runtime as MR
        G.configure(self.version_two());native=G.NativeSession(G.descriptor(),time.time()+60)
        native.begin_request('a'*32,{})
        native.request['input_key']='fixture-key'
        with patch.object(MR,'validate_provider_start',side_effect=[{'phase':'owned'},ValueError('guard lost')]) as admission,patch.object(B,'api',return_value={'models':[]}),patch.object(G.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(ValueError,'guard lost'):native.start(time.monotonic()+60)
        self.assertEqual(admission.call_args.args,(self.root,native.model,'fixture-key'))
        launch.assert_not_called()

    def test_long_manifest_requires_the_exact_host_profile(self):
        self.assertEqual(G.manifest_timeout_ceiling({}),1800)
        descriptor=G.descriptor(verify=True)
        self.assertEqual(G.manifest_timeout_ceiling({'model_profile_sha256':descriptor['parameters_sha256']},descriptor),21600)
        self.assertEqual(G.manifest_timeout_ceiling({'model_profile_sha256':descriptor['parameters_sha256']},{'model':'qwen'}),1800)
        with self.assertRaises(ValueError):G.manifest_timeout_ceiling({'model_profile_sha256':'0'*64},descriptor)

    def test_phase_ceilings_remain_inside_the_maximum_owner_trial_window(self):
        for key,value in [('worker_timeout_seconds',86401),('supervisor_timeout_seconds',86401),('worker_stall_seconds',1801)]:
            with self.subTest(key=key),self.assertRaises(ValueError):G.validate_config(dict(self.value,**{key:value}))

    def test_long_native_prefill_can_use_the_remaining_owner_trial_window(self):
        accepted=True
        try:G.validate_config(dict(self.value,worker_timeout_seconds=86400,supervisor_timeout_seconds=86400))
        except ValueError:accepted=False
        self.assertTrue(accepted,'A valid full-window profile must not inherit the short experimental phase caps')

    def test_only_the_pinned_glm_worker_gets_the_prefill_stall_allowance(self):
        import project_backend as B
        descriptor=G.descriptor(verify=True)
        self.assertEqual(B.worker_stall_limit(None),240)
        self.assertEqual(B.worker_stall_limit({'MIDNIGHT_LOCAL_WORKER_MANIFEST':'fixed','MIDNIGHT_WORKER_DESCRIPTOR':json.dumps({'model':'qwen'})}),240)
        self.assertEqual(B.worker_stall_limit({'MIDNIGHT_LOCAL_WORKER_MANIFEST':'fixed','MIDNIGHT_WORKER_DESCRIPTOR':json.dumps(descriptor)}),1800)

    def test_project_action_budget_fits_glm_phases_but_preserves_expiry_and_qwen(self):
        import midnight as C
        from contextlib import nullcontext
        instant=dt.datetime.now(dt.timezone.utc);window=instant+dt.timedelta(hours=12)
        descriptor=G.descriptor(verify=True)
        with patch.object(C,'now',return_value=instant),patch.object(C,'project_brief',return_value={}),patch.object(C.PA,'launch',side_effect=lambda *args:args[-1]):
            for model,seconds in [(descriptor,28800),({'model':'qwen'},5400)]:
                with self.subTest(model=model['model']),patch.object(C.MM,'task_selection',return_value=nullcontext(model)),patch.object(G,'trial_status',return_value={'expires_at':window.isoformat()}):
                    self.assertEqual(C.submit_project({},window,{'id':'fixed'}),instant+dt.timedelta(seconds=seconds))
            short=instant+dt.timedelta(minutes=5)
            for expiry,cutoff in [(short,window),(window,short)]:
                with patch.object(C.MM,'task_selection',return_value=nullcontext(descriptor)),patch.object(G,'trial_status',return_value={'expires_at':expiry.isoformat()}):
                    self.assertEqual(C.submit_project({},cutoff,{'id':'fixed'}),short)

    def test_pinned_owned_action_survives_end_trial_but_new_work_does_not(self):
        from unittest.mock import Mock
        descriptor=G.descriptor(verify=True);self.enable();G.disable()
        with self.assertRaises(ValueError):G.authorize(descriptor)
        job={'state':'running','payload':{'worker_model':dict(descriptor,promotion_generation=None)},'lease':{'pid':2},'deadline':time.time()+60}
        with patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':'fixed'}),patch('action_store.Actions') as store,patch.object(G.P,'alive',return_value=True),patch.object(G.P,'descendants',return_value=[{'pid':os.getpid()}]):
            store.return_value.get.return_value=job
            self.assertEqual(G.authorize(descriptor),job['deadline'])

    def test_native_teardown_precedes_shared_lock_release(self):
        lock=self.root/'gpu.lock';observed=[]
        class Native:
            error=None
            def __init__(self,model,authorization_epoch):pass
            def close(self):
                with lock.open('a') as second:
                    with self_case.assertRaises(BlockingIOError):fcntl.flock(second,fcntl.LOCK_EX|fcntl.LOCK_NB)
                observed.append('drained')
        self_case=self
        self.enable();descriptor=G.descriptor(verify=True)
        with patch.object(G,'NativeSession',Native):
            with lock.open('a') as owned,G.session(descriptor):
                fcntl.flock(owned,fcntl.LOCK_EX)
            self.assertEqual(observed,['drained'])
        with lock.open('a') as available:fcntl.flock(available,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_qwen_never_loads_a_native_session(self):
        with patch.object(G,'NativeSession') as native:
            with G.session({'model':'qwen'}):pass
            native.assert_not_called()

    def response(self,finish='stop',content='{"action":"finish"}',usage=True,tool=False,delay=0,keepalives=0,on_done=None):
        records=[{'choices':[{'delta':{'reasoning_content':'short reasoning'},'finish_reason':None}]},
            {'choices':[{'delta':{'tool_calls':[{}]} if tool else {'content':content},'finish_reason':None}]},
            {'choices':[{'delta':{},'finish_reason':finish}]}]
        if usage:records.append({'choices':[],'usage':{'prompt_tokens':100,'completion_tokens':12} if usage is True else usage})
        empty={'choices':[{'delta':{'reasoning_content':''},'finish_reason':None}]}
        lines=[('data: '+json.dumps(empty)+'\n').encode()]*keepalives+[('data: '+json.dumps(value)+'\n').encode() for value in records]+[b'data: [DONE]\n']
        class Response:
            status=200
            def readline(self,*args):
                if delay:time.sleep(delay)
                result=lines.pop(0) if lines else b''
                if result==b'data: [DONE]\n' and on_done:on_done()
                return result
        class Connection:
            def __init__(self,*args,**kwargs):pass
            def request(self,*args,**kwargs):pass
            def getresponse(self):return Response()
            def close(self):pass
        return Connection

    def infer(self,*,seconds=10,memory_age=0,**options):
        descriptor=G.descriptor(verify=True)
        native=G.NativeSession(descriptor,time.time()+60);native.directory=self.root;native.port=1;native.key='synthetic'
        native.profile=G.config();native.memory_observed_monotonic=None if memory_age is None else time.monotonic()-memory_age
        schema={'type':'object','additionalProperties':False,'properties':{'action':{'type':'string','enum':['finish']}},'required':['action']}
        with patch.object(G,'SESSION',native),patch.object(native,'start'),patch.object(native,'emit'),patch.object(G.http.client,'HTTPConnection',self.response(**options)):
            return G.infer(descriptor,[{'role':'user','content':'synthetic'}],time.monotonic()+seconds,schema=schema,predict=1024)

    def test_typed_stream_uses_only_final_answer_and_receipted_usage(self):
        value,metrics=self.infer()
        self.assertEqual(value,{'action':'finish'})
        self.assertEqual(metrics['prompt_eval_count'],100)

    def test_truncation_native_tools_and_missing_usage_never_complete(self):
        for options in ({'finish':'length'},{'usage':False},{'tool':True},{'content':'not JSON'}):
            with self.subTest(options=options),self.assertRaises(ValueError):self.infer(**options)

    def test_partial_and_malformed_usage_never_complete(self):
        for usage in ({'prompt_tokens':100},{'prompt_tokens':100,'completion_tokens':True},{'prompt_tokens':100,'completion_tokens':-1},{'prompt_tokens':100,'completion_tokens':1025},{'prompt_tokens':100,'completion_tokens':'12'},{'prompt_tokens':100,'completion_tokens':12,'total_tokens':100}):
            with self.subTest(usage=usage),self.assertRaises(ValueError):self.infer(usage=usage)

    def test_late_stream_never_returns_a_successful_decision(self):
        with self.assertRaises(TimeoutError):self.infer(seconds=.01,delay=.02)

    def test_many_empty_keepalives_do_not_exhaust_the_response_content_budget(self):
        value,_=self.infer(keepalives=16000)
        self.assertEqual(value,{'action':'finish'})
        self.assertGreater(next(self.root.glob('*-stream.jsonl')).stat().st_size,1024*1024)
        with self.assertRaises(ValueError):self.infer(content='x'*(1024*1024))

    def test_only_validated_empty_keepalives_are_exempt_from_budget(self):
        record={'choices':[{'delta':{'reasoning_content':''},'finish_reason':None}]}
        self.assertTrue(G.empty_keepalive(record))
        self.assertFalse(G.empty_keepalive(dict(record,private='x')))
        self.assertFalse(G.empty_keepalive({'choices':[{'delta':{'reasoning_content':'x'}}]}))
        self.assertFalse(G.empty_keepalive({'choices':[{'delta':{'tool_calls':[]}}]}))

    def test_memory_stop_racing_done_never_saves_success(self):
        def fail():G.SESSION.fail('charged_limit','synthetic charged stop')
        with self.assertRaisesRegex(RuntimeError,'charged stop'):self.infer(on_done=fail)
        self.assertFalse(list(self.root.glob('*-result.json')))
        self.assertEqual(json.loads((self.root/'failure-intent.json').read_text())['kind'],'charged_limit')

    def test_memory_stop_racing_validation_never_saves_success(self):
        import local_supervisor as L
        def validate(value,schema):G.SESSION.fail('host_pressure','synthetic pressure stop');return value
        with patch.object(L,'validate',side_effect=validate),self.assertRaisesRegex(RuntimeError,'pressure stop'):self.infer()
        self.assertFalse(list(self.root.glob('*-result.json')))

    def test_version_two_completion_requires_recent_memory_observation(self):
        G.configure(self.version_two())
        value,_=self.infer(memory_age=5)
        self.assertEqual(value,{'action':'finish'})
        for path in self.root.glob('*-result.json'):path.unlink()
        for age in (None,11,-60):
            with self.subTest(age=age):
                failure=self.root/'failure-intent.json'
                if failure.exists():failure.unlink()
                with self.assertRaisesRegex(RuntimeError,'fresh memory observation'):self.infer(memory_age=age)
                self.assertFalse(list(self.root.glob('*-result.json')))
                self.assertEqual(json.loads(failure.read_text())['kind'],'telemetry_unavailable')

    def test_version_two_done_during_first_observation_gap_never_completes(self):
        G.configure(self.version_two())
        class OneSample:
            def __init__(self):self.calls=0;self.stopped=False
            def wait(self,seconds):self.calls+=1;return self.stopped or self.calls>1
            def set(self):self.stopped=True
            def is_set(self):return self.stopped
        def gap():
            native=G.SESSION;native.stop=OneSample()
            native.last_memory={'at':time.time(),'charged_bytes':1,'pressure':1}
            with patch.object(native,'capture',side_effect=OSError('synthetic first telemetry gap')):
                native.watch()
        with self.assertRaisesRegex(RuntimeError,'memory observation unavailable'):self.infer(on_done=gap)
        self.assertFalse(list(self.root.glob('*-result.json')))
        self.assertEqual(json.loads((self.root/'failure-intent.json').read_text())['kind'],'telemetry_unavailable')

    def watched_samples(self,times,*,version=2):
        value=self.version_two() if version==2 else self.value
        G.configure(value);native=G.NativeSession(G.descriptor());native.begin_request('a'*32,{})
        native.profile=value;(native.directory/'native.log').write_text('')
        clock=[0];scheduled=iter(times)
        class Samples:
            stopped=False
            def wait(self,seconds):
                if self.stopped:return True
                try:clock[0]=next(scheduled);return False
                except StopIteration:return True
            def set(self):self.stopped=True
            def is_set(self):return self.stopped
        native.stop=Samples()
        with patch.object(G.time,'monotonic',side_effect=lambda:clock[0]),patch.object(native,'capture',return_value=[{'footprint':1}]) as capture,patch.object(G.subprocess,'check_output',return_value='1'):
            native.watch();clock[0]=times[-1]+1
            return native,capture.call_count

    def test_version_two_good_sample_after_gap_cannot_erase_lost_coverage(self):
        G.configure(self.version_two());clock=[0]
        class Samples:
            stopped=False;scheduled=iter([0,20])
            def wait(self,seconds):
                if self.stopped:return True
                try:clock[0]=next(self.scheduled);return False
                except StopIteration:return True
            def set(self):self.stopped=True
            def is_set(self):return self.stopped
        def gap_then_good():
            native=G.SESSION;native.stop=Samples();(native.directory/'native.log').write_text('')
            with patch.object(native,'capture',return_value=[{'footprint':1}]) as capture,patch.object(G.subprocess,'check_output',return_value='1'):
                native.watch();self.assertEqual(capture.call_count,2)
            clock[0]=21
        with patch.object(G.time,'monotonic',side_effect=lambda:clock[0]):
            with self.assertRaises(RuntimeError):self.infer(seconds=60,on_done=gap_then_good)
        self.assertFalse(list(self.root.glob('*-result.json')))
        self.assertEqual(json.loads((self.root/'failure-intent.json').read_text())['kind'],'telemetry_unavailable')

    def test_version_two_delayed_first_sample_or_reversed_clock_stays_failed(self):
        for times in ([20],[0,-1]):
            with self.subTest(times=times):
                native,samples=self.watched_samples(times)
                self.assertEqual(samples,len(times))
                with patch.object(G.time,'monotonic',return_value=times[-1]+1):
                    with self.assertRaises(RuntimeError):native.check_completion()
                self.assertEqual(native.failure['kind'],'telemetry_unavailable')

    def test_continuous_ten_second_boundary_and_v1_delayed_sample_remain_valid(self):
        for version,times in ((2,[0,10]),(1,[0,20])):
            with self.subTest(version=version):
                native,samples=self.watched_samples(times,version=version)
                self.assertEqual(samples,2)
                with patch.object(G.time,'monotonic',return_value=times[-1]+1):native.check_completion()
                self.assertIsNone(native.failure)

    def failed_native_creation(self,error):
        import io
        import model_bench as B
        import memory_runtime as MR
        G.configure(self.version_two());model=G.descriptor();native=G.NativeSession(model,time.time()+60)
        with patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':'first-job','MIDNIGHT_RESOURCE_INPUT_KEY':'same-input'}):
            native.begin_request('a'*32,{'private':'same-input'})
            proof={'phase':'owned-fixture','job':'first-job','input_key':'same-input','boot_id':D.boot_id(),
                   'model_sha256':G.P.fingerprint(model)}
            with patch.object(MR,'validate_provider_start',return_value=proof),patch.object(B,'api',return_value={'models':[]}),patch.object(G.subprocess,'Popen',side_effect=error) as launch:
                with self.assertRaises(type(error)):native.start(time.monotonic()+60)
                self.assertEqual(launch.call_count,1)
        with patch('sys.stdout',new_callable=io.StringIO) as output:
            native.close();native.close()
        return native,model,output.getvalue()

    def test_native_creation_oserror_blocks_a_new_job_before_second_charge(self):
        import memory_recovery as MEM
        charges=['first-job'];native,model,events=self.failed_native_creation(FileNotFoundError(errno.ENOENT,'synthetic exec failure','/usr/bin/sandbox-exec'))
        with patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':'different-job'}):
            with self.assertRaises(MEM.MemoryHeld) as held:
                MEM.check(self.root/'runtime','same-input',model,quiet=True)
                charges.append('different-job')
        self.assertEqual(charges,['first-job']);self.assertEqual(held.exception.record['kind'],'native_startup_failed')
        receipt=json.loads((native.directory/'failure.json').read_text())
        self.assertEqual(receipt['native_launch'],'creation_failed');self.assertIsNone(receipt['gateway'])
        self.assertEqual(receipt['action_id'],'first-job');self.assertEqual(receipt['input_key'],'same-input')
        self.assertEqual(receipt['model'],model);self.assertTrue(receipt['drain']['owned_processes_drained'])
        self.assertEqual(receipt['input_sha256'],G.hashlib.sha256(G.canonical({'private':'same-input'})).hexdigest())
        self.assertEqual(receipt['drain']['unloaded_sha256'],G.digest(native.directory/'unloaded.json'))
        self.assertEqual(len(events.splitlines()),1)
        self.assertEqual(json.loads(events)['receipt_sha256'],G.digest(native.directory/'failure.json'))
        import memory_coordination as MC
        self.assertFalse(MC.unresolved_native(self.root/'runtime'))

    def test_uncertain_native_creation_never_claims_drainage_or_recharges(self):
        import memory_recovery as MEM
        charges=['first-job'];native,model,events=self.failed_native_creation(RuntimeError('synthetic unresolved launch'))
        with patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':'different-job'}):
            with self.assertRaises(MEM.MemoryHeld) as held:
                MEM.check(self.root/'runtime','same-input',model,quiet=True)
                charges.append('different-job')
        self.assertEqual(charges,['first-job']);self.assertEqual(held.exception.record['kind'],'cleanup_unconfirmed')
        receipt=json.loads((native.directory/'failure.json').read_text())
        self.assertEqual(receipt['native_launch'],'unknown');self.assertFalse(receipt['drain']['owned_processes_drained'])
        self.assertFalse((native.directory/'unloaded.json').exists());self.assertEqual(len(events.splitlines()),1)
        before=(native.directory/'failure-intent.json').read_bytes();native.close()
        self.assertEqual((native.directory/'failure-intent.json').read_bytes(),before)

    def test_interrupted_or_unidentified_oserror_launch_never_proves_cleanup(self):
        import memory_coordination as MC
        for error in (InterruptedError('Stopped by SIGTERM'),OSError(errno.EIO,'unknown pipe failure')):
            with self.subTest(error=type(error).__name__):
                native,model,events=self.failed_native_creation(error)
                receipt=json.loads((native.directory/'failure.json').read_text())
                self.assertEqual(receipt['native_launch'],'unknown')
                self.assertFalse(receipt['drain']['owned_processes_drained'])
                self.assertFalse((native.directory/'unloaded.json').exists())
                self.assertTrue(MC.unresolved_native(self.root/'runtime'))

    def test_unknown_launch_blocks_different_input_heavy_admission_without_execution_row(self):
        import memory_coordination as MC
        import memory_recovery as MEM
        from action_store import Actions
        native,model,_=self.failed_native_creation(InterruptedError('Stopped by SIGTERM'))
        runtime=self.root/'runtime';D.save_json(runtime/'memory-coordination.json',{'version':1,'enabled':True})
        self.assertEqual(Actions(runtime).executions(),[])
        charges=['first-job'];waiting=[]
        with patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':'different-job','MIDNIGHT_RESOURCE_INPUT_KEY':'different-input'}):
            MEM.check(runtime,'different-input',model,quiet=True)
            with self.assertRaises(G.P.PhaseInterrupted):
                with MC.heavy(runtime,time.time()+30,waiting=waiting.append,stopped=lambda:bool(waiting),poll=0):
                    charges.append('different-job')
        self.assertEqual(charges,['first-job']);self.assertEqual(waiting,['resource_heavy_memory'])

    def test_final_admission_check_follows_intent_fsync_and_unattempted_cleanup_is_linked(self):
        import model_bench as B
        import memory_runtime as MR
        import memory_coordination as MC
        G.configure(self.version_two());model=G.descriptor();native=G.NativeSession(model,time.time()+60)
        with patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':'fixture-job','MIDNIGHT_RESOURCE_INPUT_KEY':'fixture-key'}):native.begin_request('a'*32,{})
        proof={'phase':'owned-fixture','job':'fixture-job','input_key':'fixture-key','boot_id':D.boot_id(),
               'model_sha256':G.P.fingerprint(model)}
        checks=[]
        def validate(*args):
            checks.append(True)
            if len(checks)==2:
                self.assertTrue((native.directory/'native-launch-intent.json').is_file())
                self.assertTrue((native.directory/'native.log').is_file())
                raise ValueError('proof expired during durable preparation')
            return proof
        with patch.object(MR,'validate_provider_start',side_effect=validate),patch.object(B,'api',return_value={'models':[]}),patch.object(G.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(ValueError,'proof expired'):native.start(time.monotonic()+60)
        launch.assert_not_called();self.assertTrue(MC.unresolved_native(self.root/'runtime'))
        native.close();unloaded=json.loads((native.directory/'unloaded.json').read_text())
        self.assertEqual(unloaded['native_launch'],'not_attempted')
        self.assertEqual(unloaded['native_launch_intent_sha256'],G.digest(native.directory/'native-launch-intent.json'))
        self.assertFalse(MC.unresolved_native(self.root/'runtime'))

    def test_chunk_progress_keeps_partial_lines_and_layer_resets_without_replay(self):
        native=G.NativeSession(G.descriptor());native.begin_request('a'*32,{'synthetic':'private'})
        log=native.directory/'native.log';log.write_bytes(b'[prefill] layer 78/78 \xc2\xb7 1024 token \xc2\xb7 +20.00s\n[prefill] layer 1/78 ')
        with patch.object(native,'emit') as emit:
            native.read_progress();self.assertEqual(emit.call_args.kwargs['last_logged_layer'],78)
            with log.open('ab') as handle:handle.write(b'\xc2\xb7 1024 token \xc2\xb7 +0.00s\n')
            native.read_progress();self.assertEqual(emit.call_args.kwargs['last_logged_layer'],1)
            self.assertEqual(emit.call_args.kwargs['layer_scope'],'current_chunk')
            self.assertEqual(emit.call_args.kwargs['request_id'],'a'*32)
            offset=emit.call_args.kwargs['log_offset'];native.read_progress();self.assertEqual(emit.call_count,2)
            self.assertEqual(offset,log.stat().st_size)
            native.begin_request('b'*32,{});native.read_progress();self.assertEqual(emit.call_count,2)
        log.write_text('truncated')
        with self.assertRaises(ValueError):native.read_progress()

    def test_first_failure_is_immutable_bound_to_input_and_finalised_only_after_drain(self):
        from types import SimpleNamespace
        import io
        native=G.NativeSession(G.descriptor());lease={'pid':123,'stamp':'birth','boot_id':'boot','birth_us':10}
        with patch.dict(os.environ,{'MIDNIGHT_RESOURCE_INPUT_KEY':'key','MIDNIGHT_ACTION_ID':'action'}):
            native.begin_request('a'*32,{'private':'input'})
        native.gateway_lease=lease;native.members={('fixture',):lease};native.last_memory={'charged_bytes':27_000_000_000}
        native.server=SimpleNamespace(wait=lambda **kwargs:0)
        native.fail('charged_limit','synthetic failure');intent=(native.directory/'failure-intent.json').read_bytes()
        self.assertFalse((native.directory/'failure.json').exists())
        native.fail('host_pressure','later failure');self.assertEqual((native.directory/'failure-intent.json').read_bytes(),intent)
        with patch.object(G.P,'alive',return_value=False),patch.object(G.P,'stop_tree'),patch('sys.stdout',new_callable=io.StringIO) as output:
            native.close();native.close()
        final=json.loads((native.directory/'failure.json').read_text())
        self.assertEqual(final['input_key'],'key');self.assertEqual(final['action_id'],'action')
        self.assertEqual(final['input_sha256'],G.hashlib.sha256(G.canonical({'private':'input'})).hexdigest())
        self.assertEqual(final['profile_sha256'],G.descriptor()['parameters_sha256'])
        self.assertTrue(final['drain']['owned_processes_drained'])
        self.assertEqual(final['gateway'],lease);self.assertEqual(final['kind'],'charged_limit')
        event=json.loads(output.getvalue());self.assertEqual(event['receipt_sha256'],G.digest(native.directory/'failure.json'))
        self.assertNotIn('private',output.getvalue())

    def test_memory_watcher_records_limit_or_missing_observation_and_stops_owned_tree(self):
        class Ticks:
            def __init__(self):self.count=0;self.stopped=False
            def wait(self,seconds):self.count+=1;return self.stopped or self.count>3
            def set(self):self.stopped=True
            def is_set(self):return self.stopped
        for unavailable in (False,True):
            with self.subTest(unavailable=unavailable):
                native=G.NativeSession(G.descriptor());native.begin_request('a'*32,{})
                native.profile=self.value;native.stop=Ticks();native.gateway_lease={'pid':123};(native.directory/'native.log').write_text('')
                capture={'side_effect':OSError('no observation')} if unavailable else {'return_value':[{'footprint':23_000_000_000}]}
                with patch.object(native,'capture',**capture),patch.object(G.subprocess,'check_output',return_value='1'),patch.object(G.P,'stop_tree') as stop:
                    native.watch()
                self.assertTrue(native.stop.is_set());stop.assert_called_once()
                self.assertEqual(native.failure['kind'],'telemetry_unavailable' if unavailable else 'charged_limit')

    def test_model_loading_obeys_absolute_deadline(self):
        from types import SimpleNamespace
        native=G.NativeSession(G.descriptor(verify=True),time.time()+60)
        native.server=SimpleNamespace(poll=lambda:None);native.port=1;native.key='synthetic'
        class Connection:
            def __init__(self,*a,**k):pass
            def request(self,*a,**k):pass
            def getresponse(self):return SimpleNamespace(status=503)
            def close(self):pass
        with patch.object(native,'track'),patch.object(G.http.client,'HTTPConnection',Connection):
            with self.assertRaises(TimeoutError):native.wait_ready(time.monotonic()+.02)


if __name__=='__main__':unittest.main()
