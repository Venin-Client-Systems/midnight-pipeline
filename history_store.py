"""Indexed, transactional Midnight history; no model or filesystem-history scans.

The database is private runtime state, separate from durability's checkpoints.
The qc_ namespace is owned here; other shared services may own other prefixes.
Readers never create or migrate a database. All public pages have bounded limits.
"""
from contextlib import contextmanager
import datetime as dt
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid

from durability import sync_directory

SCHEMA_VERSION = 1
MAX_PAGE = 500
APPLICATION_ID = 0x4D494851
HISTORY_TABLES = {'qc_meta','qc_project','qc_events','qc_runs','qc_findings',
                  'qc_task_lineage','qc_assessments','qc_tickets'}


def timestamp(value=None):
    if value is None:
        return time.time()
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError('History timestamp must be finite')
        return float(value)
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if isinstance(value, dt.datetime) and value.tzinfo is not None:
        return value.timestamp()
    raise ValueError('History timestamps require UTC epoch seconds or a timezone-aware timestamp')


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def bounded_limit(value):
    if type(value) is not int or not 1 <= value <= MAX_PAGE:
        raise ValueError(f'Page limit must be 1..{MAX_PAGE}')
    return value


SCHEMA = """
CREATE TABLE IF NOT EXISTS qc_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS qc_project(project_id TEXT PRIMARY KEY,findings_revision INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS qc_events(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,
 project_id TEXT NOT NULL,kind TEXT NOT NULL,episode_id TEXT,task_id TEXT,
 occurred_at REAL NOT NULL,outcome TEXT,worker_kind TEXT,duration_seconds REAL,
 payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS qc_events_project_time ON qc_events(project_id,occurred_at DESC,sequence DESC);
CREATE INDEX IF NOT EXISTS qc_events_project_kind_time ON qc_events(project_id,kind,occurred_at DESC,sequence DESC);
CREATE INDEX IF NOT EXISTS qc_events_project_sequence ON qc_events(project_id,sequence DESC);
CREATE INDEX IF NOT EXISTS qc_events_project_kind_sequence ON qc_events(project_id,kind,sequence DESC);
CREATE INDEX IF NOT EXISTS qc_events_episode ON qc_events(project_id,episode_id,sequence DESC);
CREATE TABLE IF NOT EXISTS qc_runs(
 project_id TEXT NOT NULL,run_id TEXT NOT NULL,created_at REAL NOT NULL,
 outcome TEXT NOT NULL,worker_kind TEXT NOT NULL,duration_seconds REAL NOT NULL,
 accepted_commit TEXT,payload TEXT NOT NULL,PRIMARY KEY(project_id,run_id));
CREATE INDEX IF NOT EXISTS qc_runs_project_time ON qc_runs(project_id,created_at DESC,run_id DESC);
CREATE INDEX IF NOT EXISTS qc_runs_project_outcome ON qc_runs(project_id,outcome,created_at DESC);
CREATE TABLE IF NOT EXISTS qc_findings(
 project_id TEXT NOT NULL,finding_id TEXT NOT NULL,episode INTEGER NOT NULL,
 state TEXT NOT NULL,material INTEGER NOT NULL,updated_at REAL NOT NULL,payload TEXT NOT NULL,
 PRIMARY KEY(project_id,finding_id));
CREATE INDEX IF NOT EXISTS qc_findings_open ON qc_findings(project_id,state,material,updated_at DESC);
CREATE TABLE IF NOT EXISTS qc_task_lineage(
 project_id TEXT NOT NULL,task_id TEXT NOT NULL,issue_id TEXT NOT NULL,episode_id TEXT NOT NULL,
 payload TEXT NOT NULL,PRIMARY KEY(project_id,task_id));
CREATE INDEX IF NOT EXISTS qc_task_episode ON qc_task_lineage(project_id,episode_id);
CREATE TABLE IF NOT EXISTS qc_assessments(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,assessment_id TEXT NOT NULL UNIQUE,
 project_id TEXT NOT NULL,head TEXT NOT NULL,profile_hash TEXT NOT NULL,
 kind TEXT NOT NULL,outcome TEXT NOT NULL,assessed_at REAL NOT NULL,
 findings_revision INTEGER NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS qc_assessments_kind ON qc_assessments(project_id,profile_hash,kind,sequence DESC);
CREATE INDEX IF NOT EXISTS qc_assessments_head ON qc_assessments(project_id,profile_hash,head,sequence DESC);
CREATE TABLE IF NOT EXISTS qc_tickets(
 ticket_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,task_id TEXT NOT NULL,
 head TEXT NOT NULL,profile_hash TEXT NOT NULL,findings_revision INTEGER NOT NULL,
 assessment_id TEXT NOT NULL,issued_at REAL NOT NULL,expires_at REAL NOT NULL,
 consumed_at REAL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS qc_tickets_project ON qc_tickets(project_id,task_id,issued_at DESC);
"""


class HistoryStore:
    """A lazy handle; constructor and read APIs do not modify runtime state."""
    def __init__(self, runtime, *, write_ahead=False, read_timeout=15):
        runtime = Path(runtime)
        self.path = runtime if runtime.suffix == '.sqlite3' else runtime / 'history.sqlite3'
        self.write_ahead = write_ahead
        if type(read_timeout) not in (float, int) or not math.isfinite(read_timeout) or not 0 <= read_timeout <= 15:
            raise ValueError('Invalid history observation timeout')
        self.read_timeout = read_timeout

    def connect(self, *, write=False):
        if self.path.is_symlink():
            raise ValueError('History database must not be a symlink')
        if not write and not self.path.exists():
            return None
        created = False
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
                created = True
        con = sqlite3.connect(str(self.path) if write else self.path.resolve().as_uri() + '?mode=ro',
                              uri=not write, timeout=15 if write else self.read_timeout, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            identity = con.execute('PRAGMA application_id').fetchone()[0]
            if identity not in (0, APPLICATION_ID):
                raise ValueError('Unrecognised history database identity')
            has_meta = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qc_meta'").fetchone()
            if identity == APPLICATION_ID and not has_meta:
                raise ValueError('History schema is incomplete; preserve the initialized ledger for recovery')
            if has_meta:
                version = con.execute("SELECT value FROM qc_meta WHERE key='schema_version'").fetchone()
                if version is None or version[0] != str(SCHEMA_VERSION):
                    raise ValueError('Unsupported history schema')
            if write:
                con.execute('PRAGMA synchronous=EXTRA')
                con.execute('PRAGMA fullfsync=ON')
                if self.write_ahead:
                    # Only the admitted writer migrates. Read-only connections
                    # keep normal transactional visibility, never immutable=1.
                    mode=con.execute('PRAGMA journal_mode').fetchone()[0]
                    if mode.lower()!='wal':mode=con.execute('PRAGMA journal_mode=WAL').fetchone()[0]
                    if mode.lower()!='wal':raise ValueError('Workflow write-ahead logging unavailable')
                    con.execute('PRAGMA checkpoint_fullfsync=ON')
                initialized = identity == APPLICATION_ID and bool(has_meta)
                if initialized:
                    tables={row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'qc_%'")}
                    if not HISTORY_TABLES <= tables:raise ValueError('History schema is incomplete; preserve the existing ledger for recovery')
                else:
                    # Bootstrap once, atomically. Ordinary connections do not
                    # rewrite the database header/schema before their real
                    # transaction; a concurrent writer must not block setup.
                    con.executescript('BEGIN IMMEDIATE;\nPRAGMA application_id=' + str(APPLICATION_ID) + ';\n' + SCHEMA +
                                      "\nINSERT OR IGNORE INTO qc_meta VALUES('schema_version','1');\nCOMMIT;")
                changed_mode=stat.S_IMODE(self.path.stat().st_mode)!=0o600
                if changed_mode:os.chmod(self.path, 0o600)
                if created or not initialized or changed_mode:sync_directory(self.path.parent)
            elif not has_meta:
                con.close()
                return None
            return con
        except BaseException:
            con.close()
            raise

    @contextmanager
    def transaction(self):
        con = self.connect(write=True)
        try:
            con.execute('BEGIN IMMEDIATE')
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    @contextmanager
    def reader(self):
        con = self.connect()
        try:
            if con is not None:
                con.execute('BEGIN')
            yield con
        finally:
            if con is not None:
                con.close()

    def append(self, kind, project_id, payload, *, event_id=None, episode_id=None,
               task_id=None, occurred_at=None, connection=None):
        event_id = event_id or uuid.uuid4().hex
        raw = encoded(payload)
        def insert(con):
            prior = con.execute('SELECT * FROM qc_events WHERE event_id=?', (event_id,)).fetchone()
            if prior:
                if (prior['project_id'], prior['kind'], prior['payload'], prior['episode_id'], prior['task_id']) != (project_id, kind, raw, episode_id, task_id):
                    raise ValueError('Conflicting history event replay')
                if occurred_at is not None and prior['occurred_at'] != timestamp(occurred_at):
                    raise ValueError('Conflicting history event timestamp')
                return event_id
            con.execute('INSERT INTO qc_events(event_id,project_id,kind,episode_id,task_id,occurred_at,outcome,worker_kind,duration_seconds,payload) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (event_id, project_id, kind, episode_id, task_id, timestamp(occurred_at),
                         payload.get('outcome'), payload.get('worker_kind'), payload.get('duration_seconds'), raw))
            return event_id
        if connection is not None:
            return insert(connection)
        with self.transaction() as con:
            return insert(con)

    def query(self, project_id, *, kind=None, episode_id=None, limit=50, cursor=None,
              since=None, until=None):
        limit = bounded_limit(limit)
        where, args = ['project_id=?'], [project_id]
        for column, value in [('kind', kind), ('episode_id', episode_id)]:
            if value is not None:
                where.append(column + '=?'); args.append(value)
        if cursor is not None:
            where.append('sequence<?'); args.append(int(cursor))
        if since is not None:
            where.append('occurred_at>=?'); args.append(timestamp(since))
        if until is not None:
            where.append('occurred_at<?'); args.append(timestamp(until))
        with self.reader() as con:
            rows = [] if con is None else con.execute('SELECT * FROM qc_events WHERE ' +
                ' AND '.join(where) + ' ORDER BY sequence DESC LIMIT ?', args + [limit]).fetchall()
        return [dict(row, payload=json.loads(row['payload'])) for row in rows]

    def record_run(self, project_id, run_id, payload, created_at=None, *, connection=None):
        """Upsert the same run as pending -> terminal without duplicating counters.

        A terminal accepted commit cannot be overwritten with a different commit.
        Accepted work requires an actual commit receipt supplied by the backend.
        """
        raw = encoded(payload)
        commit = payload.get('accepted_commit') or payload.get('commit')
        outcome = payload.get('outcome') or payload.get('status') or 'unknown'
        if isinstance(outcome, dict):
            outcome = outcome.get('status', 'unknown')
        if outcome == 'accepted' and not commit:
            raise ValueError('Accepted run requires an accepted commit receipt')
        worker = payload.get('worker_kind', 'local')
        duration = float(payload.get('duration_seconds') or 0)
        def insert(con):
            prior = con.execute('SELECT * FROM qc_runs WHERE project_id=? AND run_id=?', (project_id, run_id)).fetchone()
            if prior and prior['accepted_commit'] and (commit != prior['accepted_commit'] or outcome != 'accepted'):
                raise ValueError('Cannot replace an accepted run receipt')
            created = prior['created_at'] if prior else timestamp(created_at if created_at is not None else payload.get('created_at'))
            con.execute('INSERT INTO qc_runs VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(project_id,run_id) DO UPDATE SET outcome=excluded.outcome,worker_kind=excluded.worker_kind,duration_seconds=excluded.duration_seconds,accepted_commit=excluded.accepted_commit,payload=excluded.payload',
                        (project_id, run_id, created, outcome, worker, duration, commit, raw))
            return run_id
        if connection is not None:
            return insert(connection)
        with self.transaction() as con:
            return insert(con)

    def page_runs(self, project_id, limit=50, offset=0, *, before=None):
        limit = bounded_limit(limit)
        if type(offset) is not int or offset < 0 or offset > 1000000:
            raise ValueError('Invalid run page offset')
        where, args = 'project_id=?', [project_id]
        if before is not None:
            where += ' AND (created_at,run_id)<(?,?)'
            args.extend([timestamp(before[0]), before[1]])
        with self.reader() as con:
            rows = [] if con is None else con.execute('SELECT * FROM qc_runs WHERE ' + where +
                ' ORDER BY created_at DESC,run_id DESC LIMIT ? OFFSET ?', args + [limit, offset]).fetchall()
        return [dict(row, payload=json.loads(row['payload'])) for row in rows]

    def counters(self, project_id, *, since=None, until=None):
        where, args = ['project_id=?'], [project_id]
        if since is not None:
            where.append('created_at>=?'); args.append(timestamp(since))
        if until is not None:
            where.append('created_at<?'); args.append(timestamp(until))
        with self.reader() as con:
            rows = [] if con is None else con.execute('SELECT outcome,worker_kind,COUNT(*) AS count,SUM(duration_seconds) AS seconds FROM qc_runs WHERE ' +
                ' AND '.join(where) + ' GROUP BY outcome,worker_kind', args).fetchall()
        groups = [dict(row) for row in rows]
        total = sum(row['count'] for row in rows)
        accepted = sum(row['count'] for row in rows if row['outcome'] == 'accepted')
        return {'total': total, 'accepted': accepted, 'attempted': total,
                'acceptance_rate': accepted / total if total else None, 'groups': groups,
                'local_attempts': sum(r['count'] for r in rows if r['worker_kind'] == 'local'),
                'cloud_attempts': sum(r['count'] for r in rows if r['worker_kind'] == 'cloud')}

    def log_git_event(self, project_id, operation, payload, *, event_id=None, **kwargs):
        return self.append('git', project_id, dict(payload, operation=operation), event_id=event_id, **kwargs)

    def record_attempt(self, project_id, payload, **kwargs):
        return self.append('attempt', project_id, payload, **kwargs)

    def record_guidance(self, project_id, payload, **kwargs):
        return self.append('guidance', project_id, payload, **kwargs)
