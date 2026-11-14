"""Actual host brief/consumer boundary rejects the PR42 five-field grant model."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from action_store import Actions
import commissioning as C
import midnight as M
import model_manager as MM
import project_backend as B


# Exact non-secret worker_model from the failed installed PR42 grant. No owner
# reason, task body, grant identity, or other live workflow data is copied here.
PR42_MODEL={'model':'colibri/glm-5.3','context':32768,'bytes':419316798141,
    'digest':'37479ea84567d275094cc3d0dd12c1f62aefc1674664e4e185656b29ab7b80cb',
    'parameters_sha256':'26844ac3f3db8fd2607261f64d9484dc60f8ed17cf3f8250396374363c980f53'}


class CommissioningDescriptor(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.runtime=self.root/'runtime'
        self.registry=json.loads((Path(M.__file__).parent/'projects.json').read_text())
        self.project=dict(self.registry['projects'][0],state_dir=str(self.root/'project'))
        self.registry['projects']=[self.project]
        state=Path(self.project['state_dir']);state.mkdir()
        (state/'state.json').write_text(json.dumps({'active_attempt':{'task':{'id':'retained','title':'Retained repair'}}}))
        self.instant=dt.datetime(2026,11,7,7,tzinfo=dt.timezone.utc)
        self.deadline=self.instant+dt.timedelta(hours=2)

    def direction(self,model):
        with patch.object(M,'STATE',self.runtime):
            brief=M.project_brief(self.project,model,{'id':'preflight-fixture'})
        path=self.root/'direction.json';path.write_text(json.dumps(brief))
        return path

    def activate(self,model):
        return C.activate(self.runtime,self.registry,self.project['id'],'retained',self.deadline,
                          'a'*40,model,'Owner-authorised temporary fixture',self.instant)

    def assert_unreserved(self):
        store=Actions(self.runtime)
        self.assertIsNone(store.meta(C.KEY));self.assertEqual(store.jobs(),[])
        self.assertEqual(store.phases(),[]);self.assertEqual(store.executions(),[])
        self.assertFalse(self.runtime.exists(),'Rejected activation must not create a workflow or charge ledger')

    def test_exact_pr42_model_is_rejected_by_consumer_and_before_any_grant(self):
        path=self.direction(PR42_MODEL)
        with self.assertRaisesRegex(ValueError,'host worker model descriptor'):B.managed_direction(path)
        with self.assertRaisesRegex(ValueError,'host worker model descriptor'):self.activate(PR42_MODEL)
        self.assert_unreserved()

    def test_real_dispatch_descriptor_crosses_brief_consumer_and_activation_without_inference(self):
        with patch.object(MM,'active',return_value=dict(PR42_MODEL)):
            model=MM.dispatch_descriptor()
        with patch.object(B.GP,'phase_profile',return_value={'worker_timeout_seconds':86400,'supervisor_timeout_seconds':86400}),\
             patch.object(B.GP.NativeSession,'start',side_effect=AssertionError('No inference')):
            direction=B.managed_direction(self.direction(model))
        self.assertEqual(direction['worker_model'],dict(PR42_MODEL,promotion_generation=None))
        grant=self.activate(direction['worker_model'])
        self.assertEqual(grant['worker_model'],model);self.assertEqual(grant['state'],'armed')
        self.assertIsNone(grant['job_id'])
        self.assertEqual(Actions(self.runtime).jobs(),[])
        self.assertEqual(Actions(self.runtime).phases(),[])
        self.assertEqual(Actions(self.runtime).executions(),[])

    def test_existing_descriptor_constraints_remain_identical_on_both_boundaries(self):
        valid=dict(PR42_MODEL,promotion_generation=None)
        for model in (dict(valid,bytes=True),dict(valid,context=16384),dict(valid,digest='bad'),
                      dict(valid,promotion_generation=1),dict(valid,unexpected=True),dict(valid,model='INVALID')):
            with self.subTest(model=model):
                with self.assertRaisesRegex(ValueError,'host worker model descriptor'):B.managed_direction(self.direction(model))
                with self.assertRaisesRegex(ValueError,'host worker model descriptor'):self.activate(model)
                self.assert_unreserved()

    def test_valid_non_glm_canary_identity_is_preserved_without_selection_change(self):
        model=dict(PR42_MODEL,model='local-coder:context32',promotion_generation='b'*32)
        direction=B.managed_direction(self.direction(model))
        grant=self.activate(direction['worker_model'])
        self.assertEqual(grant['worker_model'],model)
        self.assertEqual(grant['worker_model_hash'],C.fingerprint(model))

    def test_invalid_reactivation_cannot_change_existing_grant_or_create_an_action(self):
        self.activate(dict(PR42_MODEL,promotion_generation=None))
        before=Actions(self.runtime).meta(C.KEY)
        with self.assertRaisesRegex(ValueError,'host worker model descriptor'):self.activate(PR42_MODEL)
        self.assertEqual(Actions(self.runtime).meta(C.KEY),before)
        self.assertEqual(Actions(self.runtime).jobs(),[])

    def test_expired_failed_grant_keeps_its_job_outcome_without_mutation(self):
        model=dict(PR42_MODEL,promotion_generation=None);self.activate(model)
        store=Actions(self.runtime)
        job=C.reserve(self.runtime,self.project,{'worker_model':model,'source_head':'a'*40},self.instant)
        store.finish(job['id'],'failed',{'reason':'retained fixture failure'})
        before_grant=store.meta(C.KEY);before_job=store.get(job['id'])
        status=C.snapshot(self.runtime,self.deadline+dt.timedelta(seconds=1))
        self.assertEqual(status['state'],'expired');self.assertFalse(status['active'])
        self.assertEqual(status.get('job_state'),'failed')
        self.assertEqual(store.meta(C.KEY),before_grant);self.assertEqual(store.get(job['id']),before_job)


if __name__=='__main__':unittest.main()
