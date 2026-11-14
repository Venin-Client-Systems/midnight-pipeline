"""Source-bound local review ratings and conservatively budgeted Codex audits."""
import json,re,time,uuid
from history_store import HistoryStore,encoded
from local_supervisor import validate
import worker_quality as W

WINDOW=7*86400
DIMENSIONS=('correctness','security','maintainability','tests')
RATED_REVIEW_SCHEMA={'title':'RatedLocalReview','type':'object','additionalProperties':False,'properties':{
    'decision':{'type':'string','enum':['accept','revise','hold']},'summary':{'type':'string'},
    'issues':{'type':'array','items':{'type':'string'}},'confidence':{'type':'string','enum':['low','medium','high']},
    **{k:{'type':'integer','minimum':0,'maximum':5} for k in DIMENSIONS}},
    'required':['decision','summary','issues','confidence',*DIMENSIONS]}
AUDIT_SCHEMA={'type':'object','additionalProperties':False,'properties':{
    'decision':{'type':'string','enum':['healthy','regression','inconclusive']},
    'summary':{'type':'string'},'issues':{'type':'array','items':{'type':'string'}}},
    'required':['decision','summary','issues']}


def policy():
    return {'version':1,'routine_provider':'local','independent_local_review':True,
        'cloud_implementation':'existing_same_issue_rescue_policy',
        'routine_audits_per_project_7d':1,'corrective_audits_per_project_7d':1,
        'audit_only_changed_locally_reviewed_source':True,'unknown_reservations_count':True,
        'operator_checkpoint_audit':'same_routine_budget_exact_completed_private_checkpoint',
        'ratings_are_model_judgement':True,'automatic_cloud_qualification':False}


class QualityMonitor:
    def __init__(self,runtime,project,*,clock=time.time):self.store=HistoryStore(runtime);self.project=project;self.clock=clock

    def schema(self,con):
        script='''CREATE TABLE IF NOT EXISTS qm_reviews(project TEXT NOT NULL,run TEXT NOT NULL,created REAL NOT NULL,commit_id TEXT,payload TEXT NOT NULL,PRIMARY KEY(project,run));
            CREATE INDEX IF NOT EXISTS qm_review_time ON qm_reviews(project,created);
            CREATE INDEX IF NOT EXISTS qm_review_commit ON qm_reviews(project,commit_id);
            CREATE TABLE IF NOT EXISTS qm_audits(id TEXT PRIMARY KEY,project TEXT NOT NULL,created REAL NOT NULL,kind TEXT NOT NULL,source TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS qm_audit_budget ON qm_audits(project,kind,created);
            CREATE TABLE IF NOT EXISTS qm_holds(project TEXT PRIMARY KEY,payload TEXT NOT NULL);'''
        for statement in script.split(';'):
            if statement.strip():con.execute(statement)

    def exists(self,con):return bool(con and con.execute("SELECT 1 FROM sqlite_master WHERE name='qm_reviews'").fetchone())

    def record_review(self,run,head,patch_sha256,model,review,checks):
        validate(review,RATED_REVIEW_SCHEMA)
        value={'run':run,'head':head,'patch_sha256':patch_sha256,'model':model,'review':review,
               'host_checks_total':len(checks),'host_checks_passed':sum(c.get('code')==0 and c.get('reason')=='exited' for c in checks),
               'ratings_kind':'local_model_judgement','accepted_commit':None}
        with self.store.transaction() as con:
            self.schema(con);old=con.execute('SELECT payload FROM qm_reviews WHERE project=? AND run=?',(self.project,run)).fetchone()
            if old:
                prior=json.loads(old[0]);value['accepted_commit']=prior.get('accepted_commit')
                if prior!=value:raise ValueError('Conflicting review receipt for one run')
                return prior
            con.execute('INSERT INTO qm_reviews VALUES(?,?,?,NULL,?)',(self.project,run,self.clock(),encoded(value)))
            self.store.append('local_quality_review',self.project,value,event_id='local-quality:'+run,task_id=run,connection=con)
        return value

    def accepted(self,run,commit):
        with self.store.transaction() as con:
            self.schema(con);row=con.execute('SELECT payload FROM qm_reviews WHERE project=? AND run=?',(self.project,run)).fetchone()
            if not row:return
            value=json.loads(row[0])
            if value['review'].get('decision')!='accept' or value['review'].get('issues') or value['review'].get('confidence')=='low' or min(value['review'].get(k,0) for k in ('correctness','security','tests'))<4 or not value['host_checks_total'] or value['host_checks_passed']!=value['host_checks_total']:
                raise ValueError('Only an accepted independent local review can bind an accepted commit')
            if value.get('accepted_commit') not in (None,commit):raise ValueError('Accepted review commit changed')
            value['accepted_commit']=commit
            con.execute('UPDATE qm_reviews SET commit_id=?,payload=? WHERE project=? AND run=?',(commit,encoded(value),self.project,run))
            hold=self._hold(con)
            if hold and hold.get('approved_patch_sha256')==value['patch_sha256'] and hold.get('approved_source_head')==value['head']:
                con.execute('DELETE FROM qm_holds WHERE project=?',(self.project,))

    def _hold(self,con):
        row=con.execute('SELECT payload FROM qm_holds WHERE project=?',(self.project,)).fetchone() if self.exists(con) else None
        return json.loads(row[0]) if row else None

    def snapshot(self):
        with self.store.reader() as con:
            if not self.exists(con):return {'available':True,'policy':policy(),'local_reviews':[],'audits':[],'hold':None,'accepted_local_reviews':0,'audit_state':'No local-reviewed changes audited yet'}
            rows=con.execute('SELECT created,payload FROM qm_reviews WHERE project=? ORDER BY created DESC LIMIT 10',(self.project,)).fetchall()
            audits=con.execute('SELECT * FROM qm_audits WHERE project=? ORDER BY created DESC LIMIT 10',(self.project,)).fetchall()
            counts={kind:con.execute('SELECT COUNT(*) FROM qm_audits WHERE project=? AND kind=? AND created>=?',(self.project,kind,self.clock()-WINDOW)).fetchone()[0] for kind in ('routine','corrective')}
            return {'available':True,'policy':policy(),'local_reviews':[{**json.loads(r['payload']),'recorded_at':r['created']} for r in rows],
                'audits':[dict(r,payload=json.loads(r['payload'])) for r in audits],'hold':self._hold(con),
                'accepted_local_reviews':con.execute('SELECT COUNT(*) FROM qm_reviews WHERE project=? AND commit_id IS NOT NULL',(self.project,)).fetchone()[0],
                'cloud_audit_reservations_7d':counts,'audit_state':'Sampled audits; ratings remain model judgement'}

    def due(self,head,kind='routine'):
        if kind not in {'routine','corrective'}:raise ValueError('Unknown audit category')
        with self.store.reader() as con:
            if not self.exists(con):return False
            if con.execute('SELECT COUNT(*) FROM qm_audits WHERE project=? AND kind=? AND created>=?',(self.project,kind,self.clock()-WINDOW)).fetchone()[0]>=1:return False
            if kind=='corrective':return bool(self._hold(con))
            if self._hold(con):return False
            accepted=con.execute('SELECT 1 FROM qm_reviews WHERE project=? AND commit_id=?',(self.project,head)).fetchone()
            if not accepted:return False
            return not con.execute('SELECT 1 FROM qm_audits WHERE project=? AND kind=? AND source=?',(self.project,kind,head)).fetchone()

    def pending(self):
        with self.store.reader() as con:
            if not self.exists(con):return []
            rows=con.execute("SELECT * FROM qm_audits WHERE project=? AND state='reserved_unknown' ORDER BY created LIMIT 501",(self.project,)).fetchall()
            if len(rows)>500:raise ValueError('Too many unresolved audit receipts')
            return [dict(r,payload=json.loads(r['payload'])) for r in rows]

    def corrective_rejection(self,head,patch):
        with self.store.reader() as con:
            if not self.exists(con):return None
            row=con.execute("SELECT payload FROM qm_audits WHERE project=? AND kind='corrective' AND source=? AND state='regression' ORDER BY created DESC LIMIT 1",(self.project,patch)).fetchone()
            value=json.loads(row[0]) if row else None
            return value['result'] if value and value['binding']['head']==head else None

    def reserve(self,source,kind='routine',*,head=None):
        if kind not in {'routine','corrective'}:raise ValueError('Unknown audit category')
        with self.store.transaction() as con:
            self.schema(con)
            hold=self._hold(con)
            if kind=='corrective' and (not hold or not head):raise ValueError('Corrective audit needs an existing quality hold and exact base')
            if kind=='routine' and (self._hold(con) or not con.execute('SELECT 1 FROM qm_reviews WHERE project=? AND commit_id=?',(self.project,source)).fetchone()):raise ValueError('Routine audit needs changed locally reviewed accepted source')
            if kind=='routine' and con.execute('SELECT 1 FROM qm_audits WHERE project=? AND kind=? AND source=?',(self.project,kind,source)).fetchone():raise ValueError('Exact source was already sampled')
            binding={'head':head if kind=='corrective' else source,'hold_audit':hold['audit'] if hold else None}
            token=self._reserve(con,source,kind,binding)
        return token

    def _reserve(self,con,source,kind,binding):
        if con.execute('SELECT COUNT(*) FROM qm_audits WHERE project=? AND kind=? AND created>=?',(self.project,kind,self.clock()-WINDOW)).fetchone()[0]>=1:raise ValueError('Codex audit budget already reserved')
        token=uuid.uuid4().hex;con.execute('INSERT INTO qm_audits VALUES(?,?,?,?,?,?,?)',(token,self.project,self.clock(),kind,source,'reserved_unknown',encoded({'binding':binding})))
        self.store.append('quality_audit_reserved',self.project,{'id':token,'kind':kind,'source':source,**binding},event_id='quality-audit-reserved:'+token,connection=con)
        return token

    def _checkpoint(self,task,episode,head,profile_hash,run,patch_sha256):
        if not re.fullmatch(r'[a-f0-9]{40}',head) or not re.fullmatch(r'[a-f0-9]{64}',patch_sha256) or not re.fullmatch(r'[A-Za-z0-9_-]+',run):raise ValueError('Invalid checkpoint audit identity')
        return {'run':run,'episode':episode,'task_sha256':W.task_fingerprint(task),'patch_sha256':patch_sha256,'finding_ids':task.get('finding_ids',[]),'profile_hash':profile_hash}

    def _checkpoint_row(self,con,head,checkpoint):
        if not self.exists(con):return None
        rows=con.execute("SELECT * FROM qm_audits WHERE project=? AND kind='routine' AND json_extract(payload,'$.binding.operator_checkpoint.run')=?",(self.project,checkpoint['run'])).fetchall()
        if not rows:return None
        if len(rows)!=1:raise ValueError('Conflicting checkpoint audit history')
        value=dict(rows[0]);value['payload']=json.loads(value['payload']);binding=value['payload']['binding']
        if binding.get('head')!=head or binding.get('operator_checkpoint')!=checkpoint:raise ValueError('Existing checkpoint audit binding changed')
        return value

    def checkpoint_audit(self,task,episode,head,profile_hash,run,patch_sha256):
        """Recover the exact durable reservation/result; never reserve on read."""
        checkpoint=self._checkpoint(task,episode,head,profile_hash,run,patch_sha256)
        with self.store.reader() as con:return self._checkpoint_row(con,head,checkpoint)

    def reserve_checkpoint(self,task,episode,head,profile_hash,run,patch_sha256):
        """An interactive Codex spot-check shares the one routine audit budget.

        This records a real operator review; it starts no model or implementation.
        Only a host-completed, locally reviewed intermediate receipt is eligible.
        """
        checkpoint=self._checkpoint(task,episode,head,profile_hash,run,patch_sha256)
        with self.store.transaction() as con:
            self.schema(con)
            if self._checkpoint_row(con,head,checkpoint):raise ValueError('Exact checkpoint was already reserved; recover its durable audit')
            if self._hold(con):raise ValueError('An existing quality hold must be corrected first')
            if not con.execute("SELECT 1 FROM sqlite_master WHERE name='micro_completed'").fetchone():raise ValueError('No completed local checkpoint')
            plan=con.execute('SELECT payload FROM micro_plans WHERE project=? AND episode=?',(self.project,episode)).fetchone()
            row=con.execute('SELECT payload FROM micro_completed WHERE project=? AND episode=? AND json_extract(payload,\'$.run\')=?',(self.project,episode,run)).fetchone()
            plan=json.loads(plan[0]) if plan else {};receipt=json.loads(row[0]) if row else {}
            if (plan.get('task_sha256')!=W.task_fingerprint(task) or plan.get('head')!=head or plan.get('profile_hash')!=profile_hash
                    or receipt.get('patch_sha256')!=patch_sha256 or receipt.get('accepted_product') is not False
                    or not receipt.get('checks') or any(c.get('code')!=0 or c.get('reason')!='exited' for c in receipt['checks'])
                    or receipt.get('review',{}).get('decision')!='accept' or receipt.get('review',{}).get('issues') or not receipt.get('evidence')):
                raise ValueError('Checkpoint audit requires the exact host-checked locally reviewed task and patch')
            return self._reserve(con,head,'routine',{'head':head,'hold_audit':None,'operator_checkpoint':checkpoint})

    def finish(self,token,result,evidence,*,approved_patch=None):
        validate(result,AUDIT_SCHEMA)
        if not evidence:raise ValueError('Audit evidence required')
        if result['decision']=='healthy' and result['issues']:raise ValueError('A healthy audit cannot have unresolved issues')
        if result['decision']=='regression' and not result.get('issues'):raise ValueError('A regression requires concrete findings')
        with self.store.transaction() as con:
            self.schema(con);row=con.execute('SELECT * FROM qm_audits WHERE id=? AND project=?',(token,self.project)).fetchone()
            if not row:raise ValueError('Unknown audit reservation')
            prior=json.loads(row['payload']);binding=prior['binding']
            if evidence.get('source_head')!=binding['head']:raise ValueError('Audit source identity changed')
            checkpoint=binding.get('operator_checkpoint')
            if checkpoint and (evidence.get('checkpoint_run')!=checkpoint['run'] or evidence.get('candidate_patch_sha256')!=checkpoint['patch_sha256']):raise ValueError('Checkpoint audit candidate identity changed')
            if row['kind']=='corrective' and evidence.get('candidate_patch_sha256')!=row['source']:raise ValueError('Audit candidate identity changed')
            payload={'binding':binding,'result':result,'evidence':evidence}
            if approved_patch and (row['kind']!='corrective' or approved_patch!=row['source']):raise ValueError('Corrective audit patch identity changed')
            if row['kind']=='corrective' and result['decision']=='healthy' and approved_patch!=row['source']:raise ValueError('Healthy corrective audit must approve its exact patch')
            if row['state']!='reserved_unknown':
                if row['payload']!=encoded(payload):raise ValueError('Conflicting audit completion')
                return
            hold=self._hold(con)
            if row['kind']=='corrective' and (not hold or hold['audit']!=binding['hold_audit']):raise ValueError('Quality hold changed before corrective audit')
            con.execute('UPDATE qm_audits SET state=?,payload=? WHERE id=?',(result['decision'],encoded(payload),token))
            if result['decision']=='regression' and row['kind']=='routine':
                con.execute('INSERT INTO qm_holds VALUES(?,?) ON CONFLICT(project) DO UPDATE SET payload=excluded.payload',
                    (self.project,encoded({'audit':token,'source':row['source'],'summary':result['summary'],'issues':result['issues'],'automatic_merge':'held',
                                          **({'operator_checkpoint':checkpoint} if checkpoint else {})})))
            elif result['decision']=='regression':
                hold.update(last_corrective_audit=token,corrective_findings=result['issues'])
                hold.pop('approved_patch_sha256',None);hold.pop('approved_source_head',None);hold.pop('corrective_audit',None)
                con.execute('UPDATE qm_holds SET payload=? WHERE project=?',(encoded(hold),self.project))
            elif row['kind']=='corrective' and result['decision']=='healthy' and approved_patch:
                hold.update(approved_patch_sha256=approved_patch,approved_source_head=binding['head'],corrective_audit=token)
                con.execute('UPDATE qm_holds SET payload=? WHERE project=?',(encoded(hold),self.project))
            self.store.append('quality_audit_finished',self.project,{'id':token,**payload},event_id='quality-audit-finished:'+token,connection=con)

    def delivery_allowed(self,patch_sha256,head=None):
        with self.store.reader() as con:
            hold=self._hold(con)
            return not hold or (hold.get('approved_patch_sha256')==patch_sha256 and head is not None and hold.get('approved_source_head')==head)
