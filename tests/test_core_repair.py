import hashlib
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import unittest
from unittest import mock
import core_repair as C
from action_store import Actions
import project_action as PA


class CoreRepairTests(unittest.TestCase):
    def setUp(self):
        authority=mock.patch.object(C,'check_authority',return_value={'permission_sha256':'fixture'});authority.start();self.addCleanup(authority.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.runtime=self.root/'runtime';self.runtime.mkdir()
        self.directory=self.runtime/'maintenance'/'repair-1';self.directory.mkdir(parents=True)
        (self.root/'midnight.py').write_text('trusted controller');(self.root/'dashboard.py').write_text('trusted dashboard')

    def git(self,root,*args):
        if args==('rev-parse','HEAD'):return self.head
        if args==('rev-parse','HEAD^{tree}'):return 'verified-tree' if self.head=='merged' else 'original-tree'
        if args==('status','--porcelain'):return ''
        if args==('rev-parse','merged^{tree}'):return 'verified-tree'
        return ''

    def test_install_replays_crash_after_source_mutation_without_duplicate_merge(self):
        self.head='merged';receipt={'status':'merged','commit':'merged'}
        with mock.patch.object(C.G,'git',side_effect=self.git) as git,mock.patch.object(C,'permitted',return_value=True), \
             mock.patch.object(C.GU,'restart_controller',return_value=True),mock.patch.object(C.subprocess,'run',return_value=mock.Mock(returncode=0)),mock.patch.object(C,'fetch'):
            self.assertTrue(C.install(self.root,receipt,'original','verified-tree',self.directory,time.time()+30))
        self.assertFalse(any(c.args[1:3]==('merge','--ff-only') for c in git.call_args_list))
        self.assertEqual(json.loads((self.directory/'installation.json').read_text())['installed'],'merged')
        self.assertFalse((self.runtime/'INSTALLING').exists())
        self.assertEqual(Actions(self.runtime).meta('install_health')['state'],'verifying')

    def test_pause_after_fetch_prevents_mutation_and_clears_internal_marker(self):
        self.head='original'
        with mock.patch.object(C.G,'git',side_effect=self.git) as git,mock.patch.object(C,'permitted',side_effect=[True,True,True,False]), \
             mock.patch.object(C.GU,'restart_controller') as restart,mock.patch.object(C,'fetch'):
            with self.assertRaises(ValueError):C.install(self.root,{'status':'merged','commit':'merged'},'original','verified-tree',self.directory,time.time()+30)
        self.assertFalse(any(c.args[1:3]==('merge','--ff-only') for c in git.call_args_list));restart.assert_not_called()
        self.assertFalse((self.runtime/'INSTALLING').exists())

    def test_dispatch_cannot_enter_while_installation_owns_source(self):
        (self.runtime/'INSTALLING').touch()
        with mock.patch.object(PA,'source_head') as source:
            self.assertIsNone(PA.launch(self.runtime,{'id':'notes'}, {},{},'session',__import__('datetime').datetime.now(__import__('datetime').timezone.utc)))
        source.assert_not_called()

    def test_pending_delivery_stays_charged_and_single_writer_after_restart(self):
        store=Actions(self.runtime);incident=store.incident('midnight','backend_error','root','evidence',{})
        identity=store.reserve_repair(incident,{})
        store.repair(identity,state='unknown');directory=self.runtime/'maintenance'/identity;directory.mkdir(parents=True)
        patch=b'diff --git a/example b/example\n';(directory/'candidate.patch').write_bytes(patch)
        verified={'base':'base','tree':'tree','patch_sha256':hashlib.sha256(patch).hexdigest(),
            'review':{'decision':'accept','issues':[]},'checks':[{'code':0,'reason':'exited'}]}
        (directory/'verified.json').write_text(json.dumps(verified))
        delivery=mock.Mock();delivery.deliver.return_value={'status':'pending','reason':'Waiting for required checks'}
        with mock.patch.object(C.G,'GitDelivery',return_value=delivery),mock.patch.object(C,'check_authority'),mock.patch.object(C,'permitted',return_value=True):C.reconcile(self.root,store.repair(identity))
        self.assertEqual(store.repair(identity)['state'],'unknown');self.assertEqual(store.incidents()[0]['attempts'],1)
        with self.assertRaises(ValueError):store.reserve_repair(incident,{})
        self.assertEqual(delivery.deliver.call_args.args[2],identity)

    def test_interrupted_unverified_implementation_is_failed_without_refund(self):
        store=Actions(self.runtime);incident=store.incident('midnight','error','root','evidence',{})
        identity=store.reserve_repair(incident,{});store.repair(identity,state='unknown')
        with mock.patch.object(C.G,'GitDelivery') as delivery,mock.patch.object(C,'permitted',return_value=True):C.reconcile(self.root,store.repair(identity))
        delivery.assert_not_called();self.assertEqual(store.repair(identity)['state'],'failed')
        self.assertEqual(store.incidents()[0]['attempts'],1)

    def test_zero_exit_without_a_complete_runner_receipt_cannot_pass(self):
        with self.assertRaises(ValueError):C.validate_completion(self.directory/'missing.json','token','candidate',['test.C.test_case'])
        receipt=self.directory/'completion.json'
        receipt.write_text(json.dumps({'schema_version':1,'token':'token','part':'candidate','selected':['test.C.test_case'],
            'executed':[],'tests_run':0,'passed':True,'errors':0,'failures':0}))
        with self.assertRaises(ValueError):C.validate_completion(receipt,'token','candidate',['test.C.test_case'])

    def test_real_runner_early_exit_during_import_is_rejected(self):
        work=self.root/'early-exit';(work/'tests').mkdir(parents=True)
        runner=work/'verification_runner.py';runner.write_bytes(Path(C.__file__).with_name('verification_runner.py').read_bytes())
        (work/'tests/test_recovery_exit.py').write_text('import os,unittest\nos._exit(0)\nclass ExitTests(unittest.TestCase):\n def test_case(self):pass\n')
        receipt=work/'completion.json'
        result=subprocess.run([sys.executable,'-B',str(runner),'candidate','--receipt',str(receipt),'--token','nonce'],cwd=work,capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0)
        with self.assertRaises(ValueError):C.validate_completion(receipt,'nonce','candidate',['test_recovery_exit.ExitTests.test_case'])

    def test_source_receipt_without_finalization_is_replayed_to_complete(self):
        store=Actions(self.runtime);incident=store.incident('midnight','error','root','evidence',{})
        identity=store.reserve_repair(incident,{});store.repair(identity,state='unknown')
        directory=self.runtime/'maintenance'/identity;directory.mkdir(parents=True)
        patch=b'diff --git a/x b/x\n';(directory/'candidate.patch').write_bytes(patch)
        (directory/'verified.json').write_text(json.dumps({'base':'original','tree':'verified-tree','patch_sha256':hashlib.sha256(patch).hexdigest(),
            'review':{'decision':'accept','issues':[]},'checks':[{'code':0,'reason':'exited'}]}))
        (directory/'delivery.json').write_text(json.dumps({'status':'merged','commit':'merged'}))
        (directory/'installation.json').write_text(json.dumps({'previous':'original','installed':'merged','tree':'verified-tree','at':'2026-11-01T00:00:00+00:00'}))
        fence={'owner':'midnight-maintenance-install','generation':'fixture-generation','previous':'original','installed':'merged','repair':identity}
        (directory/'install-fence.json').write_text(json.dumps(fence))
        (self.runtime/'INSTALLING').write_text(json.dumps(fence,sort_keys=True)+'\n');self.head='merged'
        with mock.patch.object(C,'permitted',return_value=True),mock.patch.object(C,'fetch'),mock.patch.object(C.G,'git',side_effect=self.git), \
             mock.patch.object(C.GU,'restart_controller',return_value=True),mock.patch.object(C.subprocess,'run',return_value=mock.Mock(returncode=0)):
            C.reconcile(self.root,store.repair(identity))
        self.assertEqual(store.repair(identity)['state'],'passed');self.assertFalse((self.runtime/'INSTALLING').exists())
        self.assertEqual(store.meta('install_health')['installed'],'merged');self.assertTrue((directory/'installation-complete.json').exists())


if __name__=='__main__':unittest.main()
