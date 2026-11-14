"""One explicitly activated, retained project action with an immutable deadline.

The grant and its single job reservation share the existing workflow transaction.
Observations never write, launch or renew a grant. Terminal/unknown work is spent.
"""
import datetime as dt
import json
from pathlib import Path
import re
import uuid
import durability as D
from action_store import Actions, encoded, fingerprint
import schedule_control as S
import worker_quality as Q

KEY = 'owner_commissioning_v1'
MAX_SECONDS = 9 * 3600


def _stamp(instant):
    if instant.tzinfo is None: raise ValueError('Commissioning requires an aware timestamp')
    return instant.timestamp()


def retained_task(project, task_id, scope_id=None):
    state = D.read_json(Path(project['state_dir']) / 'state.json', {})
    tasks = [state.get(key, {}).get('task') for key in ('pending', 'active_attempt')]
    matches = [task for task in tasks if isinstance(task, dict) and task.get('id') == task_id]
    if not matches and scope_id:
        from manager_recovery import ManagerRecovery
        matches = [task for task in ManagerRecovery(Path(project['state_dir']), project['id']).tasks() if task.get('id') == task_id]
    if not matches or any(Q.task_fingerprint(task) != Q.task_fingerprint(matches[0]) for task in matches):
        raise ValueError('Commissioning must match one unchanged retained task')
    return matches[0]


def retained_scope(project, task, scope_id):
    if not scope_id: return None
    from history_store import HistoryStore
    with HistoryStore(Path(project['state_dir'])).reader() as con:
        row = con.execute('SELECT * FROM pr_scopes WHERE id=? AND project=?', (scope_id, project['id'])).fetchone() if con else None
    if not row or row['task_hash'] != Q.task_fingerprint(task) or json.loads(row['payload']).get('task_id') != task['id']:
        raise ValueError('Commissioning recovery scope does not match the retained task')
    return {key: row[key] for key in ('id', 'project', 'episode', 'task_hash', 'stage', 'head', 'profile')}


def _claim(value):
    claim = {key: value.get(source) for key, source in
             (('grant_id', 'id'), ('project', 'project'), ('task_id', 'task_id'), ('task_hash', 'task_hash'), ('deadline', 'deadline'))}
    if value.get('recovery_scope'): claim['recovery_scope'] = value['recovery_scope']
    return claim


def activate(runtime, registry, project_id, task_id, deadline, source_head, worker_model, reason, instant, *, scope_id=None):
    """Audited owner operation. A prior grant is never replaced or extended."""
    runtime = Path(runtime); start = _stamp(instant); end = _stamp(deadline)
    if not 0 < end - start <= MAX_SECONDS: raise ValueError('Commissioning lasts at most nine hours')
    project = next((p for p in registry['projects'] if p['id'] == project_id), None)
    if not project or not project.get('on_schedule'): raise ValueError('Commissioning requires a registered scheduled project')
    if any(p.exists() or p.is_symlink() for p in (runtime/'PAUSED', runtime/'INSTALLING', Path(project['state_dir'])/'PAUSED')):
        raise ValueError('Commissioning cannot override a pause or installation fence')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000: raise ValueError('A bounded owner reason is required')
    if not isinstance(source_head, str) or not re.fullmatch('[a-f0-9]{40}', source_head) or not isinstance(worker_model, dict) or not worker_model:
        raise ValueError('Exact source and worker descriptor are required')
    from project_backend import validate_worker_model
    validate_worker_model(worker_model)
    task = retained_task(project, task_id, scope_id)
    value = {'schema_version': 1, 'id': 'commission-'+uuid.uuid4().hex,
             'project': project_id, 'task_id': task_id, 'task_hash': Q.task_fingerprint(task),
             'project_state_dir': project['state_dir'],
             'created_at': instant.isoformat(), 'deadline': end, 'source_head': source_head,
             'worker_model': worker_model, 'worker_model_hash': fingerprint(worker_model),
             'reason': reason.strip(), 'job_id': None,
             'recovery_scope': retained_scope(project, task, scope_id),
             'resume_schedule_at': S.window(instant, registry)['next_start'].timestamp()}
    store = Actions(runtime)
    with store.write() as con:
        if con.execute('SELECT 1 FROM wf_meta WHERE key=?', (KEY,)).fetchone():
            raise ValueError('The one-use commissioning grant already exists; it cannot be renewed')
        jobs = [store.unpack(row) for row in con.execute("SELECT * FROM wf_jobs WHERE state IN ('queued','running')")]
        if any(not S.performance_review(job) for job in jobs): raise ValueError('Drain existing project and discussion actions before activation')
        con.execute('INSERT INTO wf_meta VALUES(?,?)', (KEY, encoded(value)))
        store.event(con, project_id, 'commissioning_activated', value['id'],
                    {k: value[k] for k in ('task_id', 'task_hash', 'deadline', 'source_head', 'worker_model_hash', 'reason')})
    return snapshot(runtime, instant)


def snapshot(runtime, instant):
    store = Actions(runtime); value = store.meta(KEY)
    if not value: return {'available': True, 'active': False, 'state': 'absent', 'blocks_dispatch': False}
    stamp = _stamp(instant); job = store.get(value['job_id']) if value.get('job_id') else None
    state = ('expired' if stamp >= value['deadline'] else
             'armed' if not value.get('job_id') else job['state'] if job else 'unknown')
    active = state in {'armed', 'queued', 'running'}
    return dict(value, available=True, active=active, state=state, job_state=(job or {}).get('state'),
                blocks_dispatch=active or stamp < value['resume_schedule_at'])


def active(runtime, instant):
    return snapshot(runtime, instant)['active']


def permits(runtime, job, instant):
    if not job or job.get('state') not in {'queued', 'running'}: return False
    value = snapshot(runtime, instant); payload = job.get('payload', {}); claim = payload.get('commissioning', {})
    if any(path.exists() or path.is_symlink() for path in (Path(runtime)/'PAUSED', Path(runtime)/'INSTALLING', Path(runtime)/'WEEKDAY_ACTIVATION_PENDING')): return False
    if value.get('project_state_dir') and (Path(value['project_state_dir'])/'PAUSED').exists(): return False
    return bool(value['active'] and value.get('job_id') == job.get('id') and
        value.get('project') == job.get('project') and job.get('deadline') == value.get('deadline') and
        payload.get('source_head') == value.get('source_head') and
        fingerprint(payload.get('worker_model')) == value.get('worker_model_hash') and
        claim == _claim(value))


def reserve(runtime, project, payload, instant):
    """Atomically consume the grant and create its only job; replay returns that job."""
    runtime = Path(runtime); store = Actions(runtime); stamp = _stamp(instant)
    with store.write() as con:
        row = con.execute('SELECT value FROM wf_meta WHERE key=?', (KEY,)).fetchone()
        if not row: raise ValueError('No commissioning grant')
        grant = json.loads(row[0])
        if grant.get('job_id'):
            return store.unpack(con.execute('SELECT * FROM wf_jobs WHERE id=?', (grant['job_id'],)).fetchone())
        if stamp >= grant['deadline']: raise ValueError('Commissioning deadline expired')
        scope_id=(grant.get('recovery_scope') or {}).get('id')
        task=retained_task(project, grant['task_id'], scope_id)
        if (project['id'] != grant['project'] or payload.get('source_head') != grant['source_head'] or
                fingerprint(payload.get('worker_model')) != grant['worker_model_hash'] or
                Q.task_fingerprint(task) != grant['task_hash'] or retained_scope(project,task,scope_id)!=grant.get('recovery_scope')):
            raise ValueError('Commissioning source, model or retained task changed')
        if any(p.exists() or p.is_symlink() for p in (runtime/'PAUSED', runtime/'INSTALLING', runtime/'WEEKDAY_ACTIVATION_PENDING', Path(project['state_dir'])/'PAUSED')):
            raise ValueError('Commissioning is paused or fenced')
        jobs = [store.unpack(row) for row in con.execute("SELECT * FROM wf_jobs WHERE state IN ('queued','running')")]
        if any(not S.performance_review(job) for job in jobs): return None
        identity = 'job-'+uuid.uuid4().hex
        payload = dict(payload, resume=True, commissioning=_claim(grant))
        con.execute('INSERT INTO wf_jobs VALUES(?,?,?,?,?,?,?,?,?)',
                    (identity, project['id'], 'queued', stamp, stamp, grant['deadline'], encoded(payload), None, None))
        grant['job_id'] = identity
        con.execute('UPDATE wf_meta SET value=? WHERE key=?', (encoded(grant), KEY))
        store.event(con, project['id'], 'action_queued', identity, {'deadline': grant['deadline'], 'commissioning': grant['id']})
        store.event(con, project['id'], 'commissioning_consumed', grant['id'], {'job_id': identity})
        return store.unpack(con.execute('SELECT * FROM wf_jobs WHERE id=?', (identity,)).fetchone())
