"""Durable, episode-scoped exceptional cloud implementation budget.

Only the host records outcomes. A reservation consumes budget before launching;
unknown launches and crashes are not refunded. SQLite serializes competing hosts.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from durability import sync_directory

SUBSTANTIVE_FAILURES = frozenset({'acceptance', 'review', 'substantive', 'worker_no_change', 'implementation'})
WINDOW_SECONDS = 168 * 60 * 60
PROJECT_ATTEMPTS = 3


def policy():
    return {'version': 1, 'local_failures_before_guidance': 2, 'guided_local_failures': 2,
            'project_attempts': PROJECT_ATTEMPTS, 'window_seconds': WINDOW_SECONDS,
            'automatic_attempts_per_episode': 1, 'reserve_before_launch': True,
            'unknown_launch_counts': True, 'infrastructure_failures_count': False,
            'saved_draft_requires_host_verification': True}


class RescueDenied(ValueError):
    pass


class RescueStore:
    def __init__(self, path, *, clock=time.time):
        self.path, self.clock = Path(path), clock

    @contextmanager
    def _connect(self, *, write=False):
        if self.path.is_symlink():
            raise RescueDenied('Rescue database must not be a symlink')
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            con = sqlite3.connect(self.path, timeout=15)
            if con.execute('PRAGMA application_id').fetchone()[0] not in (0, 0x4D494851):
                con.close()
                raise RescueDenied('Unrecognised shared history database identity')
            con.execute('PRAGMA synchronous=EXTRA')
            con.execute('PRAGMA fullfsync=ON')
            con.executescript('''
              CREATE TABLE IF NOT EXISTS cr_episodes(
                project TEXT NOT NULL, episode TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0,
                guidance_id TEXT, guidance TEXT, guidance_seq INTEGER,
                draft_id TEXT, draft_verified INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(project,episode));
              CREATE TABLE IF NOT EXISTS cr_events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL,
                episode TEXT NOT NULL, event_id TEXT NOT NULL, task_id TEXT,
                kind TEXT NOT NULL, evidence TEXT NOT NULL, substantive INTEGER NOT NULL,
                created REAL NOT NULL, UNIQUE(project,event_id));
              CREATE INDEX IF NOT EXISTS cr_episode_events ON cr_events(project,episode,substantive,seq);
              CREATE INDEX IF NOT EXISTS cr_task_events ON cr_events(project,episode,task_id,substantive);
              CREATE TABLE IF NOT EXISTS cr_reservations(
                token TEXT PRIMARY KEY, project TEXT NOT NULL, episode TEXT NOT NULL,
                task_id TEXT NOT NULL, created REAL NOT NULL, state TEXT NOT NULL,
                evidence TEXT NOT NULL, UNIQUE(project,episode));
              CREATE INDEX IF NOT EXISTS cr_budget ON cr_reservations(project,created);
              CREATE TABLE IF NOT EXISTS cr_bindings(
                project TEXT NOT NULL, fingerprint TEXT NOT NULL, episode TEXT NOT NULL,
                PRIMARY KEY(project,fingerprint));
            ''')
            con.commit()
            os.chmod(self.path, 0o600)
            sync_directory(self.path.parent)
            con.execute('BEGIN IMMEDIATE')
        else:
            if not self.path.exists():
                yield None
                return
            con = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=15)
            if not con.execute("SELECT 1 FROM sqlite_master WHERE name='cr_episodes'").fetchone():
                con.close()
                yield None
                return
        con.row_factory = sqlite3.Row
        try:
            yield con
            if write:
                con.commit()
        except BaseException:
            if write:
                con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def _episode(con, project, episode):
        if not project or not episode:
            raise RescueDenied('Stable project and episode identity required')
        con.execute('INSERT OR IGNORE INTO cr_episodes(project,episode) VALUES(?,?)', (project, episode))
        row = con.execute('SELECT * FROM cr_episodes WHERE project=? AND episode=?', (project, episode)).fetchone()
        if row['resolved']:
            raise RescueDenied('Resolved episode cannot be reused')
        return row

    def bind_episode(self, project_id, fingerprint, episode_id=None):
        """The same original behaviour fingerprint always resolves to one episode."""
        if not fingerprint:
            raise RescueDenied('Original task fingerprint required')
        with self._connect(write=True) as con:
            row = con.execute('SELECT episode FROM cr_bindings WHERE project=? AND fingerprint=?',
                              (project_id, fingerprint)).fetchone()
            if row:
                return row['episode']
            episode_id = episode_id or hashlib.sha256((project_id + ':' + fingerprint).encode()).hexdigest()[:32]
            self._episode(con, project_id, episode_id)
            con.execute('INSERT INTO cr_bindings VALUES(?,?,?)', (project_id, fingerprint, episode_id))
            return episode_id

    def record_local_failure(self, project_id, episode_id, task_id, attempt_id, failure_kind,
                             evidence, *, host_verified=True, guidance_id=None):
        substantive = failure_kind in SUBSTANTIVE_FAILURES and host_verified and bool(evidence)
        with self._connect(write=True) as con:
            row = self._episode(con, project_id, episode_id)
            if row['guidance_id'] and guidance_id != row['guidance_id']:
                substantive = False
            raw = json.dumps(evidence, sort_keys=True)
            existing = con.execute('SELECT * FROM cr_events WHERE project=? AND event_id=?', (project_id, attempt_id)).fetchone()
            if existing and (existing['episode'], existing['task_id'], existing['kind'], existing['evidence']) != (episode_id, task_id, failure_kind, raw):
                raise RescueDenied('Conflicting local failure replay')
            con.execute('INSERT OR IGNORE INTO cr_events(project,episode,event_id,task_id,kind,evidence,substantive,created) '
                        'VALUES(?,?,?,?,?,?,?,?)', (project_id, episode_id, attempt_id, task_id, failure_kind,
                        raw, int(substantive), self.clock()))
        return self.eligibility(project_id, episode_id)

    def record_guidance(self, project_id, episode_id, guidance_id, guidance):
        """Concrete host-provided guidance, after two substantive local failures."""
        if not guidance_id or not isinstance(guidance, (str, dict)) or len(json.dumps(guidance).strip()) < 20:
            raise RescueDenied('Concrete Codex guidance and its stable receipt are required')
        with self._connect(write=True) as con:
            row = self._episode(con, project_id, episode_id)
            if row['guidance_id']:
                if row['guidance_id'] != guidance_id or row['guidance'] != json.dumps(guidance, sort_keys=True):
                    raise RescueDenied('Guidance already recorded for this episode; conflicting replay')
                return
            count = con.execute('SELECT COUNT(*) FROM cr_events WHERE project=? AND episode=? AND substantive=1',
                                (project_id, episode_id)).fetchone()[0]
            if count < 2:
                raise RescueDenied('Two substantive local failures must precede guidance')
            seq = con.execute('SELECT COALESCE(MAX(seq),0) FROM cr_events').fetchone()[0]
            con.execute('UPDATE cr_episodes SET guidance_id=?,guidance=?,guidance_seq=? WHERE project=? AND episode=?',
                        (guidance_id, json.dumps(guidance, sort_keys=True), seq, project_id, episode_id))

    def guidance(self, project_id, episode_id):
        """Read a committed guidance receipt after an interrupted state projection."""
        with self._connect() as con:
            if con is None:
                return None
            row = con.execute('SELECT guidance_id,guidance FROM cr_episodes WHERE project=? AND episode=?',
                              (project_id, episode_id)).fetchone()
            if not row or not row['guidance_id']:
                return None
            return {'guidance_id': row['guidance_id'], 'guidance': json.loads(row['guidance'])}

    def exclude_verification_failure(self, project_id, episode_id, attempt_id, evidence):
        """Correct a legacy host-only check miscount, retaining the original event.

        The host must establish that this run verified an older draft without
        invoking a worker. This can only reduce eligibility, never grant rescue.
        Premature guidance is journalled and replaced through the normal gate.
        Existing reservations are retained and continue consuming their quota.
        """
        if not evidence:
            raise RescueDenied('Host verification correction requires evidence')
        correction_id='verification-correction:'+attempt_id
        with self._connect(write=True) as con:
            row=con.execute('SELECT * FROM cr_events WHERE project=? AND event_id=?', (project_id,attempt_id)).fetchone()
            if row is None:
                return
            if row['episode']!=episode_id or row['kind']!='acceptance':
                raise RescueDenied('Correction is not a host-verification acceptance failure')
            if con.execute('SELECT 1 FROM cr_events WHERE project=? AND event_id=?', (project_id,correction_id)).fetchone():
                return
            episode=con.execute('SELECT * FROM cr_episodes WHERE project=? AND episode=?', (project_id,episode_id)).fetchone()
            if episode['resolved']:
                raise RescueDenied('Resolved history requires coordinator inspection')
            proof={'excluded_attempt':attempt_id,'previous_substantive':row['substantive'],
                   'original_evidence':json.loads(row['evidence']),'host_evidence':evidence}
            con.execute('INSERT INTO cr_events(project,episode,event_id,kind,evidence,substantive,created) VALUES(?,?,?,?,?,0,?)',
                        (project_id,episode_id,correction_id,'verification_correction',json.dumps(proof,sort_keys=True),self.clock()))
            con.execute('UPDATE cr_events SET substantive=0 WHERE project=? AND event_id=?', (project_id,attempt_id))
            if episode['guidance_id']:
                before=con.execute('SELECT COUNT(*) FROM cr_events WHERE project=? AND episode=? AND substantive=1 AND seq<=?',
                                   (project_id,episode_id,episode['guidance_seq'])).fetchone()[0]
                if before<2:
                    old={'guidance_id':episode['guidance_id'],'guidance':json.loads(episode['guidance']),
                         'guidance_seq':episode['guidance_seq'],'correction':correction_id}
                    con.execute('INSERT INTO cr_events(project,episode,event_id,kind,evidence,substantive,created) VALUES(?,?,?,?,?,0,?)',
                                (project_id,episode_id,'guidance-invalidated:'+episode['guidance_id'],
                                 'guidance_invalidated',json.dumps(old,sort_keys=True),self.clock()))
                    con.execute('UPDATE cr_episodes SET guidance_id=NULL,guidance=NULL,guidance_seq=NULL WHERE project=? AND episode=?',
                                (project_id,episode_id))

    def record_saved_draft(self, project_id, episode_id, draft_id):
        if not draft_id:
            raise RescueDenied('Draft identity required')
        with self._connect(write=True) as con:
            row = self._episode(con, project_id, episode_id)
            if row['draft_id'] != draft_id:
                con.execute('UPDATE cr_episodes SET draft_id=?,draft_verified=0 WHERE project=? AND episode=?',
                            (draft_id, project_id, episode_id))

    def record_host_verification(self, project_id, episode_id, draft_id, passed, evidence):
        if not evidence or not isinstance(passed, bool):
            raise RescueDenied('Host verification result and evidence required')
        with self._connect(write=True) as con:
            row = self._episode(con, project_id, episode_id)
            if row['draft_id'] != draft_id:
                raise RescueDenied('Verification does not match the saved draft')
            con.execute('UPDATE cr_episodes SET draft_verified=1 WHERE project=? AND episode=?', (project_id, episode_id))
            con.execute('INSERT OR IGNORE INTO cr_events(project,episode,event_id,kind,evidence,substantive,created) '
                        'VALUES(?,?,?,?,?,?,?)', (project_id, episode_id, 'verification:' + draft_id,
                        'host_verification_passed' if passed else 'host_verification_failed',
                        json.dumps(evidence), 0, self.clock()))

    def resolve_episode(self, project_id, episode_id, evidence):
        """Call only after host acceptance AND independent review are complete."""
        if not evidence:
            raise RescueDenied('Accepted outcome evidence required')
        with self._connect(write=True) as con:
            raw = json.dumps(evidence, sort_keys=True)
            existing = con.execute('SELECT evidence FROM cr_events WHERE project=? AND episode=? AND event_id=?',
                                   (project_id, episode_id, 'accepted:' + episode_id)).fetchone()
            if existing:
                if existing['evidence'] != raw:
                    raise RescueDenied('Conflicting accepted episode replay')
                return
            self._episode(con, project_id, episode_id)
            con.execute('UPDATE cr_episodes SET resolved=1 WHERE project=? AND episode=?', (project_id, episode_id))
            con.execute('INSERT INTO cr_events(project,episode,event_id,kind,evidence,substantive,created) '
                        'VALUES(?,?,?,?,?,?,?)', (project_id, episode_id, 'accepted:' + episode_id,
                        'accepted', raw, 0, self.clock()))

    def _eligibility(self, con, project_id, episode_id):
        result = {'allowed': False, 'status': 'local_retries_required', 'local_failures': 0,
                  'guided_failures': 0, 'guidance_id': None, 'attempts_168h': 0}
        if con is None:
            return result
        row = con.execute('SELECT * FROM cr_episodes WHERE project=? AND episode=?', (project_id, episode_id)).fetchone()
        result['attempts_168h'] = con.execute('SELECT COUNT(*) FROM cr_reservations WHERE project=? AND created>=?',
                                            (project_id, self.clock() - WINDOW_SECONDS)).fetchone()[0]
        if row is None:
            return result
        result['guidance_id'] = row['guidance_id']
        boundary = row['guidance_seq']
        counts = con.execute('SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN seq>? THEN 1 ELSE 0 END),0) AS guided '
                             'FROM cr_events WHERE project=? AND episode=? AND substantive=1',
                             (boundary if boundary is not None else 9223372036854775807, project_id, episode_id)).fetchone()
        result['guided_failures'] = counts['guided']
        result['local_failures'] = counts['total'] - counts['guided']
        reservation = con.execute('SELECT * FROM cr_reservations WHERE project=? AND episode=?', (project_id, episode_id)).fetchone()
        if row['resolved']:
            result['status'] = 'resolved'
        elif not row['draft_verified']:
            result['status'] = 'host_verification_required'
        elif reservation:
            result['status'] = 'manager_required' if reservation['state'] == 'failed' else 'episode_reserved'
        elif result['local_failures'] < 2:
            pass
        elif not row['guidance_id']:
            result['status'] = 'guidance_required'
        elif result['guided_failures'] < 2:
            result['status'] = 'guided_retries_required'
        elif result['attempts_168h'] >= PROJECT_ATTEMPTS:
            result['status'] = 'project_quota_exhausted'
        else:
            result.update(allowed=True, status='eligible')
        return result

    def task_failures(self, project_id, episode_id, task_id):
        """Count host-observed substantive failures for one concrete task."""
        with self._connect() as con:
            if con is None:
                return 0
            return con.execute('SELECT COUNT(*) FROM cr_events WHERE project=? AND episode=? AND task_id=? AND substantive=1',
                               (project_id, episode_id, task_id)).fetchone()[0]

    def eligibility(self, project_id, episode_id):
        with self._connect() as con:
            return self._eligibility(con, project_id, episode_id)

    def failure_evidence(self, project_id, episode_id):
        """Bounded actual attempts supporting each side of the guidance gate."""
        with self._connect() as con:
            if con is None:return {'initial':[],'guided':[]}
            episode=con.execute('SELECT guidance_seq FROM cr_episodes WHERE project=? AND episode=?',
                                (project_id,episode_id)).fetchone()
            if episode is None:return {'initial':[],'guided':[]}
            boundary=episode['guidance_seq']
            boundary=boundary if boundary is not None else 9223372036854775807
            result={}
            for phase,comparison in (('initial','<='),('guided','>')):
                rows=con.execute('SELECT event_id,task_id,kind,evidence FROM cr_events WHERE project=? AND episode=? '
                                 'AND substantive=1 AND seq'+comparison+'? ORDER BY seq DESC LIMIT 2',
                                 (project_id,episode_id,boundary)).fetchall()
                result[phase]=[dict(row,evidence=json.loads(row['evidence'])) for row in reversed(rows)]
            return result

    def reserve(self, project_id, episode_id, task_id):
        with self._connect(write=True) as con:
            self._episode(con, project_id, episode_id)
            result = self._eligibility(con, project_id, episode_id)
            if not result['allowed']:
                raise RescueDenied(result['status'])
            token = uuid.uuid4().hex
            con.execute('INSERT INTO cr_reservations VALUES(?,?,?,?,?,?,?)',
                        (token, project_id, episode_id, task_id, self.clock(), 'reserved_unknown_launch', '{}'))
        return {'token': token, 'project_id': project_id, 'episode_id': episode_id, 'task_id': task_id,
                'state': 'reserved_unknown_launch'}

    def finish(self, token, success, evidence):
        if not isinstance(success, bool) or not evidence:
            raise RescueDenied('Verified outcome and evidence required')
        with self._connect(write=True) as con:
            row = con.execute('SELECT * FROM cr_reservations WHERE token=?', (token,)).fetchone()
            if not row:
                raise RescueDenied('Unknown rescue reservation')
            state = 'succeeded' if success else 'failed'
            if row['state'] in ('succeeded', 'failed'):
                if row['state'] != state:
                    raise RescueDenied('Rescue outcome already recorded')
                return
            con.execute('UPDATE cr_reservations SET state=?,evidence=? WHERE token=?', (state, json.dumps(evidence), token))
            if success:
                con.execute('UPDATE cr_episodes SET resolved=1 WHERE project=? AND episode=?', (row['project'], row['episode']))
