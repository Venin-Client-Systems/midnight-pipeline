"""Reviewed weekday activation remains a real fence through source adoption."""
import hashlib
import inspect
import json
import unittest

import coordinator_install as C
import maintenance_recovery as M
import test_recovery_coordinator_serialization as F


class ActivationGateOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.fixture=F.CoordinatorSerializationTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root;self.directory=self.fixture.directory
        self.intent=self.fixture.intent
        self.gate=self.root/'runtime/WEEKDAY_ACTIVATION_PENDING'
        self.value={'version':1,'owner':'fixture','generation':'d'*32,
                    'base':self.intent['previous'],'candidate_tree':self.intent['tree'],'plan_sha256':'e'*64}
        self.gate.write_text(json.dumps(self.value,sort_keys=True)+'\n')
        self.binding={'sha256':hashlib.sha256(self.gate.read_bytes()).hexdigest(),'plan_sha256':self.value['plan_sha256']}
        self.intent['bindings']['activation_gate']=self.binding
        M.save(self.directory/'installation-intent.json',self.intent)
        # The shared fixture simulates an already-armed generation. Preserve
        # that state while supplying the new producer's exact gate binding.
        health=self.fixture.f.actions.meta('install_health')
        health['activation_gate']=self.binding
        self.fixture.f.actions.meta('install_health',health,write=True)
        self.override=C.D.read_json(self.root/'runtime/override.json')

    def controls(self):
        # Allows the exact pre-fix module to exercise its real former path,
        # rather than producing an irrelevant new-argument TypeError.
        if 'activation_gate' in inspect.signature(C.live_controls).parameters:
            return C.live_controls(self.override,{},activation_gate=self.binding)
        return C.live_controls(self.override,{})

    def finish(self,**kwargs):
        return C.finish_installation(self.intent['bindings'],self.fixture.expected,self.controls,
                                     **self.fixture.options,**kwargs)

    def test_missing_gate_prevents_source_adoption(self):
        self.fixture.git('reset','--hard',self.intent['previous'])
        (self.directory/'installation-intent.json').unlink()
        self.gate.unlink()
        with self.assertRaisesRegex(RuntimeError,'controls changed during adoption'):
            C.adopt(self.intent['previous'],self.intent['installed'],self.intent['tree'],self.intent['bindings'],self.controls)
        self.assertEqual(self.fixture.git('rev-parse','HEAD'),self.intent['previous'])
        self.assertFalse((self.directory/'installation-intent.json').exists())

    def test_replaced_gate_after_restart_intent_prevents_restart_effect(self):
        fired=[]
        def checkpoint(stage):
            if stage=='service_restart_intent_recorded':
                fired.append(stage);self.gate.write_text(json.dumps({**self.value,'generation':'0'*32}))
        with self.assertRaisesRegex(RuntimeError,'before service restart effect'):
            self.finish(checkpoint=checkpoint)
        self.assertEqual(len(fired),1);self.assertEqual(self.fixture.calls['restart'],[])
        self.assertEqual((self.root/'runtime/INSTALLING').read_bytes(),self.fixture.expected)
        self.assertFalse((self.directory/'installation-complete.json').exists())

    def test_missing_gate_after_release_intent_prevents_fence_removal(self):
        fired=[]
        def checkpoint(stage):
            if stage=='fence_release_intent_persisted':fired.append(stage);self.gate.unlink()
        with self.assertRaisesRegex(RuntimeError,'after release intent before fence effect'):
            self.finish(checkpoint=checkpoint)
        self.assertEqual(fired,['fence_release_intent_persisted'])
        self.assertEqual((self.root/'runtime/INSTALLING').read_bytes(),self.fixture.expected)
        self.assertFalse((self.directory/'installation-complete.json').exists())

    def test_gate_replacement_before_health_registration_cannot_reuse_health(self):
        self.gate.write_text(json.dumps({**self.value,'generation':'0'*32}))
        previous=self.fixture.f.actions.meta('install_health')
        with self.assertRaisesRegex(RuntimeError,'activation gate changed'):
            C.register_generation_health(self.intent,self.fixture.expected)
        self.assertEqual(self.fixture.f.actions.meta('install_health'),previous)

    def test_valid_gate_completes_coordinator_but_remains_for_amendment(self):
        before=self.gate.read_bytes();result=self.finish()
        self.assertEqual(result['state'],'complete')
        self.assertFalse((self.root/'runtime/INSTALLING').exists())
        self.assertEqual(self.gate.read_bytes(),before)
        self.assertEqual(self.fixture.f.actions.meta('install_health')['activation_gate'],self.binding)
        self.assertEqual(len(self.fixture.calls['restart']),2)


if __name__=='__main__':unittest.main()
