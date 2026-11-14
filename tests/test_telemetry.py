import datetime as dt
import json
import os
from pathlib import Path
import plistlib
import tempfile
import unittest

import telemetry as T


VM = '''Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free: 100.
Pages speculative: 50.
Pages inactive: 70.
Pages wired down: 500.
Pages occupied by compressor: 200.
File-backed pages: 120.
Swapins: 5.
Swapouts: 8.
'''
SYS = 'hw.memsize: 32768000\nvm.swapusage: total = 1024.00M  used = 512.50M free = 511.50M\nkern.memorystatus_vm_pressure_level: 2\n'
STAMP = 'Sun Nov 1 00:00:00 2026'


class Probe:
    thermal = staticmethod(lambda: 'nominal')

    def __init__(self):
        self.ticks = [100, 50, 100, 0]
        self.cpu_ns = 1_000_000_000
        self.identity = 1
        self.swap = 10

    def cpu(self): return self.ticks
    def memory(self): return {'total_bytes': 1000, 'occupied_percent': 80, 'occupied_bytes': 800,
                              'pressure': 'warning', 'swap_used_bytes': 50,
                              'swap_in_counter': self.swap, 'swap_out_counter': self.swap * 2}
    def gpu(self): return {'name': 'Apple fixture', 'utilisation_percent': 42}
    def models(self): return [{'name': 'fixture-model', 'resident_bytes': 800}]
    def processes(self):
        return {100: {'parent': 1, 'stamp': STAMP, 'name': 'ollama'},
                101: {'parent': 1, 'stamp': STAMP, 'name': 'python'},
                102: {'parent': 101, 'stamp': STAMP, 'name': 'codex'},
                999: {'parent': 1, 'stamp': STAMP, 'name': 'unrelated-private-app'}}
    def process(self, pid):
        return {'cpu_ns': self.cpu_ns, 'identity': self.identity, 'footprint_bytes': 100,
                'resident_bytes': 80, 'read_counter': self.cpu_ns, 'write_counter': self.cpu_ns}


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.runtime = self.root / 'runtime'; self.runtime.mkdir()
        self.project = self.runtime / 'projects/notes'; self.project.mkdir(parents=True)
        self.run = '20261101T000000Z-task-abcdef'
        self.folder = self.project / 'runs' / self.run; self.folder.mkdir(parents=True)
        def save(path, value): path.write_text(json.dumps(value))
        save(self.runtime / 'status.json', {'phase': 'working'})
        save(self.runtime / 'active-child.json', {'pid': 101, 'stamp': STAMP, 'project': 'notes'})
        save(self.project / 'state.json', {'active_attempt': {'run': self.run, 'task': {'id': 'R1', 'title': 'Repair the fixture'}}})
        save(self.project / 'status.json', {'phase': 'running', 'log': str(self.folder / 'worker-events.jsonl')})
        save(self.project / 'active-child.json', {'pid': 102, 'stamp': STAMP})
        save(self.folder / 'implementation-backend.json', {'kind': 'local'})
        self.registry = lambda: {'projects': [{'id': 'notes', 'name': 'Notes', 'state_dir': str(self.project)}]}
        self.now = 10.; self.wall = 1_800_000_000.; self.probe = Probe()
        self.sampler = T.Telemetry(self.registry, self.runtime, max_samples=3, probe=self.probe,
                                   clock=lambda: self.now, wall=lambda: self.wall)

    def advance(self): self.now += 2; self.wall += 2

    def test_native_memory_units_and_pressure_are_not_inferred_from_ram_percent(self):
        value = T.memory_values(VM, SYS)
        self.assertEqual(value['pressure'], 'warning')
        self.assertEqual(value['compressed_bytes'], 200 * 16384)
        self.assertEqual(value['inactive_bytes'], 70 * 16384)
        self.assertEqual(value['occupied_bytes'], 32768000 - 150 * 16384)
        self.assertEqual(value['swap_used_bytes'], int(512.5 * 1024**2))
        self.assertEqual(value['swap_in_counter'], 5 * 16384)
        self.assertEqual(T.memory_values(VM, SYS.replace('pressure_level: 2', 'pressure_level: 7'))['pressure'], 'unavailable')
        with self.assertRaises(ValueError): T.memory_values('invalid', SYS)

    def test_gpu_zero_is_a_real_measurement_but_missing_or_invalid_counter_is_unavailable(self):
        raw = plistlib.dumps([{'model': b'Apple fixture\0', 'PerformanceStatistics': {
            'Device Utilization %': 0, 'Renderer Utilization %': 105, 'In use system memory': 2048}}])
        value = T.gpu_values(raw)
        self.assertEqual(value['utilisation_percent'], 0)
        self.assertIsNone(value['renderer_percent'])
        self.assertEqual(value['in_use_bytes'], 2048)
        self.assertEqual(value['name'], 'Apple fixture')
        with self.assertRaises(ValueError): T.gpu_values(plistlib.dumps([]))

    def test_interval_cpu_swap_and_pid_reuse_and_bounded_history(self):
        self.sampler.sample(); first = self.sampler.view()
        self.assertIsNone(first['cpu']['utilisation_percent'])
        self.assertIsNone(first['processes'][0]['cpu_percent'])
        self.advance(); self.probe.ticks = [120, 60, 220, 0]; self.probe.cpu_ns += 400_000_000; self.probe.swap = 30
        self.sampler.sample(); value = self.sampler.view()
        self.assertEqual(value['cpu']['utilisation_percent'], 20)
        self.assertEqual(value['memory']['swap_in_bytes_per_second'], 10)
        self.assertTrue(all(p['cpu_percent'] == 20 for p in value['processes']))
        self.assertNotIn(999, [p['pid'] for p in value['processes']])
        self.advance(); self.probe.identity = 2; self.sampler.sample()
        self.assertTrue(all(p['cpu_percent'] is None for p in self.sampler.view()['processes']))
        for _ in range(10): self.advance(); self.sampler.sample()
        self.assertEqual(len(self.sampler.view()['history']), 3)

    def test_counter_wrap_and_partial_probe_failure_leave_other_readings_available(self):
        self.probe.ticks = [2**32-10, 10, 100, 0]; self.sampler.sample(); self.advance()
        self.probe.ticks = [10, 10, 120, 0]
        self.probe.gpu = lambda: (_ for _ in ()).throw(OSError('private failure detail'))
        self.sampler.sample(); value = self.sampler.view()
        self.assertEqual(value['cpu']['utilisation_percent'], 50)
        self.assertTrue(value['available']); self.assertEqual(value['gpu'], {})
        self.assertIn('GPU: OSError', value['errors'])
        self.assertNotIn('private failure detail', json.dumps(value))

    def test_private_prompts_commands_and_responses_never_enter_worker_feed(self):
        secret = 'synthetic-private-secret'
        events = [{'type': 'item.started', 'item': {'id': '1', 'type': 'command_execution', 'command': 'python -m unittest ' + secret}},
                  {'type': 'item.completed', 'item': {'id': '1', 'type': 'command_execution', 'command': secret, 'aggregated_output': secret, 'exit_code': 0}},
                  {'type': 'item.completed', 'item': {'id': '2', 'type': 'agent_message', 'text': secret}},
                  {'type': 'turn.completed', 'usage': {'input_tokens': 300, 'output_tokens': 40, 'private': secret}}]
        log = self.folder / 'worker-events.jsonl'; log.write_text('\n'.join(map(json.dumps, events)) + '\n')
        self.sampler.sample(); value = self.sampler.view(); project = value['projects'][0]
        self.assertEqual(project['stage'], 'Local coding worker active')
        self.assertEqual(project['usage']['output_tokens'], 40)
        self.assertNotIn(secret, json.dumps(value)); self.assertNotIn('aggregated_output', json.dumps(value))
        # After the numeric receipt falls outside the bounded tail, keep it only
        # for this run, never attribute another attempt's tokens to the new one.
        log.write_text('x' * 70000 + '\n'); self.advance(); self.sampler.sample()
        self.assertEqual(self.sampler.view()['projects'][0]['usage']['output_tokens'], 40)
        (self.project / 'state.json').write_text(json.dumps({'active_attempt': {'run': self.run + '2', 'task': {}}}))
        self.advance(); self.sampler.sample(); self.assertIsNone(self.sampler.view()['projects'][0]['usage'])

    def test_stale_pid_is_not_reported_as_a_running_worker_and_reads_do_not_mutate_runtime(self):
        (self.project / 'active-child.json').write_text(json.dumps({'pid': 999, 'stamp': 'old stamp'}))
        (self.runtime / 'active-child.json').write_text('{}')
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.runtime.rglob('*') if p.is_file()}
        self.sampler.sample(); value = self.sampler.view()
        self.assertFalse(value['projects'][0]['process_running'])
        self.assertNotIn(999, [p['pid'] for p in value['processes']])
        after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.runtime.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.wall += 9; self.assertTrue(self.sampler.view()['stale'])

    def test_model_metadata_is_bounded_and_does_not_copy_unneeded_fields(self):
        value = T.model_values({'models': [{'name': 'local', 'size': 20, 'size_vram': 18, 'context_length': 32768,
                               'details': {'quantization_level': 'Q4', 'parameter_size': '30B'}, 'private_prompt': 'secret'}] * 30})
        self.assertEqual(len(value), 8); self.assertNotIn('secret', json.dumps(value))
        self.assertEqual(value[0]['context_tokens'], 32768)

    def test_cloud_implementation_is_not_labelled_as_local_inference(self):
        (self.folder / 'implementation-backend.json').write_text('{"kind":"cloud"}')
        self.sampler.sample()
        self.assertEqual(self.sampler.view()['projects'][0]['stage'], 'Cloud implementation active')
        (self.project / 'status.json').write_text(json.dumps({'phase': 'running', 'log': str(self.folder / 'ralphy.log')}))
        (self.folder / 'implementation-backend.json').write_text('{"kind":"local"}')
        self.sampler.sample()
        self.assertEqual(self.sampler.view()['projects'][0]['stage'], 'Local coding worker active')

    def test_malformed_project_json_does_not_take_down_mac_resource_readings(self):
        (self.project / 'state.json').write_text('[]')
        self.sampler.sample(); value=self.sampler.view()
        self.assertTrue(value['available']); self.assertEqual(value['gpu']['utilisation_percent'],42)

    def test_sensor_readings_enter_cached_snapshot_and_history_and_partial_failure_is_isolated(self):
        self.probe.hardware = lambda: {'available': True, 'temperature': {'cpu_celsius': 51, 'gpu_celsius': 47},
                                       'fans': [{'name': 'Fan 1', 'rpm': 0}]}
        self.sampler.sample(); value = self.sampler.view()
        self.assertEqual(value['hardware']['temperature']['cpu_celsius'], 51)
        self.assertEqual(value['history'][-1]['cpu_celsius'], 51)
        self.assertEqual(value['history'][-1]['fan_rpm'], 0)
        self.probe.hardware = lambda: (_ for _ in ()).throw(OSError('private device detail'))
        self.advance(); self.sampler.sample(); value = self.sampler.view()
        self.assertTrue(value['available']); self.assertEqual(value['hardware'], {})
        self.assertIsNone(value['history'][-1]['cpu_celsius'])
        self.assertIn('Temperature/fans: OSError', value['errors'])
        self.assertNotIn('private device detail', json.dumps(value))


if __name__ == '__main__': unittest.main()
