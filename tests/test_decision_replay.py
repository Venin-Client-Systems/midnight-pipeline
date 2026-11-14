import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import project_backend as B


class DecisionReplayTests(unittest.TestCase):
    def test_completed_exact_decision_survives_restart_without_another_call_or_allowance(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);backend=B.load_backend(root,'notes');backend.STATE=root/'state';backend.STATE.mkdir()
            backend.CLOUD_RUNTIME=root/'shared';directory=root/'decision';state={'cloud_calls_today':0}
            descriptor={'model':'fixture','profile_hash':'profile'}
            config={'max_cloud_calls_per_night':1,'cloud_model_descriptor':descriptor}
            manager=mock.Mock();manager.prepare_invocation.return_value={'command':['fake-codex'],'env':{},'descriptor':descriptor,'invocation_id':'a'*32}
            # Provider-chain interface: one provider, one attempt.
            manager.chain.return_value=[descriptor]
            def single(cwd,purpose,output,execute,**kwargs):
                invocation=manager.prepare_invocation.return_value;result,_=execute(invocation,0)
                return {'invocation':invocation,'result':result,'receipt':{},'attempts':[{'provider':'codex','invocation_id':'a'*32,'failure':None}],'ok':result['code']==0,'provider':'codex'}
            manager.run_with_fallback.side_effect=single
            expected={'decision':'accept','summary':'Exact checked behaviour','issues':[]}
            def invoke(args,cwd,log,seconds,cutoff,state,charge,**kwargs):
                charge();(cwd/'result.json').write_text(json.dumps(expected));log.write_text('{"type":"turn.completed"}\n')
                return {'code':0,'reason':'exited','seconds':1}
            cutoff=dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=2)
            with mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':'owned'}),mock.patch.object(backend.CP,'CloudPolicy',return_value=manager), \
                 mock.patch.object(backend,'git',return_value='head'),mock.patch.object(backend,'journal_charge'), \
                 mock.patch.object(backend,'charged_run',side_effect=invoke) as run,mock.patch.object(backend,'audit_cloud_decision'):
                self.assertEqual(backend.cloud('prompt',backend.REVIEW_SCHEMA,directory,cutoff,state,config),expected)
                self.assertEqual(state['cloud_calls_today'],1)
                self.assertEqual(backend.cloud('prompt',backend.REVIEW_SCHEMA,directory,cutoff,state,config),expected)
                self.assertEqual(run.call_count,1)
                (directory/'decision-receipt.json').unlink()
                self.assertEqual(backend.cloud('prompt',backend.REVIEW_SCHEMA,directory,cutoff,state,config),expected)
                self.assertEqual(run.call_count,1)
                with self.assertRaises(RuntimeError):backend.cloud('prompt',backend.REVIEW_SCHEMA,directory,cutoff,state,config,host_validate=lambda output:False)
                (directory/'result.json').write_text(json.dumps(dict(expected,summary='Changed bytes')))
                with self.assertRaises(RuntimeError):backend.cloud('prompt',backend.REVIEW_SCHEMA,directory,cutoff,state,config)
                self.assertEqual(run.call_count,1)


if __name__=='__main__':unittest.main()
