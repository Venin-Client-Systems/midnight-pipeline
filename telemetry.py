"""Bounded, read-only Mac/Ollama observations, isolated from dashboard requests.

Only native counters, GET /api/ps and named host receipts are read. No inference,
privileged sampler, process control, command arguments or model text is exposed.
History is a fixed in-memory ring; opening more browser tabs adds no samplers.
"""
from __future__ import annotations

from collections import deque
import copy
import ctypes
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import threading
import time
import urllib.request
import colibri_observation as CO


MAX_TAIL = 65536
RUN_ID = re.compile(r'[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9_-]{1,100}')


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def read_json(path, default=None):
    path = Path(path)
    if path.is_symlink():
        return default
    try:
        if path.stat().st_size > 1024 * 1024:
            return default
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else default
    except (OSError, ValueError):
        return default


def memory_values(vm_text, sysctl_text):
    page = re.search(r'page size of (\d+) bytes', vm_text)
    if not page:
        raise ValueError('VM page size unavailable')
    size = int(page[1])
    pages = {key.strip('" '): int(value) for key, value in re.findall(r'^([^:\n]+):\s+(\d+)\.', vm_text, re.M)}
    total = re.search(r'hw.memsize:\s*(\d+)', sysctl_text)
    pressure = re.search(r'pressure_level:\s*(\d+)', sysctl_text)
    swap = re.search(r'vm.swapusage:.*?total = ([\d.]+)M\s+used = ([\d.]+)M', sysctl_text)
    if not total:
        raise ValueError('Physical memory unavailable')
    total = int(total[1])
    def count(key):
        return pages[key] * size if key in pages else None
    free, speculative = count('Pages free'), count('Pages speculative')
    occupied = max(0, min(total, total - free - speculative)) if free is not None and speculative is not None else None
    return {'total_bytes': total, 'occupied_bytes': occupied,
            'occupied_percent': round(occupied / total * 100, 2) if total and occupied is not None else None,
            'wired_bytes': count('Pages wired down'), 'compressed_bytes': count('Pages occupied by compressor'),
            'file_backed_bytes': count('File-backed pages'), 'free_bytes': free,
            'inactive_bytes': count('Pages inactive'),
            'pressure': {1: 'normal', 2: 'warning', 4: 'critical'}.get(int(pressure[1]) if pressure else None, 'unavailable'),
            'swap_used_bytes': round(float(swap[2]) * 1024**2) if swap else None,
            'swap_total_bytes': round(float(swap[1]) * 1024**2) if swap else None,
            'swap_in_counter': count('Swapins'), 'swap_out_counter': count('Swapouts')}


def gpu_values(raw):
    rows = plistlib.loads(raw)
    for row in rows:
        stats = row.get('PerformanceStatistics', {})
        if not stats:
            continue
        def percent(key):
            value = number(stats.get(key))
            return value if value is not None and value <= 100 else None
        model = row.get('model', 'Apple GPU')
        if isinstance(model, bytes):
            model = model.rstrip(b'\0').decode('utf-8', 'replace')
        return {'name': str(model)[:100], 'utilisation_percent': percent('Device Utilization %'),
                'renderer_percent': percent('Renderer Utilization %'), 'tiler_percent': percent('Tiler Utilization %'),
                'in_use_bytes': number(stats.get('In use system memory')),
                'allocated_bytes': number(stats.get('Alloc system memory')), 'scope': 'system_wide'}
    raise ValueError('GPU driver counters unavailable')


def model_values(document):
    output = []
    for model in document.get('models', [])[:8]:
        if not isinstance(model, dict):
            continue
        details = model.get('details') or {}
        output.append({'name': str(model.get('name', 'Unknown'))[:120],
                       'resident_bytes': number(model.get('size')), 'gpu_bytes': number(model.get('size_vram')),
                       'context_tokens': number(model.get('context_length')),
                       'parameters': str(details.get('parameter_size', ''))[:30],
                       'quantisation': str(details.get('quantization_level', ''))[:30],
                       'expires_at': str(model.get('expires_at', ''))[:80]})
    return output


def worker_tail(path):
    """Return only event categories and numeric usage, never model/tool content."""
    output = {'events': [], 'usage': None, 'updated_at': None}
    path = Path(path)
    if path.is_symlink():
        return output
    try:
        with path.open('rb') as handle:
            stat = os.fstat(handle.fileno())
            handle.seek(max(0, stat.st_size - MAX_TAIL))
            if stat.st_size > MAX_TAIL:
                handle.readline()
            raw = handle.read(MAX_TAIL)
        output['updated_at'] = dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat()
    except OSError:
        return output
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get('type')
        if kind == 'turn.completed' and isinstance(event.get('usage'), dict):
            usage = {key: number(event['usage'].get(key)) for key in ('input_tokens', 'cached_input_tokens', 'output_tokens')}
            if any(value is not None for value in usage.values()):
                output['usage'] = usage
        item = event.get('item')
        if kind not in ('item.started', 'item.completed') or not isinstance(item, dict):
            continue
        tool = item.get('type')
        labels = {'command_execution': 'Command', 'file_change': 'File edit', 'mcp_tool_call': 'Tool call',
                  'agent_message': 'Worker response', 'reasoning': 'Model response'}
        if tool not in labels:
            continue
        state = 'started' if kind == 'item.started' else 'completed'
        label = labels[tool]
        if tool == 'command_execution':
            command = item.get('command', '')
            if isinstance(command, str) and re.search(r'\b(unittest|pytest)\b', command):
                label = 'Test command'
            if item.get('exit_code') not in (None, 0):
                state = 'failed'
        if tool == 'file_change':
            changes = item.get('changes')
            if isinstance(changes, list):
                label = 'File edit (' + str(len(changes)) + ' files)'
        key = hashlib.sha256((str(item.get('id', ''))[:120] + str(tool) + state).encode()).hexdigest()[:16]
        output['events'].append({'id': key, 'label': label, 'state': state})
    output['events'] = output['events'][-8:]
    return output


class ProcessUsage(ctypes.Structure):
    _fields_ = [('uuid', ctypes.c_uint8 * 16)] + [(name, ctypes.c_uint64) for name in
        ('user', 'system', 'pkg', 'interrupt', 'pageins', 'wired', 'rss', 'footprint', 'start', 'exit',
         'child_user', 'child_system', 'child_pkg', 'child_interrupt', 'child_pageins', 'child_elapsed', 'read', 'write')]


class MacProbe:
    def __init__(self):
        if sys.platform != 'darwin':
            raise RuntimeError('Native Mac telemetry requires macOS')
        self.lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        self.lib.mach_host_self.restype = ctypes.c_uint32
        self.lib.host_statistics.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        self.lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        # mach_host_self returns a send right: release it after every sample.
        self.lib.mach_port_deallocate.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        self.task = ctypes.c_uint32.in_dll(self.lib, 'mach_task_self_').value
        self.thermal = None
        self.hardware_cache = None
        self.hardware_checked = None
        try:
            ctypes.CDLL('/System/Library/Frameworks/Foundation.framework/Foundation')
            objc = ctypes.CDLL('/usr/lib/libobjc.A.dylib')
            objc.objc_getClass.argtypes = [ctypes.c_char_p]; objc.objc_getClass.restype = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]; objc.sel_registerName.restype = ctypes.c_void_p
            message = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(('objc_msgSend', objc))
            process = message(objc.objc_getClass(b'NSProcessInfo'), objc.sel_registerName(b'processInfo'))
            integer = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(('objc_msgSend', objc))
            selector = objc.sel_registerName(b'thermalState')
            self.thermal = lambda: ('nominal', 'fair', 'serious', 'critical')[integer(process, selector)]
        except (OSError, ValueError, IndexError):
            pass

    @staticmethod
    def run(command):
        result = subprocess.run(command, capture_output=True, timeout=1.2, check=True)
        if len(result.stdout) > 2 * 1024**2:
            raise ValueError('Native observation exceeded bound')
        return result.stdout

    def cpu(self):
        values = (ctypes.c_uint32 * 4)(); count = ctypes.c_uint32(4)
        host = self.lib.mach_host_self()
        try:
            if self.lib.host_statistics(host, 3, ctypes.byref(values), ctypes.byref(count)) or count.value != 4:
                raise OSError('CPU counters unavailable')
            return list(values)
        finally:
            self.lib.mach_port_deallocate(self.task, host)

    def process(self, pid):
        value = ProcessUsage()
        if self.lib.proc_pid_rusage(pid, 2, ctypes.byref(value)):
            return None
        return {'cpu_ns': value.user + value.system, 'footprint_bytes': value.footprint,
                'resident_bytes': value.rss, 'read_counter': value.read, 'write_counter': value.write,
                'identity': value.start}

    def processes(self):
        raw = self.run(['/bin/ps', '-U', str(os.getuid()), '-o', 'pid=,ppid=,lstart=,comm=']).decode('utf-8', 'replace')
        output = {}
        for line in raw.splitlines()[:4096]:
            parts = line.split(None, 7)
            if len(parts) != 8:
                continue
            try:
                output[int(parts[0])] = {'parent': int(parts[1]), 'stamp': ' '.join(parts[2:7]),
                                        'name': Path(parts[7]).name[:100]}
            except ValueError:
                continue
        return output

    def memory(self):
        vm = self.run(['/usr/bin/vm_stat']).decode()
        sysctl = self.run(['/usr/sbin/sysctl', 'hw.memsize', 'vm.swapusage', 'kern.memorystatus_vm_pressure_level']).decode()
        return memory_values(vm, sysctl)

    def gpu(self):
        return gpu_values(self.run(['/usr/sbin/ioreg', '-a', '-r', '-c', 'AGXAccelerator', '-d', '1']))

    def hardware(self):
        # The private native API is isolated behind a real process timeout.
        # Only this sampler calls it; HTTP readers see the cached observation.
        instant = time.monotonic()
        if self.hardware_checked is None or instant - self.hardware_checked >= 10:
            self.hardware_checked = instant
            try:
                raw = self.run([sys.executable, '-I', '-B', str(Path(__file__).with_name('hardware_sensors.py'))])
                value = json.loads(raw)
                if not isinstance(value, dict) or type(value.get('available')) is not bool:
                    raise ValueError('Invalid native sensor observation')
                self.hardware_cache = value
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                self.hardware_cache = {'available': False, 'sampled_at': None, 'temperature': {}, 'fans': [],
                                       'reason': 'Native sensor observation unavailable: ' + type(exc).__name__}
        return copy.deepcopy(self.hardware_cache)

    def models(self):
        # Disable inherited environment proxies and redirects; only this fixed
        # local read endpoint is permitted. No generation or model-load endpoint.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open('http://127.0.0.1:11434/api/ps', timeout=1) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError('Model observation exceeded bound')
        return model_values(json.loads(raw))


def _stamp_valid(receipt, rows):
    if not isinstance(receipt, dict):
        return None
    pid = receipt.get('pid')
    if type(pid) is not int or pid not in rows or not receipt.get('stamp'):
        return None
    return pid if ' '.join(str(receipt['stamp']).split()) == rows[pid]['stamp'] else None


def _phase(log, fallback, worker_kind=None):
    parts = Path(str(log or '')).parts
    local_role=read_json(Path(str(log or '')).parent/'supervision-model.json',{}).get('role') if log else None
    if local_role in {'review','planning'}:
        return 'Independent local review' if local_role=='review' else 'Local task planning'
    if 'assessments' in parts:
        return 'Assessing product quality'
    if 'plans' in parts:
        return 'Planning the next task'
    if 'review' in parts or 'deep-review' in parts:
        return 'Reviewing a candidate in the cloud'
    if 'guidance' in parts:
        return 'Preparing cloud guidance'
    if any(re.fullmatch(r'check-\d+\.log', part) for part in parts):
        return 'Running host checks'
    if parts and parts[-1] in ('worker-events.jsonl', 'ralphy.log'):
        if worker_kind == 'cloud':
            return 'Cloud implementation active'
        return 'Local coding worker active' if worker_kind == 'local' or parts[-1] == 'ralphy.log' else 'Worker process active'
    return {'running': 'Project process active', 'awaiting_review': 'Waiting for review',
            'quality_assessed': 'Quality assessment complete'}.get(fallback, str(fallback or 'Waiting').replace('_', ' ')[:100])


class Telemetry:
    def __init__(self, registry, runtime, *, interval=2, max_samples=300, probe=None, clock=time.monotonic, wall=time.time):
        self.registry = registry; self.runtime = Path(runtime); self.interval = interval
        self.probe = probe; self.clock = clock; self.wall = wall
        # Explicit test/prototype probes retain direct observation. The installed
        # native sampler follows the source-bound commissioning gate.
        self.gate_hardware = probe is None
        self.hardware_source = None
        self.history = deque(maxlen=max_samples); self.lock = threading.Lock(); self.stopping = threading.Event()
        self.thread = None; self.previous = None; self.previous_processes = {}
        self.current = {'schema_version': 1, 'available': False, 'sampled_at': None, 'interval_seconds': interval,
                        'errors': ['Waiting for the first telemetry sample'], 'history': []}

    def start(self):
        self.thread = threading.Thread(target=self._loop, name='midnight-telemetry', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=2)

    def _loop(self):
        try:
            self.probe = self.probe or MacProbe()
            if self.gate_hardware:
                from hardware_release import source
                self.hardware_source=source(self.runtime.parent)
        except (OSError, RuntimeError, ValueError) as exc:
            with self.lock:
                self.current['errors'] = ['Native telemetry unavailable: ' + type(exc).__name__]
            return
        while not self.stopping.is_set():
            started = self.clock()
            try:
                self.sample()
            except Exception as exc:
                with self.lock:
                    self.current = {**self.current, 'available': False,
                                    'errors': ['Telemetry sample unavailable: ' + type(exc).__name__]}
            self.stopping.wait(max(.1, self.interval - (self.clock() - started)))

    def view(self):
        with self.lock:
            value = copy.deepcopy(self.current)
        value['age_seconds'] = max(0, self.wall() - value['sampled_unix']) if value.get('sampled_unix') is not None else None
        value['stale'] = value['age_seconds'] is None or value['age_seconds'] > max(8, self.interval * 3)
        return value

    def sample(self):
        start = self.clock(); instant = self.wall(); errors = []
        def observe(name, fn, default=None):
            try:
                return fn()
            except Exception as exc:
                errors.append(name + ': ' + type(exc).__name__)
                return default
        ticks = observe('CPU', self.probe.cpu)
        memory = observe('Memory', self.probe.memory, {})
        gpu = observe('GPU', self.probe.gpu, {})
        sensor_ready=True
        if self.gate_hardware:
            from hardware_release import ready
            sensor_ready=ready(self.runtime,self.hardware_source)
        hardware = (observe('Temperature/fans', self.probe.hardware, {}) if hasattr(self.probe, 'hardware') else {}) if sensor_ready else {
            'available':False,'sampled_at':None,'temperature':{},'fans':[],
            'reason':'Temperature and fan activation awaits four healthy hours on this recovery release'}
        models = observe('Ollama', self.probe.models)
        native_models = observe('Colibri', lambda: CO.snapshot(self.runtime, instant=instant),
                                {'available': False, 'state': 'unavailable', 'sessions': []})
        rows = observe('Processes', self.probe.processes, {})
        thermal = observe('Thermal state', self.probe.thermal) if self.probe.thermal else None
        previous = self.previous
        elapsed = start - previous['time'] if previous else None
        cpu_percent = None
        if ticks is not None and previous and previous['ticks'] is not None:
            delta = [(value - before) % 2**32 for value, before in zip(ticks, previous['ticks'])]
            cpu_percent = round((sum(delta) - delta[2]) / sum(delta) * 100, 2) if sum(delta) else None
        for key in ('in', 'out'):
            counter = memory.get('swap_' + key + '_counter')
            old = previous['memory'].get('swap_' + key + '_counter') if previous else None
            memory['swap_' + key + '_bytes_per_second'] = round((counter - old) / elapsed, 2) if (
                counter is not None and old is not None and elapsed and elapsed > 0 and counter >= old) else None
        pipeline = read_json(self.runtime / 'status.json', {})
        projects = []; selected = {}; run_keys = []
        global_child = read_json(self.runtime / 'active-child.json', {})
        global_pid = _stamp_valid(global_child, rows)
        if global_pid:
            selected[global_pid] = 'Project supervisor' if global_child.get('project', '').strip('_') not in ('cloud_maintenance', 'model_maintenance') else 'Model maintenance'
        configured = self.registry().get('projects', [])
        from action_store import observed_jobs
        jobs=observed_jobs(self.runtime)
        for project in configured[:32]:
            root = Path(project['state_dir'])
            if root.is_symlink():
                continue
            state = read_json(root / 'state.json', {})
            status = read_json(root / 'status.json', {})
            active = state.get('active_attempt') or state.get('pending') or {}
            task = active.get('task') or {}; run = active.get('run')
            pid = _stamp_valid(read_json(root / 'active-child.json', {}), rows)
            if pid:
                selected[pid] = 'Worker / review'
            supervisor_live=bool(global_pid and global_child.get('project')==project['id'])
            for job in jobs:
                owner=_stamp_valid(job.get('lease') or {},rows)
                if job['project']==project['id'] and owner:
                    selected[owner]='Project supervisor';supervisor_live=True
            tail = {'events': [], 'usage': None, 'updated_at': None}
            worker_kind = None
            if isinstance(run, str) and RUN_ID.fullmatch(run):
                folder = root / 'runs' / run
                if folder.resolve().parent == (root / 'runs').resolve() and not folder.is_symlink():
                    tail = worker_tail(folder / 'worker-events.jsonl')
                    worker_kind = read_json(folder / 'implementation-backend.json', {}).get('kind')
                    run_keys.append((project['id'], run))
            last = tail.get('updated_at')
            age = max(0, instant - dt.datetime.fromisoformat(last).timestamp()) if last else None
            stage = _phase(status.get('log'), status.get('phase'), worker_kind) if pid else 'Waiting'
            if not pid and state.get('pending'):
                stage = 'Waiting for review or GitHub delivery'
            from control_status import project_control
            control = project_control(status, state, instant=dt.datetime.fromtimestamp(instant,dt.timezone.utc),
                                      process_running=bool(pid), supervisor_running=supervisor_live,
                                      paused=(root/'PAUSED').exists())
            if not pid:
                stage = control['stage']
            last_task = str(task.get('title', ''))[:300]
            if control['state'] == 'blocked' and not pid:
                task = {}
            phase_elapsed = None
            if pid:
                try:
                    phase_elapsed = max(0, instant - dt.datetime.strptime(rows[pid]['stamp'], '%a %b %d %H:%M:%S %Y').timestamp())
                except ValueError:
                    pass
            projects.append({'id': project['id'], 'name': str(project['name'])[:120],
                             'task_id': str(task.get('id', ''))[:120], 'task': str(task.get('title', ''))[:300],
                             'stage': stage, 'control': control, 'last_task': last_task,
                             'process_running': bool(pid), 'phase_elapsed_seconds': phase_elapsed,
                             'last_worker_event_age_seconds': age, 'last_worker_event_at': last,
                             'events': tail['events'], 'usage': tail['usage']})
        # Include only the selected worker's descendants, model server/runners,
        # and this dashboard process. Never publish other applications or args.
        for _ in range(12):
            additions = {pid: 'Worker tool' for pid, row in rows.items() if row['parent'] in selected and pid not in selected}
            if not additions:
                break
            selected.update(additions)
            if len(selected) >= 48:
                break
        for pid, row in rows.items():
            if row['name'] in ('ollama', 'ollama_llama_server'):
                selected[pid] = 'Ollama server / inference'
        selected[os.getpid()] = 'Dashboard'
        processes = []; next_processes = {}
        for pid, role in list(selected.items())[:64]:
            usage = observe('Process ' + str(pid), lambda pid=pid: self.probe.process(pid))
            if usage is None:
                continue
            old = self.previous_processes.get(pid)
            same = old and old['identity'] == usage['identity'] and elapsed and elapsed > 0
            cpu = max(0, (usage['cpu_ns'] - old['cpu_ns']) / (elapsed * 1e9) * 100) if same else None
            rates = {key + '_bytes_per_second': max(0, (usage[key + '_counter'] - old[key + '_counter']) / elapsed) if same else None for key in ('read', 'write')}
            next_processes[pid] = usage
            processes.append({'pid': pid, 'role': role, 'name': rows.get(pid, {}).get('name', 'python'),
                              'cpu_percent': round(cpu, 2) if cpu is not None else None,
                              'footprint_bytes': usage['footprint_bytes'], 'resident_bytes': usage['resident_bytes'], **rates})
        processes.sort(key=lambda p: (p['role'] == 'Ollama server / inference', p['footprint_bytes']), reverse=True)
        # Preserve last completed numeric usage only within the same run.
        old_projects = {p['id']: p for p in self.current.get('projects', [])}
        old_runs = self.current.get('run_keys', [])
        for p in projects:
            if not p['usage'] and any((p['id'], run) in old_runs for identity, run in run_keys if identity == p['id']):
                p['usage'] = old_projects.get(p['id'], {}).get('usage')
        self.history.append({'at': instant, 'cpu_percent': cpu_percent, 'gpu_percent': gpu.get('utilisation_percent'),
                             'memory_percent': memory.get('occupied_percent'), 'swap_bytes': memory.get('swap_used_bytes'),
                             'cpu_celsius': hardware.get('temperature', {}).get('cpu_celsius'),
                             'gpu_celsius': hardware.get('temperature', {}).get('gpu_celsius'),
                             'fan_rpm': next((f.get('rpm') for f in hardware.get('fans', []) if f.get('rpm') is not None), None),
                             'pressure': memory.get('pressure')})
        self.previous = {'time': start, 'ticks': ticks, 'memory': memory}; self.previous_processes = next_processes
        value = {'schema_version': 1, 'available': bool(ticks is not None or memory or gpu or models is not None),
                 'sampled_at': dt.datetime.fromtimestamp(instant, dt.timezone.utc).isoformat(), 'sampled_unix': instant,
                 'interval_seconds': self.interval, 'sample_cost_ms': round((self.clock() - start) * 1000, 2),
                 'cpu': {'utilisation_percent': cpu_percent, 'logical_cores': os.cpu_count(),
                         'load_average': list(os.getloadavg()), 'thermal_state': thermal},
                 'memory': memory, 'gpu': gpu, 'hardware': hardware, 'models': models,
                 'native_models': native_models, 'processes': processes,
                 'projects': projects, 'pipeline_phase': pipeline.get('phase'), 'run_keys': run_keys,
                 'project_count': len(configured), 'projects_truncated': len(configured) > len(projects),
                 'process_count': len(selected), 'processes_truncated': len(selected) > 64,
                 'history': list(self.history), 'errors': errors,
                 'notes': {'memory': 'Occupied RAM includes cache. GPU and model allocations overlap unified RAM; do not add them.',
                           'gpu': 'GPU counters describe this Mac, including other apps. Cloud hardware is not exposed.',
                           'tokens': 'Token usage is reported only for completed worker turns. Live token/s is not exposed by this client.',
                           'process_cpu': '100% process CPU equals one logical core.',
                           'history': 'The last 10 minutes are held in memory; service restart clears the chart history.'}}
        with self.lock:
            self.current = value
