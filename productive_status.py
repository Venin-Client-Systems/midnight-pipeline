"""Bounded, read-only productive-progress observations from host records.

This module never admits work, repairs evidence, calls providers or scans run
directories. Acceptance is reported as retained host evidence, independently of
scheduler responsiveness or model activity. Missing coverage remains explicit.
"""
import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
import time

from evidence_context import sanitize
from history_store import HistoryStore, encoded

RESULT_LIMIT = 100
ACCEPTED_LIMIT = 500
PAYLOAD_LIMIT = 128 * 1024
HANDOFF_FAILURES = {'unfinished_handoff', 'worker_contract', 'inference_truncated'}


def instant_value(value):
    if isinstance(value, str):
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None: raise ValueError('Naive progress time')
        return parsed.timestamp()
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError('Invalid progress time')
    return float(value)


def table(con, name):
    return con is not None and bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def payload(raw):
    if isinstance(raw, bytes): raw = raw.decode('utf-8')
    if not isinstance(raw, str) or len(raw.encode()) > PAYLOAD_LIMIT:
        raise ValueError('Oversized or missing progress record')
    def constant(_): raise ValueError('Non-finite progress value')
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value: raise ValueError('Duplicate progress key')
            value[key] = item
        return value
    value = json.loads(raw, parse_constant=constant, object_pairs_hook=pairs)
    if not isinstance(value, dict): raise ValueError('Progress record is not an object')
    return value


def acceptance(value):
    if not isinstance(value, dict) or not isinstance(value.get('run'), str) or not value['run']:
        raise ValueError('Acceptance run missing')
    if not isinstance(value.get('commit'), str) or not re.fullmatch('[0-9a-f]{40}', value['commit']):
        raise ValueError('Acceptance commit missing')
    if not isinstance(value.get('id'), str) or not value['id']: raise ValueError('Acceptance task missing')
    stamp = instant_value(value.get('at'))
    if value.get('delivery') == 'github' and value.get('remote_merge_confirmed') is not True:
        raise ValueError('Remote acceptance is not confirmed')
    return {'run': value['run'], 'commit': value['commit'], 'at': dt.datetime.fromtimestamp(stamp, dt.timezone.utc).isoformat(),
        'task': value.get('id'), 'title': sanitize(str(value.get('title', '')))[:300],
        'timestamp': stamp, 'evidence': 'retained_host_acceptance'}


def observe(runtime, project, state, *, repair=None, control=None, instant=None, active=False):
    instant = instant or dt.datetime.now(dt.timezone.utc)
    now = instant.timestamp()
    admission_observed = repair is not None
    malformed = not isinstance(state, dict) or control is not None and not isinstance(control, dict) or repair is not None and not isinstance(repair, dict)
    state = state if isinstance(state, dict) else {}
    control = control if isinstance(control, dict) else {}
    repair = repair if isinstance(repair, dict) else {}
    owner = control.get('recovery_owner')
    result = {'schema_version': 1, 'checked_at': instant.isoformat(), 'available': True,
        'last_accepted': None, 'seconds_since_accepted': None,
        'activity_observed': bool(active), 'activity_is_acceptance': False,
        'failure_groups': [], 'unfinished_handoffs': [], 'admission': [],
        'responsibility': owner if isinstance(owner, str) and owner in {'codex', 'owner', 'automatic', 'provider'} else 'unknown',
        'control_reason': sanitize(str(control.get('explanation', '')))[:600],
        'coverage': {'admission_observed': admission_observed, 'failure_limit': RESULT_LIMIT, 'failure_rows': 0, 'failure_history_truncated': False,
            'legacy_identity_rows': 0, 'accepted_hot_limit': ACCEPTED_LIMIT, 'accepted_hot_truncated': False},
        'errors': []}
    def error(message):
        result['available'] = False; result['errors'].append(message)
    if malformed: error('Productive observation input is malformed')
    if 'available' in repair and repair['available'] is not True: error('Admission evidence is unavailable')
    candidates = []; observations = []
    hot = state.get('accepted', [])
    if not isinstance(hot, list):
        error('Active acceptance history is malformed'); hot = []
    if len(hot) > ACCEPTED_LIMIT:
        result['coverage']['accepted_hot_truncated'] = True
        error('Active acceptance history exceeds the observation bound')
    for value in hot[-ACCEPTED_LIMIT:]:
        try: candidates.append(acceptance(value))
        except (ValueError, TypeError, OverflowError): error('An active acceptance receipt is incomplete')
    try:
        deadline = time.monotonic() + 0.1
        def budget():
            if time.monotonic() >= deadline: raise ValueError('Productive observation budget exceeded')
        with HistoryStore(runtime, read_timeout=0.05).reader() as con:
            if con is not None: con.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
            if table(con, 'qc_terminal_acceptances'):
                columns = 'substr(CAST(payload AS BLOB),1,?) AS payload,accepted_at,run_id,task_id'
                row = con.execute('SELECT ' + columns + ' FROM qc_terminal_acceptances WHERE project_id=? '
                    'ORDER BY accepted_at DESC,run_id DESC LIMIT 1', (PAYLOAD_LIMIT + 1, project)).fetchone()
                def indexed(row):
                    budget()
                    item = acceptance(payload(row['payload']))
                    if (item['timestamp'] != row['accepted_at'] or item['run'] != row['run_id']
                            or item['task'] != row['task_id']): raise ValueError('Acceptance index identity differs')
                    candidates.append(item)
                if row: indexed(row)
                # Validate every bounded hot/index overlap, even when an unrelated
                # newer indexed run would otherwise hide a conflicting receipt.
                runs = sorted({item['run'] for item in candidates})
                if runs:
                    for overlap in con.execute('SELECT ' + columns + ' FROM qc_terminal_acceptances '
                            'WHERE project_id=? AND run_id IN (' + ','.join('?' for _ in runs) + ') LIMIT ?',
                            (PAYLOAD_LIMIT + 1, project, *runs, ACCEPTED_LIMIT + 1)):
                        indexed(overlap)
            has_results = table(con, 'pr_results'); has_scopes = table(con, 'pr_scopes')
            if has_results != has_scopes: raise ValueError('Incomplete failure history schema')
            if has_results:
                if con.execute('SELECT 1 FROM pr_results r LEFT JOIN pr_scopes s ON s.id=r.scope '
                        'WHERE s.id IS NULL LIMIT 1').fetchone(): raise ValueError('Unattributed failure observation')
                rows = con.execute('SELECT r.run,r.created,substr(CAST(r.payload AS BLOB),1,?) AS payload,'
                    'substr(CAST(s.payload AS BLOB),1,?) AS scope_payload '
                    'FROM pr_results r JOIN pr_scopes s ON s.id=r.scope WHERE s.project=? '
                    'ORDER BY r.created DESC,r.run DESC LIMIT ?', (PAYLOAD_LIMIT + 1, PAYLOAD_LIMIT + 1, project, RESULT_LIMIT + 1)).fetchall()
                result['coverage']['failure_history_truncated'] = len(rows) > RESULT_LIMIT
                for row in rows[:RESULT_LIMIT]:
                    budget()
                    value = payload(row['payload']); scope = payload(row['scope_payload'])
                    created = instant_value(row['created'])
                    if not isinstance(value.get('kind'), str) or value.get('run') != row['run']:
                        raise ValueError('Failure observation identity is inconsistent')
                    observations.append((dict(row, created=created), value, scope.get('task_id')))
    except (ValueError, TypeError, OSError, sqlite3.Error, OverflowError, RecursionError):
        error('Indexed productive history is unavailable or inconsistent')
    if candidates:
        by_run = {}
        for item in candidates:
            if item['run'] in by_run and by_run[item['run']] != item:
                error('Conflicting acceptance receipts for one run')
            by_run[item['run']] = item
        latest = max(candidates, key=lambda item: (item['timestamp'], item['run']))
        result['last_accepted'] = {key: value for key, value in latest.items() if key != 'timestamp'}
        if latest['timestamp'] > now: error('Latest acceptance time is in the future')
        elif not result['coverage']['accepted_hot_truncated']:
            result['seconds_since_accepted'] = round(now - latest['timestamp'], 3)
    groups = {}
    for row, value, task in observations:
        kind = value.get('kind'); fingerprint = value.get('fingerprint')
        if kind in {'complete', 'checkpoint'}: continue
        result['coverage']['failure_rows'] += 1
        if not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint):
            error('A failure observation lacks its retained fingerprint'); continue
        identity = value.get('failure_identity')
        stable = isinstance(identity, dict) and type(identity.get('version')) is int and identity['version'] == 2
        if stable and (identity.get('failure_class') != kind or not {
                'phase', 'worker_contract', 'model', 'inference_policy', 'requested_budget', 'components'} <= identity.keys()):
            error('A stable failure identity is incomplete'); continue
        if stable:
            try: matches = hashlib.sha256(encoded(identity).encode()).hexdigest() == fingerprint
            except (ValueError, TypeError, OverflowError, RecursionError): matches = False
            if not matches: error('A stable failure fingerprint does not match its identity'); continue
        if not stable: result['coverage']['legacy_identity_rows'] += 1
        key = (fingerprint, stable)
        group = groups.setdefault(key, {'fingerprint': fingerprint, 'stable_harness_identity': stable,
            'kind': kind, 'observed_runs': 0, 'runs': [], 'tasks': [], 'last_observed_at': row['created'],
            'responsibility': 'codex' if kind in HANDOFF_FAILURES else 'unknown'})
        group['observed_runs'] += 1; group['runs'].append(row['run'])
        if task not in group['tasks']: group['tasks'].append(task)
        if kind in HANDOFF_FAILURES:
            result['unfinished_handoffs'].append({'run': row['run'], 'task': task, 'kind': kind,
                'fingerprint': fingerprint, 'observed_at': row['created'],
                'reported_finished': value.get('observed_finished'),
                'scope': 'retained observation; current disposition comes from admission/control'})
    result['failure_groups'] = sorted(groups.values(), key=lambda group: (-group['observed_runs'], -group['last_observed_at']))
    seen = set()
    scopes = repair.get('scopes', [])
    if not isinstance(scopes, list):
        error('Admission observation is malformed'); scopes = []
    for scope in scopes[:30]:
        if not isinstance(scope, dict) or not isinstance(scope.get('admission'), dict):
            error('Admission observation is incomplete'); continue
        task = scope.get('task_id')
        if not isinstance(task, str): error('Admission task identity is unavailable'); continue
        if task in seen: continue
        seen.add(task); admission = scope['admission']; diagnosis = scope.get('diagnosis') or {}
        identity = scope.get('scope')
        if (not isinstance(identity, dict) or not isinstance(identity.get('id'), str)
                or type(admission.get('allowed')) is not bool or not isinstance(admission.get('state'), str)
                or not isinstance(admission.get('reason'), str)):
            error('Admission observation is incomplete'); continue
        if not isinstance(diagnosis, dict): error('Admission diagnosis is malformed'); diagnosis = {}
        owner = 'codex' if admission.get('state') in {'needs_codex', 'needs_harness_repair'} else 'automatic'
        result['admission'].append({'task': task, 'scope': identity['id'],
            'allowed': admission.get('allowed'), 'state': admission.get('state'),
            'reason': sanitize(str(admission.get('reason', 'Unavailable')))[:600],
            'next_action': sanitize(str(diagnosis.get('next_action', '')))[:600], 'responsibility': owner,
            'observational_only': True})
    result['repeated_failure_groups'] = sum(group['observed_runs'] > 1 for group in result['failure_groups'])
    result['errors'] = list(dict.fromkeys(result['errors']))
    # Inconsistent coverage cannot establish a precise acceptance age.
    if not result['available']:
        result['last_accepted'] = None; result['seconds_since_accepted'] = None
    return result


def summary(value):
    if not value.get('available'): return 'Productive progress evidence is incomplete: ' + '; '.join(value.get('errors', []))
    accepted = value.get('last_accepted')
    progress = ('Last accepted commit ' + accepted['commit'][:12] + ' at ' + str(accepted['at'])) if accepted else 'No accepted commit receipt is recorded'
    if value.get('seconds_since_accepted') is not None:
        progress += ' (' + str(round(value['seconds_since_accepted'] / 3600, 1)) + ' hours ago)'
    detail = ' '.join(group['kind'] + ' ' + group['fingerprint'][:16] + ' (' + str(group['observed_runs']) + ' runs).'
        for group in value.get('failure_groups', [])[:3])
    return (progress + '; ' + str(value.get('repeated_failure_groups', 0)) + ' repeated failure fingerprints and '
        + str(len(value.get('unfinished_handoffs', []))) + ' unfinished handoffs in the bounded retained observations. '
        + 'Current responsibility: ' + str(value.get('responsibility', 'unknown')) + '. '
        + value.get('control_reason', '') + (' Retained failures: ' + detail if detail else ''))
