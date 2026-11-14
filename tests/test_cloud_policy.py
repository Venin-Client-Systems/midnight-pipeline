from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest import mock

import cloud_policy as C


def answer(descriptor, case, prompt):
    return {'decision': case['decision'], 'evidence': case['evidence'], 'reason': 'The cited host evidence establishes the decision.'}


class CloudPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = 2_000_000.
        self.cp = C.CloudPolicy(self.root / 'runtime', clock=lambda: self.now)

    def candidate(self, *, stronger=True):
        return {'descriptor': self.cp._candidate_descriptor('gpt-7-astra'),
                'official_stronger_successor': stronger,
                'official_evidence': [{'url': 'https://developers.openai.com/codex/changelog/',
                                       'digest': 'fixture', 'stronger': stronger}]}

    def stage(self):
        candidate = self.candidate()
        baseline = self.cp.qualify(self.cp.descriptor(), evaluator=answer)
        result = self.cp.qualify(candidate['descriptor'], evaluator=answer)
        return self.cp.stage_candidate(candidate, baseline, result)

    def invocation(self, purpose='review', **kwargs):
        return self.cp.prepare_invocation(self.root, purpose, self.root / 'out.json', **kwargs)

    def test_snapshot_and_static_policy_have_no_side_effects(self):
        with mock.patch.object(C.subprocess, 'run', side_effect=AssertionError('No network/model calls')):
            self.assertEqual(self.cp.snapshot()['active']['model'], 'gpt-6-astra')
            self.assertEqual(C.config_descriptor()['reasoning_effort'], 'ultra')
            self.assertEqual(C.policy()['max_depth'], 1)
        self.assertFalse(self.cp.runtime.exists())

    def test_profile_preserves_strongest_baseline_and_is_isolated(self):
        invocation = self.invocation()
        command = invocation['command']
        self.assertIn('--ignore-user-config', command)
        self.assertIn('--strict-config', command)
        self.assertEqual(command[command.index('--sandbox') + 1], 'read-only')
        self.assertIn('model_reasoning_effort="ultra"', command)
        self.assertIn('agents.enabled=false', command)
        projects = next(c for c in command if c.startswith('projects={'))
        for ancestor in (self.root.resolve(), *self.root.resolve().parents):
            self.assertIn(json.dumps(str(ancestor)) + '={trust_level="untrusted"}', projects)
        self.assertIn('features.shell_tool=false', command)
        self.assertIn('sandbox_workspace_write.network_access=false', command)
        self.assertIn('approval_policy="never"', command)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', command)
        self.assertIsNone(invocation['receipt']['provider_confirmed'])
        self.assertNotIn('ollama', json.dumps(invocation['descriptor']).lower())
        self.assertFalse((self.root / '.codex').exists())

    def test_implementation_has_tools_workspace_scope_and_no_command_network(self):
        command = self.invocation('implementation')['command']
        self.assertEqual(command[command.index('--sandbox') + 1], 'workspace-write')
        self.assertIn('features.shell_tool=true', command)
        self.assertIn('features.unified_exec=true', command)
        self.assertIn('sandbox_workspace_write.network_access=false', command)
        self.assertEqual(command[command.index('--cd') + 1], str(self.root.resolve()))
        self.assertNotIn('--add-dir', command)

    def test_two_children_same_profile_read_only_depth_one(self):
        parent = self.invocation('implementation')
        for i in range(2):
            child = self.cp.prepare_analysis_child(parent['invocation_id'], self.root, self.root / f'child{i}.json')
            self.assertEqual(child['descriptor'], parent['descriptor'])
            self.assertEqual(child['receipt']['sandbox'], 'read-only')
            self.assertEqual(child['receipt']['depth'], 1)
            with self.assertRaises(C.CloudPolicyError):
                self.cp.prepare_analysis_child(child['invocation_id'], self.root, self.root / 'grandchild.json')
        with self.assertRaises(C.CloudPolicyError):
            self.cp.prepare_analysis_child(parent['invocation_id'], self.root, self.root / 'third.json')
        with self.assertRaises(C.CloudPolicyError):
            self.invocation('implementation', parent_invocation_id=parent['invocation_id'])

    def test_corrupted_or_lower_effort_descriptors_are_refused(self):
        descriptor = self.cp.descriptor()
        descriptor['reasoning_effort'] = 'high'
        with self.assertRaises(C.CloudPolicyError):
            self.invocation(descriptor=descriptor)
        descriptor = self.cp.descriptor()
        descriptor['model'] = 'cheaper-fallback'
        with self.assertRaises(C.CloudPolicyError):
            self.invocation(descriptor=descriptor)

    def test_client_requested_echo_does_not_claim_provider_confirmation(self):
        invocation = self.invocation()
        events = '\n'.join([json.dumps({'type': 'session_meta', 'payload': {'model': 'gpt-6-astra', 'reasoning_effort': 'ultra'}}),
                            json.dumps({'type': 'item.completed', 'item': {'text': 'Provider used gpt-6-astra ultra'}})])
        result = self.cp.record_result(invocation['invocation_id'], events, 0)
        self.assertEqual(result['client_session_settings']['model'], 'gpt-6-astra')
        self.assertIsNone(result['provider_confirmed'])
        self.assertEqual(result['provider_confirmation_status'], 'not_exposed')

    def test_exposed_provider_settings_are_recorded_and_mismatch_rejected(self):
        invocation = self.invocation()
        events = json.dumps({'type': 'response.created', 'response': {'model': 'gpt-6-astra', 'reasoning': {'effort': 'ultra'}}})
        result = self.cp.record_result(invocation['invocation_id'], events, 0)
        self.assertEqual(result['provider_confirmed']['reasoning_effort'], 'ultra')
        invocation = self.invocation()
        events = json.dumps({'type': 'response.created', 'response': {'model': 'fallback', 'reasoning': {'effort': 'medium'}}})
        with self.assertRaises(C.CloudPolicyError):
            self.cp.record_result(invocation['invocation_id'], events, 0)
        self.assertIn('no fallback', self.cp.snapshot()['reason'])

    def test_successful_synthetic_runner_and_failure_have_durable_receipts(self):
        def fake(command, **kwargs):
            Path(command[command.index('--output-last-message') + 1]).write_text('{"ok":true}')
            self.assertEqual(kwargs['input'], 'review supplied evidence')
            return subprocess.CompletedProcess(command, 0, '', '')
        result = self.cp.run('review supplied evidence', self.root, 'review', runner=fake)
        self.assertEqual(json.loads(result['text']), {'ok': True})
        self.assertIsNone(result['receipt']['provider_confirmed'])
        def fail(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, '', 'requested model unavailable')
        with self.assertRaisesRegex(C.CloudPolicyError, 'no fallback'):
            self.cp.run('test', self.root, 'review', runner=fail)
        self.assertEqual(self.cp.descriptor()['model'], 'gpt-6-astra')

    def test_default_runner_resolves_at_call_time_for_host_control_and_tests(self):
        def fake(command, **kwargs):
            Path(command[command.index('--output-last-message') + 1]).write_text('synthetic')
            return subprocess.CompletedProcess(command, 0, '', '')
        with mock.patch.object(C.subprocess, 'run', side_effect=fake) as runner:
            result = self.cp.run('test fixture only', self.root, 'review')
        runner.assert_called_once()
        self.assertEqual(result['text'], 'synthetic')

    def test_qualification_cache_keys_include_complete_config_and_suite(self):
        result = self.cp.qualify(self.cp.descriptor(), evaluator=answer)
        self.assertEqual(len(result['results']), 8)
        self.assertTrue(result['all_mandatory_passed'])
        cached = self.cp.qualify(self.cp.descriptor(), evaluator=lambda *args: self.fail('Cache missed'))
        self.assertEqual(cached, result)
        other = self.cp.qualify(self.candidate()['descriptor'], evaluator=answer)
        self.assertNotEqual(other['key'], result['key'])
        with mock.patch.object(C, 'SUITE_VERSION', 'management-v2'):
            changed = self.cp.qualify(self.cp.descriptor(), evaluator=answer)
        self.assertNotEqual(changed['key'], result['key'])

    def test_all_mandatory_and_no_regression_gate(self):
        candidate = self.candidate()
        baseline = self.cp.qualify(self.cp.descriptor(), evaluator=answer)
        def bad(descriptor, case, prompt):
            value = answer(descriptor, case, prompt)
            if case['id'] == 'boundaries':
                value['decision'] = 'upload_secret'
            return value
        result = self.cp.qualify(candidate['descriptor'], evaluator=bad)
        self.assertFalse(self.cp.stage_candidate(candidate, baseline, result))
        self.assertIsNone(self.cp.snapshot()['pending'])

    def test_tied_scores_require_verified_official_stronger_successor(self):
        candidate = self.candidate(stronger=False)
        baseline = self.cp.qualify(self.cp.descriptor(), evaluator=answer)
        result = self.cp.qualify(candidate['descriptor'], evaluator=answer)
        self.assertFalse(self.cp.stage_candidate(candidate, baseline, result))
        candidate['official_stronger_successor'] = True
        candidate['official_evidence'][0].update(stronger=True, url='https://untrusted.example/announcement')
        self.assertFalse(self.cp.stage_candidate(candidate, baseline, result))

    def test_promotion_only_at_task_boundary_and_canary_rolls_back(self):
        self.assertTrue(self.stage())
        self.cp.promote(task_boundary=False)
        self.assertEqual(self.cp.descriptor()['model'], 'gpt-6-astra')
        self.cp.promote(task_boundary=True)
        self.assertEqual(self.cp.descriptor()['model'], 'gpt-7-astra')
        self.cp.record_decision('one', quality_ok=True, evidence='grounded review')
        self.cp.record_decision('one', quality_ok=True, evidence='replayed')
        self.assertEqual(self.cp.snapshot()['canary']['remaining'], 9)
        self.cp.record_decision('two', quality_ok=False, evidence='host identified unsupported claim')
        self.assertEqual(self.cp.descriptor()['model'], 'gpt-6-astra')
        self.assertTrue(self.cp.snapshot()['canary']['rolled_back'])

    def test_ten_decisions_complete_canary_and_old_pinned_decisions_do_not_count(self):
        previous = self.cp.descriptor()
        self.stage()
        self.cp.promote(task_boundary=True)
        self.cp.record_decision('old', quality_ok=False, evidence='older profile result', descriptor=previous)
        self.assertEqual(self.cp.snapshot()['canary']['remaining'], 10)
        for i in range(10):
            self.cp.record_decision(str(i), quality_ok=True, evidence='host verified decision')
        self.assertEqual(self.cp.snapshot()['canary']['remaining'], 0)
        self.assertEqual(self.cp.descriptor()['model'], 'gpt-7-astra')

    def test_discovery_requires_highest_catalog_effort_and_official_evidence(self):
        catalog = {'models': [{'slug': 'gpt-6-astra', 'supported_reasoning_levels': [{'effort': 'ultra'}]},
                              {'slug': 'gpt-7-astra', 'supported_reasoning_levels': [{'effort': 'ultra'}]},
                              {'slug': 'cheap', 'supported_reasoning_levels': [{'effort': 'high'}]}]}
        found = self.cp.discover(catalog=catalog, fetch=lambda _: 'gpt-7-astra is more capable than gpt-6-astra. cheap exists.')
        self.assertTrue(found['incumbent_available'])
        self.assertEqual(len(found['candidates']), 2)
        self.assertTrue(found['candidates'][0]['official_stronger_successor'])
        self.assertEqual(found['candidates'][1]['descriptor']['reasoning_effort'], 'high')
        self.assertFalse(found['candidates'][1]['official_stronger_successor'])
        reverse = self.cp.discover(catalog=catalog, fetch=lambda _: 'gpt-6-astra is more capable than gpt-7-astra.')
        self.assertFalse(reverse['candidates'][0]['official_stronger_successor'])
        negated = self.cp.discover(catalog=catalog, fetch=lambda _: 'gpt-7-astra is not stronger than gpt-6-astra.')
        self.assertFalse(negated['candidates'][0]['official_stronger_successor'])

    def test_qualified_stronger_successor_uses_its_highest_supported_max(self):
        catalog = {'models': [
            {'slug': 'gpt-6-astra', 'supported_reasoning_levels': [{'effort': 'ultra'}]},
            {'slug': 'gpt-7-astra', 'supported_reasoning_levels': [{'effort': e} for e in ('low', 'high', 'max')]},
        ]}
        discovery = self.cp.discover(catalog=catalog, fetch=lambda _: 'gpt-7-astra outperforms gpt-6-astra.')
        candidate = discovery['candidates'][0]
        self.assertEqual(candidate['descriptor']['reasoning_effort'], 'max')
        baseline = self.cp.qualify(self.cp.descriptor(), evaluator=answer)
        qualified = self.cp.qualify(candidate['descriptor'], evaluator=answer)
        self.assertTrue(self.cp.stage_candidate(candidate, baseline, qualified))
        self.cp.promote(task_boundary=True)
        invocation = self.invocation('implementation')
        self.assertIn('model_reasoning_effort="max"', invocation['command'])
        self.assertIn('agents.default_subagent_reasoning_effort="max"', invocation['command'])
        child = self.cp.prepare_analysis_child(invocation['invocation_id'], self.root, self.root / 'child.json')
        self.assertEqual(child['descriptor'], invocation['descriptor'])
        response = json.dumps({'type': 'response.created', 'response': {'model': 'gpt-7-astra', 'reasoning': {'effort': 'max'}}})
        self.assertFalse(self.cp.record_result(invocation['invocation_id'], response, 0)['profile_mismatch'])
        with self.assertRaises(C.CloudPolicyError):
            self.cp.record_result(child['invocation_id'], response.replace('max', 'high'), 0)

    def test_unknown_effort_requires_assessment_and_baseline_never_falls_back(self):
        catalog = {'models': [
            {'slug': 'gpt-6-astra', 'supported_reasoning_levels': [{'effort': 'high'}]},
            {'slug': 'gpt-future', 'supported_reasoning_levels': [{'effort': 'ultra'}, {'effort': 'new-unknown-level'}]},
        ]}
        found = self.cp.discover(catalog=catalog, fetch=lambda _: 'gpt-future outperforms gpt-6-astra.')
        self.assertFalse(found['incumbent_available'])
        self.assertEqual(found['candidates'], [])
        self.assertEqual(len(found['held']), 2)
        self.assertEqual(self.cp.descriptor()['reasoning_effort'], 'ultra')

    def test_rehashed_successor_still_cannot_lower_its_supported_effort(self):
        descriptor = self.cp._candidate_descriptor('gpt-7-astra', ['high', 'max'])
        descriptor['reasoning_effort'] = 'high'
        descriptor.pop('profile_hash')
        descriptor['profile_hash'] = C._hash(descriptor)
        with self.assertRaises(C.CloudPolicyError):
            self.invocation('qualification', descriptor=descriptor)
        baseline = self.cp.descriptor()
        baseline['reasoning_effort'] = 'high'
        baseline.pop('profile_hash')
        baseline['profile_hash'] = C._hash(baseline)
        with self.assertRaises(C.CloudPolicyError):
            self.invocation('qualification', descriptor=baseline)

    def test_daily_maintenance_promotes_and_reuses_discovery(self):
        catalog = {'models': [{'slug': m, 'supported_reasoning_levels': [{'effort': 'ultra'}]} for m in ['gpt-6-astra', 'gpt-7-astra']]}
        self.cp.maintenance(fetch=lambda _: 'gpt-7-astra outperforms gpt-6-astra.', catalog=catalog, evaluator=answer)
        self.assertEqual(self.cp.descriptor()['model'], 'gpt-7-astra')
        self.cp.maintenance(fetch=lambda _: self.fail('Not due'), catalog=catalog, evaluator=answer)

    def test_maintenance_holds_unproven_candidates_without_any_qualification_calls(self):
        models = ['gpt-6-astra', 'gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-6-sol', 'gpt-6-luna', 'gpt-5.6-sol']
        catalog = {'models': [{'slug': model, 'supported_reasoning_levels': [{'effort': 'ultra'}]}
                              for model in models]}
        active = self.cp.descriptor()
        with mock.patch.object(self.cp, 'qualify', side_effect=AssertionError('No candidate is eligible')) as qualifier, \
             mock.patch.object(self.cp, 'run', side_effect=AssertionError('No model calls permitted')) as runner, \
             mock.patch.object(C.subprocess, 'run', side_effect=AssertionError('Catalog is supplied')) as process:
            result = self.cp.maintenance(fetch=lambda _: 'Available models: ' + ', '.join(models), catalog=catalog)
        qualifier.assert_not_called()
        runner.assert_not_called()
        process.assert_not_called()
        self.assertEqual(result['active'], active)
        self.assertIsNone(result['pending'])
        self.assertIn('without qualification', result['reason'])
        self.assertIn('held for coordinator assessment', result['reason'])
        discovery = self.cp._load()['discovery']
        self.assertEqual(len(discovery['candidates']), len(models) - 1)
        self.assertTrue(all(candidate['official_stronger_successor'] is False for candidate in discovery['candidates']))
        self.assertEqual({held['model'] for held in discovery['held']}, set(models[1:]))
        self.assertFalse((self.cp.runtime / 'cloud-qualification').exists())

    def test_unavailable_or_failed_discovery_retains_incumbent_with_visible_reason(self):
        result = self.cp.maintenance(fetch=lambda _: 'release', catalog={'models': []})
        self.assertEqual(result['active']['model'], 'gpt-6-astra')
        self.assertIn('unavailable', result['reason'])
        self.now += 86401
        def failed(_):
            raise OSError('offline')
        catalog = {'models': [{'slug': 'gpt-6-astra', 'supported_reasoning_levels': [{'effort': 'ultra'}]}]}
        result = self.cp.maintenance(fetch=failed, catalog=catalog)
        self.assertIn('incomplete', result['reason'])
        self.assertEqual(result['active']['model'], 'gpt-6-astra')

    def test_deadline_defers_without_model_calls(self):
        result = self.cp.maintenance(cutoff=self.now + 1, fetch=lambda _: self.fail('Deadline ignored'))
        self.assertEqual(result['active']['model'], 'gpt-6-astra')
        with self.assertRaises(C.CloudPolicyError):
            self.cp.qualify(self.cp.descriptor(), cutoff=self.now + 1, evaluator=lambda *args: self.fail('Deadline ignored'))


class SupervisorEventTests(unittest.TestCase):
    def inventory(self, tool='list_mcp_resources', identity='inventory-1'):
        key = 'resources' if tool == 'list_mcp_resources' else 'resourceTemplates'
        item = {'id': identity, 'type': 'mcp_tool_call', 'server': 'codex', 'tool': tool,
                'arguments': {}, 'result': None, 'error': None, 'status': 'in_progress'}
        return [{'type': 'item.started', 'item': item},
                {'type': 'item.completed', 'item': dict(item, status='completed', result={
                    'content': [{'type': 'text', 'text': json.dumps({key: []})}], 'structured_content': None})}]

    def validate(self, events):
        return C.validate_supervisor_events('\n'.join(json.dumps(event) for event in events))

    def test_plain_text_and_interleaved_empty_native_inventory_pairs(self):
        resources, templates = self.inventory(), self.inventory('list_mcp_resource_templates', 'inventory-2')
        events = [{'type': 'thread.started', 'thread_id': 'fixture'}, {'type': 'turn.started'},
                  {'type': 'item.completed', 'item': {'id': 'message-1', 'type': 'agent_message', 'text': 'Reviewing supplied evidence.'}},
                  resources[0], templates[0], resources[1], templates[1],
                  {'type': 'item.completed', 'item': {'id': 'message-2', 'type': 'agent_message', 'text': '{}'}},
                  {'type': 'turn.completed', 'usage': {}}]
        self.assertEqual(self.validate(events), {'empty_inventory_calls': 2})
        self.assertEqual(self.validate([events[0], events[2], events[-1]]), {'empty_inventory_calls': 0})
        self.assertEqual(C.validate_supervisor_events('CLI diagnostic\n'), {'empty_inventory_calls': 0})

    def test_incomplete_duplicate_reordered_or_changed_identity_is_rejected(self):
        events = self.inventory()
        variants = [events[:1], events[1:], list(reversed(events)), [*events, events[1]],
                    [events[0], events[0], events[1]],
                    [events[0], {'type': 'item.completed', 'item': dict(events[1]['item'], id='other')}],
                    [events[0], {'type': 'item.completed', 'item': dict(events[1]['item'], tool='list_mcp_resource_templates')}],
                    [events[0], {'type': 'item.completed', 'item': {'id': 'inventory-1', 'type': 'agent_message', 'text': 'done'}}]]
        for events in variants:
            with self.subTest(events=events), self.assertRaises(C.CloudPolicyError):
                self.validate(events)

    def test_only_exact_builtin_empty_argument_calls_are_allowed(self):
        mutations = [{'server': 'connected-account'}, {'tool': 'read_mcp_resource'},
                     {'tool': 'mcp__codex__list_mcp_resources'}, {'arguments': {'server': 'codex'}},
                     {'arguments': {'cursor': ''}}, {'arguments': []}, {'arguments': None},
                     {'tool': []}, {'extra': 'metadata'}, {'error': 'failure'}]
        for mutation in mutations:
            events = self.inventory()
            events = [dict(event, item=dict(event['item'], **mutation)) for event in events]
            with self.subTest(mutation=mutation), self.assertRaises(C.CloudPolicyError):
                self.validate(events)

    def test_nonempty_malformed_or_enriched_results_are_rejected(self):
        results = [None, {}, {'content': [], 'structured_content': None},
                   {'content': [{'type': 'text', 'text': '{"resources":[{"uri":"private"}]}'}], 'structured_content': None},
                   {'content': [{'type': 'text', 'text': '{"resources":[],"nextCursor":"next"}'}], 'structured_content': None},
                   {'content': [{'type': 'text', 'text': '{"resources":[],"resources":[]}'}], 'structured_content': None},
                   {'content': [{'type': 'text', 'text': '{"resourceTemplates":[]}'}], 'structured_content': None},
                   {'content': [{'type': 'text', 'text': 'invalid'}], 'structured_content': None},
                   {'content': [{'type': 'text', 'text': '{"resources":[]}', 'annotations': {}}], 'structured_content': None},
                   {'content': [{'type': 'resource', 'resource': {'uri': 'private'}}], 'structured_content': None},
                   {'content': [{'type': 'text', 'text': '{"resources":[]}'}], 'structured_content': {'resources': []}},
                   {'content': [{'type': 'text', 'text': '{"resources":[]}'}], 'structured_content': None, 'isError': False}]
        for result in results:
            events = self.inventory()
            events[1]['item']['result'] = result
            with self.subTest(result=result), self.assertRaises(C.CloudPolicyError):
                self.validate(events)

    def test_success_status_and_start_null_result_are_required(self):
        for index, mutation in [(0, {'status': 'completed'}), (0, {'result': {}}),
                                (1, {'status': 'failed'}), (1, {'status': 'in_progress'}),
                                (1, {'error': {'message': 'failed'}})]:
            events = self.inventory()
            events[index]['item'].update(mutation)
            with self.subTest(index=index, mutation=mutation), self.assertRaises(C.CloudPolicyError):
                self.validate(events)
        events = self.inventory()
        events[1]['type'] = 'item.updated'
        with self.assertRaises(C.CloudPolicyError):
            self.validate(events)

    def test_all_other_action_and_unknown_item_types_are_rejected(self):
        for kind in ['command_execution', 'file_change', 'web_search', 'collab_tool_call',
                     'tool_search', 'computer_use', 'todo_list', 'future_unknown_tool']:
            with self.subTest(kind=kind), self.assertRaises(C.CloudPolicyError):
                self.validate([{'type': 'item.completed', 'item': {'id': 'tool', 'type': kind}}])

    def test_malformed_json_events_fail_closed(self):
        for raw in ['{"type":"item.started",', '[]', 'null', '{"type":"turn.started","type":"turn.completed"}',
                    '{"type":"item.completed","item":null}', '{"type":"future_tool_event"}']:
            with self.subTest(raw=raw), self.assertRaises(C.CloudPolicyError):
                C.validate_supervisor_events(raw)


if __name__ == '__main__':
    unittest.main()
