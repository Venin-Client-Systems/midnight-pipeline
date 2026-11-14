"""Indexed authoritative sessions and crash-replayable incremental CSV export."""
import csv
import io
import hashlib
import uuid
import json
import os
from pathlib import Path
import sqlite3
import durability as D

LEGACY_FIELDS=['date','session_id','started_at','ended_at','mode','project','why','achieved','accepted_count','attempt_count','outcome','summary','evidence']
FIELDS=LEGACY_FIELDS+['cloud_implementation_count','schema_version']


def parse(payload):
    if not payload:return []
    reader=csv.DictReader(io.StringIO(payload.decode()))
    if reader.fieldnames not in (LEGACY_FIELDS,FIELDS):raise ValueError('Session CSV header does not match its schema')
    rows=list(reader)
    if any(None in r or any(v is None for v in r.values()) for r in rows):raise ValueError('Session CSV contains an incomplete row')
    return [dict(r,cloud_implementation_count=r.get('cloud_implementation_count','0'),schema_version=r.get('schema_version','1')) for r in rows]


def encode(rows,header=False):
    out=io.StringIO(newline='');w=csv.DictWriter(out,fieldnames=FIELDS)
    if header:w.writeheader()
    w.writerows(rows);return out.getvalue().encode()


class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()


class Sessions:
    def __init__(self,root):self.root=Path(root);self.db=self.root/'session-history.sqlite3';self.csv=self.root/'sessions.csv'
    def connect(self,writable=False):
        if self.db.is_symlink():raise ValueError("Session database symlink refused")
        if writable:
            self.root.mkdir(parents=True,exist_ok=True)
            c=sqlite3.connect(self.db,timeout=30,factory=ClosingConnection)
            c.execute('PRAGMA synchronous=FULL');c.execute('CREATE TABLE IF NOT EXISTS session_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)');c.execute('CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY, session TEXT, project TEXT, payload TEXT, UNIQUE(session,project))')
            c.execute('CREATE TABLE IF NOT EXISTS export(id INTEGER PRIMARY KEY CHECK(id=1), last_id INTEGER, byte_count INTEGER)');c.execute('CREATE TABLE IF NOT EXISTS export_hash(id INTEGER PRIMARY KEY CHECK(id=1), digest TEXT)');c.commit();os.chmod(self.db,0o600);return c
        return sqlite3.connect(self.db.as_uri()+'?mode=ro',uri=True,timeout=10,factory=ClosingConnection)
    def exists(self):return self.db.exists()
    def ready(self):
        if not self.exists():return False
        with self.connect() as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_meta'").fetchone():return False
            row=c.execute("SELECT value FROM session_meta WHERE key='legacy_import_ready'").fetchone()
            return bool(row and row[0]=='1')
    def legacy_rows(self):
        # Bypass durability.read_bytes' indexed-session routing until the import
        # commits. The original verified checkpoint or CSV remains authoritative.
        if self.csv.is_symlink():raise ValueError('Session CSV symlink refused')
        saved=D.snapshot(self.csv)
        if saved is not None:
            raw=b'' if saved['deleted'] else saved['payload']
        else:
            try:raw=self.csv.read_bytes()
            except FileNotFoundError:raw=b''
        return parse(raw)
    def rows(self,limit=None):
        if not self.ready():
            merged={(r['session_id'],r['project']):r for r in self.legacy_rows()}
            if self.exists():
                with self.connect() as c:
                    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone():
                        for payload, in c.execute('SELECT payload FROM sessions ORDER BY id'):
                            row=json.loads(payload);merged[(row['session_id'],row['project'])]=row
            values=list(merged.values());return values[-limit:] if limit else values
        with self.connect() as c:
            query='SELECT payload FROM sessions ORDER BY id '+('DESC LIMIT ?' if limit else 'ASC')
            values=[json.loads(r[0]) for r in c.execute(query,(limit,) if limit else ())]
            return list(reversed(values)) if limit else values
    def receipt(self,session_id,project):
        """Read the authoritative completed row without scanning lifetime history."""
        if not self.ready():
            return next((row for row in self.rows()
                         if row['session_id']==session_id and row['project']==project),None)
        with self.connect() as c:
            row=c.execute('SELECT payload FROM sessions WHERE session=? AND project=?',
                          (session_id,project)).fetchone()
            return json.loads(row[0]) if row else None
    def commit_rows(self,rows):
        # A writable open first recovers any hot SQLite journal left by a crash.
        with self.connect(True) as c:
            c.execute('BEGIN IMMEDIATE')
            marker=c.execute("SELECT value FROM session_meta WHERE key='legacy_import_ready'").fetchone()
            legacy=self.legacy_rows() if not marker or marker[0]!='1' else []
            for r in legacy+rows:
                value={k:str(r.get(k,'0' if k=='cloud_implementation_count' else '2' if k=='schema_version' else '')) for k in FIELDS}
                previous=c.execute('SELECT payload FROM sessions WHERE session=? AND project=?',(value['session_id'],value['project'])).fetchone()
                if previous:
                    old=json.loads(previous[0]);compare=lambda r:{k:v for k,v in r.items() if k!='ended_at'}
                    if compare(old)!=compare(value):raise ValueError('Conflicting session receipt replay')
                c.execute('INSERT OR IGNORE INTO sessions(session,project,payload) VALUES(?,?,?)',(value['session_id'],value['project'],json.dumps(value)))
            # This marker and every imported receipt become visible together.
            # A schema-only file from an interrupted first import is never ready.
            c.execute("INSERT OR REPLACE INTO session_meta VALUES('legacy_import_ready','1')")
            c.commit()
        D.sync_directory(self.root)
    def append(self,rows):
        self.commit_rows(rows)
        self.export()
    def export(self):
        """Commit rows first; truncate an uncertain append to the last committed offset."""
        with self.connect(True):pass  # Recover a hot journal before read-only readiness checks.
        if not self.ready():self.commit_rows([])
        with self.connect(True) as c:
            c.execute('BEGIN IMMEDIATE');state=c.execute('SELECT last_id,byte_count FROM export WHERE id=1').fetchone()
            if self.csv.is_symlink():raise ValueError('Session CSV symlink refused')
            saved_hash=c.execute('SELECT digest FROM export_hash WHERE id=1').fetchone()
            prefix=None
            if state and self.csv.exists():
                with self.csv.open('rb') as check:prefix=hashlib.sha256(check.read(state[1])).hexdigest()
            if not state or not self.csv.exists() or self.csv.stat().st_size<state[1] or not saved_hash or saved_hash[0]!=prefix:
                # Migration / missing or truncated mirror. Stream, never materialise lifetime history.
                temp=self.csv.with_name('.sessions-'+uuid.uuid4().hex+'.rebuilding')
                with temp.open('xb') as f:
                    f.write(encode([],True));last=0
                    for identity,payload in c.execute('SELECT id,payload FROM sessions ORDER BY id'):
                        f.write(encode([json.loads(payload)]));last=identity
                    f.flush();os.fsync(f.fileno());size=f.tell()
                os.chmod(temp,0o600);os.replace(temp,self.csv);D.sync_directory(self.root)
            else:
                last,size=state
                with self.csv.open('r+b') as f:
                    f.truncate(size);f.seek(size)
                    for identity,payload in c.execute('SELECT id,payload FROM sessions WHERE id>? ORDER BY id',(last,)):
                        f.write(encode([json.loads(payload)]));last=identity
                    f.flush();os.fsync(f.fileno());size=f.tell()
            c.execute('INSERT OR REPLACE INTO export VALUES(1,?,?)',(last,size));c.execute('INSERT OR REPLACE INTO export_hash VALUES(1,?)',(hashlib.sha256(self.csv.read_bytes()).hexdigest(),));c.commit()
    def health(self):
        try:
            if not self.ready():return {'file':'sessions.csv','status':'repair_needed','storage':'legacy_import_pending'}
            with self.connect() as c:
                check=c.execute('PRAGMA quick_check').fetchone()[0];state=c.execute('SELECT byte_count FROM export WHERE id=1').fetchone();digest=c.execute('SELECT digest FROM export_hash WHERE id=1').fetchone()
            return {'file':'sessions.csv','status':'healthy' if check=='ok' and state and self.csv.exists() and self.csv.stat().st_size==state[0] and digest and hashlib.sha256(self.csv.read_bytes()).hexdigest()==digest[0] else 'repair_needed','storage':'indexed_sessions_v2'}
        except (OSError,sqlite3.Error):return {'file':'sessions.csv','status':'unavailable'}
