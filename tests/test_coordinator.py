"""Automated coordinator: bounded decisions on stalled escalations.

Design decision: auto-answer within bounds and notify every decision.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import coordinator as K
import progress_recovery as RP

MODEL = {'model': 'local-qwen38:context32', 'digest': 'd' * 64}
TASK = {'id': 'R005F', 'title': 'Load one workspace', 'files': ['pkg/workspace.py', 'tests/test_unit_workspace.py'], 'acceptance': ['loads']}


class RecoveryStoreCoordinatorProofTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.store = RP.Recovery(self.root / 'state', 'fixture', evidence_roots=[self.root])
        self.identity = RP.scope('fixture', 'R005F:1', TASK, 'a' * 40, 'profile')
        self.store.enroll(self.identity, TASK, MODEL, legacy_tries=2)
        self.record = self.root / 'state' / 'coordinator' / 'decision.json'; self.record.parent.mkdir(parents=True)
        self.record.write_text('{"answer":"guidance"}')

    def test_coordinator_advice_requires_independent_review_before_one_guided_launch(self):
        self.assertFalse(self.store.admission(self.identity, MODEL)['allowed'])
        self.store.offer_coordinator(self.identity, MODEL, 'a' * 32, 'Create tests/test_unit_workspace.py with real cases.', [RP.ref(self.record)])
        admitted = self.store.admission(self.identity, MODEL)
        self.assertFalse(admitted['allowed'])
        self.assertIn('real cases', self.store.coordinator_advice(self.identity,MODEL)['instruction'])
        review={'decision':'accept','issues':[]};path=self.root/'state'/'review.json';path.write_text(json.dumps(review))
        work=self.root/'state'/'work';source=work/'pkg/workspace.py';source.parent.mkdir(parents=True);source.write_text('def load():\n    return False\n')
        anchor=RP.source_anchor(work,TASK,{'path':'pkg/workspace.py','symbol':'load','evidence_quote':source.read_text()})
        self.store.offer_narrowing(self.identity,MODEL,obligation=0,instruction='Add the independently reviewed real cases.',refs=[RP.ref(path),anchor['source']],review=review,anchor=anchor,coordinator_advice_id='a'*32)
        admitted=self.store.admission(self.identity,MODEL)
        self.assertTrue(admitted['allowed']);self.assertEqual(admitted['reason'],'reviewed_narrowing')
        self.store.reserve(self.identity, 'run-1', MODEL)
        self.assertFalse(self.store.admission(self.identity, MODEL)['allowed'])

    def test_overrule_withdraws_only_an_unconsumed_proof(self):
        self.store.offer_coordinator(self.identity, MODEL, 'b' * 32, 'A concrete bounded instruction here.', [RP.ref(self.record)])
        self.assertEqual(self.store.revoke_coordinator('b' * 32), 1)
        self.assertFalse(self.store.admission(self.identity, MODEL)['allowed'])

    def test_unbounded_or_unbacked_guidance_is_refused(self):
        with self.assertRaises(RP.RecoveryHeld):self.store.offer_coordinator(self.identity, MODEL, 'not-hex', 'x' * 30, [RP.ref(self.record)])
        with self.assertRaises(RP.RecoveryHeld):self.store.offer_coordinator(self.identity, MODEL, 'c' * 32, '   ', [RP.ref(self.record)])
        outside = Path(tempfile.mkdtemp()) / 'x.json'; outside.write_text('{}')
        with self.assertRaises(RP.RecoveryHeld):self.store.offer_coordinator(self.identity, MODEL, 'c' * 32, 'A concrete bounded instruction.', [RP.ref(outside)])


class FakeBackend:
    def __init__(self, state_dir, scopes, tasks, retired=None):
        self.state_dir = state_dir; self.scopes = scopes; self.tasks = tasks; self.retired = retired or {}
        self.event = mock.Mock()
        self.store = mock.Mock(); self.store.snapshot.return_value = {'scopes': scopes}
        self.store.offer_coordinator.return_value = {'decision_id':'advice-1','admission':'independent_review_required'}; self.store.revoke_coordinator.return_value = 1

    def progress_store(self):return self.store
    def git(self, *args):return 'a' * 40
    def all_tasks(self, state):return self.tasks
    def coordinator_retired(self):return json.loads((self.state_dir / 'coordinator-retired.json').read_text()) if (self.state_dir / 'coordinator-retired.json').exists() else {}


def scope_item(task_id, *, state='needs_codex', head='a' * 40, allowed=False, kind='unknown'):
    scope = {'id': (task_id * 64)[:64].lower().replace('_', 'a'), 'project': 'exampleapp', 'episode': task_id + ':1', 'task_hash': 'h' * 64,
             'stage': 'task', 'head': head, 'profile': 'p'}
    return {'scope': scope, 'task_id': task_id, 'admission': {'allowed': allowed, 'state': state},
            'diagnosis': {'kind': kind, 'fingerprint': 'f' + task_id, 'next_action': 'Codex owns the retained diagnosis'},
            'historical_launches': 1, 'legacy_launches': 2}


class CoordinatorRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); (self.root / 'runtime').mkdir()
        self.state_dir = self.root / 'runtime' / 'projects' / 'exampleapp'; self.state_dir.mkdir(parents=True)
        (self.state_dir / 'state.json').write_text(json.dumps({'accepted': [{'id': 'DONE'}], 'feedback': {'R005F': 'NO TESTS RAN'}}))
        self.registry = {'projects': [{'id': 'exampleapp', 'on_schedule': True, 'path': str(self.root), 'state_dir': str(self.state_dir), 'why': 'x'}]}
        tasks = [dict(TASK), {'id': 'OLD', 'title': 'Stale', 'files': ['a.py'], 'acceptance': ['a']}, {'id': 'DONE', 'title': 'd', 'files': [], 'acceptance': []},
                 {'id': 'FINE', 'title': 'f', 'files': [], 'acceptance': []}, {'id': 'STALEHEAD', 'title': 's', 'files': [], 'acceptance': []}]
        scopes = [scope_item('R005F', kind='review'), scope_item('R005F', kind='older'), scope_item('OLD'), scope_item('DONE'),
                  scope_item('FINE', allowed=True), scope_item('STALEHEAD', head='b' * 40)]
        self.backend = FakeBackend(self.state_dir, scopes, tasks)
        self.answers = {'R005F': {'action': 'guidance', 'rationale': 'The test module exists but is empty.',
                                  'instruction': 'Add unittest cases to tests/test_unit_workspace.py covering valid load and symlink rejection.', 'owner_question': ''},
                        'OLD': {'action': 'retire', 'rationale': 'Superseded on 31 Oct; failure evidence is gone.', 'instruction': '', 'owner_question': ''}}
        self.asked = []
        def ask(runtime, directory, prompt, timeout):
            task = next(t for t in ('R005F', 'OLD') if '"id": "' + t + '"' in prompt)
            self.asked.append(task)
            return self.answers[task], {'provider': 'claude', 'attempts': [{'provider': 'claude', 'invocation_id': 'x'}]}
        for target, name, kwargs in [(K, 'project_backend', {'return_value': self.backend}), (K, 'ask', {'side_effect': ask}),
                                     (K, 'notify', {'return_value': {'helper_exit': 0}}), (K.MM, 'dispatch_descriptor', {'return_value': MODEL})]:
            p = mock.patch.object(target, name, **kwargs); self.mocks = getattr(self, 'mocks', {}); self.mocks[name] = p.start(); self.addCleanup(p.stop)

    def run_(self, mode, **kw):return K.run(self.root, mode=mode, registry=self.registry, **kw)

    def test_only_current_unfinished_escalations_are_considered_newest_first(self):
        found = K.escalations(self.registry['projects'][0], self.backend)
        self.assertEqual([e['task']['id'] for e in found], ['R005F', 'OLD'])
        self.assertEqual(found[0]['diagnosis']['kind'], 'review'); self.assertEqual(found[0]['launches'], 3)

    def test_shadow_decides_without_effects_or_notifications_and_is_idempotent(self):
        result = self.run_('shadow')
        self.assertEqual([d['action'] for d in result['decided']], ['guidance', 'retire'])
        self.backend.store.offer_coordinator.assert_not_called(); self.mocks['notify'].assert_not_called()
        self.assertFalse((self.state_dir / 'coordinator-retired.json').exists())
        self.assertEqual(self.run_('shadow')['decided'], []); self.assertEqual(len(self.asked), 2)

    def test_live_guidance_is_advisory_and_retire_parks_the_task_with_notifications(self):
        result = self.run_('live')
        guidance, retire = result['decided']
        args = self.backend.store.offer_coordinator.call_args.args
        self.assertEqual(args[1], MODEL); self.assertIn('symlink rejection', args[3])
        self.assertTrue(Path(args[4][0]['path']).is_file()); self.assertEqual(guidance['effect'], {'advice': {'decision_id':'advice-1','admission':'independent_review_required'},'coding_admitted':False})
        retired = json.loads((self.state_dir / 'coordinator-retired.json').read_text())
        self.assertIn('OLD', retired); self.assertEqual(self.mocks['notify'].call_count, 2)
        self.assertEqual(self.backend.event.call_count, 2)
        self.assertEqual(self.mocks['notify'].call_args_list[0].args[1], 'Advice saved for independent admission review · R005F')

    def test_run_and_daily_caps_bound_decisions(self):
        self.assertEqual(len(self.run_('live', limit=1)['decided']), 1)
        (self.root / 'runtime' / 'coordinator' / 'config.json').write_text(json.dumps({'max_per_day': 1}))
        self.assertEqual(self.run_('live')['decided'], [])

    def test_invalid_answer_is_recorded_as_an_error_without_effects(self):
        self.answers['R005F'] = {'action': 'merge', 'rationale': 'x', 'instruction': '', 'owner_question': ''}
        def bad(runtime, directory, prompt, timeout):
            raise ValueError('Coordinator answer does not match its schema')
        self.mocks['ask'].side_effect = bad
        result = self.run_('live', limit=1)
        self.assertIn('schema', result['decided'][0]['error']); self.backend.store.offer_coordinator.assert_not_called()

    def test_overrule_withdraws_guidance_and_restores_retired_tasks(self):
        decided = self.run_('live')['decided']
        self.assertEqual(K.overrule(self.root, decided[0]['decision_id'], self.registry)['withdrawn_proofs'], 1)
        self.assertTrue(K.overrule(self.root, decided[1]['decision_id'], self.registry)['restored'])
        self.assertNotIn('OLD', json.loads((self.state_dir / 'coordinator-retired.json').read_text()))

    def test_off_mode_does_nothing(self):
        self.assertEqual(self.run_('off')['decided'], []); self.assertEqual(self.asked, [])

    def assert_control_changed_during_answer(self,marker):
        original=self.mocks['ask'].side_effect
        def changed(*args):
            answer=original(*args)
            if marker=='pause':(self.state_dir/'PAUSED').touch()
            else:K.set_mode(self.root/'runtime','off')
            return answer
        self.mocks['ask'].side_effect=changed
        result=self.run_('live',limit=1)
        self.assertIn('effect withheld',result['decided'][0]['error'])
        self.backend.store.offer_coordinator.assert_not_called()

    def test_pause_during_cloud_answer_withholds_effects(self):
        self.assert_control_changed_during_answer('pause')

    def test_off_during_cloud_answer_withholds_effects(self):
        self.assert_control_changed_during_answer('off')

    def test_validation_rules(self):
        ok = {'action': 'guidance', 'rationale': 'why', 'instruction': 'Do this concrete bounded thing please.', 'owner_question': ''}
        self.assertEqual(K.validate(ok), ok)
        for bad in (dict(ok, action='merge'), dict(ok, instruction='short'), dict(ok, rationale=' '), {k: v for k, v in ok.items() if k != 'owner_question'}):
            with self.assertRaises(ValueError):K.validate(bad)


if __name__ == '__main__':
    unittest.main()
