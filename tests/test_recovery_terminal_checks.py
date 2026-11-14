"""Persisted failed host checks cannot be erased by an unchanged rerun."""
import fcntl
import hashlib
from pathlib import Path
import sys
import time
import unittest
from unittest import mock
import action_store as A
import core_repair as R
import maintenance_recovery as M
import phase_runtime as P
import progress_recovery as RP
import test_recovery_maintenance_progress as F


class TerminalCheckTests(unittest.TestCase):
    def setUp(self):
        self.f=F.MaintenanceProgressTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def interruption(self,part,*,baseline=False,infrastructure=False,changed_log=False,phase_missing=False,receipt_missing=False,resource_wait=False):
        f=self.f;identity,directory,binding,patch=f.prepared_checked_session();payload=f.manager.session(identity)['payload']
        payload.update(stage='preparing' if baseline else 'candidate',action='implementation' if baseline else 'verification')
        f.manager.update(identity,'queued',payload=payload);calls=[];failed_log=None
        for work in (directory/'canonical',directory/'work'):
            (work/'tests').mkdir();(work/'tests/test_fixture.py').write_text('import unittest\nclass Fixture(unittest.TestCase):\n def test_case(self):self.assertTrue(True)\n')
        (directory/'work/tests/test_recovery_fixture.py').write_text('import unittest\nclass Regression(unittest.TestCase):\n def test_original(self):self.assertTrue(True)\n')
        def regression(root,job,work,canonical,checkdir,*args):
            log=checkdir/'regression-on-base.log';log.write_text('AssertionError: original fixture failure\n')
            receipt=checkdir/'regression-completion.json';selected=R.expected_tests(work,'regression',['test_recovery_fixture']);token='regression-token'
            M.save(receipt,{'schema_version':1,'part':'regression','token':token,'selected':selected,'executed':selected,'tests_run':len(selected),'passed':False,'failures':1,'errors':0,'skipped':0})
            command=[sys.executable,'-B',str(canonical/'verification_runner.py'),'regression','--receipt',str(receipt),'--token',token,'--modules','test_recovery_fixture']
            self.phase(f,job,command,canonical,log,True)
        def checks(root,job,work,checkdir,deadline,*,baseline=False):
            nonlocal failed_log
            calls.append(str(checkdir));checkdir.mkdir(exist_ok=True);parts=[('candidate',work,'checks.log','completion.json')]
            if not baseline:parts.append(('native',Path(R.__file__).resolve().parent,'native-checks.log','native-completion.json'))
            results=[]
            for current,cwd,logname,receiptname in parts:
                failed=len(calls)==1 and current==part;log=checkdir/logname
                if failed and resource_wait:
                    lock=f.root/'runtime/resources/host-0.lock';lock.parent.mkdir(parents=True,exist_ok=True)
                    command=[sys.executable,'-B',str(cwd/'verification_runner.py'),current,'--receipt',str(checkdir/receiptname),'--token','never-launched-token']
                    with lock.open('a') as held:
                        fcntl.flock(held,fcntl.LOCK_EX)
                        def waiting(reason):raise P.PhaseInterrupted('fixture stopped while waiting for host resource')
                        with P.phase(f.root/'runtime','midnight','host',command,cwd,log,time.time()+60,waiting=waiting):
                            raise AssertionError('An occupied host resource must prevent launch')
                log.write_text(('ERROR: fixed host case\nsubprocess.TimeoutExpired: host process timed out\n' if infrastructure else 'FAIL: fixed assertion\nAssertionError: original semantic obligation remains unmet\n') if failed else 'fixed checks passed\n')
                receipt=checkdir/receiptname;selected=R.expected_tests(cwd,current);token='terminal-'+current
                M.save(receipt,{'schema_version':1,'part':current,'token':token,'selected':selected,'executed':selected,'tests_run':len(selected),
                    'passed':not failed,'failures':int(failed and not infrastructure),'errors':int(failed and infrastructure),'skipped':0})
                command=[sys.executable,'-B',str(cwd/'verification_runner.py'),current,'--receipt',str(receipt),'--token',token]
                results.append(self.phase(f,job,command,cwd,log,failed,finalize=not ((phase_missing or receipt_missing) and failed)))
                if failed:
                    failed_log=log
                    if receipt_missing:receipt.unlink()
                    raise OSError('executor died after terminal check persistence before parking')
            return results
        with mock.patch.object(M,'prepare_workspace',return_value=(directory/'canonical',directory/'work')), \
             mock.patch.object(M,'regression',side_effect=regression),mock.patch.object(R,'run_checks',side_effect=checks), \
             mock.patch.object(P,'alive',return_value=False),mock.patch.object(M,'enabled',return_value=True):
            first,implementation,review,install=f.execute_fake_host(identity,binding,patch)
            self.assertEqual(first,1);self.assertEqual(len(calls),1);self.assertEqual(f.manager.session(identity)['state'],'paused')
            if resource_wait:
                phases=[v for v in f.actions.phases(project='midnight',active=False) if (v.get('result') or {}).get('reason')=='stopped_before_start']
                self.assertEqual(len(phases),1);self.assertIsNone(phases[0]['lease'])
                self.assertFalse((f.root/'runtime/phases'/phases[0]['id']/'launch.json').exists())
                second,implementation,review,install=f.execute_fake_host(identity,binding,patch)
                self.assertEqual(len(calls),2);implementation.assert_not_called();review.assert_not_called()
                if baseline:self.assertEqual(f.manager.session(identity)['state'],'waiting')
                else:self.assertEqual(second,0,f.manager.session(identity)['result']);self.assertEqual(f.manager.session(identity)['state'],'complete')
                self.assertEqual(f.actions.get_incident(f.incident)['attempts'],4)
                with f.actions.read() as con:self.assertEqual(con.execute('SELECT count(*) FROM wf_charges').fetchone()[0],0)
                return
            original=failed_log.read_bytes()
            if changed_log:failed_log.write_bytes(original+b'changed after process completion\n')
            second,implementation,review,install=f.execute_fake_host(identity,binding,patch)
            current=f.manager.session(identity);self.assertEqual(second,1);self.assertEqual(len(calls),1)
            for operation in (implementation,review,install):operation.assert_not_called()
            if infrastructure and not changed_log:
                self.assertEqual(current['result']['reason'],'host_verification_infrastructure_wait');not_before=current['payload']['host_verification_retry']['not_before']
                early,_,_,_=f.execute_fake_host(identity,binding,patch);self.assertEqual(early,1);self.assertEqual(len(calls),1)
                self.assertEqual(f.manager.session(identity)['payload']['host_verification_retry']['not_before'],not_before)
                actual_time=time.time
                with mock.patch.object(M.time,'time',side_effect=lambda:actual_time()+1200):last,implementation,review,install=f.execute_fake_host(identity,binding,patch)
                self.assertEqual(len(calls),2)
                if baseline:self.assertEqual(f.manager.session(identity)['state'],'waiting')
                else:self.assertEqual(last,0,f.manager.session(identity)['result']);self.assertEqual(f.manager.session(identity)['state'],'complete')
            elif changed_log or phase_missing or receipt_missing:
                self.assertEqual(current['state'],'paused');self.assertEqual(current['result']['error'],'ResultUncertain')
                again,_,_,_=f.execute_fake_host(identity,binding,patch);self.assertEqual(again,1);self.assertEqual(len(calls),1)
            else:
                self.assertEqual(current['state'],'waiting');self.assertFalse(current['result']['infrastructure'])
                again,_,_,_=f.execute_fake_host(identity,binding,patch);self.assertEqual(again,0);self.assertEqual(len(calls),1)
                incident=f.actions.incident('midnight','backend_error','distinct-source-unit','distinct-real-failure',{'detail':'other actionable original failure'})
                manager=M.MaintenanceRecovery(f.root,incident);scope,_,capsule=manager.observe(f.actions.get_incident(incident),f.base,f.model,{'source_files':['source.py']})
                other=manager.create('diagnosis',scope,f.model,capsule);self.assertEqual(manager.session(other)['state'],'queued')
            self.assertEqual(f.actions.get_incident(f.incident)['attempts'],4)
            with f.actions.read() as con:self.assertEqual(con.execute('SELECT count(*) FROM wf_charges').fetchone()[0],0)
            if not changed_log:self.assertEqual(failed_log.read_bytes(),original)

    def phase(self,f,job,command,cwd,log,failed,*,finalize=True):
        key=A.fingerprint({'command':command,'cwd':str(cwd),'log':str(log),'input':hashlib.sha256(b'').hexdigest()})
        phase=f.actions.phase(job['id'],'midnight','host',key,time.time()+60,{'log':str(log),'command_sha256':A.fingerprint(command)})
        f.actions.phase_update(phase['id'],'running',lease=P.lease());M.save(f.root/'runtime/phases'/phase['id']/'launch.json',{'command':command,'cwd':str(cwd),'output_file':str(log)})
        result={'code':1 if failed else 0,'reason':'command_failed' if failed else 'exited','log_sha256':RP.ref(log)['sha256']}
        if finalize:f.actions.phase_update(phase['id'],'failed' if failed else 'succeeded',result=result)
        return result

    def test_candidate_assertion_persisted_before_crash_parks_without_rerun(self):self.interruption('candidate')
    def test_native_assertion_persisted_before_crash_parks_without_rerun(self):self.interruption('native')
    def test_baseline_assertion_persisted_before_crash_parks_without_rerun(self):self.interruption('candidate',baseline=True)
    def test_candidate_infrastructure_requires_saved_backoff_before_host_retry(self):self.interruption('candidate',infrastructure=True)
    def test_native_infrastructure_requires_saved_backoff_before_host_retry(self):self.interruption('native',infrastructure=True)
    def test_baseline_infrastructure_requires_saved_backoff_before_host_retry(self):self.interruption('candidate',baseline=True,infrastructure=True)
    def test_changed_candidate_failure_log_stays_uncertain_without_rerun(self):self.interruption('candidate',changed_log=True)
    def test_changed_baseline_failure_log_stays_uncertain_without_rerun(self):self.interruption('candidate',baseline=True,changed_log=True)


    def test_retained_failed_baseline_aggregate_cannot_admit_a_coding_launch(self):
        f=self.f;identity,directory,binding,patch=f.prepared_checked_session();payload=f.manager.session(identity)['payload']
        f.offer(payload['scope']);payload.update(stage='preparing',action='implementation');f.manager.update(identity,'queued',payload=payload)
        M.save(directory/'baseline-receipt.json',{'base':f.base,'checks':[{'code':1,'reason':'command_failed'}]})
        with mock.patch.object(M,'prepare_workspace',return_value=(directory/'canonical',directory/'work')), \
             mock.patch.object(R.CP,'CloudPolicy',side_effect=AssertionError('Failed baseline cannot prepare paid implementation')) as cloud, \
             mock.patch.object(R,'run_checks',side_effect=AssertionError('Failed baseline cannot be silently rerun')) as checks:
            result,implementation,review,install=f.execute_fake_host(identity,binding,patch)
        self.assertEqual(result,1);self.assertEqual(f.manager.session(identity)['state'],'waiting')
        for operation in (cloud,checks,implementation,review,install):operation.assert_not_called()
        self.assertTrue(f.manager.admission(payload['scope'],f.model)['allowed']);self.assertEqual(f.actions.get_incident(f.incident)['attempts'],4)


    def invalid_baseline(self,value):
        f=self.f;identity,directory,binding,patch=f.prepared_checked_session();payload=f.manager.session(identity)['payload']
        f.offer(payload['scope']);payload.update(stage='preparing',action='implementation');f.manager.update(identity,'queued',payload=payload)
        M.save(directory/'baseline-receipt.json',value(f))
        with mock.patch.object(M,'prepare_workspace',return_value=(directory/'canonical',directory/'work')), \
             mock.patch.object(R.CP,'CloudPolicy',side_effect=AssertionError('Invalid baseline must not prepare implementation')) as cloud, \
             mock.patch.object(R,'run_checks',side_effect=AssertionError('Unbound baseline evidence must not be replaced')) as checks:
            result,implementation,review,install=f.execute_fake_host(identity,binding,patch)
        self.assertEqual(result,1);self.assertEqual(f.manager.session(identity)['state'],'waiting')
        for operation in (cloud,checks,implementation,review,install):operation.assert_not_called()
        self.assertTrue(f.manager.admission(payload['scope'],f.model)['allowed']);self.assertEqual(f.actions.get_incident(f.incident)['attempts'],4)

    def test_empty_baseline_record_holds_before_provider_preparation(self):self.invalid_baseline(lambda f:{})
    def test_unbound_success_baseline_holds_before_provider_preparation(self):self.invalid_baseline(lambda f:{'base':f.base,'checks':[{'code':0,'reason':'exited'}]})


    def test_candidate_failed_receipt_before_phase_finalization_stays_uncertain(self):self.interruption('candidate',phase_missing=True)
    def test_native_failed_receipt_before_phase_finalization_stays_uncertain(self):self.interruption('native',phase_missing=True)
    def test_baseline_failed_receipt_before_phase_finalization_stays_uncertain(self):self.interruption('candidate',baseline=True,phase_missing=True)


    def test_launched_candidate_with_log_but_no_completion_stays_uncertain(self):self.interruption('candidate',receipt_missing=True)
    def test_launched_native_with_log_but_no_completion_stays_uncertain(self):self.interruption('native',receipt_missing=True)
    def test_launched_baseline_with_log_but_no_completion_stays_uncertain(self):self.interruption('candidate',baseline=True,receipt_missing=True)


    def test_candidate_resource_wait_interruption_resumes_without_coding_charge(self):self.interruption('candidate',resource_wait=True)
    def test_native_resource_wait_interruption_resumes_without_coding_charge(self):self.interruption('native',resource_wait=True)
    def test_baseline_resource_wait_interruption_resumes_without_coding_charge(self):self.interruption('candidate',baseline=True,resource_wait=True)


if __name__=='__main__':unittest.main()
