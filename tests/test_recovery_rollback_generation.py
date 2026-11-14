"""Real rollback evaluation with disposable native processes and isolated Git."""
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import guardian as G
from action_store import Actions


class RollbackGenerationTests(unittest.TestCase):
    def setUp(self):
        authority=mock.patch.object(G,'check_authority',return_value={'permission_sha256':'fixture'});authority.start();self.addCleanup(authority.stop)
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.runtime=self.root/'runtime';self.runtime.mkdir()
        self.children=[];self.addCleanup(self.cleanup_children)
        self.git('init','-q');self.git('config','user.name','Fixture');self.git('config','user.email','fixture@localhost')
        (self.root/'.gitignore').write_text('runtime/\n')
        (self.root/'midnight.py').write_text('# restored controller\n');(self.root/'dashboard.py').write_text('# restored dashboard\n')
        (self.root/'projects.json').write_text('{}')
        self.git('add','.');self.git('-c','commit.gpgsign=false','commit','-qm','restorable generation');self.base=self.git('rev-parse','HEAD')
        (self.root/'midnight.py').write_text('# rejected controller\n');self.git('add','midnight.py');self.git('-c','commit.gpgsign=false','commit','-qm','rejected generation')
        self.bad=self.git('rev-parse','HEAD');self.old=self.child()
        self.fence=(json.dumps({'owner':'checked-installer','generation':'isolated-unique-fixture'},sort_keys=True)+'\n').encode()
        (self.runtime/'INSTALLING').write_bytes(self.fence)
        self.store=Actions(self.runtime)
        self.store.meta('install_health',{'previous':self.base,'installed':self.bad,'repair':'fixture-review','tree':self.git('rev-parse','HEAD^{tree}'),
            'state':'verifying','at':time.time()-200,'controller_sha256':self.sha('midnight.py'),'dashboard_sha256':self.sha('dashboard.py'),
            'installation_fence_sha256':hashlib.sha256(self.fence).hexdigest()},write=True)
        self.dashboard=None

    def cleanup_children(self):
        for child in self.children:
            if child.poll() is None:child.terminate()
        for child in self.children:
            try:child.wait(timeout=3)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)

    def child(self):
        child=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(120)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.children.append(child);lease=G.P.lease(child.pid)
        self.assertTrue(G.P.alive(lease));self.assertGreater(lease['birth_us'],0)
        return lease

    def git(self,*args):return subprocess.check_output(['/usr/bin/git',*args],cwd=self.root,text=True,stderr=subprocess.DEVNULL).strip()
    def sha(self,name):return hashlib.sha256((self.root/name).read_bytes()).hexdigest()

    def controller_restart(self):
        process=self.child()
        (self.runtime/'status.json').write_text(json.dumps({'updated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'phase':'installing','code_sha256':self.sha('midnight.py'),'process':process}))
        return True

    def dashboard_restart(self):self.dashboard=self.child();return True

    def response(self,*args,**kwargs):
        return io.BytesIO(json.dumps({'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'observation':{'available':True},
            'workflow':{'version':1},'dashboard_process':self.dashboard,'dashboard_code_sha256':self.sha('dashboard.py')}).encode())

    def evaluate(self,controller,dashboard):
        instant=dt.datetime.now(dt.timezone.utc)
        value={'active_window':True,'paused':False,'installing':True,'controller':{'updated_at':instant.isoformat(),
               'code_sha256':self.sha('midnight.py'),'phase':'needs_attention','process':self.old}}
        with mock.patch.object(G,'window',return_value=True),mock.patch.object(G,'restart_controller',side_effect=controller), \
             mock.patch.object(G,'restart_dashboard',side_effect=dashboard),mock.patch.object(G.urllib.request,'urlopen',side_effect=self.response):
            G.installation_health(self.root,value,instant)
        return self.store.meta('install_health')

    def test_restart_failure_preserves_fence_and_original_uncertain_intent(self):
        state=self.evaluate(lambda:False,lambda:False)
        self.assertEqual(self.git('rev-parse','HEAD'),self.base);self.assertFalse(state['finalized'])
        self.assertEqual((self.runtime/'INSTALLING').read_bytes(),self.fence);self.assertTrue(G.P.alive(self.old))
        intents=state['rollback_restart_intents'];controller=mock.Mock(side_effect=self.controller_restart);dashboard=mock.Mock(side_effect=self.dashboard_restart)
        state=self.evaluate(controller,dashboard)
        controller.assert_not_called();dashboard.assert_not_called()
        self.assertFalse(state['finalized']);self.assertEqual(state['rollback_restart_intents'],intents)
        self.assertEqual(state['rollback_waiting'],'uncertain_restart_controller')
        self.assertEqual((self.runtime/'INSTALLING').read_bytes(),self.fence);self.assertTrue(G.P.alive(self.old))

    def test_confirmed_native_replacements_release_the_exact_fence(self):
        state=self.evaluate(self.controller_restart,self.dashboard_restart)
        self.assertTrue(state['finalized']);self.assertFalse((self.runtime/'INSTALLING').exists());self.assertFalse(G.P.alive(self.old))
        self.assertEqual(state['restored_generation_proof']['source'],self.base)
        self.assertTrue(G.P.alive(state['restored_generation_proof']['controller']))
        self.assertTrue(G.P.alive(state['restored_generation_proof']['dashboard']['process']))

    def test_dashboard_restart_interruption_keeps_successful_controller_request(self):
        def timeout():raise subprocess.TimeoutExpired(['fixture-restart'],1)
        state=self.evaluate(self.controller_restart,timeout)
        self.assertTrue(state['restart_requests']['controller']);self.assertFalse(state['finalized'])
        self.assertEqual((self.runtime/'INSTALLING').read_bytes(),self.fence)
        controller=mock.Mock(side_effect=AssertionError('successful controller request must not replay'))
        dashboard=mock.Mock(side_effect=self.dashboard_restart);intents=state['rollback_restart_intents']
        state=self.evaluate(controller,dashboard)
        controller.assert_not_called();dashboard.assert_not_called();self.assertFalse(state['finalized'])
        self.assertTrue(state['restart_requests']['controller']);self.assertEqual(state['rollback_restart_intents'],intents)
        self.assertEqual(state['rollback_waiting'],'uncertain_restart_dashboard')
        self.assertEqual((self.runtime/'INSTALLING').read_bytes(),self.fence)

    def test_foreign_fence_after_failed_restart_is_preserved_without_more_effects(self):
        self.evaluate(lambda:False,lambda:False)
        foreign=b'{"owner":"later-operator","generation":"distinct"}\n';(self.runtime/'INSTALLING').write_bytes(foreign)
        controller=mock.Mock();dashboard=mock.Mock();state=self.evaluate(controller,dashboard)
        controller.assert_not_called();dashboard.assert_not_called();self.assertFalse(state['finalized'])
        self.assertEqual(state['rollback_waiting'],'foreign_or_replaced_fence');self.assertEqual((self.runtime/'INSTALLING').read_bytes(),foreign)
        self.assertTrue(G.P.alive(self.old))

    def test_saved_completion_recovers_interrupted_owned_fence_release(self):
        marker=G.D.marker
        def interrupted(path,present):
            if not present:raise OSError('failure after completion transaction, before fence release')
            return marker(path,present)
        with mock.patch.object(G.D,'marker',side_effect=interrupted):
            with self.assertRaisesRegex(OSError,'after completion transaction'):
                self.evaluate(self.controller_restart,self.dashboard_restart)
        self.assertTrue(self.store.meta('install_health')['finalized']);self.assertEqual((self.runtime/'INSTALLING').read_bytes(),self.fence)
        controller=mock.Mock();dashboard=mock.Mock();state=self.evaluate(controller,dashboard)
        controller.assert_not_called();dashboard.assert_not_called();self.assertTrue(state['finalized'])
        self.assertFalse((self.runtime/'INSTALLING').exists())

    def test_acknowledged_restart_without_fresh_processes_cannot_release_dispatch(self):
        state=self.evaluate(lambda:True,lambda:True)
        self.assertFalse(state['finalized']);self.assertEqual((self.runtime/'INSTALLING').read_bytes(),self.fence)
        self.assertEqual(state['rollback_waiting'],'fresh_restored_generation_process_proof')
        self.assertFalse(G.P.alive(self.old))


if __name__=='__main__':unittest.main()
