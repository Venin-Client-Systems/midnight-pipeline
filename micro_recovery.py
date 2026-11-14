"""One bounded local decomposition of a retained narrow repair; no promotion."""
import hashlib,json,re
from pathlib import Path
from history_store import HistoryStore,encoded
import worker_quality as Q
from local_supervisor import validate

MAX_STEPS=4
ATTEMPTS_PER_STEP=2
SCHEMA={'type':'object','additionalProperties':False,'properties':{
 'decision':{'type':'string','enum':['decompose','hold']},'reason':{'type':'string','maxLength':2000},
 'steps':{'type':'array','maxItems':MAX_STEPS,'items':{'type':'object','additionalProperties':False,
    'properties':{'title':{'type':'string','maxLength':120},'description':{'type':'string','maxLength':1600},
      'acceptance':{'type':'array','maxItems':3,'items':{'type':'string','maxLength':700}}},
    'required':['title','description','acceptance']}}},'required':['decision','reason','steps']}


class MicroHeld(ValueError):pass


class MicroRecovery:
 def __init__(self,runtime,project):self.store=HistoryStore(runtime);self.project=project

 def _get(self,con,episode):
  if not con or not con.execute("SELECT 1 FROM sqlite_master WHERE name='micro_plans'").fetchone():return None
  row=con.execute('SELECT payload FROM micro_plans WHERE project=? AND episode=?',(self.project,episode)).fetchone()
  return json.loads(row[0]) if row else None

 def get(self,episode):
  with self.store.reader() as con:return self._get(con,episode)

 def _extension(self,con,episode):
  if not con.execute("SELECT 1 FROM sqlite_master WHERE name='micro_owner_extensions'").fetchone():return None
  row=con.execute('SELECT payload FROM micro_owner_extensions WHERE project=? AND episode=?',(self.project,episode)).fetchone()
  return json.loads(row[0]) if row else None

 def extend_after_worker_fix(self,task,episode,head,profile_hash,*,owner_path,qualification_path,review):
  """One explicit owner exception for the exhausted current step, never a reset."""
  import worker_recovery as W
  if review.get('decision')!='accept' or review.get('issues') or not re.fullmatch('[a-f0-9]{64}',review.get('receipt_sha256','')):
   raise MicroHeld('Worker recovery needs separate independent review')
  proof=W.qualification(qualification_path)
  with self.store.transaction() as con:
   step=self._current(con,task,episode,head,profile_hash)
   if not step:raise MicroHeld('An existing immutable microplan is required')
   owner=W.owner_direction(owner_path,self.project,episode,task['id'],step['index'])
   value={'task_sha256':Q.task_fingerprint(task),'head':head,'profile_hash':profile_hash,'step':step['index'],
       'additional_dispatches':2,'original_limit':ATTEMPTS_PER_STEP,'owner':owner,'qualification':proof,'review':review}
   old=self._extension(con,episode)
   if old:
    if old!=value:raise MicroHeld('This issue already has its one owner extension; no renewed allowance')
    return old
   if step['used']!=ATTEMPTS_PER_STEP:raise MicroHeld('Only the exhausted current step may receive the owner extension')
   con.execute('CREATE TABLE IF NOT EXISTS micro_owner_extensions(project TEXT NOT NULL,episode TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(project,episode))')
   con.execute('INSERT INTO micro_owner_extensions VALUES(?,?,?)',(self.project,episode,encoded(value)))
   self.store.append('local_microstep_owner_extension',self.project,value,event_id='micro-owner-extension:'+episode,
       episode_id=episode,task_id=task['id'],connection=con)
  return value

 def approve(self,task,episode,head,profile_hash,proposal,checkpoint,review):
  validate(proposal,SCHEMA)
  files=task.get('local_worker_files',[])
  if (proposal['decision']!='decompose' or not 2<=len(proposal['steps'])<=MAX_STEPS
      or any(not s['title'].strip() or not s['description'].strip() or not s['acceptance'] or any(not a.strip() for a in s['acceptance']) for s in proposal['steps'])
      or len(files)!=1 or not re.fullmatch(r'tests/[A-Za-z0-9_./-]+',files[0]) or '..' in Path(files[0]).parts
      or not re.search(r'(^test_.*\.py$|\.(test|spec)\.[cm]?[jt]sx?$)',Path(files[0]).name)
      or review.get('decision')!='accept' or review.get('issues')
      or not re.fullmatch('[a-f0-9]{64}',review.get('receipt_sha256',''))
      or checkpoint.get('task_sha256')!=Q.task_fingerprint(task)
      or checkpoint.get('base')!=head or not re.fullmatch('[a-f0-9]{64}',checkpoint.get('patch_sha256',''))):
   raise MicroHeld('Decomposition needs bounded test-only steps, exact retained source and independent approval')
  value={'task_sha256':Q.task_fingerprint(task),'episode':episode,'head':head,'profile_hash':profile_hash,
    'proposal':proposal,'checkpoint':checkpoint,'review':review,'worker_file':files[0],
    'attempts_per_step':ATTEMPTS_PER_STEP,'final_contract':task['acceptance'],
    'dispatch_policy':'This one reviewed supplemental plan supersedes only the old local continuation dispatch ceiling. All original charges, task identity, frozen paths and complete functional/final acceptance remain.'}
  with self.store.transaction() as con:
   parent=con.execute('SELECT payload FROM mr_plans WHERE project=? AND episode=?',(self.project,episode)).fetchone()
   if not parent:raise MicroHeld('A reviewed narrow continuation is required')
   parent=json.loads(parent[0])
   if any(parent[k]!=value[k] for k in ('task_sha256','head','profile_hash')):raise MicroHeld('Original continuation changed')
   count=con.execute('SELECT COUNT(*) FROM mr_dispatches WHERE project=? AND episode=?',(self.project,episode)).fetchone()[0]
   if count<2:raise MicroHeld('Ordinary narrow attempts must finish before decomposition')
   con.execute('CREATE TABLE IF NOT EXISTS micro_plans(project TEXT NOT NULL,episode TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(project,episode))')
   con.execute('CREATE TABLE IF NOT EXISTS micro_dispatches(project TEXT NOT NULL,episode TEXT NOT NULL,step INTEGER NOT NULL,run TEXT NOT NULL,PRIMARY KEY(project,episode,run))')
   con.execute('CREATE TABLE IF NOT EXISTS micro_completed(project TEXT NOT NULL,episode TEXT NOT NULL,step INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(project,episode,step))')
   old=self._get(con,episode)
   if old and old!=value:raise MicroHeld('This issue already has its one immutable decomposition')
   con.execute('INSERT OR IGNORE INTO micro_plans VALUES(?,?,?)',(self.project,episode,encoded(value)))
   self.store.append('local_microplan_approved',self.project,value,event_id='microplan:'+episode,episode_id=episode,task_id=task['id'],connection=con)
  return value

 def _current(self,con,task,episode,head,profile_hash):
  plan=self._get(con,episode)
  if not plan:return None
  if plan['task_sha256']!=Q.task_fingerprint(task) or plan['head']!=head or plan['profile_hash']!=profile_hash:raise MicroHeld('Microstep source, profile or task contract changed')
  completed={r[0] for r in con.execute('SELECT step FROM micro_completed WHERE project=? AND episode=?',(self.project,episode))}
  index=next((i for i in range(len(plan['proposal']['steps'])) if i not in completed),None)
  if index is None:raise MicroHeld('Final promotion remains separate from intermediate checkpoints')
  count=con.execute('SELECT COUNT(*) FROM micro_dispatches WHERE project=? AND episode=? AND step=?',(self.project,episode,index)).fetchone()[0]
  last=con.execute('SELECT run FROM micro_dispatches WHERE project=? AND episode=? AND step=? ORDER BY rowid DESC LIMIT 1',(self.project,episode,index)).fetchone()
  extension=self._extension(con,episode);extra=0
  if extension and extension['step']==index:
   if any(extension[k]!=v for k,v in (('task_sha256',Q.task_fingerprint(task)),('head',head),('profile_hash',profile_hash))):raise MicroHeld('Owner extension contract changed')
   extra=extension['additional_dispatches']
  return {**plan['proposal']['steps'][index],'index':index,'total':len(plan['proposal']['steps']),
          'worker_file':plan['worker_file'],'used':count,'last_run':last[0] if last else None,'remaining':max(0,ATTEMPTS_PER_STEP+extra-count),
          'original_limit':ATTEMPTS_PER_STEP,'owner_additional_dispatches':extra}

 def current(self,task,episode,head,profile_hash):
  with self.store.reader() as con:return self._current(con,task,episode,head,profile_hash)

 def completed(self,episode):
  with self.store.reader() as con:
   if not self._get(con,episode):return []
   return [json.loads(r[0]) for r in con.execute('SELECT payload FROM micro_completed WHERE project=? AND episode=? ORDER BY step',(self.project,episode))]

 def reserve(self,task,episode,head,profile_hash,run,model=None,*,evidence_roots=()):
  with self.store.transaction() as con:
   step=self._current(con,task,episode,head,profile_hash)
   if not step:raise MicroHeld('Unknown decomposition')
   import progress_recovery as P
   recovery=P.Recovery(self.store.path.parent,self.project,evidence_roots=[*evidence_roots,*P.certificate_roots(Path(__file__).parent/'runtime')])
   identity=P.scope(self.project,episode,task,head,profile_hash,'micro-'+str(step['index']))
   if recovery._scope(con,identity['id']):
    prior=con.execute('SELECT step FROM micro_dispatches WHERE project=? AND episode=? AND run=?',(self.project,episode,run)).fetchone()
    historical=con.execute('SELECT 1 FROM mr_dispatches WHERE project=? AND episode=? AND run=?',(self.project,episode,run)).fetchone()
    existing=con.execute('SELECT scope FROM pr_launches WHERE run=?',(run,)).fetchone()
    if historical and (not existing or existing['scope']!=identity['id'] or not prior or prior[0]!=step['index']):raise MicroHeld('An earlier charged run cannot be reused as a progress launch')
    if prior and prior[0]!=step['index']:raise MicroHeld('An earlier dispatch cannot move to another step')
    value=recovery.reserve(identity,run,model,connection=con)
    con.execute('INSERT OR IGNORE INTO micro_dispatches VALUES(?,?,?,?)',(self.project,episode,step['index'],run))
    con.execute('INSERT OR IGNORE INTO mr_dispatches VALUES(?,?,?)',(self.project,episode,run))
    return value
   prior=con.execute('SELECT step FROM micro_dispatches WHERE project=? AND episode=? AND run=?',(self.project,episode,run)).fetchone()
   if not prior and con.execute('SELECT 1 FROM mr_dispatches WHERE project=? AND episode=? AND run=?',(self.project,episode,run)).fetchone():raise MicroHeld('An earlier charged dispatch cannot be reused as a new microstep')
   if prior and prior[0]!=step['index']:raise MicroHeld('A dispatch cannot move to another step')
   if not prior and not step['remaining']:raise MicroHeld('Microstep attempts exhausted; all charges retained')
   if not prior and step['used']>=ATTEMPTS_PER_STEP:
    import worker_recovery as W
    extension=self._extension(con,episode)
    if not model or not extension:raise MicroHeld('An exact qualified model is required for extra launches')
    try:
     actual=W.qualification(extension['qualification']['path'],model)
     owner=W.owner_direction(extension['owner']['path'],self.project,episode,task['id'],step['index'])
     if actual!=extension['qualification'] or owner!=extension['owner']:raise ValueError('Recovery proof or owner direction changed')
    except (OSError,ValueError,KeyError,TypeError) as exc:raise MicroHeld('Worker recovery needs requalification: '+str(exc)) from exc
   con.execute('INSERT OR IGNORE INTO micro_dispatches VALUES(?,?,?,?)',(self.project,episode,step['index'],run))
   con.execute('INSERT OR IGNORE INTO mr_dispatches VALUES(?,?,?)',(self.project,episode,run))
   self.store.append('local_microstep_reserved',self.project,{'run':run,'step':step['index']},event_id='microstep-reserved:'+run,episode_id=episode,task_id=task['id'],connection=con)
  return {'episode':episode,'run':run,'charged':True,'step':step['index'],'attempts_per_step':ATTEMPTS_PER_STEP}

 def complete_step(self,task,episode,head,profile_hash,index,run,patch_sha256,checks,review,evidence):
  if (not checks or any(c.get('code')!=0 or c.get('reason')!='exited' for c in checks)
      or review.get('decision')!='accept' or review.get('issues') or not evidence
      or not re.fullmatch('[a-f0-9]{64}',patch_sha256)):
   raise MicroHeld('An intermediate checkpoint requires actual checks and independent review')
  value={'run':run,'patch_sha256':patch_sha256,'checks':checks,'review':review,'evidence':evidence,'accepted_product':False}
  with self.store.transaction() as con:
   step=self._current(con,task,episode,head,profile_hash)
   old=con.execute('SELECT payload FROM micro_completed WHERE project=? AND episode=? AND step=?',(self.project,episode,index)).fetchone()
   if old:
    if json.loads(old[0])!=value:raise MicroHeld('Conflicting microstep completion')
    return
   if step['index']!=index or index==step['total']-1:raise MicroHeld('Only the current intermediate step may checkpoint; final step needs full acceptance')
   if not con.execute('SELECT 1 FROM micro_dispatches WHERE project=? AND episode=? AND step=? AND run=?',(self.project,episode,index,run)).fetchone():raise MicroHeld('Unreserved microstep')
   con.execute('INSERT INTO micro_completed VALUES(?,?,?,?)',(self.project,episode,index,encoded(value)))
   self.store.append('local_microstep_checkpoint',self.project,value,event_id='microstep-completed:'+episode+':'+str(index),episode_id=episode,task_id=task['id'],connection=con)
