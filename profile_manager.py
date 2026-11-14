"""Coordinator-owned additive quality-profile growth after accepted features.

SQLite is authoritative. Workers cannot write this runtime outside their clone.
Each update retains its accepted source identity, proposal, verification evidence,
original static profile hash and resulting version/hash. Read APIs never mutate.
"""
import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import time

import durability as D
from history_store import HistoryStore, bounded_limit, encoded
from quality_cycle import load_profile, profile_hash


class ProfileError(ValueError):
    pass


SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS qc_profile_updates(
       project_id TEXT NOT NULL,update_id TEXT NOT NULL,commit_sha TEXT NOT NULL,
       task_id TEXT NOT NULL,state TEXT NOT NULL,created_at REAL NOT NULL,
       payload TEXT NOT NULL,PRIMARY KEY(project_id,update_id),UNIQUE(project_id,commit_sha))''',
    '''CREATE INDEX IF NOT EXISTS qc_profile_pending ON qc_profile_updates(project_id,state,created_at,update_id)''',
    '''CREATE TABLE IF NOT EXISTS qc_profile_current(
       project_id TEXT PRIMARY KEY,static_hash TEXT NOT NULL,version INTEGER NOT NULL,
       profile_hash TEXT NOT NULL,payload TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS qc_profile_bases(
       project_id TEXT NOT NULL,static_hash TEXT NOT NULL,payload TEXT NOT NULL,
       PRIMARY KEY(project_id,static_hash))''',
    '''CREATE TABLE IF NOT EXISTS qc_profile_versions(
       project_id TEXT NOT NULL,version INTEGER NOT NULL,profile_hash TEXT NOT NULL,
       static_hash TEXT NOT NULL,update_id TEXT NOT NULL,created_at REAL NOT NULL,
       payload TEXT NOT NULL,PRIMARY KEY(project_id,version))''',
)


def _exists(con, table):
    return con is not None and con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _verify_schema(con):
    if _exists(con, 'qc_profile_updates') or _exists(con, 'qc_profile_current'):
        row = con.execute("SELECT value FROM qc_meta WHERE key='profile_manager_schema'").fetchone()
        if row is None or row[0] != '1':
            raise ProfileError('Unsupported profile manager schema')


def _initialize(con):
    for statement in SCHEMA:
        con.execute(statement)
    version = con.execute("SELECT value FROM qc_meta WHERE key='profile_manager_schema'").fetchone()
    if version and version[0] != '1':
        raise ProfileError('Unsupported profile manager schema')
    con.execute("INSERT OR IGNORE INTO qc_meta VALUES('profile_manager_schema','1')")


def _effective(con, project_id, static):
    _verify_schema(con)
    static = load_profile(static)
    if static.get('project_id', project_id) != project_id:
        raise ProfileError('Static quality profile belongs to another project')
    if not _exists(con, 'qc_profile_current'):
        return static
    row = con.execute('SELECT * FROM qc_profile_current WHERE project_id=?', (project_id,)).fetchone()
    if not row:
        return static
    if row['static_hash'] != profile_hash(static):
        raise ProfileError('Static profile changed; coordinator must reconcile the preserved additive profile')
    result = load_profile(json.loads(row['payload']))
    if profile_hash(result) != row['profile_hash'] or result['version'] != row['version']:
        raise ProfileError('Effective quality profile integrity mismatch')
    return result


def effective_profile(runtime, project_id, static):
    with HistoryStore(runtime).reader() as con:
        return _effective(con, project_id, static)


def status(runtime, project_id, *, connection=None):
    def observe(con):
        _verify_schema(con)
        counts = {}
        if _exists(con, 'qc_profile_updates'):
            counts = {row['state']: row['count'] for row in con.execute(
                'SELECT state,COUNT(*) AS count FROM qc_profile_updates WHERE project_id=? GROUP BY state', (project_id,))}
        row = con.execute('SELECT version,profile_hash,static_hash FROM qc_profile_current WHERE project_id=?',
                          (project_id,)).fetchone() if _exists(con, 'qc_profile_current') else None
        return {'pending_count': counts.get('pending', 0), 'needs_manager_count': counts.get('needs_manager', 0),
                'applied_count': counts.get('applied', 0),
                'feature_hold': bool(counts.get('pending', 0) + counts.get('needs_manager', 0)),
                'effective_version': row['version'] if row else None,
                'profile_hash': row['profile_hash'] if row else None,
                'static_hash': row['static_hash'] if row else None}
    if connection is not None:
        return observe(connection)
    with HistoryStore(runtime).reader() as con:
        return observe(con)


def _identity(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}', value):
        raise ProfileError('Invalid ' + label)
    return value


def _text(value, label, minimum=1, maximum=2000):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ProfileError('Missing or oversized ' + label)
    return value.strip()


def _path(value):
    if not isinstance(value, str) or not value or len(value) > 500 or '\\' in value or any(ord(c) < 32 for c in value):
        raise ProfileError('Invalid committed source path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ('.', '..', '.git') for part in path.parts) or str(path) != value:
        raise ProfileError('Source paths must be normalized repository-relative paths')
    return value


def _test_path(value):
    path = PurePosixPath(value)
    return path.suffix == '.py' and (path.name.startswith('test') or path.name.endswith('_test.py'))


def queue_update(runtime, project_id, accepted_task, commit, *, evidence=None):
    """Host calls this with the actual changed files after verified acceptance."""
    if not isinstance(project_id, str) or not project_id or not re.fullmatch(r'[a-f0-9]{40}', commit or ''):
        raise ProfileError('Profile update requires project and accepted commit identity')
    if not isinstance(accepted_task, dict) or accepted_task.get('kind', 'feature') != 'feature':
        raise ProfileError('Only accepted feature work queues profile growth')
    task_id = _identity(accepted_task.get('id'), 'accepted task identity')
    files = accepted_task.get('files')
    if not isinstance(files, list) or not files or len(files) > 40 or any(not isinstance(x, str) for x in files):
        raise ProfileError('Accepted task must identify bounded actual changed files')
    for path in files:
        _path(path)
    if len(encoded(accepted_task).encode()) > 64000:
        raise ProfileError('Accepted task exceeds the bounded profile-manager context')
    payload = {'project_id': project_id, 'task': accepted_task, 'commit': commit,
               'evidence': evidence or {'accepted_commit': commit}, 'state': 'pending'}
    digest = hashlib.sha256(encoded({'project': project_id, 'commit': commit, 'task': accepted_task}).encode()).hexdigest()
    payload.update(id='profile-' + digest[:32], created_at=time.time())
    store = HistoryStore(runtime)
    with store.transaction() as con:
        _initialize(con)
        prior = con.execute('SELECT payload FROM qc_profile_updates WHERE project_id=? AND commit_sha=?', (project_id, commit)).fetchone()
        if prior:
            old = json.loads(prior[0])
            if old['task'] != accepted_task or old['evidence'] != payload['evidence']:
                raise ProfileError('Conflicting accepted feature profile-update replay')
            return old
        con.execute('INSERT INTO qc_profile_updates VALUES(?,?,?,?,?,?,?)',
                    (project_id, payload['id'], commit, task_id, 'pending', payload['created_at'], encoded(payload)))
        store.append('profile_update_queued', project_id, payload, event_id=payload['id'] + ':queued',
                     task_id=task_id, occurred_at=payload['created_at'], connection=con)
    return payload


def pending_updates(runtime, project_id, limit=20):
    bounded_limit(limit)
    with HistoryStore(runtime).reader() as con:
        _verify_schema(con)
        rows = [] if not _exists(con, 'qc_profile_updates') else con.execute(
            "SELECT payload FROM qc_profile_updates WHERE project_id=? AND state IN ('pending','needs_manager') ORDER BY created_at,update_id LIMIT ?",
            (project_id, limit)).fetchall()
    return [json.loads(row[0]) for row in rows]


def _check_covers(check, test_path):
    """Recognize protected unittest contracts; never guess unknown execution."""
    command = check.get('command')
    if not isinstance(command, list) or any(not isinstance(arg, str) for arg in command):
        return False
    if len(command) < 3 or command[1:3] != ['-m', 'unittest']:
        return False
    args = command[3:]
    if any(arg.startswith('-k') for arg in args):
        return False
    if 'discover' in args:
        if not args or args[0] != 'discover':
            return False
        start, pattern = '.', 'test*.py'
        index = 1
        while index < len(args):
            arg = args[index]
            if arg in ('-s', '--start-directory', '-p', '--pattern'):
                if index + 1 >= len(args):
                    return False
                if arg in ('-s', '--start-directory'):
                    start = args[index + 1]
                else:
                    pattern = args[index + 1]
                index += 2
                continue
            elif arg.startswith('--start-directory='):
                start = arg.split('=', 1)[1]
            elif arg.startswith('--pattern='):
                pattern = arg.split('=', 1)[1]
            elif arg not in ('-v', '--verbose', '-q', '--quiet', '-f', '--failfast', '-c', '--catch', '-b', '--buffer', '--locals'):
                # Positional start/pattern/top-level forms and unfamiliar flags
                # need explicit coordinator handling; never assume root coverage.
                return False
            index += 1
        path = PurePosixPath(test_path)
        if start != '.':
            try:
                path.relative_to(PurePosixPath(_path(start)))
            except (ValueError, ProfileError):
                return False
        return fnmatch.fnmatchcase(path.name, pattern)
    module = test_path[:-3].replace('/', '.')
    # A class or method target does not establish that a new regression elsewhere
    # in the same file ran. Only an explicit whole-module target proves selection.
    return module in args


def _discovery_packages(check, test_path):
    """Explicit regular packages prevent silently skipped nested discovery."""
    args = check['command'][3:]
    if 'discover' not in args:
        return []
    start = '.'
    for index, arg in enumerate(args):
        if arg in ('-s', '--start-directory'):
            start = args[index + 1]
        elif arg.startswith('--start-directory='):
            start = arg.split('=', 1)[1]
    root = PurePosixPath(start)
    relative = PurePosixPath(test_path).relative_to(root)
    return [str(root.joinpath(*relative.parts[:index], '__init__.py')) for index in range(1, len(relative.parts))]


def validate_proposal(current_profile, pending_update, proposal, verify_source):
    """Pure host validation. Callback proves each path at the accepted commit."""
    current_profile = load_profile(current_profile)
    if not isinstance(proposal, dict) or set(proposal) != {'capability', 'contracts', 'workflow', 'coverage'}:
        raise ProfileError('Profile growth accepts only capability, contracts, workflow and coverage additions')
    if not callable(verify_source):
        raise ProfileError('Host committed-source verification is required')
    if len(encoded(proposal).encode()) > 32000:
        raise ProfileError('Profile proposal exceeds bounded review scope')
    capability, contracts, workflow, coverage = (proposal[key] for key in ('capability', 'contracts', 'workflow', 'coverage'))
    if not isinstance(capability, dict) or set(capability) != {'id', 'title', 'description', 'source_paths'}:
        raise ProfileError('Capability must identify its bounded accepted source')
    _identity(capability['id'], 'capability identity')
    _text(capability['title'], 'capability title', maximum=200)
    _text(capability['description'], 'capability description', minimum=20)
    if capability['id'] in {item['id'] for item in current_profile['capabilities']}:
        raise ProfileError('Existing capabilities cannot be modified or replaced')
    source_paths = capability['source_paths']
    if not isinstance(source_paths, list) or not 1 <= len(source_paths) <= 20 or any(not isinstance(x, str) for x in source_paths) or len(set(source_paths)) != len(source_paths):
        raise ProfileError('Capability requires bounded distinct source paths')
    accepted_paths = {_path(path) for path in pending_update['task']['files']}
    for path in source_paths:
        _path(path)
        if path not in accepted_paths or _test_path(path):
            raise ProfileError('Capability source must be an actual accepted product change')
    changed_python = {path for path in accepted_paths if path.endswith('.py') and not _test_path(path)}
    if not changed_python.issubset(source_paths):
        raise ProfileError('Every accepted Python product change needs explicit profile coverage')
    if not isinstance(contracts, list) or not 1 <= len(contracts) <= 20:
        raise ProfileError('Additive contracts are required')
    existing_contracts = {item['id'] for item in current_profile['contracts']}
    seen_contracts, contract_paths = set(), set()
    for contract in contracts:
        if not isinstance(contract, dict) or set(contract) != {'id', 'path', 'requirements'}:
            raise ProfileError('Contracts may only add requirements for accepted source')
        identity = _identity(contract['id'], 'contract identity')
        if identity in existing_contracts or identity in seen_contracts:
            raise ProfileError('Existing or duplicate contracts cannot be replaced')
        seen_contracts.add(identity)
        path = _path(contract['path'])
        if path not in source_paths:
            raise ProfileError('Contract source is outside the accepted capability')
        contract_paths.add(path)
        requirements = contract['requirements']
        if not isinstance(requirements, list) or not 1 <= len(requirements) <= 20:
            raise ProfileError('Contract requirements are required')
        for requirement in requirements:
            _text(requirement, 'contract requirement', minimum=10, maximum=1000)
    if set(source_paths) != contract_paths:
        raise ProfileError('Every capability source needs an additive contract')
    if not isinstance(workflow, dict) or set(workflow) != {'id', 'check_ids'}:
        raise ProfileError('Workflow may only reference protected existing checks')
    _identity(workflow['id'], 'workflow identity')
    if workflow['id'] in {item['id'] for item in current_profile['workflows']}:
        raise ProfileError('Existing workflows cannot be replaced')
    required = {check['id']: check for check in current_profile['checks'] if check.get('required', True)}
    ids = workflow['check_ids']
    if not isinstance(ids, list) or not ids or len(ids) > 20 or any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids) or not set(ids).issubset(required):
        raise ProfileError('Workflow must use existing required protected check IDs; new commands need manager review')
    if not isinstance(coverage, list) or not 1 <= len(coverage) <= 40:
        raise ProfileError('Accepted regression-test coverage evidence is required')
    covered, verified = set(), set()
    for entry in coverage:
        if not isinstance(entry, dict) or set(entry) != {'source_path', 'test_path', 'check_id', 'explanation'}:
            raise ProfileError('Regression coverage needs source, accepted test, protected check and explanation')
        source, test = _path(entry['source_path']), _path(entry['test_path'])
        check = entry['check_id']
        if source not in source_paths or test not in accepted_paths or not _test_path(test) or not isinstance(check, str) or check not in ids:
            raise ProfileError('Regression evidence is outside the accepted source/test/check contract')
        if not _check_covers(required[check], test):
            raise ProfileError('Protected check does not demonstrate execution of the accepted regression test')
        _text(entry['explanation'], 'regression coverage explanation', minimum=20)
        covered.add(source)
        verified.update((source, test))
        # Package initializers can predate this feature, but must exist in its
        # exact accepted tree. Otherwise unittest discovery may skip the file.
        verified.update(_discovery_packages(required[check], test))
    if covered != set(source_paths):
        raise ProfileError('Capability source lacks accepted regression coverage')
    for path in sorted(verified):
        if verify_source(pending_update['commit'], path) is not True:
            raise ProfileError('Host cannot verify source or regression test at the accepted commit: ' + path)
    return json.loads(encoded(proposal))


def apply_update(runtime, project_id, static, update_id, proposal, verify_source):
    store = HistoryStore(runtime)
    static = load_profile(static)
    with store.transaction() as con:
        _initialize(con)
        row = con.execute('SELECT payload FROM qc_profile_updates WHERE project_id=? AND update_id=?', (project_id, update_id)).fetchone()
        if not row:
            raise ProfileError('Unknown accepted feature profile update')
        pending = json.loads(row[0])
        current = _effective(con, project_id, static)
        proposal_hash = hashlib.sha256(encoded(proposal).encode()).hexdigest()
        if pending['state'] == 'applied':
            if pending.get('proposal_hash') != proposal_hash:
                raise ProfileError('Conflicting applied profile-update replay')
            D.save_json(Path(runtime) / 'effective-quality-profile.json',
                        {'project_id': project_id, 'static_hash': profile_hash(static),
                         'profile_hash': profile_hash(current), 'profile': current})
            return current
        proposal = validate_proposal(current, pending, proposal, verify_source)
        result = json.loads(encoded(current))
        result['version'] += 1
        result['capabilities'].append(dict(proposal['capability'], status='shipped', accepted_commit=pending['commit']))
        result['contracts'].extend(proposal['contracts'])
        result['workflows'].append(proposal['workflow'])
        result['profile_manager'] = {'static_hash': profile_hash(static), 'last_update_id': update_id,
                                     'last_accepted_commit': pending['commit']}
        result = load_profile(result)
        digest, base_digest, created = profile_hash(result), profile_hash(static), time.time()
        pending.update(state='applied', proposal=proposal, proposal_hash=proposal_hash,
                       applied_at=created, effective_version=result['version'], effective_profile_hash=digest)
        con.execute('INSERT OR IGNORE INTO qc_profile_bases VALUES(?,?,?)', (project_id, base_digest, encoded(static)))
        con.execute('INSERT INTO qc_profile_current VALUES(?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET static_hash=excluded.static_hash,version=excluded.version,profile_hash=excluded.profile_hash,payload=excluded.payload',
                    (project_id, base_digest, result['version'], digest, encoded(result)))
        con.execute('INSERT INTO qc_profile_versions VALUES(?,?,?,?,?,?,?)',
                    (project_id, result['version'], digest, base_digest, update_id, created, encoded(pending)))
        con.execute('UPDATE qc_profile_updates SET state=?,payload=? WHERE project_id=? AND update_id=?',
                    ('applied', encoded(pending), project_id, update_id))
        store.append('profile_updated', project_id, pending, event_id=update_id + ':applied',
                     task_id=pending['task']['id'], occurred_at=created, connection=con)
    # SQLite commits first. This checkpoint mirror is useful to inspect/recover;
    # a failed mirror write never rolls back the authoritative profile or hold.
    mirror = Path(runtime) / 'effective-quality-profile.json'
    D.save_json(mirror, {'project_id': project_id, 'static_hash': base_digest, 'profile_hash': digest, 'profile': result})
    return result


def mark_needs_manager(runtime, project_id, update_id, reason):
    reason = _text(reason, 'profile manager hold reason', maximum=1000)
    store = HistoryStore(runtime)
    with store.transaction() as con:
        _initialize(con)
        row = con.execute('SELECT payload FROM qc_profile_updates WHERE project_id=? AND update_id=?', (project_id, update_id)).fetchone()
        if not row:
            raise ProfileError('Unknown profile update')
        pending = json.loads(row[0])
        if pending['state'] == 'applied':
            return pending
        if pending['state'] == 'needs_manager' and pending.get('reason') == reason:
            return pending
        pending.update(state='needs_manager', reason=reason)
        con.execute('UPDATE qc_profile_updates SET state=?,payload=? WHERE project_id=? AND update_id=?',
                    ('needs_manager', encoded(pending), project_id, update_id))
        store.append('profile_update_held', project_id, pending, task_id=pending['task']['id'], connection=con)
        return pending


def missing_evidence(reason):
    reason=str(reason).lower()
    if any(s in reason for s in ('owner must','owner decision','permission required','authority required','scope decision')):return False
    return any(s in reason for s in ('truncated','no repository-reading','no available tool','missing evidence','complete accepted test source','execution evidence'))


def provide_evidence(runtime,project_id,update_id,evidence_hash,*,complete):
    """Reconsider a missing-evidence hold once per genuinely changed proof."""
    if not re.fullmatch('[0-9a-f]{64}',evidence_hash):raise ProfileError('Invalid profile evidence identity')
    store=HistoryStore(runtime)
    with store.transaction() as con:
        _initialize(con)
        row=con.execute('SELECT payload FROM qc_profile_updates WHERE project_id=? AND update_id=?',(project_id,update_id)).fetchone()
        if not row:raise ProfileError('Unknown profile update')
        value=json.loads(row[0])
        if value['state']=='applied':return value
        if value.get('context_hash')==evidence_hash:return value
        missing=missing_evidence(value.get('reason',''))
        if value['state']=='needs_manager' and (not complete or not missing):return value
        old=value['state'];previous_hash=value.get('context_hash');value['context_hash']=evidence_hash
        if old=='needs_manager':
            value.setdefault('prior_holds',[]).append({'reason':value.get('reason'),'context_hash':previous_hash})
            value['state']='pending';value['reason']='New complete source and actual protected-check evidence supplied for reconsideration'
        con.execute('UPDATE qc_profile_updates SET state=?,payload=? WHERE project_id=? AND update_id=?',(value['state'],encoded(value),project_id,update_id))
        if old=='needs_manager':store.append('profile_evidence_reconsideration',project_id,value,task_id=value['task']['id'],connection=con)
        return value
