"""Old-source non-reproduction and uncertainty cannot be retried into proof."""
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


class TerminalRegressionTests(unittest.TestCase):
    def setUp(self):
        self.f=F.MaintenanceProgressTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def boundary(self,kind):
        f=self.f;identity,directory,binding,patch=f.prepared_checked_session();payload=f.manager.session(identity)['payload'];payload.update(stage='candidate',action='verification');f.manager.update(identity,'queued',payload=payload)
        for work in (directory/'canonical',directory/'work'):(work/'tests').mkdir()
        (directory/'work/tests/test_recovery_fixture.py').write_text('import unittest\nclass Regression(unittest.TestCase):\n def test_original(self):self.assertTrue(True)\n')
        calls=[];original_log=None;original=None
        def regression(root,job,work,canonical,checkdir,*args):
            nonlocal original_log,original
            calls.append(str(checkdir));first=len(calls)==1;error=first and kind in {'infrastructure','error'};passed=first and not error
            log=checkdir/'regression-on-base.log'
            if first and kind=='resource_wait':
                lock=f.root/'runtime/resources/host-0.lock';lock.parent.mkdir(parents=True,exist_ok=True)
                command=[sys.executable,'-B',str(canonical/'verification_runner.py'),'regression','--receipt',str(checkdir/'regression-completion.json'),'--token','never-launched','--modules','test_recovery_fixture']
                with lock.open('a') as held:
                    fcntl.flock(held,fcntl.LOCK_EX)
                    def waiting(reason):raise P.PhaseInterrupted('fixture regression interrupted during host resource wait')
                    with P.phase(f.root/'runtime','midnight','host',command,canonical,log,time.time()+60,waiting=waiting):
                        raise AssertionError('Occupied host resource must prevent original regression launch')
            log.write_text('ERROR: fixed host case\n'+('subprocess.TimeoutExpired: host process timed out\n' if kind=='infrastructure' else 'RuntimeError: invalid original regression\n') if error else 'original source passed without reproducing\n' if passed else 'FAIL: original bug\nAssertionError: exact original defect\n')
            receipt=checkdir/'regression-completion.json';selected=R.expected_tests(work,'regression',['test_recovery_fixture']);token='regression-terminal-token'
            M.save(receipt,{'schema_version':1,'part':'regression','token':token,'selected':selected,'executed':selected,'tests_run':len(selected),
                'passed':passed,'failures':int(not passed and not error),'errors':int(error),'skipped':0})
            command=[sys.executable,'-B',str(canonical/'verification_runner.py'),'regression','--receipt',str(receipt),'--token',token,'--modules','test_recovery_fixture']
            key=A.fingerprint({'command':command,'cwd':str(canonical),'log':str(log),'input':hashlib.sha256(b'').hexdigest()})
            phase=f.actions.phase(job['id'],'midnight','host',key,time.time()+60,{'log':str(log),'command_sha256':A.fingerprint(command)})
            f.actions.phase_update(phase['id'],'running',lease=P.lease());M.save(f.root/'runtime/phases'/phase['id']/'launch.json',{'command':command,'cwd':str(canonical),'output_file':str(log)})
            if not (first and kind in {'uncertain','no_receipt'}):
                f.actions.phase_update(phase['id'],'succeeded' if passed else 'failed',result={'code':0 if passed else 1,'reason':'exited' if passed else 'command_failed','log_sha256':RP.ref(log)['sha256']})
            if first:
                if kind=='no_receipt':receipt.unlink()
                original_log=log;original=log.read_bytes();raise OSError('executor died after original regression phase before classification')
        with mock.patch.object(M,'regression',side_effect=regression),mock.patch.object(P,'alive',return_value=False), \
             mock.patch.object(R,'run_checks',side_effect=OSError('separate full-check boundary')) as full:
            first,implementation,review,install=f.execute_fake_host(identity,binding,patch);self.assertEqual(first,1);self.assertEqual(len(calls),1)
            second,implementation,review,install=f.execute_fake_host(identity,binding,patch);self.assertEqual(second,1)
            if kind=='resource_wait':
                self.assertEqual(len(calls),2);self.assertEqual(full.call_count,1)
                phases=[p for p in f.actions.phases(project='midnight',active=False) if (p.get('result') or {}).get('reason')=='stopped_before_start']
                self.assertEqual(len(phases),1);self.assertIsNone(phases[0]['lease'])
                for operation in (implementation,review,install):operation.assert_not_called()
                self.assertEqual(f.actions.get_incident(f.incident)['attempts'],4)
                with f.actions.read() as con:self.assertEqual(con.execute('SELECT count(*) FROM wf_charges').fetchone()[0],0)
                return
            self.assertEqual(len(calls),1);current=f.manager.session(identity)
            if kind=='infrastructure':
                self.assertEqual(current['result']['reason'],'host_verification_infrastructure_wait');not_before=current['payload']['host_verification_retry']['not_before']
                full.assert_not_called();actual=time.time
                with mock.patch.object(M.time,'time',side_effect=lambda:actual()+1200):third,_,_,_=f.execute_fake_host(identity,binding,patch)
                self.assertEqual(third,1);self.assertEqual(len(calls),2);self.assertEqual(full.call_count,1)
            elif kind in {'uncertain','no_receipt'}:
                self.assertEqual(current['state'],'paused');self.assertEqual(current['result']['error'],'ResultUncertain');full.assert_not_called()
            else:
                self.assertEqual(current['state'],'waiting');full.assert_not_called()
                third,_,_,_=f.execute_fake_host(identity,binding,patch);self.assertEqual(third,0);self.assertEqual(len(calls),1)
            for operation in (implementation,review,install):operation.assert_not_called()
            self.assertEqual(original_log.read_bytes(),original);self.assertEqual(f.actions.get_incident(f.incident)['attempts'],4)
            with f.actions.read() as con:self.assertEqual(con.execute('SELECT count(*) FROM wf_charges').fetchone()[0],0)

    def test_persisted_successful_old_source_phase_parks_without_rerunning(self):self.boundary('pass')
    def test_successful_old_source_receipt_without_terminal_phase_stays_uncertain(self):self.boundary('uncertain')
    def test_validated_original_regression_infrastructure_obeys_bound_backoff(self):self.boundary('infrastructure')
    def test_terminal_original_regression_error_cannot_be_rerun_into_reproduction(self):self.boundary('error')


    def test_original_regression_resource_wait_interruption_continues_without_coding_charge(self):self.boundary('resource_wait')
    def test_launched_original_regression_without_completion_remains_uncertain(self):self.boundary('no_receipt')


if __name__=='__main__':unittest.main()
