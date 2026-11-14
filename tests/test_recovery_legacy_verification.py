"""Original legacy check failures cannot become fresh unchanged verification."""
import hashlib
import json
from pathlib import Path
import subprocess
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


class LegacyVerificationTests(unittest.TestCase):
    def setUp(self):
        self.f=F.MaintenanceProgressTests();self.f.setUp();self.addCleanup(self.f.doCleanups);f=self.f
        self.git=lambda root,*args:subprocess.check_output(['/usr/bin/git',*args],cwd=root,text=True,stderr=subprocess.DEVNULL).strip()
        g=lambda *args:self.git(f.root,*args)
        g('init','-q');g('config','user.name','Fixture');g('config','user.email','fixture@localhost')
        (f.root/'.gitignore').write_text('runtime/\n');(f.root/'verification_runner.py').write_bytes(Path(R.__file__).with_name('verification_runner.py').read_bytes())
        (f.root/'tests').mkdir();(f.root/'tests/test_fixture.py').write_text('import unittest\nclass Case(unittest.TestCase):\n def test_case(self):self.assertTrue(True)\n')
        g('add','.');g('-c','commit.gpgsign=false','commit','-qm','legacy source');f.base=g('rev-parse','HEAD')
        self.original=f.legacy();self.repair=self.original[0];self.directory=f.root/'runtime/maintenance'/self.repair['id'];self.directory.mkdir(parents=True)
        self.work=self.directory/'work';self.canonical=self.directory/'canonical'
        for target in (self.work,self.canonical):subprocess.run(['/usr/bin/git','clone','--quiet',str(f.root),str(target)],check=True)
        (self.work/'source.py').write_text('def repair(value):\n    return value + 2\n')
        (self.work/'tests/test_recovery_fixture.py').write_text('import unittest\nclass Original(unittest.TestCase):\n def test_original(self):self.assertTrue(True)\n')
        self.git(self.work,'add','--all');(self.directory/'candidate.patch').write_text(self.git(self.work,'diff','--binary',f.base)+'\n')
        self.job=f.actions.submit('midnight',{'maintenance':self.repair['id'],'source_head':f.base},time.time()+60);f.actions.claim(self.job['id'],P.lease())
        self.assertFalse((self.directory/'verified.json').exists());self.assertFalse((self.directory/'review.json').exists())

    def phase(self,kind,*,regression=False):
        f=self.f;cwd=self.canonical if regression else self.work;part='regression' if regression else 'candidate'
        log=self.directory/('regression-on-base.log' if regression else 'checks.log');receipt=self.directory/('regression-completion.json' if regression else 'completion.json')
        selected=R.expected_tests(self.work,part,['test_recovery_fixture'] if regression else None);token='legacy-terminal-token'
        command=[sys.executable,'-B',str(cwd/'verification_runner.py'),part,'--receipt',str(receipt),'--token',token]
        if regression:command+=['--modules','test_recovery_fixture']
        key=A.fingerprint({'command':command,'cwd':str(cwd),'log':str(log),'input':hashlib.sha256(b'').hexdigest()})
        phase=f.actions.phase(self.job['id'],'midnight','host',key,time.time()+60,{'log':str(log),'command_sha256':A.fingerprint(command)})
        if kind=='prelaunch':
            f.actions.phase_update(phase['id'],'interrupted',result={'reason':'stopped_before_start'});return
        failed=kind!='pass';infrastructure=kind=='infrastructure'
        log.write_text('ERROR: fixture\nsubprocess.TimeoutExpired: original host timed out\n' if infrastructure else 'FAIL: original obligation\nAssertionError: retained failure\n' if failed else 'Original source passed without reproducing\n')
        M.save(receipt,{'schema_version':1,'part':part,'token':token,'selected':selected,'executed':selected,'tests_run':len(selected),
            'passed':not failed,'failures':int(failed and not infrastructure),'errors':int(infrastructure),'skipped':0})
        f.actions.phase_update(phase['id'],'running',lease=P.lease());M.save(f.root/'runtime/phases'/phase['id']/'launch.json',{'command':command,'cwd':str(cwd),'output_file':str(log)})
        if kind=='uncertain':receipt.unlink();return
        f.actions.phase_update(phase['id'],'failed' if failed else 'succeeded',result={'code':1 if failed else 0,'reason':'command_failed' if failed else 'exited','log_sha256':RP.ref(log)['sha256']})
        if kind=='changed':log.write_text(log.read_text()+'changed after result persistence\n')

    def selection(self,expected,known,hold=False):
        f=self.f;f.actions.finish(self.job['id'],'failed',{'legacy':'original terminal host outcome'},lease=P.lease())
        candidate=M.retained_history(f.root,f.incident)
        self.assertEqual(candidate['failure']['original_host_verification']['state'],expected)
        self.assertEqual(candidate['known_rejection'],known);self.assertEqual(candidate['verification_hold'],hold)
        with mock.patch.object(M,'enabled',return_value=True),mock.patch.object(M,'running',return_value=False), \
             mock.patch.object(R,'permitted',return_value=True),mock.patch.object(R,'check_authority',return_value={'permission_sha256':'fixture'}), \
             mock.patch.object(R.CP,'CloudPolicy') as cloud,mock.patch.object(M,'spawn_session') as spawn, \
             mock.patch.object(R,'run_checks',side_effect=AssertionError('Legacy failed checks must not run again')) as checks:
            cloud.return_value.descriptor.return_value=f.model;identity=M.launch(f.root,f.actions,f.incident)
        self.assertIsNotNone(identity);self.assertEqual(f.manager.session(identity)['payload']['action'],'diagnosis' if known or hold else 'verification')
        checks.assert_not_called();cloud.return_value.prepare_invocation.assert_not_called();spawn.assert_called_once()
        self.assertEqual(f.actions.repairs(active=False),self.original);self.assertEqual(f.actions.get_incident(f.incident)['attempts'],4)
        with f.actions.read() as con:self.assertEqual(con.execute('SELECT count(*) FROM wf_charges').fetchone()[0],0)

    def test_terminal_legacy_assertion_is_diagnosed_instead_of_reverified(self):self.phase('assertion',regression=True);self.phase('assertion');self.selection('failed',True)
    def test_terminal_legacy_infrastructure_failure_requires_bound_retry(self):self.phase('assertion',regression=True);self.phase('infrastructure');self.selection('failed',True)
    def test_legacy_launched_check_without_completion_blocks_verification(self):self.phase('assertion',regression=True);self.phase('uncertain');self.selection('uncertain',False,True)
    def test_changed_legacy_result_is_uncertain_and_cannot_be_replaced(self):self.phase('assertion',regression=True);self.phase('changed');self.selection('uncertain',False,True)
    def test_proven_legacy_prelaunch_interruption_remains_eligible(self):self.phase('assertion',regression=True);self.phase('prelaunch');self.selection('not_started',False)
    def test_legacy_original_source_pass_cannot_be_repeated_to_seek_reproduction(self):self.phase('pass',regression=True);self.selection('non_reproducer',True)
    def test_legacy_original_source_uncertain_execution_blocks_verification(self):self.phase('uncertain',regression=True);self.selection('uncertain',False,True)


if __name__=='__main__':unittest.main()
