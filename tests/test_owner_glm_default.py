"""Explicit persistent GLM selection, with temporary state and no inference."""
from contextlib import ExitStack, nullcontext, redirect_stdout, redirect_stderr
import copy
import datetime as dt
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from action_store import Actions
import agent_discussion as A
import agent_workspace as W
import colibri_provider as G
import durability as D
import midnight as C
import model_manager as M
import model_observation as O


class OwnerDefaultTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.directory=self.root/'runtime/models/colibri'
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ,{},clear=False));os.environ.pop('MIDNIGHT_ACTION_ID',None)
        for module,name,value in [(G,'ROOT',self.root),(G,'DIRECTORY',self.directory),(M,'RUNTIME',self.root/'runtime/models'),
                                  (C,'STATE',self.root/'runtime'),(A,'ROOT',self.root),(A,'STOP',False)]:
            self.stack.enter_context(patch.object(module,name,value))
        engine=self.root/'engine';engine.mkdir();model=self.root/'model';model.mkdir()
        sources=[engine/name for name in ('coli','colibri','openai_server.py')]
        sources += [self.root/name for name in ('colibri_provider.py','local_worker.py','structured_worker.py',
                    'local_supervisor.py','model_manager.py','worker_quality.py','project_backend.py')]
        for path in sources:path.write_text('fixed synthetic source\n')
        data=model/'one.safetensors';data.write_bytes(b'x');stat=data.stat()
        manifest=self.root/'manifest.json';manifest.write_text(json.dumps({'revision':'fixed','files':[{'path':data.name,'size':1}]}))
        sources.append(manifest)
        acquisition=self.root/'acquisition.json';acquisition.write_text(json.dumps({'phase':'complete','revision':'fixed',
            'manifest_sha256':G.digest(manifest),'verified_files':1}))
        self.profile={'version':1,'context':32768,'worker_timeout_seconds':86400,'supervisor_timeout_seconds':86400,
            'worker_stall_seconds':1800,'weights_bytes':1,'charged_limit_bytes':22000000000,'revision':'fixed',
            'model':str(model),'engine_cli':str(engine/'coli'),'manifest':str(manifest),'acquisition':str(acquisition),
            'source_sha256':{str(p):G.digest(p) for p in sources},
            'file_metadata':{data.name:{'size':1,'mtime_ns':stat.st_mtime_ns,'inode':stat.st_ino}},
            'environment':{'DIRECT':'1','CAP_RAISE':'0','PIN_GB':'0','KVB_FLASH_MB':'128','KVB_TILE_MB':'64','COLI_RAM_OVERCOMMIT':'1','RSS_GUARD_GB':'20'}}
        G.configure(self.profile);self.model=G.descriptor(verify=True)
        self.incumbent={'model':'qwen:retained','digest':'a'*64,'context':32768,'bytes':1,'parameters_sha256':'b'*64}
        self.state={'active':self.incumbent,'history':[{'kind':'retained-evidence'}],
                    'comparison':{'id':'original-comparison','results':[]},'evaluated':{'original':{'passed':False}}}
        M.save(self.state);cfg=M.config();cfg['baseline']=self.incumbent
        self.stack.enter_context(patch.object(M,'config',return_value=cfg))
        for module,name in [(M.B,'api'),(M.B,'probe'),(M,'public_json'),(M,'discover')]:
            self.stack.enter_context(patch.object(module,name,side_effect=AssertionError('No model, download or benchmark allowed')))
        self.data={'timezone':'Australia/Adelaide','schedule':{'start':'00:00','end':'09:00'},'projects':[]}
        self.stack.enter_context(patch.object(C,'registry',return_value=self.data))

    def persistent_record(self):
        # This fixture is understood by existing read APIs, so the old source
        # fails on behavior rather than importing a newly introduced function.
        record={'enabled':True,'mode':'owner_default','expires_at':None,'started_at':'2026-01-01T00:00:00+00:00',
                'parameters_sha256':self.model['parameters_sha256'],'scope':'Explicit owner default; quality unproven'}
        D.save_json(self.directory/'trial.json',record)
        return record

    def cli(self,*arguments):
        with patch.object(sys,'argv',['midnight','models',*arguments]),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            try:return C.main()
            except SystemExit as exc:return exc.code

    def test_owner_default_remains_active_after_the_former_trial_window(self):
        self.persistent_record()
        status=G.trial_status()
        self.assertTrue(status.get('active'),status)
        self.assertIsNone(status.get('expires_at'))

    def test_only_explicit_owner_default_accepts_null_expiry(self):
        for mode,expiry in [(None,None),('trial',None),('unknown',None),('owner_default','2099-01-01T00:00:00+00:00')]:
            with self.subTest(mode=mode,expiry=expiry):
                record=self.persistent_record();record['expires_at']=expiry
                if mode is None:record.pop('mode')
                else:record['mode']=mode
                D.save_json(self.directory/'trial.json',record)
                self.assertFalse(G.trial_status().get('active'))
                with self.assertRaises(ValueError):G.active()

    def test_new_default_cli_is_explicit_and_keeps_continuous_override(self):
        override={'mode':'continuous','expires_at':None,'created_at':'original-owner-selection'}
        D.save_json(C.STATE/'override.json',override)
        self.assertEqual(self.cli('--default','glm'),0)
        self.assertEqual(G.trial_status()['mode'],'owner_default')
        self.assertEqual(D.read_json(C.STATE/'override.json',{}),override)
        self.assertEqual(M.load(),self.state)

    def test_persistent_status_is_not_a_trial_or_quality_certificate(self):
        self.persistent_record()
        view=M.snapshot()
        self.assertEqual(view.get('selection_kind'),'owner_default',view)
        self.assertEqual(view['active'],self.model);self.assertEqual(view['incumbent'],self.incumbent)
        self.assertIsNone(view['owner_trial']);self.assertIsNone(view['owner_selection']['expires_at'])
        self.assertIn('unproven',view['qualification_scope'])
        with patch.object(O.C,'snapshot',return_value={'state':'not_observed'}):
            self.assertEqual(O.snapshot()['display_phase'],'owner_default_selected')

    def test_prior_trial_and_default_are_archived_and_incumbent_is_preserved(self):
        trial=G.enable(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=5))
        selected=G.enable_default()
        snapshots=[D.read_json(p,{}) for p in (self.directory/'selections').glob('*.json')]
        self.assertIn(trial,snapshots)
        self.assertEqual(self.cli('--restore-incumbent'),0)
        self.assertEqual(M.active(),self.incumbent);self.assertEqual(M.load(),self.state)
        snapshots=[D.read_json(p,{}) for p in (self.directory/'selections').glob('*.json')]
        self.assertIn(selected,snapshots)
        self.assertFalse(G.trial_status()['active'])

    def test_default_never_resumes_a_saved_comparison_or_download(self):
        G.enable_default()
        with patch.object(M,'CONTROL_CHECK',side_effect=AssertionError('Maintenance must stop before its work')):
            result=M._step()
        self.assertEqual(result,{'status':'owner_colibri_default_active','benchmarks_suspended':True})
        self.assertEqual(M.load(),self.state)

    def test_finite_trials_still_expire_and_legacy_rollback_still_works(self):
        trial=G.enable(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=5))
        trial.pop('mode',None);trial['expires_at']='2026-01-01T00:00:00+00:00';D.save_json(self.directory/'trial.json',trial)
        self.assertFalse(G.trial_status()['active']);self.assertEqual(M.active(),self.incumbent)
        G.enable(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=5));G.disable()
        self.assertEqual(M.active(),self.incumbent)

    def test_default_does_not_bypass_source_attestation_or_profile_drift(self):
        G.enable_default()
        (self.root/'colibri_provider.py').write_text('changed synthetic source')
        with self.assertRaises(ValueError):G.active(verify=True)
        (self.root/'colibri_provider.py').write_text('fixed synthetic source\n')
        changed=copy.deepcopy(self.profile);changed['worker_timeout_seconds']=72000
        with self.assertRaises(ValueError):G.configure(changed)
        D.save_json(self.directory/'config.json',changed)
        with self.assertRaises(ValueError):G.active()

    def test_selection_and_rollback_do_not_renew_an_owned_job_or_charges(self):
        store=Actions(C.STATE);deadline=time.time()+120
        model=dict(self.model,promotion_generation=None)
        job=store.submit('one',{'worker_model':model,'session':'original'},deadline)
        lease={'pid':7};store.claim(job['id'],lease);store.charge(job['id'],'spent','local_supervision')
        before=store.get(job['id'])
        with patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':job['id']}),patch.object(G.P,'alive',return_value=True), \
             patch.object(G.P,'descendants',return_value=[{'pid':os.getpid()}]):
            for change in (G.enable_default,G.disable):
                change()
                self.assertEqual(G.authorize(self.model),deadline)
                self.assertEqual(store.get(job['id']),before)
                self.assertEqual(store.session_charges('original','one'),{'local_supervision':1})
            with patch.object(G.time,'time',return_value=deadline+1),self.assertRaises(ValueError):G.authorize(self.model)

    def test_standalone_default_session_uses_one_finite_profile_allowance(self):
        G.enable_default();start=time.time()
        with patch.object(G.time,'time',return_value=start):authorization=G.authorize(self.model)
        self.assertEqual(authorization,start+86400)
        session=G.NativeSession(self.model,authorization)
        with patch.object(G.time,'time',return_value=start+500):
            self.assertEqual(session.authorization_epoch,authorization)
        self.assertIsNone(session.server)

    def test_new_project_jobs_use_existing_profile_and_window_without_trial_expiry(self):
        G.enable_default();instant=dt.datetime.now(dt.timezone.utc);cutoff=instant+dt.timedelta(hours=24)
        with patch.object(C,'now',return_value=instant),patch.object(C,'project_brief',return_value={}), \
             patch.object(M,'task_selection',return_value=nullcontext(dict(self.model,promotion_generation=None))), \
             patch.object(C.PA,'launch',side_effect=lambda *args:args[-1]):
            self.assertEqual(C.submit_project({},cutoff,{'id':'original'}),cutoff)

    def test_new_discussions_use_existing_profile_and_window_without_trial_expiry(self):
        G.enable_default();instant=dt.datetime.now(dt.timezone.utc);cutoff=instant+dt.timedelta(hours=12)
        project={'id':'one','name':'One','on_schedule':True,'state_dir':str(self.root/'one')}
        workspace=W.Workspace(C.STATE/'agent-workspace');workspace.bootstrap([project])
        workspace.operator_post('codex','project-one','Report observed progress',kind='question',mentions=['planner:one'])
        with patch.object(M,'task_selection',return_value=nullcontext(dict(self.model,promotion_generation=None))), \
             patch.object(A.PA,'source_head',return_value='a'*40),patch.object(A.PA,'spawn_pending'):
            identity=A.schedule(self.root,{'projects':[project]},instant,cutoff,'original')
        self.assertEqual(Actions(C.STATE).get(identity)['deadline'],cutoff.timestamp())

    def test_default_cli_rejects_expiry_or_ambiguous_selection(self):
        for arguments in [('--default','glm','--until','2099-01-01'),('--default','glm','--trial','glm'),
                          ('--default','glm','--restore-incumbent')]:
            with self.subTest(arguments=arguments):
                try:result=self.cli(*arguments)
                except ValueError:result=2
                self.assertNotEqual(result,0)
        self.assertIsNone(G.trial_status())


if __name__=='__main__':unittest.main()
