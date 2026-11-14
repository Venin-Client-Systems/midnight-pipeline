"""Public observations also run against PR39: missing facts fail assertions.

No new-module import or fabricated baseline implementation is needed. All native
identities, model selection and files are temporary fixtures; no inference runs.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import midnight as N
import model_manager as M
import model_observation as O
import phase_runtime as P
import telemetry as T


class GLMObservationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name) / 'runtime'
        self.models = self.runtime / 'models'
        self.directory = self.models / 'colibri' / 'sessions' / ('a' * 32)
        self.directory.mkdir(parents=True)
        self.epoch = 1_900_000_000.
        self.lease = {'pid': 101, 'birth_us': 123456, 'boot_id': 'test-boot', 'stamp': 'same-second'}
        self.child = dict(self.lease, pid=102, birth_us=123457)
        self.processes = {item['pid']: dict(item, parent=1 if item['pid'] == 101 else 101, state='S')
                          for item in (self.lease, self.child)}
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for patch in (
            mock.patch.object(M, 'RUNTIME', self.models),
            mock.patch.object(M, 'snapshot', return_value={'active': {'model': 'qwen'}, 'selection_kind': 'qualified_incumbent'}),
            mock.patch.object(M, 'load', return_value={}),
            mock.patch.object(M.B, 'api', side_effect=AssertionError('Observation must not call models')),
            mock.patch.object(P.D, 'boot_id', return_value='test-boot'),
            mock.patch.object(P.sys, 'platform', 'darwin'),
            mock.patch.object(P.PO, 'info', side_effect=lambda pid: self.processes.get(pid)),
            mock.patch.object(P.PO, 'processes', side_effect=lambda: list(self.processes.values())),
            mock.patch('time.time', return_value=self.epoch),
        ):
            self.stack.enter_context(patch)
        self.save('process.json', {'gateway': self.lease, 'model': {'model': 'colibri/glm-5.3'}})
        self.save('memory.json', {'at': self.epoch, 'charged_bytes': 123456789})

    def save(self, name, value):
        path = self.directory / name
        path.write_text(json.dumps(value)); os.utime(path, (self.epoch, self.epoch))
        return path

    def stream(self, records):
        path = self.directory / ('b' * 32 + '-stream.jsonl')
        path.write_text(''.join(json.dumps(item) + '\n' for item in records))
        os.utime(path, (self.epoch, self.epoch))
        return path

    def native(self):
        return O.snapshot().get('native_activity', {})

    def session(self):
        rows = self.native().get('sessions', [])
        self.assertEqual(len(rows), 1, 'A birth-verified native session must be observable independently of Ollama')
        return rows[0]

    def test_selected_next_is_separate_from_birth_verified_native_model(self):
        view = O.snapshot()
        self.assertEqual(view.get('selected_next'), {'model': 'qwen'})
        session = self.session()
        self.assertEqual(session['model'], 'colibri/glm-5.3')
        self.assertEqual(session['native_descendants'], 1)
        self.assertEqual(session['memory']['charged_bytes'], 123456789)

    def test_owner_trial_selection_is_not_claimed_to_be_native_residency(self):
        self.processes.clear()
        M.snapshot.return_value = {'active': {'model': 'colibri/glm-5.3'}, 'selection_kind': 'owner_trial'}
        view = O.snapshot()
        self.assertEqual(view.get('display_phase'), 'owner_trial_selected')
        self.assertEqual(view.get('native_activity', {}).get('state'), 'not_observed')
        self.assertEqual(view['native_activity']['sessions'], [])

    def test_same_second_reused_pid_and_previous_boot_do_not_count_as_live(self):
        self.assertTrue(self.session()['birth_verified'])
        self.processes[101]['birth_us'] += 1
        self.assertEqual(self.native()['sessions'], [])
        self.processes[101]['birth_us'] -= 1
        self.save('process.json', {'gateway': dict(self.lease, boot_id='old-boot'), 'model': {'model': 'colibri/glm-5.3'}})
        self.assertEqual(self.native()['sessions'], [])

    def test_keepalives_are_not_generated_output_and_prefill_is_only_a_checkpoint(self):
        self.stream([{'elapsed_seconds': 10, 'record': {'choices': [{'delta': {}}]}}])
        log = self.directory / 'native.log'
        log.write_text('[prefill] layer 5/78 · 11795 token · +0.00s\n')
        os.utime(log, (self.epoch, self.epoch))
        self.save('b' * 32 + '-request.json', {'messages': ['private-prompt']})
        value = self.session()['progress']
        self.assertFalse(value['generated_output_observed'])
        self.assertEqual(value['answer_characters_in_tail'], 0)
        self.assertEqual(value['prefill']['last_logged_layer'], 5)
        self.assertEqual(value['prefill']['layer_scope'], 'current_chunk')
        self.assertEqual(value['prefill']['chunk_tokens'], 11795)
        self.assertNotIn('input_tokens',value['prefill'])
        self.assertIsNone(value['prefill']['request_progress_fraction'])
        self.assertFalse(value['prefill']['checkpoint_is_exact_current_layer'])
        self.assertIsNone(value['prefill']['checkpoint_at'])
        self.assertNotIn('private-prompt', json.dumps(value))

    def test_fresh_heartbeat_does_not_refresh_old_generated_output(self):
        self.stream([{'elapsed_seconds': 1, 'record': {'choices': [{'delta': {'content': 'secret-answer', 'reasoning_content': 'secret-reasoning'}}]}},
                     {'elapsed_seconds': 120, 'record': {'choices': [{'delta': {}}]}}])
        value = self.session()['progress']
        self.assertFalse(value['stream_stale'])
        self.assertEqual(value['state'], 'stale_output_observation')
        self.assertEqual(value['last_output_elapsed_seconds'], 1)
        self.assertEqual(value['output_seconds_behind_latest_record'], 119)
        self.assertFalse(value['output_wall_time_available'])
        self.assertIsNone(value['last_output_at'])
        self.assertNotIn('secret-', json.dumps(value))

    def test_stream_reads_are_bounded_and_counts_explicitly_cover_only_the_tail(self):
        self.stream([{'elapsed_seconds': i, 'record': {'choices': [{'delta': {'content': 'x'}}]}} for i in range(3000)])
        value = self.session()['progress']
        self.assertTrue(value['stream_tail_truncated'])
        self.assertLess(value['stream_records_in_tail'], 3000)
        self.assertEqual(value['answer_characters_in_tail'], value['stream_records_in_tail'])
        self.assertEqual(value['output_counts_scope'], 'bounded stream tail')

    def test_malformed_stream_values_do_not_hide_valid_numeric_observations(self):
        self.stream([None, [], {'record': []}, {'record': {'choices': {'wrong': 2}}},
                     {'record': {'choices': [{'delta': {'content': ['not', 'text']}}]}},
                     {'elapsed_seconds': 2, 'record': {'choices': [{'delta': {'content': 'abc'}}], 'usage': {'completion_tokens': 2, 'private': 'secret'}}}])
        value = self.session()['progress']
        self.assertEqual(value['answer_characters_in_tail'], 3)
        self.assertEqual(value['usage']['completion_tokens'], 2)
        self.assertNotIn('secret', json.dumps(value))

    def test_stale_and_future_samples_are_explicitly_unavailable(self):
        stream = self.stream([{'elapsed_seconds': 1, 'record': {'choices': [{'delta': {}}]}}])
        for stamp in (self.epoch - 60, self.epoch + 60):
            self.save('memory.json', {'at': stamp, 'charged_bytes': 999})
            os.utime(stream, (stamp, stamp))
            session = self.session()
            self.assertFalse(session['memory']['available'])
            self.assertIsNone(session['memory']['charged_bytes'])
            self.assertTrue(session['progress']['stream_stale'])

    def test_links_and_special_files_are_unavailable_without_following_or_waiting(self):
        memory = self.directory / 'memory.json'; memory.unlink(); os.mkfifo(memory)
        self.assertFalse(self.session()['memory']['available'])
        process = self.directory / 'process.json'; process.unlink()
        outside = self.runtime / 'private.json'; outside.write_text('{"secret":"not-public"}')
        process.symlink_to(outside)
        value = self.native()
        self.assertFalse(value.get('available'))
        self.assertEqual(value.get('state'), 'unavailable')
        self.assertNotIn('not-public', json.dumps(value))

    def test_truncated_session_inventory_does_not_claim_no_current_native_activity(self):
        self.processes.clear()
        for index in range(130):
            folder = self.directory.parent / f'{index:032x}'; folder.mkdir()
            (folder / 'process.json').write_text(json.dumps({'gateway': self.lease, 'model': {'model': 'colibri/glm-5.3'}}))
        value = self.native()
        self.assertTrue(value.get('truncated'))
        self.assertFalse(value['available'])
        self.assertEqual(value['state'], 'unavailable')

    def test_observation_never_mutates_receipts_or_starts_inference(self):
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.runtime.rglob('*') if p.is_file()}
        self.assertEqual(self.session()['state'], 'native_process_active')
        after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.runtime.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        M.B.api.assert_not_called()

    def test_owned_job_retains_glm_when_selection_changes_after_trial(self):
        project = {'id': 'one', 'name': 'One', 'on_schedule': True, 'path': str(self.runtime),
                   'why': 'fixture', 'instruction': 'fixture', 'local_model': 'qwen',
                   'state_dir': str(self.runtime / 'projects' / 'one'), 'config': 'config.json'}
        job = {'id': 'job-one', 'project': 'one', 'state': 'running', 'lease': self.lease,
               'deadline': self.epoch + 60, 'payload': {'worker_model': {'model': 'colibri/glm-5.3'}}}
        with mock.patch.object(N, 'STATE', self.runtime), mock.patch.object(N, 'queue', return_value={}), \
             mock.patch.object(N.AW, 'observed_jobs', return_value=[job]), \
             mock.patch.object(N.AW, 'observed_phases', return_value=[]), \
             mock.patch.object(N, 'improvement_snapshot', return_value={}), \
             mock.patch.object(N.QM, 'QualityMonitor'), mock.patch.object(N.RP, 'Recovery'), \
             mock.patch.object(N.AS, 'accepted_count', return_value=0), \
             mock.patch.object(N.CP, 'CloudPolicy') as cloud:
            cloud.return_value.descriptor.return_value = {'model': 'fixture', 'reasoning_effort': 'low'}
            view = N.project_snapshot(project)
            self.assertEqual(view.get('local_model'), 'colibri/glm-5.3')
            self.assertEqual(view.get('selected_next_model'), 'qwen')
            self.assertEqual(view.get('owned_action_model', {}).get('job'), 'job-one')
            self.processes[101]['birth_us'] += 1
            view = N.project_snapshot(project)
            self.assertEqual(view.get('local_model'), 'qwen')
            self.assertIsNone(view.get('owned_action_model'))

    def test_native_telemetry_survives_empty_ollama_inventory(self):
        probe = mock.Mock()
        probe.cpu.return_value = [1, 2, 3, 4]
        probe.memory.return_value = {}; probe.gpu.return_value = {}; probe.hardware.return_value = {}
        probe.models.return_value = []; probe.processes.return_value = {}; probe.thermal.return_value = 'nominal'
        probe.process.return_value = None
        sampler = T.Telemetry(lambda: {'projects': []}, self.runtime, probe=probe,
                              clock=lambda: 1., wall=lambda: self.epoch)
        sampler.sample(); view = sampler.view()
        self.assertEqual(view['models'], [])
        self.assertEqual(view.get('native_models', {}).get('state'), 'native_process_active')
        self.assertEqual(view['native_models']['sessions'][0]['model'], 'colibri/glm-5.3')


if __name__ == '__main__':
    unittest.main()
