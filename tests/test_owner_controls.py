import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
import dashboard as D
import midnight as M
import coordinator as K

class OwnerControls(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.state=self.root/'runtime';self.state.mkdir()
        self.project={'id':'fixture','state_dir':str(self.state/'projects'/'fixture')}
        Path(self.project['state_dir']).mkdir(parents=True)
        self.data={'projects':[self.project]}
        for name,val in [('STATE',self.state),('ROOT',self.root)]:
            p=mock.patch.object(M,name,val);p.start();self.addCleanup(p.stop)
        p=mock.patch.object(M,'registry',return_value=self.data);p.start();self.addCleanup(p.stop)
        p=mock.patch.object(M,'kick');self.kick=p.start();self.addCleanup(p.stop)
        self.server=D.DashboardServer(0,provider=lambda:{})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    def post(self,action='pause',detail=None,headers=None,body=None):
        h={'Origin':'http://127.0.0.1:'+str(self.server.server_port),'X-Midnight-Token':self.server.token,'Content-Type':'application/json'}
        h.update(headers or {})
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        c.request('POST','/api/control',body=body if body is not None else json.dumps({'action':action,'detail':detail or {}}),headers=h)
        r=c.getresponse();result=r.status;r.read();c.close();return result

    def test_authentication_json_and_allowlist(self):
        for h in ({'X-Midnight-Token':''},{'X-Midnight-Token':'wrong'},{'Origin':'https://foreign.invalid'},{'Origin':''},{'Host':'localhost:'+str(self.server.server_port)}):
            self.assertEqual(self.post(headers=h),403)
        self.assertEqual(self.post(headers={'Content-Type':'text/plain'}),400)
        self.assertEqual(self.post(body='not json'),400)
        self.assertEqual(self.post('shell',{'command':'anything'}),400)
        self.assertEqual(self.post('pause',{'extra':'field'}),400)
        self.assertFalse((self.state/'PAUSED').exists())
        self.assertFalse((self.state/'owner-actions.jsonl').exists())

    def test_every_action_takes_effect_and_records_event(self):
        self.assertEqual(self.post(),200);self.assertTrue((self.state/'PAUSED').exists())
        self.assertEqual(self.post('resume'),200);self.assertFalse((self.state/'PAUSED').exists());self.kick.assert_called_once()
        with mock.patch.object(M.CP.CloudPolicy,'select',return_value={'primary':'codex'}) as select:
            self.assertEqual(self.post('set_primary_provider',{'primary':'codex'}),200);select.assert_called_once_with('codex','dashboard')
        path=Path(self.project['state_dir'])/'coordinator-retired.json';path.write_text('{"old":{"decision_id":"abc"}}')
        self.assertEqual(self.post('unretire_task',{'project':'fixture','task':'old'}),200);self.assertEqual(json.loads(path.read_text()),{})
        with mock.patch.object(K,'overrule',return_value={'withdrawn_proofs':1}) as over:
            self.assertEqual(self.post('overrule_decision',{'decision_id':'abc'}),200);over.assert_called_once()
        rows=[json.loads(x) for x in (self.state/'owner-actions.jsonl').read_text().splitlines()]
        self.assertEqual(len(rows),5);self.assertTrue(all(r['via']=='dashboard' for r in rows))
        self.assertEqual(len(M.AW.Actions(self.state).events()),5)
        self.assertNotEqual(self.server.token,D.secrets.token_urlsafe(32))

    def test_real_provider_selection_is_persisted_without_changing_the_worker(self):
        self.assertEqual(self.post('set_primary_provider',{'primary':'codex'}),200)
        self.assertEqual(json.loads((self.state/'cloud-selection.json').read_text())['primary'],'codex')
        self.assertEqual(self.post('set_primary_provider',{'primary':'claude'}),200)
        self.assertEqual(json.loads((self.state/'cloud-selection.json').read_text())['primary'],'claude')
        self.assertFalse((self.state/'models').exists())
