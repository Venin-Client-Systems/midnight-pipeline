"""Bounded read-only observations of Midnight's native Colibri sessions.

Selection, a live process, sampled memory and generated output are separate
facts. This module never starts inference or reads request/prompt bodies.
"""
import json
import math
import os
from pathlib import Path
import re
import stat
import time

import phase_runtime as P

LIMIT = 65536
MAX_SESSIONS = 128
MAX_FILES = 128
FRESH_SECONDS = 30


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def bounded(path, *, tail=False):
    """No links, special files or unbounded reads; callers own the directory."""
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0)), 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Not a regular observation file')
        clipped = info.st_size > LIMIT
        if clipped:
            if not tail:
                raise ValueError('Observation exceeds bound')
            handle.seek(info.st_size - LIMIT)
        raw = handle.read(LIMIT)
        if clipped:
            raw = raw.partition(b'\n')[2]
        return raw, info.st_mtime, clipped


def document(path):
    raw, _, _ = bounded(path)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Invalid observation')
    return value


def entries(path, limit):
    values = []
    with os.scandir(path) as scan:
        for entry in scan:
            if len(values) >= limit:
                return values, True
            values.append(entry)
    return values, False


def progress(directory, instant):
    value = {'state': 'unavailable', 'generated_output_observed': False,
             'output_counts_scope': 'bounded stream tail', 'usage': None,
             'last_output_at': None, 'output_wall_time_available': False}
    try:
        raw, stamp, clipped = bounded(directory / 'native.log', tail=True)
        matches = list(re.finditer(rb'^\[prefill\] layer (\d+)/(\d+).*? (\d+) token', raw, re.M))
        if matches:
            match = matches[-1]
            value['prefill'] = {'last_logged_layer': int(match[1]), 'layers': int(match[2]),
                               'layer_scope': 'current_chunk', 'chunk_tokens': int(match[3]),
                               'chunk_index': None, 'request_progress_fraction': None, 'log_updated_at': stamp,
                               'log_age_seconds': instant - stamp,
                               'log_stale': not 0 <= instant - stamp <= FRESH_SECONDS,
                               'checkpoint_at': None,
                               'scope': 'last logged chunk checkpoint; request, chunk index and wall time unavailable',
                               'checkpoint_is_exact_current_layer': False}
            value['state'] = 'prefill_checkpoint'
    except (OSError, ValueError):
        pass
    try:
        files, truncated = entries(directory, MAX_FILES)
        streams = [entry for entry in files if re.fullmatch(r'[a-f0-9]{32}-stream\.jsonl', entry.name)
                   and entry.is_file(follow_symlinks=False)]
        value['files_truncated'] = truncated
        if streams:
            latest = max(streams, key=lambda entry: entry.stat(follow_symlinks=False).st_mtime)
            raw, stamp, clipped = bounded(directory / latest.name, tail=True)
            answer = reasoning = records = 0
            last_output = last_record = None
            for line in raw.splitlines():
                try:
                    item = json.loads(line)
                    record = item.get('record') or {}
                    choice = (record.get('choices') or [{}])[0]
                    delta = choice.get('delta') or {}
                    content = len(delta['content']) if isinstance(delta.get('content'), str) else 0
                    thought = len(delta['reasoning_content']) if isinstance(delta.get('reasoning_content'), str) else 0
                    answer += content
                    reasoning += thought
                    elapsed = number(item.get('elapsed_seconds'))
                    if elapsed is not None:
                        last_record = elapsed
                        if content or thought:
                            last_output = elapsed
                    records += 1
                    usage = record.get('usage')
                    if isinstance(usage, dict):
                        value['usage'] = {key: number(usage.get(key)) for key in
                                          ('prompt_tokens', 'completion_tokens', 'total_tokens')}
                except (ValueError, TypeError, AttributeError, IndexError, KeyError):
                    continue
            age = instant - stamp
            fresh = 0 <= age <= FRESH_SECONDS
            value.update(stream_updated_at=stamp, stream_age_seconds=age,
                         stream_stale=not fresh, stream_tail_truncated=clipped,
                         answer_characters_in_tail=answer, reasoning_characters_in_tail=reasoning,
                         stream_records_in_tail=records, generated_output_observed=bool(answer or reasoning))
            # Stream mtime includes empty keepalives. It is not the output time.
            lag = last_record - last_output if last_record is not None and last_output is not None and last_record >= last_output else None
            value.update(last_output_elapsed_seconds=last_output,
                         last_record_elapsed_seconds=last_record,
                         output_seconds_behind_latest_record=lag)
            if answer or reasoning:
                value['state'] = 'stale_output_observation' if not fresh or (lag is not None and lag > FRESH_SECONDS) else 'output_observed_time_unavailable'
            elif not value.get('prefill'):
                value['state'] = 'waiting_for_output'
    except (OSError, ValueError):
        value['stream_unavailable'] = True
    return value


def snapshot(runtime, *, instant=None, alive=None, descendants=None):
    instant = time.time() if instant is None else instant
    alive = P.alive if alive is None else alive
    descendants = P.descendants if descendants is None else descendants
    root = Path(runtime) / 'models' / 'colibri' / 'sessions'
    value = {'available': True, 'scope': 'Midnight Colibri sessions', 'observed_at': instant,
             'sessions': [], 'truncated': False, 'state': 'not_observed'}
    try:
        for path in (root, root.parent, root.parent.parent, Path(runtime)):
            if path.is_symlink():
                raise ValueError('Linked observation directory')
        if not root.exists():
            return value
        found, value['truncated'] = entries(root, MAX_SESSIONS)
        if value['truncated']:
            value['available'] = False
        for entry in found:
            if not re.fullmatch(r'[a-f0-9]{32}', entry.name) or not entry.is_dir(follow_symlinks=False):
                continue
            directory = root / entry.name
            try:
                receipt = document(directory / 'process.json')
                gateway = receipt.get('gateway')
                if not birth_bound(gateway):
                    raise ValueError('Missing birth-bound native identity')
                if not alive(gateway):
                    continue
                children = [item for item in descendants(gateway) if birth_bound(item) and alive(item)]
                # Recheck the root after collecting descendants to reject exit/reuse.
                if not alive(gateway):
                    continue
                record = {'session': entry.name, 'model': 'colibri/glm-5.3',
                          'gateway_pid': gateway['pid'], 'birth_verified': True,
                          'native_descendants': len(children),
                          'state': 'native_process_active' if children else 'gateway_active',
                          'memory': {'available': False}, 'progress': progress(directory, instant)}
                if not isinstance(receipt.get('model'), dict) or receipt['model'].get('model') != record['model']:
                    raise ValueError('Native model receipt mismatch')
                try:
                    memory = document(directory / 'memory.json')
                    stamp = number(memory.get('at'))
                    age = instant - stamp if stamp is not None else None
                    fresh = age is not None and 0 <= age <= FRESH_SECONDS
                    record['memory'] = {'available': fresh, 'stale': not fresh,
                                        'sampled_at': stamp, 'age_seconds': age,
                                        'charged_bytes': number(memory.get('charged_bytes')) if fresh else None,
                                        'scope': 'native session charged footprint, including compressed pages'}
                except (OSError, ValueError):
                    pass
                value['sessions'].append(record)
            except (OSError, ValueError, TypeError):
                value['available'] = False
        if value['sessions']:
            value['state'] = 'native_process_active' if any(item['native_descendants'] for item in value['sessions']) else 'gateway_active'
        elif not value['available'] or value['truncated']:
            value['state'] = 'unavailable'
        return value
    except (OSError, ValueError, TypeError):
        return dict(value, available=False, state='unavailable')


def birth_bound(value):
    return (isinstance(value, dict) and type(value.get('pid')) is int and value['pid'] > 0
            and type(value.get('birth_us')) is int and value['birth_us'] > 0
            and isinstance(value.get('boot_id'), str) and bool(value['boot_id']))


def project_models(selected, jobs, phase, *, alive=None):
    """An owned action keeps its pinned model after next-selection changes."""
    alive = P.alive if alive is None else alive
    owned = next((job for job in jobs if job.get('id') == (phase or {}).get('job')
                  and job.get('state') == 'running' and alive(job.get('lease'))), None)
    if owned is None:
        owned = next((job for job in jobs if job.get('state') == 'running'
                      and alive(job.get('lease'))), None)
    model = (owned or {}).get('payload', {}).get('worker_model') or {}
    pinned = {'job': owned['id'], 'model': model['model'], 'deadline': owned.get('deadline')} if owned and model.get('model') else None
    return {'selected_next_model': selected.get('model'), 'owned_action_model': pinned,
            'local_model': pinned['model'] if pinned else selected.get('model')}
