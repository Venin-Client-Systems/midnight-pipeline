"""Shared managed GLM admission, before charge and before native effects.

The phase guard retains all leases until its execution tree has drained. This
module does not grant model output authority or refund any recorded attempt.
"""
from contextlib import contextmanager, ExitStack
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import time

import durability as D
import phase_runtime as P
import memory_coordination as MC
import memory_recovery as MEM

SOURCES = ('memory_runtime.py', 'memory_admission.py', 'memory_coordination.py',
           'memory_recovery.py', 'phase_runtime.py', 'phase_guard.py', 'colibri_provider.py')


def pins():
    root = Path(__file__).resolve().parent
    return {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in SOURCES}


def save(path, value):
    D.atomic_write(path, (json.dumps(value, sort_keys=True, allow_nan=False)+'\n').encode())


def version_two(model):
    import colibri_provider as GP
    return bool(GP.matches(model) and GP.config().get('version') == 2)


def pin_maintenance_model(runtime, job_id, model):
    """Bind the saved local descriptor once; never repin a resumed action."""
    store = P.Actions(runtime)
    with store.write() as con:
        job = store.unpack(con.execute('SELECT * FROM wf_jobs WHERE id=?', (job_id,)).fetchone())
        if (not job or job['project'] != 'midnight' or not (job['payload'].get('maintenance') or job['payload'].get('continuation'))
                or job['state'] != 'running' or job['lease'] != P.lease() or time.time() >= job['deadline']):
            raise P.PhaseInterrupted('Local maintenance model needs its live action')
        payload = job['payload']
        if payload.get('continuation'):
            row=con.execute('SELECT * FROM mr_sessions WHERE id=?',(payload['continuation'],)).fetchone()
            saved=store.unpack(row)
            if (not saved or saved['state']!='running' or saved['lease']!=job['lease']
                    or saved['payload'].get('local_model')!=model):
                raise P.PhaseInterrupted('Maintenance descriptor differs from its original saved continuation')
        if payload.get('worker_model') is not None and payload['worker_model'] != model:
            raise ValueError('Original maintenance local model changed')
        if payload.get('worker_model') is None:
            payload['worker_model'] = model
            con.execute('UPDATE wf_jobs SET payload=? WHERE id=?', (json.dumps(payload, sort_keys=True), job_id))
            store.event(con, 'midnight', 'maintenance_local_model_pinned', job_id,
                        {'model_sha256': P.fingerprint(model)})
        return dict(job, payload=payload)


class Admission:
    def __init__(self, runtime, phase, model, key, deadline, stopped):
        self.runtime, self.phase, self.model, self.key = Path(runtime), phase, model, key
        self.deadline, self.stopped = deadline, stopped
        self.managed = version_two(model)
        self.gate = None

    def before_launch(self, charge=None):
        if self.managed:
            import colibri_provider as GP
            observation_monotonic = time.monotonic()
            observation = self.gate.observe()
            if self.stopped() or time.time() >= self.deadline or not observation['ready']:
                raise P.PhaseInterrupted('Memory admission changed before charge')
            GP.phase_profile(self.model)
            job = P.Actions(self.runtime).get(os.environ['MIDNIGHT_ACTION_ID'])
            if (not job or job['state'] != 'running' or not P.alive(job['lease'])
                    or job['payload'].get('worker_model') != self.model):
                raise P.PhaseInterrupted('Pinned local action changed before charge')
            phase=next((p for p in P.Actions(self.runtime).phases() if p['id']==self.phase),None)
            if (not phase or phase['state']!='running' or phase['job']!=job['id']
                    or time.time()>=min(job['deadline'],phase['deadline'])):
                raise P.PhaseInterrupted('Managed phase deadline or ownership changed before charge')
            import model_bench as MB
            proof = {'version': 1, 'job': job['id'], 'phase': self.phase, 'input_key': self.key,
                'model_sha256': P.fingerprint(MEM.resource_model(self.model)),
                'owner_deadline': job['deadline'], 'admitted_at': time.time(),
                'boot_id': D.boot_id(), 'quiet_observed_monotonic': observation_monotonic,
                'quiet': observation, 'sources': pins(), 'slot_name': P.SLOT_NAMES.get(P.SLOT_FD), 'locks': {}}
            for name, fd in (('gpu', P.GPU_FD), ('heavy', P.MEMORY_FD), ('local', P.SLOT_FD)):
                if fd is None: raise P.PhaseInterrupted('Managed admission lacks its inherited '+name+' lease')
                info = os.fstat(fd)
                proof['locks'][name] = {'device': info.st_dev, 'inode': info.st_ino}
            path = self.runtime/'phases'/self.phase/'memory-admission-proof.json'
            if path.exists() or path.is_symlink():
                raise P.PhaseInterrupted('Admission was already consumed; no silent replay')
            save(path, proof)
            MEM.reserve_recovery(self.runtime, self.key, self.model)
            current=next((p for p in P.Actions(self.runtime).phases() if p['id']==self.phase),None)
            if self.stopped() or not current or current['state']!='running' or time.time()>=current['deadline']:
                raise P.PhaseInterrupted('Managed controls changed while recording admission')
        if charge: charge()

    def environment(self, env):
        if not self.managed: return env
        return dict(os.environ if env is None else env,
                    MIDNIGHT_RESOURCE_INPUT_KEY=self.key, MIDNIGHT_MEMORY_ADMISSION_PHASE=self.phase)

    def finish(self, logs=(), *, succeeded=False):
        if not self.managed: return
        job = os.environ['MIDNIGHT_ACTION_ID']
        failure = next((f for path in logs if path and
                        (f := MEM.failure_from_events(self.runtime, path, self.key, self.model, job))), None)
        failure = failure or MEM.interrupted_failure(self.runtime, self.key, self.model, job)
        if failure: raise MEM.MemoryHeld(MEM.record(self.runtime, self.key, self.model, failure))
        if succeeded: MEM.complete(self.runtime, self.key, self.model)


@contextmanager
def admission(runtime, phase, model, key, deadline, *, stopped=lambda: False, waiting=lambda reason: None):
    value = Admission(runtime, phase, model, key, deadline, stopped)
    with ExitStack() as resources:
        if value.managed:
            import colibri_provider as GP
            import memory_admission as MA
            import model_bench as MB
            if not MC.enabled(runtime) or not key:
                raise P.PhaseInterrupted('Managed GLM requires verified coordination and a stable request identity')
            GP.phase_profile(model)
            job = P.Actions(runtime).get(os.environ.get('MIDNIGHT_ACTION_ID', ''))
            if (not job or job['state'] != 'running' or not P.alive(job['lease'])
                    or job['payload'].get('worker_model') != model or deadline > job['deadline']):
                raise P.PhaseInterrupted('Managed GLM requires its exact live pinned action and original deadline')
            MEM.check(runtime, key, model, quiet=True)
            heavy_fd=P.MEMORY_FD
            if heavy_fd is None:raise P.PhaseInterrupted('Managed GLM lacks its phase-owned heavy-memory descriptor')
            heavy_owned=True
            def park_heavy():
                nonlocal heavy_owned
                if heavy_owned:
                    fcntl.flock(heavy_fd,fcntl.LOCK_UN)
                    heavy_owned=False
            def reacquire_heavy():
                nonlocal heavy_owned
                if not heavy_owned:
                    MC.acquire_heavy(runtime,heavy_fd,deadline,stopped=stopped,waiting=waiting,own_phase=phase)
                    heavy_owned=True
            MB.GPU_LOCK.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(MB.GPU_LOCK, os.O_CREAT|os.O_RDWR|getattr(os, 'O_NOFOLLOW', 0), 0o600)
            handle = resources.enter_context(os.fdopen(fd, 'a'))
            previous = P.GPU_FD; P.GPU_FD = fd
            resources.callback(setattr, P, 'GPU_FD', previous)
            while True:
                if stopped() or time.time() >= deadline: raise P.PhaseInterrupted('GPU admission stopped before inference')
                try: fcntl.flock(handle, fcntl.LOCK_EX|fcntl.LOCK_NB); break
                except BlockingIOError:
                    park_heavy();waiting('resource_local');time.sleep(.25)
            reacquire_heavy()
            for cached in MB.api('/api/ps', timeout=5).get('models', []):
                MB.api('/api/generate', {'model': cached['name'], 'keep_alive': 0}, timeout=5)
            if MB.api('/api/ps', timeout=5).get('models'):
                raise P.PhaseInterrupted('Cached model memory has not drained')
            value.gate = MA.QuietGate()
            while True:
                if stopped() or time.time() >= deadline: raise P.PhaseInterrupted('Memory admission stopped before inference')
                observation = value.gate.observe()
                save(Path(runtime)/'phases'/phase/'memory-admission.json', observation)
                # No child/proof/reservation exists before this context yields.
                # Unsuitable observations must not reserve the host-check lane.
                # Keep the GPU owner unchanged; parked samples never count as
                # a quiet interval after another host action may have run.
                if not heavy_owned:
                    if observation['ready'] or observation.get('reason')=='quiet_window_pending':
                        reacquire_heavy()
                        value.gate=MA.QuietGate()
                        continue
                elif observation['ready']:
                    break
                elif observation.get('reason')!='quiet_window_pending':
                    park_heavy()
                    value.gate=MA.QuietGate()
                waiting('quiet_host_memory')
                P.Actions(runtime).phase_update(phase, 'running', progress=True)
                time.sleep(min(5, max(0, deadline-time.time())))
        yield value


def launch_fields(runtime, phase):
    path = Path(runtime)/'phases'/phase/'memory-admission-proof.json'
    if not path.exists(): return {}
    return {'memory_admission_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _read(path):
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('Unsafe managed admission evidence')
    return json.loads(path.read_text())


def fresh_quiet(proof):
    """Cross-process wall and monotonic bounds use the quiet gate's own gap."""
    import memory_admission as MA
    try:
        latest = proof['quiet']['latest']
        observed, admitted, monotonic = latest['at'], proof['admitted_at'], proof['quiet_observed_monotonic']
        if any(type(value) not in (int, float) or not math.isfinite(value)
               for value in (observed, admitted, monotonic)): return False
        boot = D.boot_id()
        wall_age, handoff_age = time.time()-observed, time.monotonic()-monotonic
        return (proof['quiet']['ready'] is True and proof['boot_id'] == latest['boot_id'] == boot
                and 0 <= wall_age <= MA.MAX_GAP_SECONDS and 0 <= handoff_age <= MA.MAX_GAP_SECONDS
                and 0 <= time.time()-admitted <= MA.MAX_GAP_SECONDS)
    except (KeyError, TypeError):
        return False


def validate_provider_start(root, descriptor, input_key, environ=None):
    """Require the exact guarded, still-owned admission before native effects."""
    import model_bench as MB
    import colibri_provider as GP
    env = os.environ if environ is None else environ
    runtime = Path(root)/'runtime'; identity = env.get('MIDNIGHT_MEMORY_ADMISSION_PHASE', '')
    if not identity or '/' in identity or identity in {'.', '..'}: raise ValueError('No managed memory admission')
    folder = runtime/'phases'/identity
    proof = _read(folder/'memory-admission-proof.json'); launch = _read(folder/'launch.json')
    store = P.Actions(runtime); job = store.get(env.get('MIDNIGHT_ACTION_ID', ''))
    phase = next((p for p in store.phases() if p['id'] == identity), None)
    execution = next((e for e in store.executions() if e['phase'] == identity), None)
    instant = time.time()
    if (not MC.enabled(runtime) or not job or not phase or not execution or job['state'] != 'running'
            or phase['state'] != 'running' or phase['kind'] != 'local' or phase['job'] != job['id']
            or execution['job'] != job['id'] or not P.alive(job['lease']) or not P.alive(execution['lease'])
            or any(phase['lease'].get(k) != execution['lease'].get(k) for k in ('pid','stamp','boot_id','birth_us','group')) or os.getpgrp() != execution['lease']['pid']
            or not any(p['pid'] == os.getpid() for p in P.descendants(execution['lease']))
            or instant >= min(job['deadline'], phase['deadline']) or P.control_stopped(runtime, job)
            or proof.get('version') != 1 or proof.get('job') != job['id'] or proof.get('phase') != identity
            or not input_key or proof.get('input_key') != input_key or env.get('MIDNIGHT_RESOURCE_INPUT_KEY') != input_key
            or proof.get('owner_deadline') != job['deadline'] or not fresh_quiet(proof)
            or proof.get('model_sha256') != P.fingerprint(MEM.resource_model(descriptor))
            or MEM.resource_model(job['payload'].get('worker_model')) != MEM.resource_model(descriptor)
            or proof.get('sources') != pins() or proof.get('quiet', {}).get('ready') is not True
            or proof.get('slot_name') != launch.get('slot_name') or launch.get('slot_name') != 'local-0.lock'
            or launch.get('memory_slot') != 'heavy-memory.lock' or launch.get('gpu_lease') is not True
            or launch.get('fresh_controls') is not True
            or launch.get('memory_admission_sha256') != hashlib.sha256((folder/'memory-admission-proof.json').read_bytes()).hexdigest()):
        raise ValueError('GLM native start lacks exact current guarded memory admission')
    GP.phase_profile(descriptor)
    for name, path in (('gpu', MB.GPU_LOCK), ('heavy', runtime/'resources/heavy-memory.lock'), ('local', runtime/'resources/local-0.lock')):
        fd = os.open(path, os.O_RDWR|getattr(os, 'O_NOFOLLOW', 0))
        try:
            info = os.fstat(fd)
            if proof['locks'].get(name) != {'device': info.st_dev, 'inode': info.st_ino}:
                raise ValueError('Admission lock identity changed')
            try: fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: pass
            else: raise ValueError('Admission lock no longer held by its guarded execution')
        finally: os.close(fd)
    if not fresh_quiet(proof):
        raise ValueError('Quiet-host admission expired during guarded handoff; a new quiet interval is required')
    return proof
