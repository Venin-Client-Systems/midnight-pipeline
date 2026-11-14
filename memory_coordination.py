"""Owned heavy-memory exclusion; admission never kills a foreign process."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time
import durability as D
import phase_runtime as P

MAX_NATIVE_RECEIPT_BYTES = 65536


def unresolved_native(runtime):
    """A new-protocol launch needs its exact positive cleanup before any reuse.

    This fence is independent of request identity and process-tree discovery.
    Legacy session directories without a launch intent retain their old rules.
    Each receipt is bounded; history is streamed without a lifetime quota.
    Unreadable evidence is uncertainty, never proof of drainage.
    """
    runtime = Path(runtime)
    sessions = runtime/'models/colibri/sessions'

    def read(path):
        cursor = path
        while cursor != runtime.parent:
            if cursor.is_symlink(): raise ValueError('Linked native launch evidence')
            cursor = cursor.parent
        fd = os.open(path, os.O_RDONLY|getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_NATIVE_RECEIPT_BYTES:
                raise ValueError('Native launch evidence exceeds its bound')
            raw = handle.read(MAX_NATIVE_RECEIPT_BYTES+1)
        if len(raw) > MAX_NATIVE_RECEIPT_BYTES: raise ValueError('Native evidence receipt exceeded its bound')
        value = json.loads(raw)
        if not isinstance(value, dict): raise ValueError('Invalid native launch evidence')
        return value, hashlib.sha256(raw).hexdigest()

    try:
        if not sessions.exists(): return sessions.is_symlink()
        if not sessions.is_dir() or sessions.is_symlink(): return True
        with os.scandir(sessions) as entries:
            for entry in entries:
                if entry.is_symlink(): return True
                if not entry.is_dir(follow_symlinks=False): continue
                folder = Path(entry.path); intent_path = folder/'native-launch-intent.json'
                if not intent_path.exists() and not intent_path.is_symlink(): continue
                intent, intent_sha = read(intent_path)
                for name in ('session', 'action_id', 'phase', 'input_key', 'boot_id'):
                    if not isinstance(intent.get(name), str) or not 0 < len(intent[name]) <= 256: return True
                for name in ('input_sha256', 'profile_sha256', 'admission_sha256'):
                    value = intent.get(name)
                    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value): return True
                model = intent.get('model')
                if (intent.get('version') != 1 or intent['session'] != folder.name or not isinstance(model, dict)
                        or model.get('parameters_sha256') != intent['profile_sha256']
                        or type(intent.get('created_at')) not in (int, float)
                        or not math.isfinite(intent['created_at']) or intent['created_at'] <= 0): return True
                proof, proof_sha = read(folder/'admission.json')
                if (proof_sha != intent['admission_sha256'] or proof.get('job') != intent['action_id']
                        or proof.get('phase') != intent['phase'] or proof.get('input_key') != intent['input_key']
                        or proof.get('boot_id') != intent['boot_id']
                        or proof.get('model_sha256') != P.fingerprint({k:v for k,v in model.items() if k!='promotion_generation'})):
                    return True
                closed, _ = read(folder/'unloaded.json')
                if (closed.get('owned_processes_drained') is not True
                        or closed.get('native_launch') not in {'created', 'creation_failed', 'not_attempted'}
                        or closed.get('native_launch_intent_sha256') != intent_sha
                        or any(closed.get(k) != intent[k] for k in ('model', 'session', 'action_id', 'phase'))):
                    return True
        return False
    except (OSError, ValueError, TypeError, KeyError):
        return True


def enabled(runtime):
    value = D.read_json(Path(runtime) / 'memory-coordination.json', {})
    return value.get('version') == 1 and value.get('enabled') is True


def surviving_execution(runtime, own_phase=None):
    """The durable execution fence outlives an abruptly killed flock owner."""
    store = P.Actions(runtime)
    if any(e['phase'] != own_phase and e['kind'] in {'local', 'host'}
           and P.live_tree(e['lease']) for e in store.executions()):
        return True
    if unresolved_native(runtime):
        return True
    backup = D.read_json(Path(runtime) / 'memory-backup-owner.json', {})
    # Missing completion after owner death is uncertain, not proof of drainage.
    return bool(backup and backup.get('state') != 'drained'
                and backup.get('owner', {}).get('boot_id') == D.boot_id())


def acquire_heavy(runtime, fd, deadline, *, stopped=lambda: False, waiting=lambda reason: None,
                  own_phase=None, poll=.25):
    """Reacquire the phase-owned descriptor through the unchanged safety fences."""
    runtime = Path(runtime)
    while True:
        if stopped() or time.time() >= deadline:
            raise P.PhaseInterrupted('Memory resource ownership expired or stopped')
        acquired = False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            with P.admission_lock(runtime, deadline=deadline):
                if not surviving_execution(runtime, own_phase):
                    return
        except BlockingIOError:
            pass
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        waiting('resource_heavy_memory')
        time.sleep(poll)


@contextmanager
def heavy(runtime, deadline, *, stopped=lambda: False, waiting=lambda reason: None,
          own_phase=None, poll=.25):
    runtime = Path(runtime)
    if not enabled(runtime):
        if unresolved_native(runtime):
            raise P.PhaseInterrupted('Unconfirmed native launch prevents heavy-memory admission')
        yield None
        return
    folder = runtime / 'resources'
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink():
        raise ValueError('Invalid memory lease directory')
    fd = os.open(folder / 'heavy-memory.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        acquire_heavy(runtime,fd,deadline,stopped=stopped,waiting=waiting,own_phase=own_phase,poll=poll)
        yield fd
    finally:
        os.close(fd)


def backup_blocked(root, instant):
    """Fail open for backups, fail closed at Midnight's separate admission gate."""
    root = Path(root)
    gate = root / 'runtime' / 'WEEKDAY_ACTIVATION_PENDING'
    if gate.exists() or gate.is_symlink():
        return True, 'midnight_activation_pending'
    if unresolved_native(root / 'runtime'):
        return True, 'native_cleanup_unconfirmed'
    if not enabled(root / 'runtime'):
        return False, 'coordination_disabled'
    try:
        import schedule_control as S
        import commissioning as C
        registry = D.read_json(root / 'projects.json', {})
        timing = S.window(instant, registry, {})
        upcoming = timing.get('next_start')
        if timing.get('active') or (upcoming and 0 <= (upcoming - instant).total_seconds() <= 300):
            return True, 'midnight_weekday_priority'
        if C.active(root / 'runtime', instant):
            return True, 'midnight_commissioning_priority'
        return False, 'outside_midnight_window'
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False, 'coordination_unavailable'
