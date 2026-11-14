"""One independently reviewed, narrower local continuation per issue.

This does not alter RescueStore eligibility or refund failed attempts. The host
retains a frozen candidate and gives the local worker a strict subset of its
original writable scope. No intermediate checkpoint is accepted or promoted.
"""
import hashlib
import json
from pathlib import Path
import re
from history_store import HistoryStore
import worker_quality as Q
import evidence_context as E
from micro_recovery import MicroRecovery,MicroHeld

MAX_DISPATCHES=2


class RecoveryHeld(ValueError):
    pass


class ManagerRecovery:
    def __init__(self,runtime,project):
        self.store=HistoryStore(runtime);self.project=project

    def _get(self,con,episode):
        if not con or not con.execute("SELECT 1 FROM sqlite_master WHERE name='mr_plans'").fetchone():return None
        row=con.execute('SELECT payload FROM mr_plans WHERE project=? AND episode=?',(self.project,episode)).fetchone()
        return json.loads(row[0]) if row else None

    def get(self,episode):
        with self.store.reader() as con:return self._get(con,episode)

    def _limit(self,con,episode):
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='mr_host_replays'").fetchone():return MAX_DISPATCHES
        row=con.execute('SELECT payload FROM mr_host_replays WHERE project=? AND episode=?',(self.project,episode)).fetchone()
        if not row:return MAX_DISPATCHES
        value=json.loads(row[0])
        current=hashlib.sha256(Path(__file__).with_name('project_backend.py').read_bytes()).hexdigest()
        return MAX_DISPATCHES+1 if value['corrected_backend_sha256']==current else MAX_DISPATCHES

    def approve_host_replay(self,task,episode,head,profile_hash,*,failed_runs,comparison,review,corrected_backend_sha256):
        """One host-bug replay; original two dispatches and all rescue charges stay."""
        if (len(failed_runs)!=MAX_DISPATCHES or len(set(failed_runs))!=MAX_DISPATCHES
                or not comparison or review.get('decision')!='accept' or review.get('issues')
                or not re.fullmatch('[a-f0-9]{64}',review.get('receipt_sha256',''))
                or corrected_backend_sha256!=hashlib.sha256(Path(__file__).with_name('project_backend.py').read_bytes()).hexdigest()):
            raise RecoveryHeld('Host correction replay needs exact failed dispatches and independently reviewed corrected source')
        proof={}
        for run in failed_runs:
            if not re.fullmatch('[A-Za-z0-9_-]+',run):raise RecoveryHeld('Invalid failed dispatch identity')
            directory=self.store.path.parent/'runs'/run
            files={name:directory/name for name in ('task.json','outcome.json','work/PRD.md')}
            if not all(E.safe_file(path,directory) for path in files.values()):raise RecoveryHeld('Host correction evidence is unavailable')
            outcome=json.loads(files['outcome.json'].read_text());saved=json.loads(files['task.json'].read_text())
            if outcome.get('accepted') is not False or outcome.get('outcome')!='failed' or Q.task_fingerprint(saved)!=Q.task_fingerprint(task):raise RecoveryHeld('Replay requires the exact failed task contract')
            proof[run]={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in files.items()}
        value={'task_sha256':Q.task_fingerprint(task),'corrected_backend_sha256':corrected_backend_sha256,
               'failed_dispatches':proof,'contract_comparison':comparison,'review':review,'additional_dispatches':1}
        with self.store.transaction() as con:
            plan=self._get(con,episode)
            if not plan or plan['task_sha256']!=value['task_sha256'] or plan['head']!=head or plan['profile_hash']!=profile_hash:raise RecoveryHeld('Host replay source or immutable continuation changed')
            con.execute('CREATE TABLE IF NOT EXISTS mr_host_replays(project TEXT NOT NULL,episode TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(project,episode))')
            old=con.execute('SELECT payload FROM mr_host_replays WHERE project=? AND episode=?',(self.project,episode)).fetchone()
            if old and json.loads(old[0])!=value:raise RecoveryHeld('This issue already has its one immutable host correction replay')
            if old:return json.loads(old[0])
            charged={r[0] for r in con.execute('SELECT run FROM mr_dispatches WHERE project=? AND episode=?',(self.project,episode))}
            if charged!=set(failed_runs):raise RecoveryHeld('Host replay cannot refund or replace dispatch charges')
            con.execute('INSERT OR IGNORE INTO mr_host_replays VALUES(?,?,?)',(self.project,episode,json.dumps(value,sort_keys=True)))
            self.store.append('manager_host_correction_replay_approved',self.project,value,event_id='manager-host-replay:'+episode,episode_id=episode,task_id=task['id'],connection=con)
        return value

    def tasks(self):
        with self.store.reader() as con:
            if not con or not con.execute("SELECT 1 FROM sqlite_master WHERE name='mr_plans'").fetchone():return []
            rows=con.execute('SELECT p.payload FROM mr_plans p LEFT JOIN mr_completed c ON p.project=c.project AND p.episode=c.episode '
                'WHERE p.project=? AND c.episode IS NULL LIMIT 101',(self.project,)).fetchall()
            if len(rows)>100:raise RecoveryHeld('Too many pending manager continuations; retained for inspection')
            return [json.loads(r[0])['task'] for r in rows]

    def approve(self,task,parent,episode,head,profile_hash,checkpoint,frozen_paths,review,evidence):
        retry={'task_id':parent.get('id'),'task_sha256':Q.task_fingerprint(parent)}
        worker=task.get('local_worker_files',[])
        original=parent.get('files',[])
        if (not episode or not re.fullmatch('[a-f0-9]{40}',head or '') or not profile_hash
                or task.get('retry_of')!=retry or task.get('kind')!='repair'
                or task.get('files')!=original or task.get('finding_ids')!=parent.get('finding_ids')
                or not worker or len(set(worker))!=len(worker) or not set(worker)<set(original)
                or any(not isinstance(p,str) or any(c in p for c in '*?[') for p in original)
                or set(frozen_paths)!=set(original)-set(worker)
                or any(not re.fullmatch('[a-f0-9]{64}',v or '') for v in frozen_paths.values())
                or not re.fullmatch('[a-f0-9]{64}',checkpoint.get('patch_sha256',''))
                or checkpoint.get('task_sha256')!=Q.task_fingerprint(parent)
                or review.get('decision')!='accept' or review.get('issues') or not review.get('receipt_sha256')
                or not evidence or not evidence.get('reason') or not evidence.get('contract_comparison')):
            raise RecoveryHeld('Narrow continuation lacks exact parent, frozen scope, evidence or independent approval')
        grant={'task_sha256':Q.task_fingerprint(task),'task':task,'parent':parent,'episode':episode,
               'head':head,'profile_hash':profile_hash,'checkpoint':checkpoint,'frozen_paths':frozen_paths,
               'review':review,'evidence':evidence,'max_dispatches':MAX_DISPATCHES}
        with self.store.transaction() as con:
            con.execute('CREATE TABLE IF NOT EXISTS mr_plans(project TEXT NOT NULL,episode TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(project,episode))')
            con.execute('CREATE TABLE IF NOT EXISTS mr_dispatches(project TEXT NOT NULL,episode TEXT NOT NULL,run TEXT NOT NULL,PRIMARY KEY(project,episode,run))')
            con.execute('CREATE TABLE IF NOT EXISTS mr_completed(project TEXT NOT NULL,episode TEXT NOT NULL,evidence TEXT NOT NULL,PRIMARY KEY(project,episode))')
            old=self._get(con,episode)
            if old and old!=grant:raise RecoveryHeld('This issue already has its one immutable continuation')
            con.execute('INSERT OR IGNORE INTO mr_plans VALUES(?,?,?)',(self.project,episode,json.dumps(grant,sort_keys=True)))
            self.store.append('manager_recovery_approved',self.project,grant,event_id='manager-recovery:'+episode,
                              episode_id=episode,task_id=task['id'],connection=con)
        return grant

    def complete(self,task,episode,evidence):
        if not evidence.get('accepted_commit') or evidence.get('checks_passed') is not True or evidence.get('review_approved') is not True:
            raise RecoveryHeld('A continuation is complete only after final independent acceptance')
        with self.store.transaction() as con:
            plan=self._get(con,episode)
            if not plan or plan['task_sha256']!=Q.task_fingerprint(task):raise RecoveryHeld('Unknown accepted continuation')
            con.execute('INSERT OR IGNORE INTO mr_completed VALUES(?,?,?)',(self.project,episode,json.dumps(evidence,sort_keys=True)))
            self.store.append('manager_recovery_completed',self.project,evidence,event_id='manager-recovery-completed:'+episode,
                episode_id=episode,task_id=task['id'],connection=con)

    def retire(self,task,episode,evidence):
        with self.store.transaction() as con:
            plan=self._get(con,episode)
            if not plan or plan['task_sha256']!=Q.task_fingerprint(task):raise RecoveryHeld('Unknown retired continuation')
            con.execute('INSERT OR IGNORE INTO mr_completed VALUES(?,?,?)',(self.project,episode,json.dumps(evidence,sort_keys=True)))
            self.store.append('manager_recovery_retired',self.project,evidence,event_id='manager-recovery-retired:'+episode,
                episode_id=episode,task_id=task['id'],connection=con)

    def eligible(self,task,episode,head,profile_hash):
        if not re.fullmatch('[a-f0-9]{40}',head or '') or not profile_hash:return False
        micro=MicroRecovery(self.store.path.parent,self.project)
        if micro.get(episode):
            try:
                import progress_recovery as P
                current=micro.current(task,episode,head,profile_hash)
                recovery=P.Recovery(self.store.path.parent,self.project,evidence_roots=P.certificate_roots(Path(__file__).parent/'runtime'))
                identity=P.scope(self.project,episode,task,head,profile_hash,'micro-'+str(current['index']))
                if recovery.enrolled(identity):return recovery.admission(identity)['allowed']
                return bool(current['remaining'])
            except (MicroHeld,P.RecoveryHeld):return False
        import progress_recovery as P
        recovery=P.Recovery(self.store.path.parent,self.project,evidence_roots=P.certificate_roots(Path(__file__).parent/'runtime'))
        identity=P.scope(self.project,episode,task,head,profile_hash)
        if recovery.enrolled(identity):return recovery.admission(identity)['allowed']
        with self.store.reader() as con:
            plan=self._get(con,episode)
            if not plan or plan['task_sha256']!=Q.task_fingerprint(task) or plan['head']!=head or plan['profile_hash']!=profile_hash:return False
            count=con.execute('SELECT COUNT(*) FROM mr_dispatches WHERE project=? AND episode=?',(self.project,episode)).fetchone()[0]
            return count<self._limit(con,episode)

    def reserve(self,task,episode,head,profile_hash,run,model=None,*,evidence_roots=()):
        micro=MicroRecovery(self.store.path.parent,self.project)
        if micro.get(episode):return micro.reserve(task,episode,head,profile_hash,run,model,evidence_roots=evidence_roots)
        with self.store.transaction() as con:
            plan=self._get(con,episode)
            if not plan or plan['task_sha256']!=Q.task_fingerprint(task) or plan['head']!=head or plan['profile_hash']!=profile_hash:
                raise RecoveryHeld('Continuation contract or source changed')
            import progress_recovery as P
            recovery=P.Recovery(self.store.path.parent,self.project,evidence_roots=[*evidence_roots,*P.certificate_roots(Path(__file__).parent/'runtime')])
            identity=P.scope(self.project,episode,task,head,profile_hash)
            if recovery._scope(con,identity['id']):
                historical=con.execute('SELECT 1 FROM mr_dispatches WHERE project=? AND episode=? AND run=?',(self.project,episode,run)).fetchone()
                existing=con.execute('SELECT scope FROM pr_launches WHERE run=?',(run,)).fetchone()
                if historical and (not existing or existing['scope']!=identity['id']):raise RecoveryHeld('An earlier charged run cannot become a new progress launch')
                value=recovery.reserve(identity,run,model,connection=con)
                con.execute('INSERT OR IGNORE INTO mr_dispatches VALUES(?,?,?)',(self.project,episode,run))
                return value
            prior=con.execute('SELECT 1 FROM mr_dispatches WHERE project=? AND episode=? AND run=?',(self.project,episode,run)).fetchone()
            count=con.execute('SELECT COUNT(*) FROM mr_dispatches WHERE project=? AND episode=?',(self.project,episode)).fetchone()[0]
            limit=self._limit(con,episode)
            if not prior and count>=limit:raise RecoveryHeld('Narrow continuation exhausted; charges retained')
            con.execute('INSERT OR IGNORE INTO mr_dispatches VALUES(?,?,?)',(self.project,episode,run))
            self.store.append('manager_recovery_dispatch_reserved',self.project,{'run':run,'task_sha256':plan['task_sha256']},
                event_id='manager-recovery-dispatch:'+run,episode_id=episode,task_id=task['id'],connection=con)
        return {'episode':episode,'run':run,'charged':True,'max_dispatches':limit}

    def validate_frozen(self,task,episode,work):
        plan=self.get(episode)
        if not plan or plan['task_sha256']!=Q.task_fingerprint(task):raise RecoveryHeld('Unapproved continuation')
        for name,expected in plan['frozen_paths'].items():
            path=Path(work)/name
            if not E.safe_file(path,work) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                raise RecoveryHeld('Local continuation changed a frozen candidate path: '+name)
        return plan
