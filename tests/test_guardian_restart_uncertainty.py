"""Real rollback source/health path with disposable native restart effects."""
import datetime as dt
import subprocess
import unittest
from unittest import mock

import guardian as G
import test_recovery_rollback_generation as F


class ReceiptPublicationLost(BaseException):pass


class GuardianRestartUncertaintyTests(unittest.TestCase):
    def setUp(self):
        self.f=F.RollbackGenerationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.effects=[];self.outcome=None;self.service='controller'
        self.original_run=subprocess.run

    def native_call(self,args,**kwargs):
        if args[:3]!=['/bin/launchctl','kickstart','-k']:
            return self.original_run(args,**kwargs)
        self.assertEqual(kwargs['timeout'],10)
        name='dashboard' if args[-1].endswith('-dashboard') else 'controller'
        self.effects.append(name)
        (self.f.controller_restart if name=='controller' else self.f.dashboard_restart)()
        if name==self.service:
            if isinstance(self.outcome,BaseException):raise self.outcome
            if self.outcome is False:return subprocess.CompletedProcess(args,1)
        return subprocess.CompletedProcess(args,0)

    def evaluate(self):
        f=self.f;instant=dt.datetime.now(dt.timezone.utc)
        value={'active_window':True,'paused':False,'installing':True,'controller':{
            'updated_at':instant.isoformat(),'code_sha256':f.sha('midnight.py'),
            'phase':'needs_attention','process':f.old}}
        with mock.patch.object(G,'window',return_value=True), \
             mock.patch.object(G.subprocess,'run',side_effect=self.native_call), \
             mock.patch.object(G.urllib.request,'urlopen',side_effect=f.response):
            G.installation_health(f.root,value,instant)
        return f.store.meta('install_health')

    def assert_replay_held(self):
        f=self.f;before=f.store.meta('install_health');intents=before['rollback_restart_intents']
        requests=before.get('restart_requests',{});effects=list(self.effects)
        self.assertTrue(intents);self.assertTrue(effects);self.outcome=None
        for _ in range(2):
            after=self.evaluate()
            self.assertEqual(self.effects,effects)
            self.assertEqual(after['rollback_restart_intents'],intents)
            self.assertEqual(after.get('restart_requests',{}),requests)
            self.assertFalse(after['finalized'])
            self.assertEqual(after['rollback_waiting'],'uncertain_restart_'+self.service)
            self.assertEqual((f.runtime/'INSTALLING').read_bytes(),f.fence)
            self.assertEqual(f.git('rev-parse','HEAD'),f.base)
            self.assertTrue(G.P.alive(f.old),'Unconfirmed rollback must not drain unrelated retained owner')
        with f.store.read() as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM wf_charges').fetchone()[0],0)

    def test_successful_restart_missing_receipt_never_repeats(self):
        original=type(self.f.store).meta
        def meta(store,key,*args,**kwargs):
            value=args[0] if args else {}
            if key=='install_health' and kwargs.get('write') and value.get('restart_requests',{}).get('controller'):
                raise ReceiptPublicationLost('successful native effect before acknowledgement publication')
            return original(store,key,*args,**kwargs)
        with mock.patch.object(type(self.f.store),'meta',new=meta),self.assertRaises(ReceiptPublicationLost):self.evaluate()
        self.assertEqual(self.effects,['controller']);self.assert_replay_held()

    def test_timeout_after_native_effect_never_repeats(self):
        self.outcome=subprocess.TimeoutExpired(['/bin/launchctl','kickstart'],10)
        self.evaluate();self.assertEqual(self.effects,['controller']);self.assert_replay_held()

    def test_nonzero_completion_after_native_effect_never_repeats(self):
        self.outcome=False;self.evaluate();self.assertEqual(self.effects,['controller']);self.assert_replay_held()

    def test_interrupted_completion_after_native_effect_never_repeats(self):
        self.outcome=InterruptedError('unknown native completion')
        self.evaluate();self.assertEqual(self.effects,['controller']);self.assert_replay_held()

    def test_dashboard_timeout_preserves_confirmed_controller_and_original_intents(self):
        self.service='dashboard';self.outcome=subprocess.TimeoutExpired(['/bin/launchctl','kickstart'],10)
        state=self.evaluate();self.assertTrue(state['restart_requests']['controller'])
        self.assertEqual(self.effects,['controller','dashboard']);self.assert_replay_held()

    def test_confirmed_restart_receipt_continues_without_restarting_service(self):
        original=type(self.f.store).meta;interrupted=[]
        def meta(store,key,*args,**kwargs):
            result=original(store,key,*args,**kwargs);value=args[0] if args else {}
            if (key=='install_health' and kwargs.get('write') and not interrupted
                    and value.get('restart_requests',{}).get('controller')):
                interrupted.append(True);raise ReceiptPublicationLost('after confirmed receipt publication')
            return result
        with mock.patch.object(type(self.f.store),'meta',new=meta),self.assertRaises(ReceiptPublicationLost):self.evaluate()
        intent=self.f.store.meta('install_health')['rollback_restart_intents']['controller']
        state=self.evaluate()
        self.assertEqual(self.effects,['controller','dashboard']);self.assertTrue(state['finalized'])
        self.assertEqual(state['rollback_restart_intents']['controller'],intent)
        self.assertFalse((self.f.runtime/'INSTALLING').exists());self.assertFalse(G.P.alive(self.f.old))
        self.assertEqual(state['restored_generation_proof']['source'],self.f.base)
        self.assertTrue(G.P.alive(state['restored_generation_proof']['controller']))
        self.assertTrue(G.P.alive(state['restored_generation_proof']['dashboard']['process']))


if __name__=='__main__':unittest.main()
