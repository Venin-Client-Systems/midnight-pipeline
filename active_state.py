"""Bound active checkpoints while retaining complete, indexed terminal history.

Call only at a host-owned idle boundary. A verified durable snapshot and SQLite
transaction precede removal from the returned state. The caller checkpoints that
state using durability.py. Replaying an old checkpoint is safe and conservative.
"""
import gzip
import hashlib
import json
from pathlib import Path
import time

import durability as D
from history_store import HistoryStore, bounded_limit, encoded, timestamp

KEEP_ACCEPTED = 50
TASK_MAPS = ('tries', 'feedback', 'feedback_history', 'review_findings', 'retry_candidates',
             'feature_tickets', 'issue_episodes', 'superseded_tasks', 'verification_attempts')
EPISODE_MAPS = ('episode_failure_imports', 'issue_guidance', 'rescue_decisions')
SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS qc_terminal_tasks(
       project_id TEXT NOT NULL,task_id TEXT NOT NULL,disposition TEXT NOT NULL,
       archived_at REAL NOT NULL,snapshot_sha256 TEXT NOT NULL,payload TEXT NOT NULL,
       PRIMARY KEY(project_id,task_id))''',
    '''CREATE TABLE IF NOT EXISTS qc_terminal_acceptances(
       project_id TEXT NOT NULL,run_id TEXT NOT NULL,task_id TEXT NOT NULL,
       accepted_at REAL NOT NULL,payload TEXT NOT NULL,
       PRIMARY KEY(project_id,run_id))''',
    '''CREATE INDEX IF NOT EXISTS qc_terminal_acceptances_time
       ON qc_terminal_acceptances(project_id,accepted_at,run_id)''',
    '''CREATE TABLE IF NOT EXISTS qc_state_archives(
       project_id TEXT NOT NULL,sha256 TEXT NOT NULL,path TEXT NOT NULL,
       state_sha256 TEXT NOT NULL,created_at REAL NOT NULL,
       PRIMARY KEY(project_id,sha256))''',
)


def _has_table(con, name):
    return con is not None and bool(con.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?', (name,)).fetchone())


def task_done(runtime, project_id, identity):
    """Terminal disposition, including a superseded task whose chain is closed.

    This is scheduling/dependency evidence, not an accepted-achievement count.
    """
    with HistoryStore(runtime).reader() as con:
        if not _has_table(con, 'qc_terminal_tasks'):
            return False
        return bool(con.execute('SELECT 1 FROM qc_terminal_tasks WHERE project_id=? AND task_id=?',
                                (project_id, identity)).fetchone())


def tasks_done(runtime, project_id, identities):
    """Bounded task lookup using one read connection for one observation."""
    identities=sorted(set(identities));done=set()
    if not identities:return done
    with HistoryStore(runtime).reader() as con:
        if not _has_table(con,'qc_terminal_tasks'):return done
        for start in range(0,len(identities),500):
            part=identities[start:start+500]
            done.update(row[0] for row in con.execute('SELECT task_id FROM qc_terminal_tasks WHERE project_id=? AND task_id IN ('+','.join('?' for _ in part)+')',(project_id,*part)))
    return done


def accepted_since(runtime, project_id, started_at, *, limit=500, cursor=None):
    """One ascending page of actual accepted receipts, inclusive start time.

    Continue with cursor=(timestamp(last['at']), last['run']). Callers union
    receipts still in active state by run ID; recent accepted summaries overlap.
    """
    bounded_limit(limit)
    clauses = ['project_id=?', 'accepted_at>=?']
    args = [project_id, timestamp(started_at)]
    if cursor is not None:
        if not isinstance(cursor, (tuple, list)) or len(cursor) != 2:
            raise ValueError('Acceptance cursor must be (timestamp, run_id)')
        clauses.append('(accepted_at,run_id)>(?,?)')
        args.extend([timestamp(cursor[0]), str(cursor[1])])
    with HistoryStore(runtime).reader() as con:
        if not _has_table(con, 'qc_terminal_acceptances'):
            return []
        rows = con.execute('SELECT payload FROM qc_terminal_acceptances WHERE ' + ' AND '.join(clauses) +
                           ' ORDER BY accepted_at,run_id LIMIT ?', args + [limit]).fetchall()
    return [json.loads(row[0]) for row in rows]


def accepted_count(runtime, project_id, state):
    """Actual accepted receipts, unioned by run ID across archive and hot state."""
    active = {row['run'] for row in state.get('accepted', [])
              if isinstance(row, dict) and row.get('run') and row.get('commit')}
    with HistoryStore(runtime).reader() as con:
        if not _has_table(con, 'qc_terminal_acceptances'):
            return len(active)
        total = con.execute('SELECT COUNT(*) FROM qc_terminal_acceptances WHERE project_id=?', (project_id,)).fetchone()[0]
        identities = list(active)
        for offset in range(0, len(identities), 400):
            batch = identities[offset:offset + 400]
            marks = ','.join('?' for _ in batch)
            active.difference_update(row[0] for row in con.execute(
                'SELECT run_id FROM qc_terminal_acceptances WHERE project_id=? AND run_id IN (' + marks + ')',
                [project_id] + batch))
        return total + len(active)


def local_attempt_total(state):
    values = list(state.get('tries', {}).values()) + [state.get('retired_local_attempts', 0)]
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError('Local attempt counts must be nonnegative integers')
    return sum(values)


def _task_id(value):
    if isinstance(value, dict):
        return value.get('id')
    return value if isinstance(value, str) else None


def _accepted_key(receipt):
    # Missing dates stay in the source snapshot but cannot invent timing evidence.
    return timestamp(receipt['at']), receipt.get('run', '')


def _closed_and_pinned(runtime, project_id, state, tasks):
    done = set(state.get('done', []))
    accepted_proofs = {row['id']: {'accepted_task': row['id'], 'accepted_run': row['run'], 'accepted_commit': row['commit']}
                       for row in state.get('accepted', [])
                       if isinstance(row, dict) and row.get('id') and row.get('commit') and row.get('run')}
    terminal = done | set(accepted_proofs)
    successors = dict(state.get('superseded_tasks', {}))
    for identity, task in tasks.items():
        successor = task.get('superseded_by')
        if isinstance(successor, str):
            successors[identity] = successor
    # Seed backlogs retain old task definitions after their active done entries
    # are retired. Consult only relevant indexed identities, never the full log.
    relevant = list(set(tasks) | terminal | set(successors) | set(successors.values()))
    proof_targets = set(successors.values())
    with HistoryStore(runtime).reader() as con:
        if _has_table(con, 'qc_terminal_tasks'):
            for offset in range(0, len(relevant), 400):
                batch = relevant[offset:offset + 400]
                marks = ','.join('?' for _ in batch)
                for row in con.execute(
                    'SELECT task_id,payload FROM qc_terminal_tasks WHERE project_id=? AND task_id IN (' + marks + ')',
                    [project_id] + batch):
                    terminal.add(row[0])
                    if row[0] in proof_targets and row[0] not in accepted_proofs:
                        saved = json.loads(row[1])
                        receipts = [receipt for receipt in saved.get('accepted', []) if receipt.get('run') and receipt.get('commit')]
                        if receipts:
                            receipt = receipts[-1]
                            accepted_proofs[row[0]] = {'accepted_task': row[0], 'accepted_run': receipt['run'], 'accepted_commit': receipt['commit']}
                        elif (saved.get('terminal_proof') or {}).get('kind') == 'accepted_successor_chain':
                            accepted_proofs[row[0]] = {key: saved['terminal_proof'][key]
                                                      for key in ('accepted_task', 'accepted_run', 'accepted_commit')}
    # A failed/superseded source closes only after its whole successor chain closes.
    predecessors = {}
    for source, successor in successors.items():
        predecessors.setdefault(successor, []).append(source)
    frontier = list(terminal)
    while frontier:
        for source in predecessors.get(frontier.pop(), []):
            if source not in terminal:
                terminal.add(source)
                frontier.append(source)
    verified_closed = dict(accepted_proofs)
    frontier = list(verified_closed)
    while frontier:
        successor = frontier.pop()
        for source in predecessors.get(successor, []):
            if source not in verified_closed:
                verified_closed[source] = verified_closed[successor]
                frontier.append(source)
    terminal_proofs = {source: dict(verified_closed[successor], kind='accepted_successor_chain', direct_successor=successor)
                       for source, successor in successors.items() if successor in verified_closed}
    # A completed successor makes old source drafts historical evidence. They
    # are archived below before removal. Live candidates and uncertain closure
    # stay pinned; active/pending/open-finding and descendant pins override this.
    pinned = set(state.get('retry_candidates', {})) - set(terminal_proofs)
    for name in ('active_attempt', 'pending'):
        value = state.get(name) or {}
        identity = _task_id(value.get('task')) if isinstance(value, dict) else None
        if identity:
            pinned.add(identity)
    intent = D.read_json(Path(runtime) / 'promotion-intent.json', default={}) or {}
    if isinstance(intent, dict):
        identity = _task_id(intent.get('task'))
        if identity:
            pinned.add(identity)
    # An unresolved finding remains authoritative even if a checkpoint says done.
    with HistoryStore(runtime).reader() as con:
        open_findings = set()
        if con is not None:
            open_findings = {row[0] for row in con.execute('SELECT finding_id FROM qc_findings WHERE project_id=? AND state=\'open\'', (project_id,))}
    for identity, task in tasks.items():
        if set(task.get('finding_ids', [])) & open_findings:
            pinned.add(identity)
    unresolved = (set(tasks) - terminal) | pinned
    # Preserve exact original contracts and retry proof for any live descendant.
    parents = {}
    for identity, task in tasks.items():
        source = task.get('retry_of') or {}
        if isinstance(source, dict) and source.get('task_id'):
            parents[identity] = source['task_id']
        if task.get('lineage_parent'):
            parents[identity] = task['lineage_parent']
    for source, successor in successors.items():
        parents.setdefault(successor, source)
    frontier = list(unresolved)
    seen = set(frontier)
    while frontier:
        parent = parents.get(frontier.pop())
        if parent and parent not in seen:
            seen.add(parent)
            frontier.append(parent)
    pinned |= seen
    return terminal - pinned, successors, terminal_proofs


def compact(runtime, project_id, state, seed_tasks):
    """Archive terminal task facts, keep fifty accepted summaries and live proof.

    The active checkpoint is not written here. A caller must hold its supervisor
    lock and save the returned value durably. No worker or remote call occurs.
    """
    if not isinstance(state, dict) or not isinstance(seed_tasks, list) or not project_id:
        raise ValueError('Compaction requires project identity, state and source task list')
    for key in ('done', 'accepted', 'planned'):
        if not isinstance(state.get(key, []), list):
            raise ValueError('Invalid active state list: ' + key)
    for key in TASK_MAPS + EPISODE_MAPS:
        if not isinstance(state.get(key, {}), dict):
            raise ValueError('Invalid active state mapping: ' + key)
    local_attempt_total(state)
    runtime = Path(runtime)
    tasks = {task['id']: task for task in seed_tasks + state.get('planned', []) if isinstance(task, dict) and task.get('id')}
    terminal, successors, terminal_proofs = _closed_and_pinned(runtime, project_id, state, tasks)
    if not terminal:
        return state
    result = json.loads(encoded(state))
    accepted_by_run = {}
    for receipt in state.get('accepted', []):
        if not isinstance(receipt, dict) or not receipt.get('run'):
            raise ValueError('Accepted receipt requires a stable run identity')
        existing = accepted_by_run.get(receipt['run'])
        if existing is not None and existing != receipt:
            raise ValueError('Conflicting active accepted receipt replay')
        accepted_by_run[receipt['run']] = receipt
    ordered = sorted(accepted_by_run.values(), key=_accepted_key)
    recent_runs = {receipt.get('run') for receipt in ordered[-KEEP_ACCEPTED:]}
    result['accepted'] = [receipt for receipt in ordered if receipt.get('run') in recent_runs or receipt.get('id') not in terminal]
    result['done'] = [identity for identity in state.get('done', []) if identity not in terminal]
    result['planned'] = [task for task in state.get('planned', []) if task.get('id') not in terminal]
    retired = sum(state.get('tries', {}).get(identity, 0) for identity in terminal)
    result['retired_local_attempts'] = state.get('retired_local_attempts', 0) + retired
    for name in TASK_MAPS:
        if name in state:
            result[name] = {key: value for key, value in state[name].items() if key not in terminal}
    live_episodes = {row.get('episode') for row in result.get('issue_episodes', {}).values() if isinstance(row, dict)}
    retired_episodes = {state.get('issue_episodes', {}).get(identity, {}).get('episode') for identity in terminal} - live_episodes - {None}
    for name in EPISODE_MAPS:
        if name in state:
            result[name] = {key: value for key, value in state[name].items() if key not in retired_episodes}
    # Do not create a fresh large archive merely because recent summaries overlap.
    if result == state:
        return state
    archive_envelope = {'version': 1, 'project_id': project_id, 'state': state, 'seed_tasks': seed_tasks}
    raw = encoded(archive_envelope).encode()
    digest = hashlib.sha256(raw).hexdigest()
    archive = runtime / 'state-archives' / (digest + '.json.gz')
    if archive.is_symlink():
        raise ValueError('State archive must not be a symlink')
    if not archive.exists():
        D.atomic_write(archive, gzip.compress(raw, mtime=0))
    if hashlib.sha256(gzip.decompress(archive.read_bytes())).hexdigest() != digest:
        raise ValueError('State archive failed read-back verification')
    accepts = {}
    for receipt in state.get('accepted', []):
        if receipt.get('id') in terminal:
            if not receipt.get('commit') or not receipt.get('run'):
                raise ValueError('Accepted task compaction requires commit and run evidence')
            _accepted_key(receipt)
            accepts.setdefault(receipt['id'], []).append(receipt)
    facts = []
    done_ids = set(state.get('done', []))
    for identity in sorted(terminal):
        episode = state.get('issue_episodes', {}).get(identity, {}).get('episode')
        fact = {'task_id': identity, 'task': tasks.get(identity), 'done': identity in done_ids,
                'disposition': 'superseded' if identity in successors and identity not in accepts else 'accepted' if identity in accepts else 'done',
                'accepted': accepts.get(identity, []),
                'terminal_proof': terminal_proofs.get(identity),
                'task_state': {name: state[name][identity] for name in TASK_MAPS if identity in state.get(name, {})},
                'episode': episode,
                'episode_state': {name: state[name][episode] for name in EPISODE_MAPS if episode in state.get(name, {})}}
        facts.append((identity, fact))
    store = HistoryStore(runtime)
    created = time.time()
    with store.transaction() as con:
        for statement in SCHEMA:
            con.execute(statement)
        for identity, fact in facts:
            prior = con.execute('SELECT payload FROM qc_terminal_tasks WHERE project_id=? AND task_id=?', (project_id, identity)).fetchone()
            if prior:
                saved = json.loads(prior[0])
                if saved.get('task') and fact.get('task') and saved['task'] != fact['task']:
                    raise ValueError('A terminal task identity was reused with another contract')
                # A crash can replay a reduced checkpoint. Keep the original full facts.
                if fact.get('task') is None:
                    fact['task'] = saved.get('task')
                fact['done'] = fact['done'] or saved.get('done', False)
                fact['episode'] = fact['episode'] or saved.get('episode')
                fact['terminal_proof'] = fact['terminal_proof'] or saved.get('terminal_proof')
                fact['task_state'] = dict(saved.get('task_state', {}), **fact['task_state'])
                fact['episode_state'] = dict(saved.get('episode_state', {}), **fact['episode_state'])
                receipts = {row['run']: row for row in saved.get('accepted', [])}
                receipts.update({row['run']: row for row in fact['accepted']})
                fact['accepted'] = list(receipts.values())
                fact['disposition'] = 'accepted' if fact['accepted'] else saved.get('disposition', fact['disposition'])
            con.execute('INSERT INTO qc_terminal_tasks VALUES(?,?,?,?,?,?) ON CONFLICT(project_id,task_id) DO UPDATE SET payload=excluded.payload',
                        (project_id, identity, fact['disposition'], created, digest, encoded(fact)))
            for receipt in fact['accepted']:
                prior_receipt = con.execute('SELECT payload FROM qc_terminal_acceptances WHERE project_id=? AND run_id=?',
                                            (project_id, receipt['run'])).fetchone()
                if prior_receipt and json.loads(prior_receipt[0]) != receipt:
                    raise ValueError('Conflicting accepted receipt during state compaction')
                con.execute('INSERT OR IGNORE INTO qc_terminal_acceptances VALUES(?,?,?,?,?)',
                            (project_id, receipt['run'], identity, timestamp(receipt['at']), encoded(receipt)))
        con.execute('INSERT OR IGNORE INTO qc_state_archives VALUES(?,?,?,?,?)',
                    (project_id, digest, str(archive.relative_to(runtime)), hashlib.sha256(encoded(state).encode()).hexdigest(), created))
    result['state_compaction'] = {'version': 1, 'snapshot_sha256': digest,
                                  'archive_path': str(archive.relative_to(runtime)), 'retired_local_attempts': result['retired_local_attempts']}
    return result
