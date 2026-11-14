"""Failed local process receipts park unchanged evidence without replaying calls."""
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import time
import unittest
from unittest import mock
import core_repair as R
import maintenance_recovery as M
import progress_recovery as RP
import test_recovery_maintenance_progress as F


class TerminalDiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.f=F.MaintenanceProgressTests();self.f.setUp();self.addCleanup(self.f.doCleanups);f=self.f
        self.original=f.legacy();scope,_,capsule=f.observe();self.scope=scope
        self.identity=f.manager.create('diagnosis',scope,f.model,capsule)
        payload=f.manager.session(self.identity)['payload'];payload['local_model']={'model':'fixture-local'};f.manager.update(self.identity,'queued',payload=payload)
        self.directory=f.root/'runtime/maintenance'/self.identity;self.directory.mkdir(parents=True)
        self.prompt='Exact qualified original diagnosis evidence';self.phase=None
        def git(root,*args):return '' if args[0]=='status' else f.base
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(R,'permitted',return_value=True));stack.enter_context(mock.patch.object(R,'maintenance_current',return_value=True));stack.enter_context(mock.patch.object(R,'check_authority'))
        stack.enter_context(mock.patch.object(R.G,'git',side_effect=git))
        stack.enter_context(mock.patch.object(M,'prepare_workspace',return_value=(self.directory/'canonical',self.directory/'work')))
        def diagnosis(root,manager,session,directory,deadline):
            return M.local_decision(root,manager,manager.session(self.identity),directory/'diagnosis','diagnosis',self.prompt,M.PROPOSAL_SCHEMA,deadline)
        stack.enter_context(mock.patch.object(M,'diagnose',side_effect=diagnosis))
        def command(root,job,kind,args,cwd,log,seconds,deadline,**kwargs):
            kwargs['on_launch']();raw=b'Known terminal diagnosis subprocess failure\n';Path(log).write_bytes(raw)
            phase=f.actions.phase(job['id'],'midnight','local','original-diagnosis',deadline,{'log':str(log)})
            M.save(f.root/'runtime/phases'/phase['id']/'launch.json',{'command':list(map(str,args)),'cwd':str(cwd),'output_file':str(log)})
            result={'code':7,'reason':'command_failed','log_sha256':hashlib.sha256(raw).hexdigest()};f.actions.phase_update(phase['id'],'failed',result=result);self.phase=phase['id']
            return result
        self.command=stack.enter_context(mock.patch.object(R,'command',side_effect=command))

    def interrupted_process_receipt(self):
        save=M.save;interrupted=False
        def persist(path,value):
            nonlocal interrupted
            save(path,value)
            if Path(path).name=='process.json' and not interrupted:interrupted=True;raise OSError('process failure persisted before execution parked')
        with mock.patch.object(M,'save',side_effect=persist):self.assertEqual(M.execute(self.f.root,self.identity),1)
        state=self.f.manager.session(self.identity);self.assertEqual(state['state'],'paused')
        dead={'pid':99999999,'stamp':'original executor exited','boot_id':R.D.boot_id(),'birth_us':1}
        self.f.manager.update(self.identity,'paused',lease=dead,expected=state['lease'],result={**state['result'],'next_retry_at':0})

    def reconcile(self,missing_process=False):
        self.interrupted_process_receipt()
        if missing_process:(self.directory/'diagnosis/process.json').unlink()
        self.assertEqual(M.execute(self.f.root,self.identity),1)
        state=self.f.manager.session(self.identity);self.assertEqual(state['state'],'waiting');self.assertEqual(state['result']['error'],'LocalDecisionFailed')
        self.assertEqual(self.command.call_count,1);self.assertEqual(self.f.actions.repairs(active=False),self.original)
        with self.f.actions.read() as con:self.assertEqual(con.execute("SELECT count(*) FROM wf_charges WHERE kind='maintenance_local_supervision'").fetchone()[0],1)
        with mock.patch.object(M,'enabled',return_value=True):self.assertFalse(M.running(self.f.root,spawn=mock.Mock()))
        # The failed call grants nothing, while a distinct current incident may
        # admit a separately reviewed source intervention on the available lane.
        f=self.f;f.incident=f.actions.incident('midnight','backend_error','distinct-ready-work','new-evidence',{'detail':'Another actual original obligation'})
        f.manager=M.MaintenanceRecovery(f.root,f.incident);scope,_,capsule=f.observe();f.offer(scope)
        model=mock.Mock();model.descriptor.return_value=f.model;spawn=mock.Mock()
        with mock.patch.object(M,'enabled',return_value=True),mock.patch.object(R.CP,'CloudPolicy',return_value=model):identity=M.launch(f.root,f.actions,f.incident,spawn=spawn)
        self.assertIsNotNone(identity);self.assertEqual(f.manager.session(identity)['payload']['action'],'implementation');self.assertEqual(spawn.call_count,1)
        self.assertEqual(self.command.call_count,1)

    def test_failed_process_persistence_crash_parks_and_allows_distinct_eligible_work(self):self.reconcile()
    def test_original_failed_phase_recovers_when_process_mirror_is_missing(self):self.reconcile(missing_process=True)

    def test_changed_failure_log_remains_uncertain_without_call_replay(self):
        self.interrupted_process_receipt();(self.directory/'diagnosis/events.jsonl').write_text('changed unbound bytes\n')
        f=self.f
        with self.assertRaises(M.ResultUncertain):M.local_decision(f.root,f.manager,f.manager.session(self.identity),self.directory/'diagnosis','diagnosis',self.prompt,M.PROPOSAL_SCHEMA,time.time()+60)
        self.assertEqual(self.command.call_count,1)


if __name__=='__main__':unittest.main()
