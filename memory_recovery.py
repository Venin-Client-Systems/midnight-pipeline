"""Durable exact-request resource holds, separate from coding-quality failures."""
from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import time
import durability as D


ENVIRONMENTAL = {'host_pressure', 'native_startup_memory'}
FAILURE_KINDS = {'charged_limit', 'host_pressure', 'telemetry_unavailable',
                 'native_startup_memory', 'native_startup_failed'}


class MemoryHeld(RuntimeError):
    def __init__(self, record):
        self.record = record
        super().__init__('Memory resource wait: ' + record['kind'])


def resource_model(model):
    return {k: v for k, v in model.items() if k != 'promotion_generation'}


def identity(key, model):
    return hashlib.sha256(json.dumps({'input_key': key, 'model': resource_model(model)}, sort_keys=True).encode()).hexdigest()


@contextmanager
def locked(runtime, key, model):
    folder = Path(runtime) / 'memory-holds'
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink():
        raise ValueError('Invalid memory hold directory')
    name = identity(key, model)
    fd = os.open(folder / (name + '.lock'), os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield folder / (name + '.json')


def check(runtime, key, model, *, quiet=False, instant=None):
    instant = time.time() if instant is None else instant
    reconcile(runtime, key, model, instant=instant)
    with locked(runtime, key, model) as path:
        saved = D.read_json(path, {})
        if not saved or saved.get('state') == 'complete':
            return
        if saved['kind'] not in ENVIRONMENTAL or saved.get('state') == 'retry_reserved':
            if instant >= saved['retry_after']:
                checks=saved.get('observation_checks',0)+1
                saved=dict(saved,observation_checks=checks,retry_after=instant+(900 if checks==1 else 3600))
                D.save_json(path,saved)
            raise MemoryHeld(saved)
        if instant < saved['retry_after'] or not quiet:
            raise MemoryHeld(saved)


def reserve_recovery(runtime, key, model, *, instant=None):
    instant = time.time() if instant is None else instant
    reconcile(runtime, key, model, instant=instant)
    with locked(runtime, key, model) as path:
        saved = D.read_json(path, {})
        if not saved or saved.get('state') == 'complete':
            return
        if saved['kind'] not in ENVIRONMENTAL or saved.get('state') != 'waiting' or instant < saved['retry_after']:
            raise MemoryHeld(saved)
        D.save_json(path, dict(saved, state='retry_reserved', reserved_at=instant))


def _host_failure(folder, key, model, action_id=None):
    """Read an immutable provider intent, preferring its verified final receipt.

    These files are outside every worker's writable workspace. An event in a
    model response cannot supply or replace them. Cleanup is proven only by the
    provider's exact, hash-linked unload receipt, never by a vanished PID.
    """
    path = folder / 'failure-intent.json'
    if folder.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        return None
    raw = path.read_bytes()
    value = json.loads(raw)
    if (value.get('input_key') != key or value.get('model') != resource_model(model)
            or value.get('session') != folder.name
            or value.get('profile_sha256') != model.get('parameters_sha256')
            or value.get('kind') not in FAILURE_KINDS
            or not isinstance(value.get('action_id'), str) or not value['action_id']
            or (action_id is not None and value['action_id'] != action_id)
            or not re.fullmatch('[a-f0-9]{64}', value.get('input_sha256', ''))):
        return None
    intent_sha = hashlib.sha256(raw).hexdigest()
    intent = dict(value, receipt_path=str(path), receipt_sha256=intent_sha,
                  failure_id=intent_sha, drain={'owned_processes_drained': False})
    final_path = folder / 'failure.json'
    try:
        if final_path.is_symlink() or final_path.stat().st_size > 65536:
            return intent
        final_raw = final_path.read_bytes()
        final = json.loads(final_raw)
        if (final.get('failure_intent_sha256') != intent_sha
                or any(final.get(k) != v for k, v in value.items())):
            return intent
        if final.get('drain', {}).get('owned_processes_drained') is not True:
            return intent
        if final.get('drain', {}).get('owned_processes_drained') is True:
            unloaded = folder / 'unloaded.json'
            if unloaded.is_symlink() or unloaded.stat().st_size > 65536:
                return intent
            closed_raw = unloaded.read_bytes()
            closed = json.loads(closed_raw)
            if (hashlib.sha256(closed_raw).hexdigest() != final['drain'].get('unloaded_sha256')
                    or closed.get('owned_processes_drained') is not True
                    or closed.get('model') != resource_model(model)):
                return intent
        return dict(final, receipt_path=str(final_path), receipt_sha256=hashlib.sha256(final_raw).hexdigest(),
                    failure_id=intent_sha)
    except (OSError, ValueError, KeyError, TypeError):
        return intent


def reconcile(runtime, key, model, *, instant=None):
    """Recover failures before admission, regardless of their original job ID.

    The stable request/model identity crosses job and selection generations.
    Recording is idempotent across repeated scans and a late final drain receipt
    refines the original failure without charging another failure or retry.
    """
    sessions = Path(runtime) / 'models/colibri/sessions'
    if not sessions.exists():
        return None
    if not sessions.is_dir() or sessions.is_symlink():
        raise ValueError('Invalid provider failure evidence directory')
    receipts = []
    for folder in sessions.iterdir():
        try:
            receipt = _host_failure(folder, key, model)
            if receipt:
                receipts.append(receipt)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    result = None
    for receipt in sorted(receipts, key=lambda r: (r.get('observed_at', 0), r['session'])):
        result = record(runtime, key, model, receipt, instant=instant)
    return result


def failure_from_events(runtime, log, key, model, action_id):
    """Only verified host receipts can create a hold; model prose is not authority."""
    sessions = (Path(runtime) / 'models/colibri/sessions').resolve()
    if not Path(log).is_file():
        return None
    # Backend event files are already transport bounded; inspect a bounded tail.
    with Path(log).open('rb') as stream:
        stream.seek(max(0, Path(log).stat().st_size - 1024 * 1024))
        lines = stream.read().decode('utf-8', 'replace').splitlines()
    for line in reversed(lines):
        try:
            event = json.loads(line)
            if event.get('type') != 'model.failure':
                continue
            path = Path(event['receipt_path'])
            if path.is_symlink() or not path.resolve().is_relative_to(sessions) or path.name != 'failure.json':
                continue
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != event['receipt_sha256']:
                continue
            receipt = json.loads(raw)
            expected = {k: v for k, v in model.items() if k != 'promotion_generation'}
            if (receipt.get('input_key') != key or receipt.get('model') != expected
                    or receipt.get('action_id') != action_id or receipt.get('session') != path.parent.name
                    or receipt.get('kind') not in {'charged_limit', 'host_pressure', 'telemetry_unavailable', 'native_startup_memory', 'native_startup_failed'}):
                continue
            if receipt.get('profile_sha256') != expected.get('parameters_sha256') or not re.fullmatch('[a-f0-9]{64}',receipt.get('input_sha256','')):
                continue
            intent=path.parent/'failure-intent.json'
            if intent.is_symlink() or hashlib.sha256(intent.read_bytes()).hexdigest()!=receipt.get('failure_intent_sha256'):
                continue
            original=json.loads(intent.read_bytes())
            if any(receipt.get(k)!=v for k,v in original.items()):
                continue
            if receipt.get('drain',{}).get('owned_processes_drained') is True:
                unloaded=path.parent/'unloaded.json'
                if unloaded.is_symlink() or hashlib.sha256(unloaded.read_bytes()).hexdigest()!=receipt['drain'].get('unloaded_sha256'):
                    continue
                closed=json.loads(unloaded.read_bytes())
                if closed.get('owned_processes_drained') is not True or closed.get('model')!=expected:
                    continue
            return dict(receipt, receipt_path=str(path), receipt_sha256=event['receipt_sha256'])
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def interrupted_failure(runtime, key, model, action_id):
    """A crash cannot erase a bound host memory refusal before its final event.

    An intent without final drainage proof parks the exact input. It cannot
    authorise a retry merely because its native PID later disappears.
    """
    sessions=Path(runtime)/'models/colibri/sessions'
    if not sessions.is_dir() or sessions.is_symlink():return None
    matches=[]
    for folder in sessions.iterdir():
        try:
            value = _host_failure(folder, key, model, action_id)
            if value: matches.append(value)
        except (OSError,ValueError,TypeError):continue
    return max(matches,key=lambda x:x.get('observed_at',0)) if matches else None


def record(runtime, key, model, receipt, *, instant=None):
    instant = time.time() if instant is None else instant
    with locked(runtime, key, model) as path:
        previous = D.read_json(path, {})
        known_receipts = previous.get('previous_receipts', []) + ([previous['receipt_sha256']] if previous else [])
        failure_id = receipt.get('failure_id', receipt.get('failure_intent_sha256', receipt['receipt_sha256']))
        known_failures = previous.get('failure_ids', [])
        same_failure = failure_id in known_failures or (bool(previous) and failure_id == previous.get('receipt_sha256'))
        if (receipt['receipt_sha256'] in known_receipts
                or (same_failure and previous.get('state') == 'complete')):
            return previous
        count = previous.get('failures', 0) + (0 if same_failure else 1)
        kind = receipt['kind']
        if receipt.get('drain', {}).get('owned_processes_drained') is not True:
            kind = 'cleanup_unconfirmed'
        failures = dict(previous.get('failure_records', {}))
        if previous and not failures:
            failures[previous.get('failure_id', previous['receipt_sha256'])] = {
                'kind': previous['kind'], 'receipt_sha256': previous['receipt_sha256'],
                'receipt_path': previous['receipt_path']}
        failures[failure_id] = {'kind': kind, 'receipt_sha256': receipt['receipt_sha256'],
                                'receipt_path': receipt['receipt_path']}
        # A newer pressure failure cannot erase an older structural failure or
        # unconfirmed cleanup. A late, exact final receipt may refine only its
        # own original intent, retaining every other outstanding refusal.
        structural = [r for r in failures.values() if r['kind'] not in ENVIRONMENTAL]
        if structural:
            kind = structural[0]['kind']
        delay = min(240, 30 * 2 ** min(count - 1, 3)) * 60 if kind in ENVIRONMENTAL else 300
        saved = {'version': 1, 'input_key': key, 'model': model, 'state': 'waiting', 'kind': kind,
                 'failures': count, 'recorded_at': instant, 'retry_after': instant + delay,
                 'receipt_sha256': receipt['receipt_sha256'], 'receipt_path': receipt['receipt_path'],
                 'failure_id': failure_id, 'failure_ids': list(dict.fromkeys(known_failures + [failure_id])),
                 'failure_records': failures,
                 'previous_receipts': known_receipts,
                 'recovery': 'quiet_host_after_backoff' if kind in ENVIRONMENTAL else 'verified_changed_profile_or_workload_required'}
        D.save_json(path, saved)
        return saved


def complete(runtime, key, model):
    with locked(runtime, key, model) as path:
        saved = D.read_json(path, {})
        if saved:
            D.save_json(path, dict(saved, state='complete', completed_at=time.time()))


def retry_iso(record):
    return dt.datetime.fromtimestamp(record['retry_after'], dt.timezone.utc).isoformat()
