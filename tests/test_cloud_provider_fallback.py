"""Owner-selected Claude/Codex provider chain with fallback on provider failure.

Design decision: Claude (Fable 5.2) main, Codex fallback, switchable.
Within one provider a silently substituted model is still rejected.
"""
import json
import tempfile
import unittest
from pathlib import Path

import cloud_policy as C

SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['decision'], 'properties': {'decision': {'type': 'string'}}}


def claude_stream(model='claude-fable-5-2', structured=None, error=None, tools=('StructuredOutput',), tool_use='StructuredOutput'):
    events = [{'type': 'system', 'subtype': 'init', 'model': model, 'tools': list(tools), 'mcp_servers': [], 'claude_code_version': '2.1'}]
    events.append({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': tool_use, 'input': {}}]}})
    result = {'type': 'result', 'subtype': 'error' if error else 'success', 'is_error': bool(error), 'modelUsage': {model: {}}}
    if error:result.update(api_error_status=429, result=error)
    else:result['structured_output'] = structured or {'decision': 'accept'}
    events.append(result)
    return '\n'.join(json.dumps(e) for e in events)


class ProviderFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.now = 2_000_000.
        self.cp = C.CloudPolicy(self.root / 'runtime', clock=lambda: self.now)
        (self.root / 'schema.json').write_text(json.dumps(SCHEMA))

    def test_default_is_claude_main_codex_fallback_and_selection_persists(self):
        self.assertEqual([C.provider_name(d) for d in self.cp.chain('review')], ['claude', 'codex'])
        self.cp.select('codex', 'dashboard')
        self.assertEqual([C.provider_name(d) for d in C.CloudPolicy(self.root / 'runtime').chain('review')], ['codex', 'claude'])
        with self.assertRaises(C.CloudPolicyError):self.cp.select('gemini')

    def test_claude_command_is_isolated_and_schema_bound(self):
        inv = self.cp.prepare_invocation(self.root, 'review', self.root / 'out.json', self.root / 'schema.json', C.claude_descriptor())
        cmd = inv['command']
        for flag in ('-p', '--strict-mcp-config', '--no-session-persistence', '--json-schema'):self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index('--tools') + 1], ''); self.assertEqual(cmd[cmd.index('--setting-sources') + 1], '')
        self.assertEqual(cmd[cmd.index('--model') + 1], 'claude-fable-5-2'); self.assertNotIn('--bare', cmd)
        self.assertEqual(json.loads(cmd[cmd.index('--json-schema') + 1]), SCHEMA)

    def test_claude_result_is_written_where_callers_read_it(self):
        inv = self.cp.prepare_invocation(self.root, 'review', self.root / 'out.json', self.root / 'schema.json', C.claude_descriptor())
        receipt = self.cp.record_result(inv['invocation_id'], claude_stream(structured={'decision': 'revise'}), 0)
        self.assertIsNone(receipt['failure']); self.assertEqual(json.loads((self.root / 'out.json').read_text()), {'decision': 'revise'})
        self.assertEqual(receipt['provider_confirmed']['model'], 'claude-fable-5-2')

    def test_claude_silent_model_substitution_is_rejected(self):
        inv = self.cp.prepare_invocation(self.root, 'review', self.root / 'out.json', self.root / 'schema.json', C.claude_descriptor())
        with self.assertRaisesRegex(C.CloudPolicyError, 'no silent substitution'):
            self.cp.record_result(inv['invocation_id'], claude_stream(model='claude-haiku-5-5'), 0)

    def test_limit_failure_falls_back_to_codex_and_opens_breaker(self):
        calls = []
        def execute(invocation, attempt):
            calls.append(invocation['descriptor']['provider'])
            if invocation['descriptor']['provider'] == 'anthropic':return {'code': 1, 'reason': 'exited'}, claude_stream(error='usage limit reached')
            (self.root / 'out.json').write_text('{"decision":"accept"}')
            return {'code': 0, 'reason': 'exited'}, ''
        outcome = self.cp.run_with_fallback(self.root, 'review', self.root / 'out.json', execute, schema_path=self.root / 'schema.json')
        self.assertTrue(outcome['ok']); self.assertEqual(outcome['provider'], 'codex'); self.assertEqual(calls, ['anthropic', 'openai'])
        self.assertEqual(outcome['attempts'][1]['fallback_of'], outcome['attempts'][0]['invocation_id'])
        self.assertEqual(self.cp.breakers()['claude']['kind'], 'limit')
        self.assertEqual([C.provider_name(d) for d in self.cp.chain('review')], ['codex', 'claude'])
        self.now += 1801
        self.assertEqual([C.provider_name(d) for d in self.cp.chain('review')], ['claude', 'codex'])

    def test_successful_primary_is_never_retried(self):
        def execute(invocation, attempt):return {'code': 0, 'reason': 'exited'}, claude_stream(structured={'decision': 'reject'})
        outcome = self.cp.run_with_fallback(self.root, 'review', self.root / 'out.json', execute, schema_path=self.root / 'schema.json')
        self.assertEqual(outcome['provider'], 'claude'); self.assertEqual(len(outcome['attempts']), 1)

    def test_owner_stop_does_not_spill_onto_the_fallback(self):
        def execute(invocation, attempt):return {'code': 143, 'reason': 'stopped'}, ''
        outcome = self.cp.run_with_fallback(self.root, 'review', self.root / 'out.json', execute, schema_path=self.root / 'schema.json')
        self.assertFalse(outcome['ok']); self.assertEqual(len(outcome['attempts']), 1)

    def test_claude_implementation_is_disabled_until_isolation_is_proven(self):
        self.assertEqual([C.provider_name(d) for d in self.cp.chain('implementation')], ['codex'])
        with self.assertRaisesRegex(C.CloudPolicyError, 'not enabled'):
            self.cp.prepare_invocation(self.root, 'implementation', self.root / 'out.txt', descriptor=C.claude_descriptor())

    def test_altered_claude_profile_is_rejected(self):
        altered = dict(C.claude_descriptor(), model='claude-haiku-5-5'); altered.pop('profile_hash')
        altered['profile_hash'] = C._hash(altered)
        with self.assertRaisesRegex(C.CloudPolicyError, 'owner-configured'):C.validate_descriptor(altered)

    def test_claude_event_validator_rejects_real_tool_calls(self):
        self.assertEqual(C.validate_supervisor_events(claude_stream())['structured_output_calls'], 1)
        with self.assertRaises(C.CloudPolicyError):C.validate_supervisor_events(claude_stream(tool_use='Bash'))
        with self.assertRaises(C.CloudPolicyError):C.validate_supervisor_events(claude_stream(tools=('StructuredOutput', 'Bash')))

    def test_breaker_honours_provider_stated_reset_time(self):
        import datetime as dt
        from zoneinfo import ZoneInfo
        now = dt.datetime(2026, 11, 13, 11, 30, tzinfo=ZoneInfo('Australia/Adelaide')).timestamp()
        self.assertEqual(C.reset_seconds("You've hit your usage limit ... try again at 4:01 PM.", now), 271 * 60 + 60)
        self.assertEqual(C.reset_seconds('limit; resets 5am', now) // 3600, 17)
        self.assertIsNone(C.reset_seconds('no time given', now))

    def test_missing_or_non_json_primary_output_uses_the_fallback(self):
        self.cp.select('codex')
        for value in (None,'not JSON'):
            calls=[]
            def execute(invocation,attempt):
                calls.append(C.provider_name(invocation['descriptor']))
                if calls[-1]=='codex':
                    if value is not None:(self.root/'out.json').write_text(value)
                    return {'code':0,'reason':'exited'},''
                return {'code':0,'reason':'exited'},claude_stream()
            result=self.cp.run_with_fallback(self.root,'review',self.root/'out.json',execute,schema_path=self.root/'schema.json')
            self.assertTrue(result['ok']);self.assertEqual(calls,['codex','claude'])
            self.assertEqual(result['attempts'][0]['failure']['kind'],'protocol')

    def test_failed_primary_output_cannot_be_mistaken_for_fallback_output(self):
        stale={'decision':'do not accept substituted result'}
        def execute(invocation,attempt):
            if attempt==0:return {'code':0,'reason':'exited'},claude_stream(model='claude-haiku-5-5',structured=stale)
            return {'code':0,'reason':'exited'},''
        result=self.cp.run_with_fallback(self.root,'review',self.root/'out.json',execute,schema_path=self.root/'schema.json')
        self.assertFalse(result['ok']);self.assertFalse((self.root/'out.json').exists())
        saved=list(self.root.glob('out.json.before-*'));self.assertEqual(len(saved),1)
        self.assertEqual(json.loads(saved[0].read_text()),stale)

    def test_failure_classes(self):
        self.assertEqual(C.classify_failure('Error: 429 Too Many Requests'), 'limit')
        self.assertEqual(C.classify_failure('Not logged in. Please run /login'), 'auth')
        self.assertEqual(C.classify_failure('segfault'), 'other')


if __name__ == '__main__':
    unittest.main()
