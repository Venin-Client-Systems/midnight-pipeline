"""Durable project actions, phase leases and recovery incidents.

Constructors and readers never create files. The wf_ namespace reuses the
existing private SQLite history implementation and its durability settings.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import time
import uuid
import sqlite3
from history_store import HistoryStore, encoded, bounded_limit

SCHEMA = '''
CREATE TABLE IF NOT EXISTS wf_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wf_jobs(id TEXT PRIMARY KEY,project TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,deadline REAL NOT NULL,payload TEXT NOT NULL,lease TEXT,result TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS wf_project_active ON wf_jobs(project) WHERE state IN ('queued','running');
CREATE INDEX IF NOT EXISTS wf_job_state ON wf_jobs(state,updated);
CREATE INDEX IF NOT EXISTS wf_job_created ON wf_jobs(created DESC);
CREATE TABLE IF NOT EXISTS wf_phases(id TEXT PRIMARY KEY,job TEXT NOT NULL,project TEXT NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,progress REAL NOT NULL,deadline REAL NOT NULL,payload TEXT NOT NULL,lease TEXT,result TEXT);
CREATE INDEX IF NOT EXISTS wf_phase_job ON wf_phases(job,updated);
CREATE INDEX IF NOT EXISTS wf_phase_state ON wf_phases(state,deadline);
CREATE TABLE IF NOT EXISTS wf_executions(phase TEXT PRIMARY KEY,job TEXT NOT NULL,kind TEXT NOT NULL,lease TEXT NOT NULL,cleared INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS wf_execution_active ON wf_executions(cleared,job);
CREATE TABLE IF NOT EXISTS wf_execution_members(phase TEXT NOT NULL,pid INTEGER NOT NULL,stamp TEXT NOT NULL,boot_id TEXT NOT NULL,birth_us INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(phase,pid,stamp,boot_id,birth_us));
CREATE TABLE IF NOT EXISTS wf_charges(job TEXT NOT NULL,key TEXT NOT NULL,project TEXT NOT NULL,session TEXT NOT NULL,kind TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(job,key,kind));
CREATE INDEX IF NOT EXISTS wf_charges_session ON wf_charges(session,project,kind);
CREATE TABLE IF NOT EXISTS wf_incidents(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL UNIQUE,project TEXT NOT NULL,code TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,evidence_hash TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wf_repairs(id TEXT PRIMARY KEY,incident TEXT NOT NULL,number INTEGER NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,lease TEXT,payload TEXT NOT NULL,UNIQUE(incident,number));
CREATE TABLE IF NOT EXISTS wf_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,at REAL NOT NULL,project TEXT NOT NULL,kind TEXT NOT NULL,reference TEXT NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS wf_event_project ON wf_events(project,sequence DESC);
CREATE TABLE IF NOT EXISTS wf_totals(kind TEXT PRIMARY KEY,count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS wf_harness_requests(id TEXT PRIMARY KEY,project TEXT NOT NULL,scope TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL);
'''


def policy():
    root=Path(__file__).resolve().parent
    modules=('action_store.py','phase_runtime.py','project_action.py','phase_guard.py','verification_sandbox.py','process_observer.py','verification_runner.py')
    return {'id':'midnight-durable-actions','version':1,'resources':{'local':1,'host':1,'cloud':2},
            'maintenance_attempts_per_incident':4,'one_candidate_per_project':True,'max_project_actions':4,
            'modules':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in modules}}


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class Actions:
    def __init__(self, runtime, *, clock=time.time):
        self.runtime=Path(runtime);self.store=HistoryStore(self.runtime/'workflow.sqlite3',write_ahead=True);self.clock=clock

    def recover(self):
        """A writable owner permits SQLite to roll back a crash-hot journal."""
        if not self.store.path.exists():return {'status':'uninitialised'}
        with self.write() as con:
            return {'status':'reconciled','version':con.execute("SELECT value FROM wf_meta WHERE key='version'").fetchone()[0]}

    @contextmanager
    def write(self):
        with self.store.transaction() as con:
            for statement in SCHEMA.split(';'):
                if statement.strip():con.execute(statement)
            version=con.execute("SELECT value FROM wf_meta WHERE key='version'").fetchone()
            if version and version[0]!='1':raise ValueError('Unsupported workflow schema')
            con.execute("INSERT OR IGNORE INTO wf_meta VALUES('version','1')")
            if not con.execute("SELECT 1 FROM wf_meta WHERE key='totals_ready'").fetchone():
                con.execute('INSERT OR REPLACE INTO wf_totals SELECT kind,COUNT(*) FROM wf_events GROUP BY kind')
                con.execute("INSERT INTO wf_meta VALUES('totals_ready','true')")
            yield con

    @contextmanager
    def read(self):
        con=self.store.connect()
        try:
            if con and not con.execute("SELECT 1 FROM sqlite_master WHERE name='wf_jobs'").fetchone():
                con.close();con=None
            yield con
        finally:
            if con:con.close()

    def event(self,con,project,kind,reference,payload):
        con.execute('INSERT INTO wf_events(at,project,kind,reference,payload) VALUES(?,?,?,?,?)',
                    (self.clock(),project,kind,reference,encoded(payload)))
        con.execute('INSERT INTO wf_totals VALUES(?,1) ON CONFLICT(kind) DO UPDATE SET count=count+1',(kind,))

    @staticmethod
    def unpack(row):
        if row is None:return None
        value=dict(row)
        for key in ('payload','lease','result'):
            if key in value:value[key]=json.loads(value[key]) if value[key] else None
        return value

    def submit(self,project,payload,deadline):
        if not isinstance(project,str) or not project or deadline<=self.clock():raise ValueError('Valid project and future deadline required')
        with self.write() as con:
            if project=='midnight' and payload.get('kind')=='agent_discussion' and con.execute("SELECT 1 FROM wf_repairs WHERE state IN ('reserved','running','unknown')").fetchone():return None
            previous=con.execute("SELECT * FROM wf_jobs WHERE project=? AND state IN ('queued','running')",(project,)).fetchone()
            if previous:return self.unpack(previous)
            if con.execute("SELECT COUNT(*) FROM wf_jobs WHERE state IN ('queued','running')").fetchone()[0]>=4:return None
            identity='job-'+uuid.uuid4().hex
            con.execute('INSERT INTO wf_jobs VALUES(?,?,?,?,?,?,?,?,?)',
                        (identity,project,'queued',self.clock(),self.clock(),deadline,encoded(payload),None,None))
            self.event(con,project,'action_queued',identity,{'deadline':deadline})
            return self.unpack(con.execute('SELECT * FROM wf_jobs WHERE id=?',(identity,)).fetchone())

    def get(self,identity):
        with self.read() as con:
            return self.unpack(con.execute('SELECT * FROM wf_jobs WHERE id=?',(identity,)).fetchone()) if con else None

    def request_harness(self,identity,failure):
        import os
        import durability as D
        import phase_runtime as P
        import progress_recovery as R
        import worker_quality as Q
        job=self.get(os.environ.get('MIDNIGHT_ACTION_ID',''))
        if not job or job['state']!='running' or job['project']!=identity['project'] or not P.alive(job.get('lease')):raise ValueError('Harness diagnosis requires a live registered project action')
        registry=D.read_json(self.runtime.parent/'projects.json',{})
        project=next((p for p in registry.get('projects',[]) if p['id']==job['project']),None)
        if not project:raise ValueError('Unregistered harness request')
        directory=Path(project['state_dir'])/'runs'/failure['run'];actual=R.classify(directory)
        if actual['kind'] not in R.HARNESS_FAILURES|{'runtime'} or any(actual[k]!=failure[k] for k in ('kind','fingerprint','refs')) or Q.task_fingerprint(D.read_json(directory/'task.json',{}))!=identity['task_hash']:raise ValueError('Harness request does not match actual host receipts')
        key=fingerprint({'scope':identity['id'],'failure':failure['fingerprint']})
        value={'id':key,'scope':identity,'failure':failure,'source_head':job['payload']['source_head']}
        with self.write() as con:
            previous=con.execute('SELECT payload FROM wf_harness_requests WHERE id=?',(key,)).fetchone()
            if previous and previous[0]!=encoded(value):
                self.event(con,job['project'],'harness_failure_reobserved',key,{'previous':json.loads(previous[0]),'current':value})
            con.execute('INSERT INTO wf_harness_requests VALUES(?,?,?,\'pending\',?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                        (key,job['project'],identity['id'],encoded(value)))
        return key

    def harness_requests(self):
        with self.read() as con:
            if not con or not con.execute("SELECT 1 FROM sqlite_master WHERE name='wf_harness_requests'").fetchone():return []
            return [json.loads(r[0]) for r in con.execute("SELECT payload FROM wf_harness_requests WHERE state='pending' ORDER BY rowid LIMIT 100")]

    def resolve_harness(self,identity):
        with self.write() as con:con.execute("UPDATE wf_harness_requests SET state='qualified_repair' WHERE scope=? AND project=?",(identity['id'],identity['project']))

    def retire_harness(self,identity):
        with self.write() as con:
            con.execute("UPDATE wf_harness_requests SET state='superseded' WHERE id=? AND state='pending'",(identity,))

    def jobs(self,*,active=True,limit=100):
        bounded_limit(limit)
        with self.read() as con:
            if not con:return []
            where="WHERE state IN ('queued','running')" if active else ''
            return [self.unpack(r) for r in con.execute('SELECT * FROM wf_jobs '+where+' ORDER BY created DESC LIMIT ?',(limit,))]

    def claim(self,identity,lease):
        with self.write() as con:
            row=con.execute('SELECT * FROM wf_jobs WHERE id=?',(identity,)).fetchone()
            if not row or row['state']!='queued' or row['deadline']<=self.clock():return False
            con.execute("UPDATE wf_jobs SET state='running',updated=?,lease=? WHERE id=?",(self.clock(),encoded(lease),identity))
            self.event(con,row['project'],'action_started',identity,{})
            return True

    def finish(self,identity,state,result,*,lease=None):
        if state not in {'succeeded','failed','interrupted'}:raise ValueError('Invalid action completion')
        with self.write() as con:
            row=con.execute('SELECT * FROM wf_jobs WHERE id=?',(identity,)).fetchone()
            if not row:raise ValueError('Unknown action')
            if row['state'] in {'succeeded','failed','interrupted'}:return
            if lease is not None and row['lease']!=encoded(lease):raise ValueError('Action lease changed')
            con.execute('UPDATE wf_jobs SET state=?,updated=?,result=? WHERE id=?',(state,self.clock(),encoded(result),identity))
            self.event(con,row['project'],'action_'+state,identity,result)

    def phase(self,job,project,kind,key,deadline,payload):
        identity='phase-'+fingerprint([job,project,kind,key])[:40]
        with self.write() as con:
            old=con.execute('SELECT * FROM wf_phases WHERE id=?',(identity,)).fetchone()
            if old:return self.unpack(old)
            con.execute('INSERT INTO wf_phases VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        (identity,job,project,kind,'waiting',self.clock(),self.clock(),self.clock(),deadline,encoded(payload),None,None))
            self.event(con,project,'phase_waiting',identity,{'kind':kind,'job':job})
            return self.unpack(con.execute('SELECT * FROM wf_phases WHERE id=?',(identity,)).fetchone())

    def phase_update(self,identity,state,*,lease=None,expected_lease=None,result=None,progress=False,deadline=None):
        if state not in {'waiting','running','succeeded','failed','interrupted'}:raise ValueError('Invalid phase state')
        with self.write() as con:
            row=con.execute('SELECT * FROM wf_phases WHERE id=?',(identity,)).fetchone()
            if not row:raise ValueError('Unknown phase')
            if row['state'] in {'succeeded','failed','interrupted'}:
                if state!=row['state'] or lease is not None and encoded(lease)!=row['lease'] or result is not None and encoded(result)!=row['result']:
                    raise ValueError('Terminal phase is immutable')
                return False
            if row['state']=='running' and state=='waiting':raise ValueError('Phase cannot return to waiting')
            if expected_lease is not None and encoded(expected_lease)!=row['lease']:raise ValueError('Phase owner changed')
            if lease is not None and row['lease'] and encoded(lease)!=row['lease'] and expected_lease is None:
                raise ValueError('Replacing a phase owner requires comparison')
            con.execute('UPDATE wf_phases SET state=?,updated=?,progress=?,lease=?,result=?,deadline=? WHERE id=?',
                        (state,self.clock(),self.clock() if progress else row['progress'],encoded(lease) if lease else row['lease'],
                         encoded(result) if result is not None else row['result'],deadline or row['deadline'],identity))
            if state!=row['state']:self.event(con,row['project'],'phase_'+state,identity,{'kind':row['kind']})
            return True

    def phases(self,*,project=None,active=True,limit=100):
        bounded_limit(limit);clauses=[];params=[]
        if project:clauses.append('project=?');params.append(project)
        if active:clauses.append("state IN ('waiting','running')")
        with self.read() as con:
            if not con:return []
            query='SELECT * FROM wf_phases'+(' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY updated DESC LIMIT ?'
            return [self.unpack(r) for r in con.execute(query,(*params,limit))]

    def execution(self,phase,lease):
        with self.write() as con:
            row=con.execute('SELECT * FROM wf_phases WHERE id=?',(phase,)).fetchone()
            if not row or row['state']!='running' or row['lease']!=encoded(lease):raise ValueError('Execution has no matching running phase owner')
            old=con.execute('SELECT * FROM wf_executions WHERE phase=?',(phase,)).fetchone()
            if old:
                if old['lease']!=encoded(lease) or old['cleared']:raise ValueError('Execution group identity is immutable')
                return
            con.execute('INSERT INTO wf_executions VALUES(?,?,?,?,0,0)',(phase,row['job'],row['kind'],encoded(lease)))

    def executions(self,*,job=None):
        with self.read() as con:
            if not con or not con.execute("SELECT 1 FROM sqlite_master WHERE name='wf_executions'").fetchone():return []
            result=[self.unpack(r) for r in con.execute('SELECT * FROM wf_executions WHERE cleared=0'+(' AND job=?' if job else ''), (job,) if job else ())]
            for item in result:
                item['lease']['members']=[dict(r) for r in con.execute('SELECT pid,stamp,boot_id,birth_us FROM wf_execution_members WHERE phase=?',(item['phase'],))]
                item['lease']['_execution_revision']=item['revision']
            return result

    def execution_members(self,phase,members):
        if not members:return
        from phase_runtime import admission_lock
        with admission_lock(self.runtime),self.write() as con:
            row=con.execute('SELECT cleared FROM wf_executions WHERE phase=?',(phase,)).fetchone()
            if not row or row[0]:raise ValueError('Execution group is not active')
            cursor=con.executemany('INSERT OR IGNORE INTO wf_execution_members VALUES(?,?,?,?,?)',
                [(phase,m['pid'],m['stamp'],m['boot_id'],m.get('birth_us',0)) for m in members])
            if cursor.rowcount:con.execute('UPDATE wf_executions SET revision=revision+1 WHERE phase=?',(phase,))

    def clear_execution(self,phase,lease,*,admission_owned=False):
        if not admission_owned:
            from phase_runtime import admission_lock
            with admission_lock(self.runtime):return self.clear_execution(phase,lease,admission_owned=True)
        with self.write() as con:
            row=con.execute('SELECT lease,revision FROM wf_executions WHERE phase=?',(phase,)).fetchone()
            if row and row[1]==lease.get('_execution_revision',0) and all(json.loads(row[0]).get(k)==lease.get(k) for k in ('pid','stamp','boot_id','group','birth_us')):
                con.execute('UPDATE wf_executions SET cleared=1 WHERE phase=? AND lease=? AND revision=?',(phase,row[0],row[1]));return True
            return False

    def incident(self,project,code,key,evidence_hash,payload):
        identity='incident-'+fingerprint([project,code,key])[:32]
        with self.write() as con:
            row=con.execute('SELECT * FROM wf_incidents WHERE id=?',(identity,)).fetchone()
            if row:
                if row['state']=='resolved':
                    con.execute("UPDATE wf_incidents SET state='open',updated=? WHERE id=?",(self.clock(),identity))
                    self.event(con,project,'incident_reopened',identity,{'code':code})
                if row['evidence_hash']!=evidence_hash:
                    con.execute('UPDATE wf_incidents SET evidence_hash=?,updated=?,payload=? WHERE id=?',
                                (evidence_hash,self.clock(),encoded(payload),identity))
                return identity
            con.execute('INSERT INTO wf_incidents VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (identity,identity,project,code,'open',self.clock(),self.clock(),evidence_hash,0,encoded(payload)))
            self.event(con,project,'incident_opened',identity,{'code':code})
            return identity

    def incidents(self,*,limit=100):
        bounded_limit(limit)
        with self.read() as con:
            return [self.unpack(r) for r in con.execute('SELECT * FROM wf_incidents ORDER BY updated DESC LIMIT ?',(limit,))] if con else []

    def get_incident(self,identity):
        with self.read() as con:
            return self.unpack(con.execute('SELECT * FROM wf_incidents WHERE id=?',(identity,)).fetchone()) if con else None

    def incident_state(self,identity,state,detail):
        if state not in {'open','recovering','resolved','held'}:raise ValueError('Invalid incident state')
        with self.write() as con:
            row=con.execute('SELECT * FROM wf_incidents WHERE id=?',(identity,)).fetchone()
            if not row:raise ValueError('Unknown incident')
            con.execute('UPDATE wf_incidents SET state=?,updated=? WHERE id=?',(state,self.clock(),identity))
            if state!=row['state']:self.event(con,row['project'],'incident_'+state,identity,{'detail':detail[:500]})

    def reserve_repair(self,incident,payload):
        with self.write() as con:
            row=con.execute('SELECT * FROM wf_incidents WHERE id=?',(incident,)).fetchone()
            if not row or row['state'] in {'resolved','held'}:raise ValueError('Incident is not repairable')
            if con.execute("SELECT 1 FROM wf_jobs WHERE project='midnight' AND state IN ('queued','running')").fetchone():return None
            if con.execute("SELECT 1 FROM wf_repairs WHERE state IN ('reserved','running','unknown')").fetchone():raise ValueError('Another maintenance repair owns the lane')
            if row['attempts']>=4:
                con.execute("UPDATE wf_incidents SET state='held',updated=? WHERE id=?",(self.clock(),incident))
                self.event(con,row['project'],'incident_held',incident,{'detail':'Four maintenance attempts exhausted'})
                return None
            number=row['attempts']+1;identity=incident+'-'+str(number)
            con.execute('INSERT INTO wf_repairs VALUES(?,?,?,?,?,?,?,?)',
                        (identity,incident,number,'reserved',self.clock(),self.clock(),None,encoded(payload)))
            con.execute("UPDATE wf_incidents SET attempts=?,state='recovering',updated=? WHERE id=?",(number,self.clock(),incident))
            self.event(con,row['project'],'maintenance_reserved',identity,{'incident':incident,'number':number})
            return identity

    def reserve_progress_repair(self,incident,payload,recovery,scope,model,session):
        """Consume one intervention and append its launch intent atomically.

        Preparation, verification and delivery use a continuation lease. Only
        this implementation callback appends a new charged repair reservation.
        A replay after commitment is uncertain and may never launch again.
        """
        with self.write() as con:
            if recovery.store.path.resolve() != self.store.path.resolve() or recovery.incident != incident:
                raise ValueError('Maintenance proof must share the incident workflow ledger')
            owner=con.execute('SELECT * FROM mr_sessions WHERE id=? AND incident=?',(session,incident)).fetchone()
            if not owner or owner['state']!='running':raise ValueError('Maintenance continuation no longer owns launch')
            jobs=con.execute("SELECT * FROM wf_jobs WHERE project='midnight' AND state='running'").fetchall()
            if len(jobs)!=1 or json.loads(jobs[0]['payload']).get('continuation')!=session or jobs[0]['lease']!=owner['lease'] or not owner['lease'] or jobs[0]['deadline']<=self.clock():
                raise ValueError('Maintenance continuation lacks its current leased action')
            prior=con.execute('SELECT 1 FROM pr_launches WHERE run=?',(session,)).fetchone()
            if prior:raise ValueError('Maintenance implementation launch is already charged; reconcile without replay')
            row=con.execute('SELECT * FROM wf_incidents WHERE id=?',(incident,)).fetchone()
            binding=payload.get('incident_binding') or {}
            observed=con.execute('SELECT * FROM wf_incidents WHERE id=?',(binding.get('id',incident),)).fetchone()
            if not row or not observed or observed['state']=='resolved':raise ValueError('Maintenance incident is no longer current')
            if binding:
                from maintenance_recovery import substantive
                if observed['project']!=binding.get('project') or observed['code']!=binding.get('code') or substantive(json.loads(observed['payload']))!=substantive(binding.get('payload',{})):
                    raise ValueError('Observed maintenance failure changed before implementation commitment')
            proof=recovery.reserve(scope,session,model,connection=con)
            unit=con.execute("SELECT json_extract(payload,'$.evidence.anchor.unit_id') FROM pr_proofs WHERE id=?",(proof['proof'],)).fetchone()[0]
            if not unit or con.execute("SELECT 1 FROM pr_launches l JOIN pr_proofs p ON p.id=l.proof JOIN pr_scopes s ON s.id=p.scope WHERE s.project='midnight' AND l.run!=? AND json_extract(p.payload,'$.evidence.anchor.unit_id')=? LIMIT 1",(session,unit)).fetchone():
                raise ValueError('This maintenance source unit is already spent under its original lineage')
            number=row['attempts']+1;identity=incident+'-'+str(number)
            value=dict(payload,progress_recovery={'version':2,'session':session,'reservation':proof})
            con.execute('INSERT INTO wf_repairs VALUES(?,?,?,?,?,?,?,?)',
                        (identity,incident,number,'running',self.clock(),self.clock(),owner['lease'],encoded(value)))
            con.execute("UPDATE wf_incidents SET attempts=?,state='recovering',updated=? WHERE id=?",(number,self.clock(),incident))
            continuation=json.loads(owner['payload']);continuation.update(stage='implementation_launched',repair=identity,retained_reservation=False,operation_identity=session)
            con.execute('UPDATE mr_sessions SET payload=?,updated=? WHERE id=?',(encoded(continuation),self.clock(),session))
            con.execute('INSERT INTO wf_charges VALUES(?,?,?,?,?,?)',
                        (jobs[0]['id'],session,'midnight','maintenance:'+session,'maintenance_implementation',self.clock()))
            self.event(con,row['project'],'maintenance_implementation_reserved',identity,
                       {'incident':incident,'number':number,'proof':proof['proof'],'session':session,'launch_state':'intent_committed'})
            return identity

    def repair(self,identity,*,state=None,lease=None,expected_lease=None,payload=None):
        if state is not None:
            if state not in {'running','passed','failed','unknown'}:raise ValueError('Invalid repair state')
            with self.write() as con:
                row=con.execute('SELECT * FROM wf_repairs WHERE id=?',(identity,)).fetchone()
                if not row:raise ValueError('Unknown repair')
                if row['state'] in {'passed','failed'}:
                    if state!=row['state'] or lease is not None and encoded(lease)!=row['lease'] or payload is not None and encoded(payload)!=row['payload']:
                        raise ValueError('Terminal maintenance attempt is immutable')
                    return self.unpack(row)
                allowed={'reserved':{'running','failed','unknown'},'running':{'running','passed','failed','unknown'},'unknown':{'unknown','passed','failed'}}
                if state not in allowed[row['state']]:raise ValueError('Invalid maintenance transition')
                if expected_lease is not None and encoded(expected_lease)!=row['lease']:raise ValueError('Maintenance owner changed')
                if row['lease'] and (lease is not None and encoded(lease)!=row['lease'] or row['state']=='unknown') and expected_lease is None:
                    raise ValueError('Replacing or reconciling maintenance ownership requires comparison')
                con.execute('UPDATE wf_repairs SET state=?,updated=?,lease=?,payload=? WHERE id=?',
                            (state,self.clock(),encoded(lease) if lease else row['lease'],encoded(payload) if payload is not None else row['payload'],identity))
        with self.read() as con:
            return self.unpack(con.execute('SELECT * FROM wf_repairs WHERE id=?',(identity,)).fetchone()) if con else None

    def repairs(self,*,active=True):
        with self.read() as con:
            return [self.unpack(r) for r in con.execute('SELECT * FROM wf_repairs '+("WHERE state IN ('reserved','running','unknown') " if active else '')+'ORDER BY created DESC LIMIT 100')] if con else []

    def meta(self,key,value=None,*,write=False):
        if write:
            with self.write() as con:con.execute('INSERT OR REPLACE INTO wf_meta VALUES(?,?)',(key,encoded(value)))
            return value
        with self.read() as con:
            row=con.execute('SELECT value FROM wf_meta WHERE key=?',(key,)).fetchone() if con else None
            return json.loads(row[0]) if row else None

    def charge(self,job,key,kind):
        if kind not in {'local','local_supervision','cloud_implementation','cloud_management','maintenance_local_supervision','maintenance_review'}:raise ValueError('Invalid launch charge')
        with self.write() as con:
            row=con.execute('SELECT * FROM wf_jobs WHERE id=?',(job,)).fetchone()
            if not row or row['state']!='running':raise ValueError('No running action owns launch charge')
            session=json.loads(row['payload']).get('session')
            if kind.startswith('maintenance_'):
                payload=json.loads(row['payload'])
                continuation=payload.get('continuation') or payload.get('maintenance')
                if row['project']!='midnight' or not continuation:raise ValueError('Maintenance supervision needs its scoped action')
                session='maintenance:'+continuation
            if not session:raise ValueError('Product launch must identify its session')
            con.execute('INSERT OR IGNORE INTO wf_charges VALUES(?,?,?,?,?,?)',(job,key,row['project'],session,kind,self.clock()))

    def session_charges(self,session,project):
        with self.read() as con:
            if not con:return {}
            return {r['kind']:r['count'] for r in con.execute('SELECT kind,COUNT(*) AS count FROM wf_charges WHERE session=? AND project=? GROUP BY kind',(session,project))}

    def events(self,*,limit=100,project=None,after=None):
        bounded_limit(limit)
        with self.read() as con:
            if not con:return []
            clauses=[];params=[]
            if project:clauses.append('project=?');params.append(project)
            if after is not None:clauses.append('sequence>?');params.append(after)
            where='WHERE '+' AND '.join(clauses) if clauses else ''
            return [self.unpack(r) for r in con.execute('SELECT * FROM wf_events '+where+' ORDER BY sequence '+('ASC' if after is not None else 'DESC')+' LIMIT ?',(*params,limit))]

    def snapshot(self):
        jobs=self.jobs();phases=self.phases();incidents=self.incidents()
        with self.read() as con:
            totals={r['kind']:r['count'] for r in con.execute('SELECT * FROM wf_totals')} if con else {}
        progress=(self.meta('maintenance_recovery') or {}).get('version')==2
        return {'version':1,'active_actions':[{k:j[k] for k in ('id','project','state','created','updated','deadline')} for j in jobs],
                'active_phases':[{k:p[k] for k in ('id','job','project','kind','state','created','progress','deadline')} for p in phases],
                'incidents':[{k:i[k] for k in ('id','project','code','state','created','updated','attempts')} for i in incidents],
                'guardian':self.meta('guardian') or {'state':'unobserved'},'events':self.events(limit=30),
                'totals':totals,
                'maintenance_recovery':{'version':2 if progress else 1,'admission':'verified_intervention' if progress else 'legacy_four_attempts',
                    'historical_attempts_are_permission':False},
                'limits':{'local':1,'host':1,'cloud':2,'maintenance_attempts':None if progress else 4}}

    def session_summary(self,project,since,until):
        with self.read() as con:
            if not con:return {}
            return {r['kind']:r['count'] for r in con.execute(
                "SELECT kind,COUNT(*) AS count FROM wf_events WHERE project IN (?, 'midnight') AND at>=? AND at<=? AND (kind LIKE 'incident_%' OR kind LIKE 'maintenance_%') GROUP BY kind",
                (project,since,until))}


def observe(runtime):
    try:return dict(Actions(runtime).snapshot(),available=True)
    except (sqlite3.Error,OSError,ValueError) as exc:
        return {'version':1,'available':False,'error':type(exc).__name__,'guardian':{'state':'unavailable'},
            'active_actions':[],'active_phases':[],'incidents':[],'events':[]}


def observed_jobs(runtime):
    try:return Actions(runtime).jobs()
    except (sqlite3.Error,OSError,ValueError):return []


def observed_phases(runtime,project):
    try:return Actions(runtime).phases(project=project)
    except (sqlite3.Error,OSError,ValueError):return []
