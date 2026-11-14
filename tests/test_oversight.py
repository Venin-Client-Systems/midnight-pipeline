import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import oversight as O
import midnight as M
import coordinator as K
import dashboard_details as DD

class Oversight(unittest.TestCase):
    def test_all_registered_tasks_are_explained_without_inference_or_runtime_writes(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);state_dir=root/'project';state_dir.mkdir()
            tasks=[{'id':i,'title':i} for i in ['done','retired','held','escalated','ready']]
            state={'done':['done'],'task_holds':{'held':{'reason':'Current source needs inspection'}}}
            (state_dir/'state.json').write_text(json.dumps(state));(state_dir/'coordinator-retired.json').write_text('{"retired":{"rationale":"Obsolete"}}')
            project={'id':'fixture','state_dir':str(state_dir)}
            scopes=[{'task_id':'escalated','admission':{'allowed':False,'state':'needs_codex','reason':'Concrete guidance required'}}]
            base={'projects':[{'id':'fixture','control':{'explanation':'Needs coordinator'},'repair_status':{'scopes':scopes}}], 'workflow':{'guardian':{'state':'observing'},'incidents':[]},'recent_sessions':[]}
            backend=mock.Mock();backend.all_tasks.return_value=tasks
            before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            with mock.patch.object(M,'STATE',root/'runtime'),mock.patch.object(M,'registry',return_value={'projects':[project]}),mock.patch.object(M.MM,'snapshot',return_value={}),mock.patch.object(M.CP.CloudPolicy,'provider_snapshot',return_value={}),mock.patch.object(K,'settings',return_value={'mode':'shadow'}),mock.patch.object(K,'decisions',return_value=[]),mock.patch.object(K,'project_backend',return_value=backend),mock.patch.object(M.AS,'task_done',return_value=False),mock.patch.object(DD,'inspect_request',return_value={'runs':[{'task_id':'ready','run':'saved','outcome':'failed'}]}):
                view=O.collect(copy.deepcopy(base))
            states={t['id']:t for t in view['projects'][0]['tasks']}
            self.assertEqual([states[t['id']]['state'] for t in tasks],['accepted','retired','held','waiting for coordinator','ready'])
            self.assertEqual(states['ready']['last_attempt']['run'],'saved')
            self.assertTrue(all(t['explain'] for t in states.values()))
            after={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            self.assertEqual(before,after);self.assertFalse((root/'runtime').exists())

    def test_unavailable_project_evidence_is_explicit(self):
        project={'id':'fixture','state_dir':'/missing-fixture'}
        base={'projects':[{'id':'fixture'}]}
        with mock.patch.object(M,'registry',return_value={'projects':[project]}),mock.patch.object(M.MM,'snapshot',return_value={}),mock.patch.object(M.CP.CloudPolicy,'provider_snapshot',return_value={}),mock.patch.object(K,'settings',return_value={'mode':'shadow'}),mock.patch.object(K,'decisions',return_value=[]),mock.patch.object(DD,'inspect_request',side_effect=ValueError('Unavailable receipts')):
            view=O.collect(base)
        self.assertIn('Unavailable receipts',view['projects'][0]['oversight_error'])

    def test_cli_outputs_the_same_document_before_any_control_write(self):
        import contextlib,io,sys
        fixture={'operation':{'happening':'Waiting for coordinator'},'projects':[]}
        out=io.StringIO()
        with mock.patch.object(sys,'argv',['midnight','oversight','--json']),mock.patch.object(M,'registry',return_value={'projects':[]}),mock.patch.object(O,'collect',return_value=fixture),mock.patch.object(M,'owner_action',side_effect=AssertionError('Read only')),contextlib.redirect_stdout(out):
            self.assertEqual(M.main(),0)
        self.assertEqual(json.loads(out.getvalue()),fixture)
