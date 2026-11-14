"""Seven fixed days of host evidence. No inference, controls or product writes.

Only the dedicated review directory is writable. Reports are observations, never
acceptance authority or a substitute for an independent engineering review.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import time
from contextlib import ExitStack

DAY = 86400
DAYS = 7
FILE_LIMIT = 2 * 1024 * 1024
ROW_LIMIT = 4000
PROJECTS = {'secondapp', 'exampleapp'}
DECISION_FILES = {'result.json', 'events.jsonl', 'process.json', 'supervision-model.json',
                  'local-metrics.json', 'input.txt', 'schema.json', 'local-request.json'}


class Unavailable(ValueError):
    pass


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def epoch(value):
    if type(value) in (int, float) and math.isfinite(value):
        return float(value)
    if not isinstance(value, str):
        raise Unavailable('Missing timestamp')
    stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise Unavailable('Timestamp requires timezone')
    return stamp.timestamp()


def iso(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


def numeric(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def model_facts(model):
    """Hash the whole descriptor; promotion bookkeeping is not a model profile."""
    if not isinstance(model, dict):
        return {'model': None, 'model_descriptor_sha256': None, 'model_descriptor_complete': False}
    canonical = {key: value for key, value in model.items() if key != 'promotion_generation'}
    complete = (isinstance(model.get('model'), str) and 0 < len(model['model']) <= 160
                and all(isinstance(model.get(key), str) and re.fullmatch('[0-9a-f]{64}', model[key])
                        for key in ('digest', 'parameters_sha256'))
                and all(type(model.get(key)) is int and model[key] > 0 for key in ('context', 'bytes')))
    return {'model': model.get('model') if isinstance(model.get('model'), str) and len(model['model']) <= 160 else None,
            'model_descriptor_sha256': digest(encoded(canonical)), 'model_descriptor_complete': bool(complete)}


def attribution(value, expected):
    if not value.get('model_descriptor_complete'):
        return 'unknown'
    if value['model'] != 'colibri/glm-5.3':
        return 'other_model'
    return 'glm_current_profile' if value['model_descriptor_sha256'] == expected else 'glm_other_profile'


def read_bytes(path, limit=FILE_LIMIT):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise Unavailable('Linked evidence refused')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0)), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise Unavailable('Evidence exceeds file bound or is not regular')
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise Unavailable('Evidence grew beyond bound')
    return raw


def document(path):
    value = json.loads(read_bytes(path))
    if not isinstance(value, dict):
        raise Unavailable('Expected object receipt')
    return value


def seal(value):
    return {'sha256': digest(encoded(value)), 'value': value}


def unseal(path):
    value = document(path)
    if value.get('sha256') != digest(encoded(value.get('value'))):
        raise Unavailable('Saved review evidence changed')
    return value['value']


def write_once(path, value):
    """Publish immutable files atomically; repeat publication must be identical."""
    write_raw_once(path, encoded(seal(value)))


def write_raw_once(path, raw):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise Unavailable('Linked review output refused')
    if len(raw) > FILE_LIMIT:
        raise Unavailable('Review output exceeds retained file bound')
    if path.exists():
        if read_bytes(path) != raw:
            raise Unavailable('Conflicting immutable review output')
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + path.name + '.' + str(os.getpid()))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(raw); out.flush(); os.fsync(out.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if read_bytes(path) != raw:
                raise Unavailable('Concurrent immutable review conflict')
    finally:
        temporary.unlink(missing_ok=True)


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, timeout=3)
    if result.returncode or len(result.stdout) > 65536:
        raise Unavailable('Local Git evidence unavailable')
    return result.stdout.decode().strip()


@contextmanager
def database(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise Unavailable('Required history database unavailable')
    con = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.25)
    con.row_factory = sqlite3.Row
    deadline = time.monotonic() + 2
    con.set_progress_handler(lambda: time.monotonic() >= deadline, 10000)
    try:
        con.execute('BEGIN')
        yield con
    finally:
        con.close()


def rows(con, query, arguments=()):
    """Bound each query and fetch in pages; never silently truncate a report."""
    cursor = con.execute(query + ' LIMIT ?', (*arguments, ROW_LIMIT + 1))
    result = []
    while True:
        page = cursor.fetchmany(250)
        if not page:
            break
        result.extend(dict(row) for row in page)
        if len(result) > ROW_LIMIT:
            raise Unavailable('History page bound reached; report remains pending')
    return result


def payload(raw):
    if not isinstance(raw, str) or len(raw) > 65536:
        raise Unavailable('History payload unavailable or oversized')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise Unavailable('History payload is not an object')
    return value


def reference(path):
    path = Path(path).absolute()
    return {'path': str(path), 'sha256': digest(read_bytes(path))}


def referenced(value):
    if (not isinstance(value, dict) or set(value) != {'path', 'sha256'}
            or not Path(value['path']).is_absolute() or not re.fullmatch('[a-f0-9]{64}', value['sha256'])):
        raise Unavailable('Invalid immutable evidence reference')
    raw = read_bytes(value['path'])
    if digest(raw) != value['sha256']:
        raise Unavailable('Referenced campaign evidence changed')
    return json.loads(raw)


def source_fact(value):
    return value if isinstance(value, str) and re.fullmatch('[a-f0-9]{40}', value) else None


class Review:
    def __init__(self, root, directory):
        self.root = Path(root).resolve()
        self.runtime = self.root / 'runtime'
        supplied = Path(directory)
        if '..' in supplied.parts:
            raise Unavailable('Review directory must not contain parent traversal')
        supplied = supplied.absolute()
        if any(p.is_symlink() for p in (supplied, *supplied.parents)):
            raise Unavailable('Review directory must not use links')
        self.directory = supplied.resolve()
        if not self.directory.is_relative_to(self.runtime / 'performance-reviews'):
            raise Unavailable('Review writes require runtime/performance-reviews/<id>')
        if self.directory == self.runtime / 'performance-reviews':
            raise Unavailable('Use a dedicated review directory')

    def projects(self):
        values = document(self.root / 'projects.json').get('projects', [])
        # A review-admitted project that was later removed from the register
        # (archived) keeps its evidence through its retirement record, so an
        # open review still completes against the same retained state.
        registered = {value.get('id') for value in values}
        retired = document(self.runtime / 'retired-projects.json').get('projects', []) if (self.runtime / 'retired-projects.json').is_file() else []
        values = values + [value for value in retired if value.get('id') in PROJECTS and value.get('id') not in registered]
        result = {}
        for value in values:
            if value.get('id') not in PROJECTS:
                continue
            state = Path(value['state_dir']).resolve()
            if not state.is_relative_to(self.runtime / 'projects'):
                raise Unavailable('Project evidence is outside admitted runtime')
            result[value['id']] = {'state': state, 'repo': Path(value['path'])}
        if set(result) != PROJECTS:
            raise Unavailable('Both admitted projects are required')
        return result

    def initialise(self, config, instant=None):
        instant = time.time() if instant is None else instant
        if (self.directory / 'baseline.json').exists():
            baseline = self.baseline()
            if baseline['config'] != config:
                raise Unavailable('Review already has a different activation')
            return baseline
        required = {'activation_at', 'expected_source', 'expected_model', 'activation_proof',
                    'activation_proof_sha256', 'projects'}
        if set(config) != required or set(config['projects']) != PROJECTS:
            raise Unavailable('Incomplete review configuration')
        start = epoch(config['activation_at'])
        if not 0 <= instant - start <= 300:
            raise Unavailable('Capture the baseline within five minutes of verified activation')
        raw = read_bytes(config['activation_proof']); proof = json.loads(raw)
        if digest(raw) != config['activation_proof_sha256']:
            raise Unavailable('Activation proof changed')
        health = proof.get('generation_health', {})
        if (proof.get('installed') != config['expected_source'] or proof.get('source_clean') is not True
                or health.get('state') != 'verified' or health.get('installed') != config['expected_source']
                or proof.get('selected_model') != config['expected_model']
                or epoch(proof['observed_at']) != start or proof.get('fence_present') is not False
                or git(self.root, 'rev-parse', 'HEAD') != config['expected_source']):
            raise Unavailable('Activation is not exact verified source/model evidence')
        if config['expected_model'].get('model') != 'colibri/glm-5.3':
            raise Unavailable('This observation is scoped to the requested GLM default')
        baseline = {'version': 1, 'config': config, 'started_at': start, 'ends_at': start + DAYS * DAY,
                    'collector_sha256': digest(read_bytes(__file__)), 'projects': {}}
        for name, project in self.projects().items():
            with database(project['state'] / 'history.sqlite3') as con:
                accepted = rows(con, "SELECT run_id,accepted_commit FROM qc_runs WHERE project_id=? AND outcome='accepted' ORDER BY run_id", (name,))
                pending = rows(con, "SELECT run_id FROM qc_runs WHERE project_id=? AND outcome IN ('in_progress','awaiting_review','pending_delivery') ORDER BY run_id", (name,))
                cursor = con.execute('SELECT COALESCE(MAX(sequence),0) FROM qc_events WHERE project_id=?', (name,)).fetchone()[0]
            historical = []; undated = []
            for row in accepted:
                try:
                    if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', row['run_id']):
                        raise Unavailable('Invalid run ID')
                    receipt = document(project['state'] / 'runs' / row['run_id'] / 'accepted.json')
                    if epoch(receipt['at']) < start:
                        historical.append(row)
                except (OSError, ValueError, KeyError, TypeError):
                    undated.append(row)
            baseline['projects'][name] = {'accepted': historical, 'accepted_without_timestamp': undated,
                                         'pending_runs': [r['run_id'] for r in pending], 'event_cursor': cursor}
        with database(self.runtime / 'workflow.sqlite3') as con:
            baseline['workflow_cursor'] = con.execute('SELECT COALESCE(MAX(sequence),0) FROM wf_events').fetchone()[0]
            baseline['carryover_phases'] = rows(con, "SELECT id,project,created FROM wf_phases WHERE state IN ('waiting','running') ORDER BY id")
            baseline['charge_counts'] = rows(con, 'SELECT project,kind,COUNT(*) AS count FROM wf_charges GROUP BY project,kind ORDER BY project,kind')
        with database(self.runtime / 'history.sqlite3') as con:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            baseline['cloud_invocations'] = rows(con, 'SELECT purpose,COUNT(*) AS count,SUM(result IS NULL) AS unresolved FROM cp_invocations GROUP BY purpose ORDER BY purpose') if 'cp_invocations' in tables else []
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_once(self.directory / 'baseline.json', baseline)
        return baseline

    def baseline(self):
        value = unseal(self.directory / 'baseline.json')
        amendments = self.amendments()
        expected = (amendments[-1]['intent']['next']['source_files']['performance_review.py']
                    if amendments else value['collector_sha256'])
        if expected != digest(read_bytes(__file__)):
            raise Unavailable('Collector source changed during the review')
        return value

    def _validate_amendment(self, intent, previous):
        from performance_review_cloud import SOURCES
        baseline = unseal(self.directory / 'baseline.json')
        original = unseal(self.directory / 'cloud-review-plan.json')
        schedule = unseal(self.directory / 'schedule.json')
        required = {'version', 'number', 'campaign', 'baseline_sha256', 'original_plan_sha256',
                    'schedule_sha256', 'previous_activation_sha256', 'started_at', 'ends_at',
                    'maximum_attempts', 'owner_direction', 'next'}
        if (set(intent) != required or type(intent['version']) is not int or intent['version'] != 1
                or type(intent['number']) is not int or intent['number'] != len(previous) + 1
                or intent['campaign'] != self.directory.name or intent['baseline_sha256'] != digest(encoded(baseline))
                or intent['original_plan_sha256'] != digest(encoded(original))
                or intent['schedule_sha256'] != digest(encoded(schedule))
                or intent['previous_activation_sha256'] != (digest(encoded(previous[-1])) if previous else None)
                or intent['started_at'] != baseline['started_at'] or intent['ends_at'] != baseline['ends_at']
                or intent['ends_at'] != intent['started_at'] + DAYS * DAY
                or intent['maximum_attempts'] != DAYS or original['maximum_attempts'] != DAYS):
            raise Unavailable('Amendment cannot replace campaign identity, intervals or allowance')
        referenced(intent['owner_direction'])
        following = intent['next']
        if (set(following) != {'source_tree', 'source_files', 'cloud_config_sha256', 'runtime_controls'}
                or not source_fact(following['source_tree']) or set(following['source_files']) != set(SOURCES)
                or any(not isinstance(v, str) or not re.fullmatch('[a-f0-9]{64}', v)
                       for v in [following['cloud_config_sha256'], *following['source_files'].values()])):
            raise Unavailable('Amendment needs the complete reviewed source binding')
        model = following['runtime_controls'].get('selected_model', {})
        if not model_facts(model)['model_descriptor_complete'] or model.get('model') != 'colibri/glm-5.3':
            raise Unavailable('Amendment requires an exact GLM descriptor')
        return baseline, original

    def prepare_amendment(self, next_binding, owner_direction):
        """Pure preparation; the returned intent is independently reviewed before activation."""
        previous = self.amendments(); baseline = unseal(self.directory / 'baseline.json')
        intent = {'version': 1, 'number': len(previous) + 1, 'campaign': self.directory.name,
            'baseline_sha256': digest(encoded(baseline)),
            'original_plan_sha256': digest(encoded(unseal(self.directory / 'cloud-review-plan.json'))),
            'schedule_sha256': digest(encoded(unseal(self.directory / 'schedule.json'))),
            'previous_activation_sha256': digest(encoded(previous[-1])) if previous else None,
            'started_at': baseline['started_at'], 'ends_at': baseline['ends_at'], 'maximum_attempts': DAYS,
            'owner_direction': owner_direction, 'next': next_binding}
        self._validate_amendment(intent, previous)
        return intent

    def _validate_activation(self, value, previous):
        if set(value) != {'intent', 'release_evidence', 'activated_at', 'plan'}:
            raise Unavailable('Incomplete campaign activation')
        intent = value['intent']; baseline, original = self._validate_amendment(intent, previous)
        refs = value['release_evidence']
        if set(refs) != {'verified', 'review', 'provider', 'delivery', 'proof'}:
            raise Unavailable('Activation needs checked release and fresh health evidence')
        evidence = {name: referenced(ref) for name, ref in refs.items()}
        verified, review, provider, delivery, proof = (evidence[k] for k in ('verified', 'review', 'provider', 'delivery', 'proof'))
        following = intent['next']; health = proof.get('generation_health', {})
        checks = verified.get('checks', [])
        expected_base = previous[-1]['plan']['source']['commit'] if previous else baseline['config']['expected_source']
        if (verified.get('tree') != following['source_tree'] or verified.get('base') != expected_base or verified.get('review') != review
                or verified.get('source_review_sha256') != refs['review']['sha256']
                or review.get('decision') != 'accept' or review.get('issues') != []
                or review.get('amendment_sha256') != digest(encoded(intent))
                or not checks or not all(c.get('code') == 0 and c.get('reason') == 'exited'
                    and c.get('completion', {}).get('complete') is True for c in checks)
                or provider.get('returncode') != 0 or not provider.get('completed')
                or provider.get('profile_mismatch') is not False or provider.get('requested') != verified.get('descriptor')
                or verified.get('descriptor') != original['model']
                or delivery.get('status') != 'merged' or delivery.get('candidate_tree') != following['source_tree']
                or delivery.get('base') != verified.get('base') or delivery.get('patch_sha256') != verified.get('patch_sha256')
                or not source_fact(delivery.get('commit')) or proof.get('installed') != delivery['commit']
                or proof.get('tree') != following['source_tree'] or proof.get('source_clean') is not True
                or proof.get('fence_present') is not False or health.get('state') != 'verified'
                or health.get('installed') != delivery['commit'] or health.get('tree') != following['source_tree']
                or proof.get('selected_model') != following['runtime_controls']['selected_model']
                or epoch(proof.get('observed_at')) != value['activated_at']
                or not baseline['started_at'] <= value['activated_at'] < baseline['ends_at']
                or previous and value['activated_at'] <= previous[-1]['activated_at']):
            raise Unavailable('Amendment lacks exact reviewed release and activation proof')
        plan = dict(original, version=2, source={'commit': delivery['commit'],
            'files': following['source_files'], 'cloud_config_sha256': following['cloud_config_sha256']},
            runtime_controls=following['runtime_controls'], amendment_sha256=digest(encoded(intent)))
        if value['plan'] != plan:
            raise Unavailable('Amended plan changed the original paid scope or binding')
        return evidence

    def amendments(self):
        directory = self.directory / 'amendments'
        if directory.is_symlink():raise Unavailable('Linked amendment inventory')
        if not directory.exists():
            return []
        paths = sorted(directory.iterdir())
        if len(paths) > 32 or [p.name for p in paths] != [f'{n:04}.json' for n in range(1, len(paths) + 1)]:
            raise Unavailable('Amendment sequence is incomplete or unexpected')
        result = []
        for path in paths:
            value = unseal(path); self._validate_activation(value, result); result.append(value)
        return result

    def plans(self):
        return [unseal(self.directory / 'cloud-review-plan.json'), *[v['plan'] for v in self.amendments()]]

    def activate_amendment(self, intent, release_evidence, instant=None):
        """Host-only publication after checked installation; never starts a model or changes controls."""
        from action_store import Actions
        import phase_runtime as P
        import performance_review_cloud as C
        instant = time.time() if instant is None else instant
        with ExitStack() as stack:
            for name in ('.schedule-observe.lock', '.tick.lock', '.schedule-review.lock', '.cloud-review.lock'):
                fd = os.open(self.directory / name, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                handle = stack.enter_context(os.fdopen(fd, 'a'))
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            stack.enter_context(P.source_lock(self.runtime))
            stack.enter_context(P.admission_lock(self.runtime))
            existing = self.amendments()
            path = self.directory / 'amendments' / f'{intent["number"]:04}.json'
            if len(existing) >= intent['number']:
                old = existing[intent['number'] - 1]
                if old['intent'] != intent or old['release_evidence'] != release_evidence:
                    raise Unavailable('Conflicting amendment replay')
                return old
            _, original = self._validate_amendment(intent, existing)
            proof = referenced(release_evidence['proof']); delivery = referenced(release_evidence['delivery'])
            following = intent['next']
            plan = dict(original, version=2, source={'commit': delivery['commit'], 'files': following['source_files'],
                'cloud_config_sha256': following['cloud_config_sha256']}, runtime_controls=following['runtime_controls'],
                amendment_sha256=digest(encoded(intent)))
            value = {'intent': intent, 'release_evidence': release_evidence, 'activated_at': epoch(proof['observed_at']), 'plan': plan}
            self._validate_activation(value, existing)
            store = Actions(self.runtime); health = store.meta('install_health') or {}
            if (not 0 <= instant - value['activated_at'] <= 300 or health != proof['generation_health']
                    or not P.alive(health.get('verified_controller_process'))
                    or not P.alive(health.get('verified_dashboard', {}).get('process'))
                    or (self.runtime / 'INSTALLING').exists() or (self.runtime / 'INSTALLING').is_symlink()
                    or git(self.root, 'rev-parse', 'HEAD^{tree}') != following['source_tree']
                    or C.source(self.root) != plan['source']
                    or C.runtime_controls(self.root, {'config': {'expected_model': following['runtime_controls']['selected_model']}}) != following['runtime_controls']
                    or C.A.check(self.root) != original['authority']
                    or C.CP.CloudPolicy(self.runtime, self.root / 'cloud_models.json').descriptor() != original['model']):
                raise Unavailable('Fresh live generation, controls or original cloud authority do not match amendment')
            if any(j['project'] == C.PROJECT for j in store.jobs()):
                raise Unavailable('Preserve active paid review before amendment')
            write_once(path, value)
            return value

    def epochs(self, baseline):
        values = [{'number': 0, 'activated_at': baseline['started_at'],
            'source': baseline['config']['expected_source'],
            'model_descriptor_sha256': model_facts(baseline['config']['expected_model'])['model_descriptor_sha256'],
            'collector_sha256': baseline['collector_sha256']}]
        for item in self.amendments():
            values.append({'number': item['intent']['number'], 'activated_at': item['activated_at'],
                'source': item['plan']['source']['commit'],
                'model_descriptor_sha256': model_facts(item['plan']['runtime_controls']['selected_model'])['model_descriptor_sha256'],
                'collector_sha256': item['intent']['next']['source_files']['performance_review.py']})
        return values

    def control_observation(self, instant):
        """Observation only. Missing prior pause samples are never reconstructed."""
        try:
            import midnight as M
            import durability as D
            registry = document(self.root / 'projects.json')
            override = D.read_json(self.runtime / 'override.json', {})
            timing = M.window(dt.datetime.fromtimestamp(instant, dt.timezone.utc), registry, override)
            marker = lambda path: path.exists() or path.is_symlink()
            return {'available': True, 'scheduled_eligible': timing['active'],
                'global_paused': marker(self.runtime / 'PAUSED'),
                'installing': any(marker(self.runtime / name) for name in ('INSTALLING', 'WEEKDAY_ACTIVATION_PENDING')),
                'project_paused': {name: marker(project['state'] / 'PAUSED') for name, project in self.projects().items()},
                'schedule_sha256': digest(encoded({'timezone': registry['timezone'], 'schedule': registry['schedule'], 'override': override}))}
        except (OSError, ValueError, KeyError, TypeError):
            return {'available': False}

    def telemetry(self, instant):
        connection = http.client.HTTPConnection('127.0.0.1', 8766, timeout=2)
        try:
            # Fixed loopback endpoint; neither config nor model text controls URLs.
            connection.request('GET', '/api/telemetry')
            response = connection.getresponse()
            if response.status != 200:
                raise Unavailable('Local telemetry unavailable; redirects are not followed')
            raw = response.read(FILE_LIMIT + 1)
            if len(raw) > FILE_LIMIT:
                raise Unavailable('Telemetry exceeds bound')
            value = json.loads(raw); stamp = epoch(value['sampled_at'])
            if value.get('available') is not True or value.get('stale') or not 0 <= instant - stamp <= 30:
                raise Unavailable('Telemetry stale')
            memory = value.get('memory', {}); native = value.get('native_models', {})
            return {'available': True, 'sampled_at': stamp,
                    'cpu_percent': numeric(value.get('cpu', {}).get('utilisation_percent')),
                    'gpu_percent': numeric(value.get('gpu', {}).get('utilisation_percent')),
                    'pressure': memory.get('pressure') if memory.get('pressure') in {'normal', 'warning', 'critical'} else 'unknown',
                    'swap_used_bytes': numeric(memory.get('swap_used_bytes')),
                    'charged_bytes': [numeric(s.get('memory', {}).get('charged_bytes')) for s in native.get('sessions', [])[:8]
                                      if s.get('birth_verified') and s.get('memory', {}).get('available')]}
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
            return {'available': False}
        finally:
            connection.close()

    def sample(self, baseline, instant):
        import phase_runtime as P
        slot = int((instant - baseline['started_at']) // 60)
        if slot < 0 or slot > DAYS * 1440:
            return
        path = self.directory / 'samples' / f'{slot:05}.json'
        if path.exists():
            unseal(path)
            return
        active = []; available = True
        try:
            with database(self.runtime / 'workflow.sqlite3') as con:
                phases = rows(con, "SELECT p.id,p.project,p.kind,p.lease,j.payload FROM wf_phases p JOIN wf_jobs j ON j.id=p.job WHERE p.state='running' ORDER BY p.id")
            for row in phases:
                lease = payload(row['lease']); model = payload(row['payload']).get('worker_model', {})
                if lease.get('birth_us') and P.alive(lease) and row['project'] in PROJECTS:
                    active.append({'phase': row['id'], 'project': row['project'], 'kind': row['kind'],
                                   'model': model.get('model'), 'model_sha256': digest(encoded(model))})
        except (OSError, ValueError, sqlite3.Error, TypeError):
            available = False
        value = {'at': instant, 'slot': slot, 'active_available': available, 'active': active,
                 'telemetry': self.telemetry(instant), 'source': git(self.root, 'rev-parse', 'HEAD'),
                 'controls': self.control_observation(instant), 'collector_sha256': digest(read_bytes(__file__))}
        amendments = self.amendments()
        value['amendment_sha256'] = digest(encoded(amendments[-1])) if amendments else None
        write_once(path, value)

    def accepted(self, project, row, baseline_ids, baseline_commits):
        run = row['run_id']
        if run in baseline_ids or row['accepted_commit'] in baseline_commits or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', run):
            return None
        folder = project['state'] / 'runs' / run
        try:
            receipt = document(folder / 'accepted.json'); when = epoch(receipt['at'])
            commit = receipt.get('commit')
            checks = json.loads(read_bytes(folder / 'checks.json'))
            review = document(folder / 'review/result.json')
            if (not re.fullmatch('[0-9a-f]{40}', commit or '') or commit != row['accepted_commit']
                    or receipt.get('run') != run or not isinstance(checks, list) or not checks
                    or not all(isinstance(c, dict) and type(c.get('code')) is int and c['code'] == 0 for c in checks)
                    or review.get('decision') != 'accept' or review.get('issues') != []
                    or receipt.get('delivery') == 'github' and receipt.get('remote_merge_confirmed') is not True):
                raise Unavailable('Accepted evidence incomplete')
            git(project['repo'], 'cat-file', '-e', commit + '^{commit}')
            model = document(folder / 'worker-model.json')
            return {'run': run, 'commit': commit, 'at': when, 'started_at': row['created_at'],
                    **model_facts(model), 'model_receipt_sha256': digest(read_bytes(folder / 'worker-model.json')),
                    'coordinator_source': None,
                    'delivery': receipt.get('delivery', 'unknown'),
                    'receipt': str((folder / 'accepted.json').relative_to(self.runtime)),
                    'receipt_sha256': digest(read_bytes(folder / 'accepted.json')), 'verified': True}
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
            return {'run': run, 'verified': False, 'classification': 'accepted_history_needs_receipt_verification'}

    def decision(self, row):
        value = {'phase': row['id'], 'project': row['project'], 'at': row['updated'],
                 'started_at': row['created'], 'verified': False}
        try:
            model = payload(row['job_payload']).get('worker_model', {})
            phase = payload(row['payload']); folder = Path(phase['log']).parent
            if not folder.resolve().is_relative_to(self.runtime / 'projects' / row['project']):
                raise Unavailable('Decision is outside project evidence')
            receipt = document(folder / 'local-decision-receipt.json')
            if set(receipt.get('files', {})) != DECISION_FILES:
                raise Unavailable('Incomplete typed decision receipt')
            for name, expected in receipt['files'].items():
                if digest(read_bytes(folder / name)) != expected:
                    raise Unavailable('Decision evidence changed')
            process = document(folder / 'process.json'); actual = document(folder / 'supervision-model.json')
            if (process.get('code') != 0 or process.get('reason') != 'exited'
                    or any(actual.get(k) != v for k, v in model.items() if k != 'promotion_generation')):
                raise Unavailable('Decision process/model mismatch')
            from local_supervisor import validate
            answer = document(folder / 'result.json')
            validate(answer, document(folder / 'schema.json'))
            value.update(verified=True, **model_facts(model), role=phase.get('role'),
                         coordinator_source=source_fact(payload(row['job_payload']).get('source_head')),
                         decision_outcome=answer.get('decision') if answer.get('decision') in {'accept','reject','revise','hold'} else None,
                         receipt=str((folder / 'local-decision-receipt.json').relative_to(self.runtime)),
                         receipt_sha256=digest(read_bytes(folder / 'local-decision-receipt.json')),
                         identity=receipt['input_hash'])
        except (OSError, ValueError, TypeError, KeyError):
            value['classification'] = 'completed_phase_without_verified_typed_receipt'
        return value

    def evidence(self, baseline, end):
        start = baseline['started_at']; result = {'accepted': [], 'phases': [], 'decisions': [], 'charges': [], 'cloud': [], 'rework': []}
        for name, project in self.projects().items():
            with database(project['state'] / 'history.sqlite3') as con:
                accepted = rows(con, "SELECT run_id,created_at,accepted_commit FROM qc_runs WHERE project_id=? AND outcome='accepted' ORDER BY run_id", (name,))
                attempts = rows(con, 'SELECT run_id,created_at,payload FROM qc_runs WHERE project_id=? AND created_at>=? AND created_at<? ORDER BY created_at,run_id', (name, start, end))
                for attempt in attempts:
                    value = payload(attempt['payload'])
                    result['rework'].append({'project': name, 'run': attempt['run_id'], 'created': attempt['created_at'],
                        'unchanged_retry_avoided': value.get('unchanged_retry') is True,
                        'unfinished_tool_call': value.get('unfinished_tool_call') is True,
                        'scope': 'run-start cohort; not a completion count'})
                tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'cr_reservations' in tables:
                    result['charges'] += [dict(r, kind='cloud_rescue_reservation') for r in rows(con, 'SELECT token AS id,project,created,state FROM cr_reservations WHERE project=? AND created>=? AND created<? ORDER BY created,token', (name, start, end))]
            old = {r['run_id'] for r in baseline['projects'][name]['accepted']}
            old_commits = {r['accepted_commit'] for r in baseline['projects'][name]['accepted']}
            for original in baseline['projects'][name].get('accepted_without_timestamp', []):
                try:
                    run = original['run_id']
                    if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', run):
                        raise Unavailable('Invalid original run ID')
                    receipt = document(project['state'] / 'runs' / run / 'accepted.json')
                    if receipt.get('run') != run or receipt.get('commit') != original['accepted_commit']:
                        raise Unavailable('Original acceptance identity changed')
                    historical = epoch(receipt['at']) < start
                except (OSError, ValueError, KeyError, TypeError):
                    historical = True
                    result['accepted'].append({'run': original['run_id'], 'project': name, 'verified': False,
                                               'classification': 'baseline_acceptance_timestamp_unresolved'})
                if historical:
                    old.add(original['run_id']); old_commits.add(original['accepted_commit'])
            for row in accepted:
                value = self.accepted(project, row, old, old_commits)
                if value:
                    result['accepted'].append(dict(value, project=name))
        with database(self.runtime / 'workflow.sqlite3') as con:
            phases = rows(con, "SELECT p.*,j.payload AS job_payload FROM wf_phases p JOIN wf_jobs j ON j.id=p.job WHERE p.updated>=? AND p.updated<? AND p.state IN ('succeeded','failed','interrupted') ORDER BY p.updated,p.id", (start, end))
            for row in phases:
                if row['project'] not in PROJECTS:
                    continue
                info = payload(row['payload']); model = payload(row['job_payload']).get('worker_model', {})
                ending = payload(row['result']) if row['result'] else {}
                reason = ending.get('reason')
                result['phases'].append({'phase': row['id'], 'project': row['project'], 'kind': row['kind'],
                    'role': info.get('role'), 'state': row['state'], 'at': row['updated'], 'started_at': row['created'],
                    **model_facts(model), 'coordinator_source': source_fact(payload(row['job_payload']).get('source_head')),
                    'code': numeric(ending.get('code')),
                    'reason': reason if reason in {'exited','deadline','interrupted','timeout','cancelled','owner_pause'} else 'unclassified'})
                if row['kind'] == 'local' and row['state'] == 'succeeded':
                    result['decisions'].append(self.decision(row))
            result['charges'] += rows(con, 'SELECT job,key,project,kind,created FROM wf_charges WHERE created>=? AND created<? ORDER BY created,job,key', (start, end))
        with database(self.runtime / 'history.sqlite3') as con:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'cp_invocations' in tables:
                for row in rows(con, 'SELECT id,purpose,created,result FROM cp_invocations WHERE created>=? AND created<? ORDER BY created,id', (start, end)):
                    outcome = payload(row['result']) if row['result'] else {}
                    result['cloud'].append({'id': row['id'], 'purpose': row['purpose'], 'created': row['created'],
                                            'completed_at': outcome.get('completed'), 'unresolved': not bool(outcome),
                                            'scope': 'pipeline invocation; project attribution unavailable'})
        return result

    def resources(self, day, start, end):
        samples = []
        for slot in range((day - 1) * 1440, day * 1440 + 1):
            path = self.directory / 'samples' / f'{slot:05}.json'
            if path.exists():
                sample = unseal(path)
                if sample['slot'] != slot:
                    raise Unavailable('Sample slot changed')
                anchor = start - (day - 1) * DAY
                if not anchor + slot * 60 <= sample['at'] < anchor + (slot + 1) * 60:
                    raise Unavailable('Sample time does not match its original slot')
                samples.append(sample)
        covered = 0.; active = Counter(); pressure = Counter(); charged = []; sources = set()
        controls = {project: Counter() for project in sorted(PROJECTS)}
        def category(value, project):
            value = value.get('controls', {})
            if value.get('available') is not True or project not in value.get('project_paused', {}):return None
            if value.get('installing'):return 'installation_hold'
            if not value.get('scheduled_eligible'):return 'outside_schedule'
            if value.get('global_paused') or value['project_paused'][project]:return 'scheduled_paused'
            return 'scheduled_unpaused'
        for sample in samples:
            if start <= sample['at'] < end:
                telemetry = sample['telemetry']; sources.add(sample['source'])
                if telemetry.get('available'):
                    pressure[telemetry.get('pressure', 'unknown')] += 1
                    charged += [v for v in telemetry.get('charged_bytes', []) if numeric(v) is not None]
        for left, right in zip(samples, samples[1:]):
            duration = min(right['at'], end) - max(left['at'], start)
            if not 0 < right['at'] - left['at'] <= 120 or duration <= 0:
                continue
            if left['telemetry'].get('available') and right['telemetry'].get('available'):
                covered += duration
            if left['active_available'] and right['active_available']:
                old = {(r['project'], r['kind'], r['phase']) for r in left['active']}
                new = {(r['project'], r['kind'], r['phase']) for r in right['active']}
                for project, kind in {(p, k) for p, k, phase in old & new}:
                    active[project + ':' + kind] += duration
            if (left.get('amendment_sha256') == right.get('amendment_sha256')
                    and left.get('controls', {}).get('schedule_sha256') == right.get('controls', {}).get('schedule_sha256')):
                for project in controls:
                    state = category(left, project)
                    if state and state == category(right, project):controls[project][state] += duration
        exposure = {project: {**{name: round(values[name], 3) for name in
                    ('scheduled_unpaused', 'scheduled_paused', 'installation_hold', 'outside_schedule')},
                    'unknown': round(DAY - sum(values.values()), 3)} for project, values in controls.items()}
        return {'sample_count': len(samples), 'telemetry_covered_seconds': round(covered, 3),
                'telemetry_unobserved_seconds': round(DAY - covered, 3), 'pressure_samples': dict(pressure),
                'peak_sampled_charged_bytes': max(charged) if charged else None,
                'sampled_continuous_active_seconds': dict(active), 'sources_observed': sorted(sources),
                'sampled_control_seconds': exposure,
                'scope': 'Adjacent observations at most 120 seconds apart; gaps and changed control boundaries are unknown, not idle. Pause/control duration is sampled, not exact. Sampled active time includes prefill/waiting.'}

    def report(self, baseline, evidence, day):
        start = baseline['started_at'] + (day - 1) * DAY; end = start + DAY
        inside = lambda value: start <= value < end
        expected_model = model_facts(baseline['config']['expected_model'])['model_descriptor_sha256']
        accepted = {}; uncertain = []; seen_commits = set(); late = []
        previously_reported = set(); previous_decisions = set()
        for earlier in range(1, day):
            path = self.directory / 'reports' / f'day-{earlier:02}.json'
            if path.exists():
                previous = unseal(path)
                previously_reported.update((r['project'], r['commit']) for r in
                                           previous['accepted'] + previous.get('late_verified_acceptances', []))
                previous_decisions.update((r['project'], r['identity']) for r in
                                          previous['typed_decisions'] + previous.get('late_verified_decisions', []))
        for value in sorted(evidence['accepted'], key=lambda r: r.get('at', float('inf'))):
            value = dict(value, model_attribution=attribution(value, expected_model))
            if not value['verified']:
                uncertain.append(value)
            else:
                key = (value['project'], value['commit'])
                if key not in seen_commits and key not in previously_reported and inside(value['at']):
                    accepted[key] = dict(value, carryover=value['started_at'] < baseline['started_at'])
                elif (key not in seen_commits and key not in previously_reported
                      and baseline['started_at'] <= value['at'] < start):
                    late.append(dict(value, original_day=int((value['at'] - baseline['started_at']) // DAY) + 1))
                seen_commits.add(key)
        decisions = {}; unverified = []; seen_decisions = set(); late_decisions = []
        for value in sorted(evidence['decisions'], key=lambda r: r['at']):
            value = dict(value, model_attribution=attribution(value, expected_model))
            key = (value['project'], value.get('identity'))
            prior = key in seen_decisions
            if value['verified']:
                seen_decisions.add(key)
            if not inside(value['at']):
                if (value['verified'] and not prior and key not in previous_decisions
                        and baseline['started_at'] <= value['at'] < start):
                    late_decisions.append(dict(value, original_day=int((value['at'] - baseline['started_at']) // DAY) + 1))
                continue
            if value['verified'] and not prior and key not in previous_decisions:
                decisions[(value['project'], value['identity'])] = dict(value, carryover=value['started_at'] < baseline['started_at'])
            elif not value['verified']:
                unverified.append(value)
        phases = [dict(p, carryover=p['started_at'] < baseline['started_at']) for p in evidence['phases'] if inside(p['at'])]
        counts = Counter(p['project'] + ':' + p['kind'] + ':' + p['state'] for p in phases)
        report = {'version': 1, 'day': day, 'start': start, 'end': end, 'baseline_sha256': digest(encoded(baseline)),
                'assessment_kind': 'deterministic host-evidence assessment; not Codex judgment',
                'expected_model_descriptor_sha256': expected_model,
                'accepted': list(accepted.values()), 'accepted_unverified': uncertain,
                'late_verified_acceptances': late,
                'late_verified_decisions': late_decisions,
                'typed_decisions': list(decisions.values()), 'typed_decisions_unverified': unverified,
                'phase_outcomes': dict(counts), 'phases': phases,
                'new_charges': [r for r in evidence['charges'] if inside(r['created'])],
                'cloud_invocations': [r for r in evidence['cloud'] if inside(r['created'])],
                'rework_start_cohort': [r for r in evidence['rework'] if inside(r['created'])],
                'resources': self.resources(day, start, end),
                'assessment': 'Accepted product progress verified.' if accepted else 'No new accepted product progress verified in this interval.',
                'limits': ['Passing a typed decision is not proof that code is correct.',
                           'Accepted receipts require passing checks, accepted review and local commit existence; no new tests or remote account audit run.',
                           'Phase failures and interruptions are separate; unclassified reasons are not attributed to model quality.',
                           'Native/provider failure causes are not machine-coded in these phase receipts; unclassified local phase failures are not proven model faults.',
                           'All-model totals, phase failures, sampled active time and pipeline cloud calls do not establish GLM-specific performance. Only exact recorded descriptor attribution identifies the activated GLM profile.',
                           'Charges are reservations/invocations, not currency costs; unknown prior charges remain historical.']}
        report['cloud_summary'] = {'day': day, 'interval_seconds': DAY, 'projects': {}}
        for project in sorted(PROJECTS):
            report['cloud_summary']['projects'][project] = {
                'accepted_commits_all_models': sum(r['project'] == project for r in report['accepted']),
                'accepted_carryover_all_models': sum(r['project'] == project and r['carryover'] for r in report['accepted']),
                'late_verified_acceptances_all_models': sum(r['project'] == project for r in late),
                'late_verified_decisions_all_models': sum(r['project'] == project for r in late_decisions),
                'typed_decisions_all_models': sum(r['project'] == project for r in report['typed_decisions']),
                'typed_decisions_unverified_all_models': sum(r['project'] == project for r in unverified),
                'accepted_receipts_unverified_all_models': sum(r['project'] == project for r in uncertain),
                'failed_phases_all_models': sum(r['project'] == project and r['state'] == 'failed' for r in phases),
                'interrupted_phases_all_models': sum(r['project'] == project and r['state'] == 'interrupted' for r in phases),
                'local_failures_unclassified_all_models': sum(r['project'] == project and r['kind'] == 'local' and r['state'] == 'failed' for r in phases),
                'rejected_typed_reviews_all_models': sum(r['project'] == project and r.get('role') in {'review','diagnosis_review'} and r.get('decision_outcome') in {'reject','revise','hold'} for r in report['typed_decisions']),
                'unchanged_retries_avoided_start_cohort_all_models': sum(r['project'] == project and r['unchanged_retry_avoided'] for r in report['rework_start_cohort']),
                'by_model': {}}
            for group in ('glm_current_profile', 'glm_other_profile', 'other_model', 'unknown'):
                same = lambda r: r['project'] == project and r['model_attribution'] == group
                report['cloud_summary']['projects'][project]['by_model'][group] = {
                    'accepted_commits': sum(same(r) for r in report['accepted']),
                    'accepted_carryover': sum(same(r) and r['carryover'] for r in report['accepted']),
                    'typed_decisions': sum(same(r) for r in report['typed_decisions']),
                    'typed_decisions_carryover': sum(same(r) and r['carryover'] for r in report['typed_decisions']),
                    'late_verified_acceptances': sum(same(r) for r in late),
                    'late_verified_decisions': sum(same(r) for r in late_decisions)}
        report['cloud_summary']['resources'] = {key: report['resources'][key] for key in
            ('sample_count', 'telemetry_covered_seconds', 'telemetry_unobserved_seconds', 'peak_sampled_charged_bytes')}
        report['cloud_summary']['new_charge_records'] = len(report['new_charges'])
        report['cloud_summary']['cloud_invocations'] = len(report['cloud_invocations'])
        epochs = self.epochs(baseline)
        report['configuration_epochs'] = epochs
        report['collector_sha256'] = digest(read_bytes(__file__))
        amendments = self.amendments()
        report['amendment_sha256'] = digest(encoded(amendments[-1])) if amendments else None
        cohorts = {}
        for project in sorted(PROJECTS):
            counts = {}
            for field, metric in (('accepted', 'accepted_commits'), ('typed_decisions', 'typed_decisions'),
                                  ('late_verified_acceptances', 'late_verified_acceptances'),
                                  ('late_verified_decisions', 'late_verified_decisions')):
                for value in report[field]:
                    if value['project'] != project:continue
                    descriptor = {'model_descriptor_sha256': value.get('model_descriptor_sha256'),
                        'source': value.get('coordinator_source'),
                        'model_kind': 'glm' if value.get('model') == 'colibri/glm-5.3' else 'other' if value.get('model') else 'unknown'}
                    key = 'cohort_' + digest(encoded(descriptor))
                    cohorts[key] = {**descriptor, 'approved_profile_epochs': [e['number'] for e in epochs
                        if value.get('model_descriptor_complete') and e['model_descriptor_sha256'] == value.get('model_descriptor_sha256')]}
                    counts.setdefault(key, {'accepted_commits': 0, 'typed_decisions': 0,
                                           'late_verified_acceptances': 0, 'late_verified_decisions': 0})[metric] += 1
            if len(counts) > 32:raise Unavailable('Too many profile cohorts for a bounded daily report')
            report['cloud_summary']['projects'][project]['by_cohort'] = counts
        report['profile_cohorts'] = cohorts
        report['cloud_summary']['resources']['sampled_control_seconds'] = report['resources']['sampled_control_seconds']
        report['evidence_ids'] = {f'accepted-{index:03}': {'receipt_sha256': value['receipt_sha256'], 'identity': value['commit']}
                                  for index, value in enumerate(report['accepted'])}
        report['evidence_ids'].update({f'decision-{index:03}': {'receipt_sha256': value['receipt_sha256'], 'identity': value['identity']}
                                      for index, value in enumerate(report['typed_decisions'])})
        report['evidence_ids'].update({f'late-accepted-{index:03}': {'receipt_sha256': value['receipt_sha256'], 'identity': value['commit']}
                                      for index, value in enumerate(late)})
        report['evidence_ids'].update({f'late-decision-{index:03}': {'receipt_sha256': value['receipt_sha256'], 'identity': value['identity']}
                                      for index, value in enumerate(late_decisions)})
        return report

    def publish(self, report):
        path = self.directory / 'reports' / f'day-{report["day"]:02}.json'
        write_once(path, report)
        text = (f"Midnight real-work review day {report['day']}\n{iso(report['start'])} to {iso(report['end'])} (exclusive)\n"
                f"{report['assessment']}\nVerified accepted commits (all models): {len(report['accepted'])}\n"
                f"Verified typed decisions (all models): {len(report['typed_decisions'])}\n"
                f"Exact activated GLM profile accepted commits: {sum(r['model_attribution'] == 'glm_current_profile' for r in report['accepted'])}\n"
                f"Exact activated GLM profile typed decisions: {sum(r['model_attribution'] == 'glm_current_profile' for r in report['typed_decisions'])}\n"
                f"Late-verified acceptances for earlier intervals: {len(report['late_verified_acceptances'])}\n"
                f"Late-verified typed decisions for earlier intervals: {len(report['late_verified_decisions'])}\n"
                f"Unverified acceptance receipts: {len(report['accepted_unverified'])}\n"
                f"Phase outcomes: {json.dumps(report['phase_outcomes'], sort_keys=True)}\n"
                f"Telemetry coverage: {report['resources']['telemetry_covered_seconds']} / 86400 seconds\n"
                f"New charge records: {len(report['new_charges'])}; pipeline cloud invocations: {len(report['cloud_invocations'])}. These are not currency costs.\n"
                'Deterministic evidence assessment only. Full identifiers, carry-over labels and limits are in the adjacent JSON.\n')
        # Text is derived solely from its immutable JSON and checked on repeats.
        write_raw_once(path.with_suffix('.txt'), text.encode())

    def tick(self, instant=None):
        instant = time.time() if instant is None else instant
        baseline = self.baseline()
        if instant < baseline['started_at']:
            raise Unavailable('Clock precedes activation')
        if any(p.is_symlink() for p in (self.directory, *self.directory.parents)):
            raise Unavailable('Linked review directory refused')
        with os.fdopen(os.open(self.directory / '.tick.lock', os.O_WRONLY | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600), 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.sample(baseline, instant)
            due = min(DAYS, int((instant - baseline['started_at']) // DAY))
            evidence = None; reports = []
            for day in range(1, due + 1):
                path = self.directory / 'reports' / f'day-{day:02}.json'
                if path.exists():
                    report = unseal(path)
                    if report['baseline_sha256'] != digest(encoded(baseline)) or report['day'] != day:
                        raise Unavailable('Report belongs to another review')
                else:
                    if evidence is None:
                        evidence = self.evidence(baseline, min(instant, baseline['ends_at']))
                    report = self.report(baseline, evidence, day)
                self.publish(report); reports.append(report)
            if due == DAYS:
                summary = {'start': baseline['started_at'], 'end': baseline['ends_at'],
                           'days': [{'day': r['day'], 'sha256': digest(encoded(r))} for r in reports],
                           'accepted_commits': sorted({(a['project'], a['commit']) for r in reports for a in r['accepted'] + r['late_verified_acceptances']}),
                           'typed_decisions': len({(a['project'], a['identity']) for r in reports for a in r['typed_decisions'] + r['late_verified_decisions']}),
                           'telemetry_covered_seconds': sum(r['resources']['telemetry_covered_seconds'] for r in reports),
                           'required_seconds': DAYS * DAY, 'assessment_kind': 'deterministic evidence, not Codex judgment'}
                write_once(self.directory / 'week-summary.json', summary)
                write_raw_once(self.directory / 'week-summary.txt',
                    (f"Seven-day Midnight evidence summary\n{iso(summary['start'])} to {iso(summary['end'])} (exclusive)\n"
                     f"Distinct verified accepted commits (all models): {len(summary['accepted_commits'])}\n"
                     f"Distinct verified typed decisions (all models): {summary['typed_decisions']}\n"
                     f"Telemetry coverage: {summary['telemetry_covered_seconds']} / {DAYS * DAY} seconds\n"
                     'Seven immutable daily reports retain failures, interruptions, carry-over work and evidence limits.\n'
                     'This is a deterministic evidence assessment, not Codex judgment or a model benchmark.\n').encode())
            return {'closed_days': due, 'complete': due == DAYS, 'next_due': None if due == DAYS else iso(baseline['started_at'] + (due + 1) * DAY)}

    def status(self):
        baseline = self.baseline(); closed = []
        for day in range(1, DAYS + 1):
            path = self.directory / 'reports' / f'day-{day:02}.json'
            if path.exists():
                value = unseal(path)
                if value['baseline_sha256'] != digest(encoded(baseline)):
                    raise Unavailable('Report baseline mismatch')
                closed.append(day)
        complete = (self.directory / 'week-summary.json').exists()
        if complete:
            summary = unseal(self.directory / 'week-summary.json')
            if summary['start'] != baseline['started_at'] or summary['end'] != baseline['ends_at'] or len(closed) != DAYS:
                raise Unavailable('Weekly summary is inconsistent')
        return {'started_at': iso(baseline['started_at']), 'ends_at': iso(baseline['ends_at']),
                'closed_days': closed, 'complete': complete,
                'scope': 'Private evidence collection; no code changes or model calls'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True); parser.add_argument('--review-dir', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init'); init.add_argument('--config', type=Path, required=True)
    commands.add_parser('tick'); commands.add_parser('status')
    args = parser.parse_args(); review = Review(args.root, args.review_dir)
    try:
        value = review.initialise(document(args.config)) if args.command == 'init' else review.tick() if args.command == 'tick' else review.status()
        print(json.dumps(value, indent=2)); return 0
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(json.dumps({'available': False, 'reason': type(error).__name__, 'detail': str(error)[:180]})); return 1


if __name__ == '__main__':
    raise SystemExit(main())
