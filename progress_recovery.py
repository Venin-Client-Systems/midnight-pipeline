"""Host-owned evidence admission. Local launch counts are history, not permission.

The ledger shares a transaction with legacy manager/microstep dispatch records.
Model proposals cannot register harness certificates or choose execution commands.
Readers never create state. Unknown launches stay charged and consume their proof.
"""
import datetime as dt
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import time
import sys
import subprocess
# The installed host publisher is invoked with python -I after source adoption.
sys.path.insert(0,str(Path(__file__).resolve().parent))
from history_store import HistoryStore,encoded
import evidence_context as E

ROOT=Path(__file__).resolve().parent
VERSION=1
HARNESS_FAILURES={'unfinished_handoff','inference_truncated','worker_contract'}
KINDS={'initial','verified_harness_fix','reviewed_narrowing','verified_checkpoint','verified_precondition_fix','codex_guidance','coordinator_guidance','owner_harness_release'}
SCHEMA=(
 'CREATE TABLE IF NOT EXISTS pr_scopes(id TEXT PRIMARY KEY,project TEXT NOT NULL,episode TEXT NOT NULL,task_hash TEXT NOT NULL,stage TEXT NOT NULL,head TEXT NOT NULL,profile TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL)',
 'CREATE INDEX IF NOT EXISTS pr_scope_project ON pr_scopes(project,created DESC)',
 'CREATE TABLE IF NOT EXISTS pr_diagnoses(scope TEXT PRIMARY KEY,run TEXT,kind TEXT NOT NULL,fingerprint TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL,updated REAL NOT NULL)',
 'CREATE TABLE IF NOT EXISTS pr_proofs(id TEXT PRIMARY KEY,scope TEXT NOT NULL,kind TEXT NOT NULL,semantic_key TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL,UNIQUE(scope,kind,semantic_key))',
 'CREATE TABLE IF NOT EXISTS pr_launches(run TEXT PRIMARY KEY,scope TEXT NOT NULL,proof TEXT NOT NULL UNIQUE,payload TEXT NOT NULL,created REAL NOT NULL)',
 'CREATE TABLE IF NOT EXISTS pr_results(run TEXT PRIMARY KEY,scope TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL)',
 'CREATE TABLE IF NOT EXISTS pr_coordinator_advice(id TEXT PRIMARY KEY,scope TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL)',
 'CREATE TABLE IF NOT EXISTS pr_proof_holds(proof TEXT PRIMARY KEY,reason TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL)',
)

class RecoveryHeld(ValueError):pass

def fingerprint(value):return hashlib.sha256(encoded(value).encode()).hexdigest()

def components():
 return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in
     ('progress_recovery.py','project_backend.py','evidence_context.py','micro_recovery.py','manager_recovery.py','local_worker.py','structured_worker.py','ralphy/run.sh')}

def scope(project,episode,task,head,profile,stage='task'):
 import worker_quality as Q
 if not re.fullmatch(r'[a-z0-9][a-z0-9-]*',project) or not episode or not profile or not re.fullmatch('[a-f0-9]{40}',head):raise RecoveryHeld('Recovery needs exact project/source/profile identity')
 value={'project':project,'episode':episode,'task_hash':Q.task_fingerprint(task),'stage':str(stage),'head':head,'profile':profile}
 return {'id':fingerprint(value),**value}

def ref(path):
 path=Path(path)
 return {'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

def verify_refs(refs,roots):
 if not isinstance(refs,list) or not refs or len(refs)>64:raise RecoveryHeld('Actual host evidence references are required')
 for item in refs:
  if not isinstance(item,dict) or set(item)!={'path','sha256'}:raise RecoveryHeld('Invalid recovery evidence reference')
  path=Path(item['path'])
  if not any(E.safe_file(path,root) for root in roots) or path.stat().st_size>8*1024*1024 or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise RecoveryHeld('Recovery evidence is missing, unsafe or changed')

NEW_FILE_SYNTAX='new-file-v1'


def new_file_anchor(work,task,proposal,evidence_paths):
    """Anchor a repair that must CREATE a declared file absent from the attempt.

    There is no current source to quote, so the verbatim quote must come from
    this attempt's retained failed-check logs. The unit is "create this
    path", so the existing no-repeat rule still stops identical retries.
    """
    path=proposal.get('path');quote=proposal.get('evidence_quote')
    if (proposal.get('symbol')!='__file__' or path not in task.get('local_worker_files',task['files'])
            or not isinstance(quote,str) or not 20<=len(quote)<=1200):
        raise RecoveryHeld('New-file narrowing needs a writable path, __file__ and an actual evidence quote')
    relative=Path(path)
    if relative.is_absolute() or not relative.parts or any(part=='..' for part in relative.parts):raise RecoveryHeld('Invalid new-file path')
    target=Path(work)/relative
    if os.path.lexists(target):raise RecoveryHeld('New-file narrowing requires the declared file to be absent')
    for n in range(1,len(relative.parts)):
        parent=Path(work).joinpath(*relative.parts[:n])
        if parent.is_symlink() or (os.path.lexists(parent) and not parent.is_dir()):raise RecoveryHeld('New-file location is unsafe')
    for log in evidence_paths:
        log=Path(log)
        try:
            if log.is_symlink() or not log.is_file() or log.stat().st_size>4*1024*1024:continue
            if quote in log.read_text(errors='replace'):
                return {'path':path,'symbol':'__file__','unit_symbol':'__file__','kind':'new_file',
                        'unit_id':fingerprint({'path':path,'syntax':NEW_FILE_SYNTAX}),'evidence_quote':quote,'source':ref(log)}
        except OSError:continue
    raise RecoveryHeld('New-file evidence quote does not occur in the retained failed checks')


def source_anchor(work,task,proposal):
    path=proposal.get('path');quote=proposal.get('evidence_quote');symbol=proposal.get('symbol')
    if path not in task.get('local_worker_files',task['files']) or not isinstance(quote,str) or not 20<=len(quote)<=1200 or not isinstance(symbol,str):raise RecoveryHeld('Narrowing needs a writable path, named source anchor and actual evidence quote')
    source=Path(work)/path
    if not E.safe_file(source,work) or source.stat().st_size>1024*1024:raise RecoveryHeld('Narrowing source is unavailable')
    text=source.read_text();body=text;unit=None;unit_symbol=symbol
    if source.suffix=='.py':
        tree=ast.parse(text);found=[];lines=text.splitlines(keepends=True)
        def walk(nodes,prefix=''):
            for node in nodes:
                if isinstance(node,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                    name=prefix+node.name
                    segment=''.join(lines[node.lineno-1:node.end_lineno])
                    found.append((name,node,segment))
                    walk(node.body,name+'.')
        walk(tree.body)
        selected=[n for n in found if n[0]==symbol]
        if symbol!='__file__':
            if len(selected)!=1:raise RecoveryHeld('Named Python repair symbol does not exist exactly once')
            unit_symbol,unit,body=selected[0]
        else:unit=tree
        # A parent or __file__ spelling cannot turn the same quoted method into
        # a different intervention. Preserve indentation in verbatim quotations.
        contained=[n for n in found if quote in n[2] and (symbol=='__file__' or n[0]==symbol or n[0].startswith(symbol+'.'))]
        leaves=[n for n in contained if not any(other[0].startswith(n[0]+'.') for other in contained)]
        if len(leaves)>1:raise RecoveryHeld('Repair quote is ambiguous between source units; name the exact method')
        if leaves:unit_symbol,unit,_=leaves[0]
    elif symbol!='__file__' and symbol not in text:raise RecoveryHeld('Named repair anchor does not occur in the source')
    if quote not in body:raise RecoveryHeld('Repair evidence quote does not occur in the named current source')
    if unit is not None:
        unit=copy.deepcopy(unit)
        for node in ast.walk(unit):
            if isinstance(node,(ast.Module,ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                if not isinstance(node,ast.Module):node.name='__repair_unit__'
                if node.body and isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant) and isinstance(node.body[0].value.value,str):node.body=node.body[1:]
        syntax=ast.dump(unit,include_attributes=False)
    else:
        # Without a language parser, conservatively admit at most one anchored
        # file unit in this scope/guidance generation. No quote, symbol or edit
        # can manufacture a new syntactic unit. Other verified proof kinds stay.
        syntax='non-python-file-unit-v1'
    unit_id=fingerprint({'path':path,'syntax':syntax})
    return {'path':path,'symbol':symbol,'unit_symbol':unit_symbol,'unit_id':unit_id,'evidence_quote':quote,'source':ref(source)}


def classify(directory):
 """Classify facts present in receipts; absent legacy details remain unknown."""
 import worker_quality as Q
 directory=Path(directory)
 def read(name,default=None):
  p=directory/name
  if not E.safe_file(p,directory):return {} if default is None else default
  if p.stat().st_size>1024*1024:return {} if default is None else default
  try:return json.loads(p.read_text())
  except (ValueError,OSError):return {} if default is None else default
 process=read('worker-process.json');completion=read('worker-completion.json');outcome=read('outcome.json')
 process=process if isinstance(process,dict) else {};completion=completion if isinstance(completion,dict) else {};outcome=outcome if isinstance(outcome,dict) else {}
 checks=[]
 for name in ('checks.json','quality-checks/checks.json','micro-checks/checks.json'):
  value=read(name,[])
  if isinstance(value,list):checks.extend(c for c in value if isinstance(c,dict))
 review=read('micro-review/result.json') or read('review/result.json')
 review=review if isinstance(review,dict) else {}
 reason=process.get('reason');failed=[c for c in checks if c.get('code')!=0 or c.get('reason')!='exited']
 if E.safe_file(directory/'accepted.json',directory) or outcome.get('outcome')=='accepted':kind='complete'
 elif outcome.get('outcome')=='checkpoint':kind='checkpoint'
 elif outcome.get('outcome')=='pending_delivery':kind='delivery'
 elif reason in {'paused_or_shutdown','09:00_cutoff','stopped_before_start','resource_unavailable'} or outcome.get('outcome')=='interrupted':kind='interrupted'
 elif failed:kind='runtime' if any(c.get('reason')!='exited' for c in failed) else 'semantic'
 elif review.get('decision')=='revise' and review.get('issues'):kind='review'
 elif process and completion.get('finished') is not True:kind='unfinished_handoff'
 elif completion.get('finished') is True:kind='verification'
 else:kind='unknown'
 worker_failure=Q.worker_failure(directory)
 # A host cutoff may arrive after a terminal worker defect. It cannot hide
 # that defect and turn ordinary narrowing into permission to retry it.
 clean_control=bool(worker_failure and worker_failure.get('error_class') in {'InterruptedError','TimeoutError'}
   and worker_failure.get('completion_reason')=='stream_error')
 terminal_failure=bool(worker_failure and (kind!='interrupted' or worker_failure.get('receipt') and not clean_control))
 if kind not in {'complete','checkpoint','delivery'} and completion.get('finished') is not True and terminal_failure:
  kind='inference_truncated' if worker_failure['completion_reason']=='length' else 'worker_contract'
 inputs=[directory/n for n in ('accepted.json','progress-scope.json','finding-bindings.json','saved-micro-verification.json','worker-process.json','worker-model.json','worker-completion.json','outcome.json','checks.json','quality-checks/checks.json','micro-checks/checks.json','micro-review/result.json','review/result.json')]
 inputs.extend(path for path,_ in Q.worker_inference_receipts(directory))
 if worker_failure and worker_failure.get('client_result'):inputs.append(directory/'worker-events.jsonl')
 inputs.append(directory/'reservation-provenance.json')
 actual=[ref(p) for p in inputs if E.safe_file(p,directory)]
 # Error messages and line numbers are display evidence, never a new permission.
 recorded_components=completion.get('recovery_components')
 provenance=read('reservation-provenance.json')
 recovered=recorded_components is None and isinstance(provenance,dict) and provenance.get('origin')=='consumed_host_proof'
 if recovered:recorded_components=provenance.get('components')
 model=read('worker-model.json') or (worker_failure or {}).get('model')
 signature={'kind':kind,'reason':reason,'failed_commands':[c.get('command') for c in failed]}
 if kind in HARNESS_FAILURES:
  policy=(worker_failure or {}).get('inference_policy')
  signature={'version':2,'failure_class':kind,'phase':(worker_failure or {}).get('phase'),
    'worker_contract':completion.get('contract') or (worker_failure or {}).get('worker_contract'),
    'model':model,'inference_policy':{k:policy.get(k) for k in ('version','thinking_capable')} if isinstance(policy,dict) else None,
    'requested_budget':(worker_failure or {}).get('requested_budget'),'components':recorded_components}
 return {'kind':kind,'fingerprint':fingerprint(signature),'reason':reason,'refs':actual,'observed_finished':completion.get('finished') is True,'worker_failure':worker_failure,
     'worker_contract':completion.get('contract'),'components':recorded_components,'model':model,'failure_identity':signature if kind in HARNESS_FAILURES else None,
     'failed_checks':len(failed),'review_issues':review.get('issues',[])[:8] if isinstance(review.get('issues',[]),list) else [],
     **({'source_provenance':'consumed_host_proof'} if recovered else {})}


def harness_certificate_matches(certificate,failure):
 """A source/model-bound worker repair, never a change of advice or run id."""
 if not isinstance(certificate,dict) or not isinstance(failure,dict):return False
 kinds=certificate.get('failure_kinds')
 if not isinstance(kinds,list) or not kinds or not all(isinstance(kind,str) for kind in kinds):return False
 if not isinstance(failure.get('model'),dict) or not failure['model'].get('model'):return False
 return (certificate.get('kind')=='verified_harness_fix' and failure.get('kind') in certificate.get('failure_kinds',[])
   and isinstance(failure.get('components'),dict) and bool(failure['components'])
   and certificate.get('from_components')==failure['components'] and certificate.get('model')==failure.get('model')
   and (not certificate.get('from_contract') or certificate['from_contract']==failure.get('worker_contract')))

def model_never_answered(failure):
 """The model server timed out before producing anything: a fault of the machine's state, not of the harness sources."""
 worker=failure.get('worker_failure') if isinstance(failure,dict) else None
 metrics=worker.get('metrics') if isinstance(worker,dict) else None
 return bool(isinstance(metrics,dict) and worker.get('error_class')=='TimeoutError' and worker.get('completion_reason')=='stream_error'
   and not any(metrics.get(k) for k in ('prompt_eval_count','eval_count','thinking_bytes','final_text_bytes')))

def owner_release_matches(evidence,failure,proof_components):
 """The owner's explicit release of this exact recorded harness failure: after its sources changed, or for the one run in which the model never answered."""
 release=evidence.get('owner_release') if isinstance(evidence,dict) else None
 old=failure.get('components') if isinstance(failure,dict) else None
 if not (isinstance(release,dict) and isinstance(old,dict) and old and failure.get('kind') in HARNESS_FAILURES
   and release.get('failure_fingerprint')==failure.get('fingerprint') and release.get('from_components')==old):return False
 if release.get('model_never_answered'):return bool(model_never_answered(failure) and failure.get('run') and release.get('run')==failure['run'])
 return proof_components!=old

class Recovery:
 def __init__(self,runtime,project,*,clock=time.time,evidence_roots=()):
  self.runtime=Path(runtime).resolve();self.store=HistoryStore(self.runtime);self.project=project;self.clock=clock
  self.roots=[self.runtime,*[Path(p).resolve() for p in evidence_roots]]

 def exists(self,con):return bool(con and con.execute("SELECT 1 FROM sqlite_master WHERE name='pr_scopes'").fetchone())
 def schema(self,con):
  for statement in SCHEMA:con.execute(statement)
 def _scope(self,con,identity):
  row=con.execute('SELECT * FROM pr_scopes WHERE id=? AND project=?',(identity,self.project)).fetchone() if self.exists(con) else None
  return dict(row) if row else None
 def enrolled(self,identity):
  with self.store.reader() as con:return bool(self._scope(con,identity['id']))

 def enroll(self,identity,task,model,*,legacy_runs=(),legacy_tries=0,checkpoint=None):
  import worker_quality as Q
  if identity!=scope(self.project,identity['episode'],task,identity['head'],identity['profile'],identity['stage']):raise RecoveryHeld('Recovery contract changed')
  payload={'task_id':task['id'],'task_sha256':Q.task_fingerprint(task),'retry_of':task.get('retry_of'),'obligations':task.get('acceptance',[]),'legacy_runs':sorted(set(legacy_runs)),
      'legacy_tries':legacy_tries,'checkpoint':checkpoint,'migration':'Supersedes numerical local dispatch ceilings only; every original charge, scope and final gate remains.'}
  with self.store.transaction() as con:
   self.schema(con);old=self._scope(con,identity['id'])
   if old:
    saved=json.loads(old['payload']);self._fresh_initial(con,identity,model,saved);return saved
   prior=[r[0] for r in con.execute('SELECT l.run FROM pr_launches l JOIN pr_scopes s ON s.id=l.scope WHERE s.project=? AND s.episode=? AND s.task_hash=? AND s.stage=?',(self.project,identity['episode'],identity['task_hash'],identity['stage']))]
   payload['legacy_runs']=sorted(set(payload['legacy_runs'])|set(prior));payload['legacy_tries']=max(payload['legacy_tries'],len(prior))
   guidance=self._guidance(con,identity);payload['baseline_guidance_id']=guidance['guidance_id'] if guidance else None
   if con.execute('SELECT 1 FROM pr_scopes WHERE project=? AND episode=? AND task_hash!=?',(self.project,identity['episode'],identity['task_hash'])).fetchone():raise RecoveryHeld('Task renaming cannot create another recovery allowance')
   con.execute('INSERT INTO pr_scopes VALUES(?,?,?,?,?,?,?,?,?)',(identity['id'],self.project,identity['episode'],identity['task_hash'],identity['stage'],identity['head'],identity['profile'],encoded(payload),self.clock()))
   self.store.append('progress_recovery_enrolled',self.project,{'scope':identity,'migration':payload},event_id='progress-enroll:'+identity['id'],episode_id=identity['episode'],task_id=task['id'],connection=con)
   self._fresh_initial(con,identity,model,payload)
  return payload

 def _fresh_initial(self,con,identity,model,payload):
  if payload['legacy_runs'] or payload['legacy_tries']:return
  prior=con.execute('SELECT 1 FROM pr_launches l JOIN pr_scopes s ON s.id=l.scope WHERE s.project=? AND s.episode=? AND s.task_hash=? AND s.stage=? LIMIT 1',(self.project,identity['episode'],identity['task_hash'],identity['stage'])).fetchone()
  if not prior:self._offer(con,identity,'initial','fresh-work-'+fingerprint({'components':components(),'model':model}),model,{'refs':[],'instruction':'Implement the admitted work unit under the complete original contract.'})

 def _offer(self,con,identity,kind,key,model,evidence):
  if not self._scope(con,identity['id']):raise RecoveryHeld('Recovery scope is not enrolled')
  if kind not in KINDS or not key or len(key)>180 or not isinstance(model,dict) or not model.get('model'):raise RecoveryHeld('Invalid evidence admission')
  value={'scope':identity,'kind':kind,'semantic_key':key,'model':model,'components':components(),'candidate':self._candidate(con,identity),'evidence':evidence}
  diagnosis=con.execute('SELECT payload FROM pr_diagnoses WHERE scope=?',(identity['id'],)).fetchone()
  if diagnosis:
   observed=json.loads(diagnosis[0]);value['failure']={k:observed.get(k) for k in ('run','kind','fingerprint','refs')}
  # The semantic key excludes proposal wording, timestamps and candidate edits.
  old=con.execute('SELECT id,payload FROM pr_proofs WHERE scope=? AND kind=? AND semantic_key=?',(identity['id'],kind,key)).fetchone()
  if old:return old['id']
  identity_hash=fingerprint(value)
  con.execute('INSERT INTO pr_proofs VALUES(?,?,?,?,?,?)',(identity_hash,identity['id'],kind,key,encoded(value),self.clock()))
  self.store.append('progress_intervention_qualified',self.project,{'scope':identity['id'],'proof':identity_hash,'kind':kind,'semantic_key':key},event_id='progress-proof:'+identity_hash,episode_id=identity['episode'],connection=con)
  return identity_hash

 def _candidate(self,con,identity,*,state=None):
  import durability as D
  saved=json.loads(self._scope(con,identity['id'])['payload'])
  if state is None:state=D.read_json(self.runtime/'state.json',{})
  if not isinstance(state,dict):raise RecoveryHeld('Current candidate state is unavailable')
  current=state.get('retry_candidates',{}).get(saved['task_id'])
  if current and current.get('task_sha256')==identity['task_hash']:return current
  parent=saved.get('retry_of') or {};prior=state.get('retry_candidates',{}).get(parent.get('task_id'))
  return prior if prior and prior.get('task_sha256')==parent.get('task_sha256') else None

 def latest_launch(self,identity):
  with self.store.reader() as con:
   if not self.exists(con):return None
   row=con.execute('SELECT run FROM pr_launches WHERE scope=? ORDER BY created DESC,rowid DESC LIMIT 1',(identity['id'],)).fetchone()
   return row[0] if row else None

 def _verified_anchor(self,anchor):
  if not isinstance(anchor,dict) or not isinstance(anchor.get('path'),str):raise RecoveryHeld('Invalid historical source anchor')
  path=Path(anchor['path'])
  if path.is_absolute() or '..' in path.parts or not path.parts:raise RecoveryHeld('Invalid historical source path')
  verify_refs([anchor.get('source')],self.roots)
  if anchor.get('kind')=='new_file':
   text=Path(anchor['source']['path']).read_text(errors='replace')
   if not isinstance(anchor.get('evidence_quote'),str) or anchor['evidence_quote'] not in text:raise RecoveryHeld('New-file evidence quote is not in its pinned check log')
   if anchor.get('unit_id')!=fingerprint({'path':anchor['path'],'syntax':NEW_FILE_SYNTAX}):raise RecoveryHeld('New-file work unit does not match its path')
   return {k:anchor[k] for k in ('path','symbol','unit_symbol','kind','unit_id','evidence_quote','source')}
  source=Path(anchor['source']['path'])
  root=source.parents[len(path.parts)-1]
  verified=source_anchor(root,{'files':[anchor['path']]},anchor)
  if verified['source']!=anchor['source']:raise RecoveryHeld('Historical source path does not match anchor')
  if anchor.get('unit_id') and anchor['unit_id']!=verified['unit_id']:raise RecoveryHeld('Source work unit does not match pinned syntax')
  return verified

 def _narrowing_history(self,con,identity,guidance_id):
  if not self.exists(con):return []
  rows=con.execute("SELECT p.id,p.payload,EXISTS(SELECT 1 FROM pr_launches l WHERE l.proof=p.id) AS charged FROM pr_proofs p WHERE p.scope=? AND p.kind='reviewed_narrowing' ORDER BY p.created,p.id",(identity['id'],))
  result=[]
  for row in rows:
   evidence=json.loads(row['payload'])['evidence']
   if evidence.get('guidance_id')!=guidance_id:continue
   item={'proof':row['id'],'charged':bool(row['charged']),**{k:evidence.get(k) for k in ('obligation','instruction','anchor')}}
   anchor=item['anchor']
   if anchor is not None and not anchor.get('unit_id'):
    try:item['anchor']={**anchor,**self._verified_anchor(anchor),'legacy_projection':True}
    except (ValueError,OSError,KeyError,TypeError,IndexError,RecursionError):item['anchor_unavailable']=True
   result.append(item)
  return result

 def narrowing_history(self,identity,guidance_id=None):
  """Host facts for diagnosis; legacy broad proofs remain spent, not ceilings."""
  with self.store.reader() as con:return self._narrowing_history(con,identity,guidance_id)

 def offer_narrowing(self,identity,model,*,obligation, instruction,refs,review,guidance_id=None,anchor=None,coordinator_advice_id=None):
  if type(obligation) is not int or obligation<0 or not isinstance(instruction,str) or not instruction.strip() or len(instruction)>2000 or review.get('decision')!='accept' or review.get('issues'):raise RecoveryHeld('Narrowing needs one original obligation and separate independent acceptance')
  verify_refs(refs,self.roots)
  backed=False
  for item in refs:
   try:backed=backed or json.loads(Path(item['path']).read_text())==review
   except (ValueError,OSError):pass
  if not backed:raise RecoveryHeld('Independent narrowing review must match its saved result')
  if anchor is not None and (not isinstance(anchor,dict) or not re.fullmatch('[a-f0-9]{64}',str(anchor.get('unit_id','')))):raise RecoveryHeld('Narrowing needs a host-verified source work unit')
  if coordinator_advice_id and anchor is None:raise RecoveryHeld('Coordinator advice requires independently reviewed source-anchored narrowing')
  if anchor is not None:self._verified_anchor(anchor)
  evidence={'obligation':obligation,'instruction':instruction,'refs':refs,'review':review,'guidance_id':guidance_id,'anchor':anchor,'coordinator_advice_id':coordinator_advice_id}
  with self.store.transaction() as con:
   row=self._scope(con,identity['id'])
   if not row:raise RecoveryHeld('Unknown recovery scope')
   if obligation>=len(json.loads(row['payload'])['obligations']):raise RecoveryHeld('Narrowing must reference an original acceptance obligation')
   current=self._guidance(con,identity)
   if guidance_id and (not current or current['guidance_id']!=guidance_id):raise RecoveryHeld('Guided work needs the actual committed Codex guidance')
   if coordinator_advice_id and (self._coordinator_advice(con,identity,model) or {}).get('decision_id')!=coordinator_advice_id:raise RecoveryHeld('Coordinator advice changed or was withdrawn before independent admission')
   if anchor is not None:
    for previous in self._narrowing_history(con,identity,guidance_id):
     old=previous.get('anchor') or {}
     if previous.get('anchor_unavailable') and old.get('path')==anchor['path']:raise RecoveryHeld('Recover the pinned historical source before narrowing this file again')
     if old.get('unit_id')==anchor['unit_id']:return previous['proof']
   # The requirement index is traceability, not a per-requirement retry ceiling.
   # Changing its index, the quote, symbol spelling or prose cannot renew a unit.
   key='unit-'+anchor['unit_id'] if anchor is not None else 'obligation-'+str(obligation)
   return self._offer(con,identity,'reviewed_narrowing',('guidance-'+guidance_id+'-' if guidance_id else '')+key,model,evidence)

 def _guidance(self,con,identity):
  if not con.execute("SELECT 1 FROM sqlite_master WHERE name='cr_episodes'").fetchone():return None
  row=con.execute('SELECT guidance_id,guidance FROM cr_episodes WHERE project=? AND episode=?',(self.project,identity['episode'])).fetchone()
  return {'guidance_id':row[0],'guidance':json.loads(row[1])} if row and row[0] and row[1] else None

 def offer_guidance(self,identity,model,refs):
  verify_refs(refs,self.roots)
  with self.store.transaction() as con:
   row=self._scope(con,identity['id']);current=self._guidance(con,identity)
   if not row or not current:return None
   if current['guidance_id']==json.loads(row['payload']).get('baseline_guidance_id'):return None
   backed=False
   for item in refs:
    try:backed=backed or json.loads(Path(item['path']).read_text())==current['guidance']
    except (ValueError,OSError):pass
   if not backed:raise RecoveryHeld('Codex guidance needs its exact saved validated result')
   return self._offer(con,identity,'codex_guidance',current['guidance_id'],model,{'refs':refs,'guidance_id':current['guidance_id'],
      'instruction':'Apply the committed same-issue Codex guidance without changing the task or final gates:\n'+'\n'.join(current['guidance']['steps'])})

 def offer_coordinator(self,identity,model,decision_id,instruction,refs):
  """Retain advisory input for the existing independent narrowing review path."""
  if not re.fullmatch('[a-f0-9]{16,64}',str(decision_id)) or not isinstance(instruction,str) or not instruction.strip() or len(instruction)>4000:
   raise RecoveryHeld('Coordinator guidance needs a decision id and a bounded instruction')
  verify_refs(refs,self.roots)
  with self.store.transaction() as con:
   self.schema(con);scope_=self._scope(con,identity['id'])
   if not scope_ or any(scope_.get(k)!=v for k,v in identity.items()):raise RecoveryHeld('Coordinator advice scope changed')
   value={'decision_id':decision_id,'scope':identity,'model':model,'components':components(),'refs':refs,
     'instruction':instruction.strip(),'admission':'independent_review_required'}
   old=con.execute('SELECT payload FROM pr_coordinator_advice WHERE id=?',(decision_id,)).fetchone()
   if old:
    if json.loads(old[0])!=value:raise RecoveryHeld('Coordinator decision identity already has different advice')
    return value
   con.execute("INSERT INTO pr_coordinator_advice VALUES(?,?,'active',?,?)",(decision_id,identity['id'],encoded(value),self.clock()))
   diagnosis=con.execute('SELECT kind,state FROM pr_diagnoses WHERE scope=?',(identity['id'],)).fetchone()
   if diagnosis and diagnosis['kind'] in {'semantic','review','unknown'} and diagnosis['state'] in {'needs_codex','diagnosing'}:
    con.execute("UPDATE pr_diagnoses SET state='diagnosing',updated=? WHERE scope=?",(self.clock(),identity['id']))
   self.store.append('progress_coordinator_advice',self.project,value,event_id='progress-advice:'+decision_id,episode_id=identity['episode'],connection=con)
   return value

 def _coordinator_advice(self,con,identity,model=None):
  if not con or not con.execute("SELECT 1 FROM sqlite_master WHERE name='pr_coordinator_advice'").fetchone():return None
  rows=con.execute("SELECT payload FROM pr_coordinator_advice WHERE scope=? AND state='active' ORDER BY created DESC,id DESC",(identity['id'],))
  for row in rows:
   value=json.loads(row[0])
   if self._coordinator_withdrawn(value['decision_id']):continue
   if value['scope']!=identity or value['components']!=components() or model is not None and value['model']!=model:continue
   try:verify_refs(value['refs'],self.roots)
   except (OSError,ValueError):continue
   return value
  return None

 def _coordinator_withdrawn(self,decision_id):
  path=self.runtime/'coordinator-withdrawals.json'
  if not path.exists() and not path.is_symlink():return False
  if not E.safe_file(path,self.runtime) or path.stat().st_size>8*1024*1024:return True
  try:value=json.loads(path.read_text())
  except (ValueError,OSError):return True
  return not isinstance(value,dict) or decision_id in value

 def coordinator_advice_record(self,decision_id):
  """Exact effect readback for crash reconciliation; never grants admission."""
  with self.store.reader() as con:
   if not con or not con.execute("SELECT 1 FROM sqlite_master WHERE name='pr_coordinator_advice'").fetchone():return None
   row=con.execute('SELECT a.state,a.payload FROM pr_coordinator_advice a JOIN pr_scopes s ON s.id=a.scope WHERE a.id=? AND s.project=?',(decision_id,self.project)).fetchone()
   if not row:return None
   value=json.loads(row['payload']);verify_refs(value['refs'],self.roots)
   return {'state':'withdrawn' if self._coordinator_withdrawn(decision_id) else row['state'],'payload':value}

 def coordinator_advice(self,identity,model=None):
  with self.store.reader() as con:return self._coordinator_advice(con,identity,model)

 def revoke_coordinator(self,decision_id):
  """Withdraw advice and hold legacy unconsumed proofs without erasing history."""
  with self.store.transaction() as con:
   if not self.exists(con):return 0
   self.schema(con)
   count=con.execute("UPDATE pr_coordinator_advice SET state='withdrawn' WHERE id=? AND state='active'",(str(decision_id),)).rowcount
   rows=con.execute("SELECT id,payload FROM pr_proofs WHERE kind='coordinator_guidance' AND semantic_key=? AND NOT EXISTS(SELECT 1 FROM pr_launches l WHERE l.proof=pr_proofs.id)",('coordinator-'+str(decision_id),)).fetchall()
   for row in rows:
    count+=con.execute('INSERT OR IGNORE INTO pr_proof_holds VALUES(?,?,?,?)',(row['id'],'owner_overrule',row['payload'],self.clock())).rowcount
   return count

 def _coordinator_proof_inventory(self,con):
  result={'available':self.exists(con),'project':self.project,'total':0,'consumed':0,'unconsumed':0,'held':0,'proofs':[]}
  if not result['available']:return result
  holds=bool(con.execute("SELECT 1 FROM sqlite_master WHERE name='pr_proof_holds'").fetchone())
  rows=con.execute("SELECT p.id,p.scope,p.semantic_key,p.payload,l.run FROM pr_proofs p JOIN pr_scopes s ON s.id=p.scope LEFT JOIN pr_launches l ON l.proof=p.id WHERE s.project=? AND p.kind='coordinator_guidance' ORDER BY p.id",(self.project,))
  for row in rows:
   hold=con.execute('SELECT reason FROM pr_proof_holds WHERE proof=?',(row['id'],)).fetchone() if holds else None
   result['proofs'].append({'id':row['id'],'scope':row['scope'],'semantic_key':row['semantic_key'],
      'payload_sha256':hashlib.sha256(row['payload'].encode()).hexdigest(),'consumed':row['run'] is not None,
      'run':row['run'],'hold_reason':hold[0] if hold else None})
  result.update(total=len(result['proofs']),consumed=sum(p['consumed'] for p in result['proofs']),
     unconsumed=sum(not p['consumed'] for p in result['proofs']),held=sum(p['hold_reason'] is not None for p in result['proofs']))
  return result

 def coordinator_proof_inventory(self):
  """Read every legacy proof in this project without creating or migrating state."""
  with self.store.reader() as con:return self._coordinator_proof_inventory(con)

 def reconcile_coordinator_proofs(self):
  """Journal holds atomically; never edit proofs, launches, results or charges."""
  before=self.coordinator_proof_inventory()
  if not before['available'] or not before['unconsumed']:return {'new_holds':[],'inventory':before}
  added=[]
  with self.store.transaction() as con:
   self.schema(con)
   for proof in self._coordinator_proof_inventory(con)['proofs']:
    if proof['consumed'] or proof['hold_reason'] is not None:continue
    value={'schema_version':1,'policy':'coordinator-advice-requires-independent-admission-v1','proof':proof}
    con.execute('INSERT INTO pr_proof_holds VALUES(?,?,?,?)',(proof['id'],'legacy_coordinator_requires_review',encoded(value),self.clock()))
    self.store.append('progress_legacy_coordinator_held',self.project,value,event_id='progress-legacy-coordinator-hold:'+proof['id'],connection=con)
    added.append(proof['id'])
   inventory=self._coordinator_proof_inventory(con)
  return {'new_holds':added,'inventory':inventory}

 def offer_certificate(self,identity,model,certificate,*,motivating_refs=()):
  """Only host-registered, separately reviewed certificates reach this method."""
  kind=certificate['kind']
  if kind not in {'verified_harness_fix','verified_precondition_fix','verified_checkpoint'}:raise RecoveryHeld('Invalid repair certificate kind')
  if certificate.get('project') not in ('all',self.project) or certificate.get('task_hash') not in (None,identity['task_hash']):raise RecoveryHeld('Certificate belongs to different work')
  verify_refs(certificate['refs'],self.roots)
  if certificate.get('model')!=model or certificate.get('worker_sources')!={n:components()[n] for n in ('local_worker.py','structured_worker.py','ralphy/run.sh')}:raise RecoveryHeld('Certificate needs the exact qualified worker and model')
  validate_qualification(certificate)
  verify_refs([*certificate['refs'],*motivating_refs],self.roots)
  evidence={'certificate':certificate,'refs':[*certificate['refs'],*motivating_refs],'instruction':certificate['instruction']}
  with self.store.transaction() as con:
   row=con.execute('SELECT payload FROM pr_diagnoses WHERE scope=?',(identity['id'],)).fetchone()
   failure=json.loads(row[0]) if row else {}
   if failure.get('kind') in HARNESS_FAILURES and not harness_certificate_matches(certificate,failure):raise RecoveryHeld('Harness repair certificate does not match the recorded failure source and model')
   return self._offer(con,identity,kind,certificate['id'],model,evidence)

 def offer_owner_release(self,identity,reason):
  """Owner control, never a qualification: one retry of one recorded harness failure once its sources have changed."""
  if not isinstance(reason,str) or not 20<=len(reason.strip())<=500:raise RecoveryHeld('An owner release needs a stated reason of 20 to 500 characters')
  with self.store.transaction() as con:
   row=con.execute('SELECT state,payload FROM pr_diagnoses WHERE scope=?',(identity['id'],)).fetchone()
   failure=json.loads(row['payload']) if row else {}
   if not row or row['state']!='needs_harness_repair' or failure.get('kind') not in HARNESS_FAILURES:raise RecoveryHeld('Scope is not held for harness repair')
   old=failure.get('components')
   if not isinstance(old,dict) or not old:raise RecoveryHeld('Recorded failure has no recovery sources')
   unanswered=old==components() and model_never_answered(failure) and bool(failure.get('run'))
   if old==components() and not unanswered:raise RecoveryHeld('Recovery sources are unchanged since the recorded failure')
   if not isinstance(failure.get('model'),dict) or not failure['model'].get('model'):raise RecoveryHeld('Recorded failure has no exact worker model')
   release={'failure_fingerprint':failure['fingerprint'],'from_components':old,'reason':reason.strip(),'qualified':False}
   if unanswered:release.update(model_never_answered=True,run=failure['run'])
   evidence={'owner_release':release,
     'instruction':('The owner released this hold after the local model server failed to answer. ' if unanswered else 'The owner released this harness hold after the worker harness changed. ')
       +'Continue the original task; every original obligation and final gate remains.'}
   return self._offer(con,identity,'owner_harness_release',fingerprint({'failure':failure['fingerprint'],'components':components(),**({'run':failure['run']} if unanswered else {})}),failure['model'],evidence)

 def _admission(self,con,identity,model=None,*,observation=None):
  if not self._scope(con,identity['id']):return {'allowed':False,'state':'diagnosing','reason':'not_enrolled','proof':None}
  finished=con.execute("SELECT payload FROM pr_diagnoses WHERE scope=? AND state='complete'",(identity['id'],)).fetchone()
  if finished:return {'allowed':False,'state':'complete','reason':'verified_scope_complete','proof':None,'diagnosis':json.loads(finished[0])}
  current_components=observation['components'] if observation is not None else components()
  diagnosis=con.execute('SELECT state,payload FROM pr_diagnoses WHERE scope=?',(identity['id'],)).fetchone()
  diagnosed_failure=json.loads(diagnosis['payload']) if diagnosis else {}
  harness=diagnosed_failure.get('kind') in HARNESS_FAILURES
  holds=bool(con.execute("SELECT 1 FROM sqlite_master WHERE name='pr_proof_holds'").fetchone())
  rows=con.execute('SELECT p.* FROM pr_proofs p WHERE p.scope=? AND NOT EXISTS(SELECT 1 FROM pr_launches l WHERE l.proof=p.id) ORDER BY p.created,p.id',(identity['id'],))
  stale=False
  for row in rows:
   value=json.loads(row['payload'])
   if row['kind']=='coordinator_guidance':continue
   if holds and con.execute('SELECT 1 FROM pr_proof_holds WHERE proof=?',(row['id'],)).fetchone():continue
   owner=row['kind']=='owner_harness_release'
   if owner and not owner_release_matches(value['evidence'],diagnosed_failure,value['components']):continue
   if harness and not owner and not harness_certificate_matches(value['evidence'].get('certificate',{}),diagnosed_failure):continue
   if row['kind']=='initial' and con.execute('SELECT 1 FROM pr_launches l JOIN pr_scopes s ON s.id=l.scope WHERE s.project=? AND s.episode=? AND s.task_hash=? AND s.stage=? LIMIT 1',(self.project,identity['episode'],identity['task_hash'],identity['stage'])).fetchone():continue
   if value['scope']!=identity or value['components']!=current_components or model is not None and value['model']!=model:stale=True;continue
   try:
    if value.get('candidate')!=self._candidate(con,identity,state=observation['state'] if observation is not None else None):stale=True;continue
    failure=value.get('failure')
    if failure and failure.get('run'):
     actual=classify(self.runtime/'runs'/failure['run'])
     if any(actual[k]!=failure[k] for k in ('kind','fingerprint','refs')):stale=True;continue
    guidance=value['evidence'].get('guidance_id')
    if guidance and (self._guidance(con,identity) or {}).get('guidance_id')!=guidance:stale=True;continue
    advice=value['evidence'].get('coordinator_advice_id')
    if advice and (self._coordinator_advice(con,identity,model) or {}).get('decision_id')!=advice:stale=True;continue
    if value['evidence'].get('refs'):verify_refs(value['evidence']['refs'],self.roots)
    if value['evidence'].get('certificate'):validate_qualification(value['evidence']['certificate'])
   except (OSError,ValueError,KeyError,TypeError):stale=True;continue
   return {'allowed':True,'state':'ready','reason':row['kind'],'proof':row['id'],'intervention':value['evidence'],'model':value['model']}
  if harness:return {'allowed':False,'state':'needs_harness_repair','reason':'verified_harness_repair_required','proof':None,'diagnosis':diagnosed_failure}
  return {'allowed':False,'state':diagnosis['state'] if diagnosis else 'diagnosing','reason':'stale_evidence' if stale else 'new_evidence_required','proof':None,'diagnosis':json.loads(diagnosis['payload']) if diagnosis else None}

 def admission(self,identity,model=None):
  with self.store.reader() as con:return self._admission(con,identity,model)

 def reserve(self,identity,run,model,*,connection=None):
  if not re.fullmatch(r'[A-Za-z0-9_-]{1,180}',run):raise RecoveryHeld('Invalid launch identity')
  def reserve(con):
   old=con.execute('SELECT * FROM pr_launches WHERE run=?',(run,)).fetchone()
   if old:
    payload=json.loads(old['payload'])
    if old['scope']!=identity['id'] or payload['model']!=model:raise RecoveryHeld('Existing launch binding changed')
    return payload
   decision=self._admission(con,identity,model)
   if not decision['allowed']:raise RecoveryHeld('Local recovery needs new verified evidence: '+decision['reason'])
   value={'scope':identity,'run':run,'proof':decision['proof'],'kind':decision['reason'],'model':model,'charged':True,'instruction':decision['intervention'].get('instruction')}
   con.execute('INSERT INTO pr_launches VALUES(?,?,?,?,?)',(run,identity['id'],decision['proof'],encoded(value),self.clock()))
   self.store.append('progress_launch_reserved',self.project,value,event_id='progress-launch:'+run,episode_id=identity['episode'],connection=con)
   return value
  if connection is not None:return reserve(connection)
  with self.store.transaction() as con:return reserve(con)

 def reservation_provenance(self,con,identity,run,directory):
  """Recover launch-time source facts from the consumed host proof, never from chat."""
  directory=Path(directory).resolve();model_path=directory/'worker-model.json'
  if directory.name!=run or not E.safe_file(model_path,self.runtime):return None
  row=con.execute('SELECT l.payload AS launch,p.id AS proof_id,p.payload AS proof FROM pr_launches l JOIN pr_proofs p ON p.id=l.proof WHERE l.run=? AND l.scope=?',(run,identity['id'])).fetchone()
  if not row:return None
  launch=json.loads(row['launch']);proof=json.loads(row['proof']);model=json.loads(model_path.read_text())
  recorded=proof.get('components')
  if (fingerprint(proof)!=row['proof_id'] or launch.get('proof')!=row['proof_id'] or launch.get('scope')!=identity
      or proof.get('scope')!=identity or launch.get('run')!=run or launch.get('model')!=model or proof.get('model')!=model
      or not isinstance(recorded,dict) or set(recorded)!=set(components())
      or any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v) for v in recorded.values())):
   raise RecoveryHeld('Launch provenance does not match its immutable host proof and model')
  value={'origin':'consumed_host_proof','run':run,'scope':identity,'proof':row['proof_id'],'model':model,'components':recorded}
  path=directory/'reservation-provenance.json';data=(encoded(value)+'\n').encode()
  if path.exists():
   if not E.safe_file(path,self.runtime) or path.read_bytes()!=data:raise RecoveryHeld('Saved launch provenance changed')
  else:
   import durability as D
   D.atomic_write(path,data)
  return {'components':recorded,'ref':ref(path)}

 def observe(self,identity,run,directory,*,state=None):
  diagnosis=classify(directory);value={'scope':identity,'run':run,**diagnosis}
  phase='needs_harness_repair' if diagnosis['kind'] in HARNESS_FAILURES else state or ('complete' if diagnosis['kind'] in {'complete','checkpoint'} else 'verifying' if diagnosis['kind'] in {'verification','delivery'} else 'waiting_for_controls' if diagnosis['kind']=='interrupted' else 'diagnosing')
  with self.store.transaction() as con:
   if not self._scope(con,identity['id']):raise RecoveryHeld('Unknown recovery scope')
   if diagnosis.get('components') is None or diagnosis.get('source_provenance')=='consumed_host_proof':
    provenance=self.reservation_provenance(con,identity,run,directory)
    if provenance:
     diagnosis=classify(directory);value.update(diagnosis)
    elif diagnosis.get('source_provenance')=='consumed_host_proof':raise RecoveryHeld('Saved launch provenance has no matching consumed host proof')
   old=con.execute('SELECT payload FROM pr_results WHERE run=?',(run,)).fetchone()
   if old:
    # A completed saved phase may gain verified receipts later; preserve both.
    if json.loads(old[0])==value:return value
    self.store.append('progress_result_reconciled',self.project,value,event_id='progress-result:'+fingerprint(value),episode_id=identity['episode'],connection=con)
   else:self.store.append('progress_run_observed',self.project,value,event_id='progress-result:'+fingerprint(value),episode_id=identity['episode'],connection=con)
   con.execute('INSERT INTO pr_results VALUES(?,?,?,?) ON CONFLICT(run) DO UPDATE SET payload=excluded.payload',(run,identity['id'],encoded(value),self.clock()))
   con.execute('INSERT INTO pr_diagnoses VALUES(?,?,?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET run=excluded.run,kind=excluded.kind,fingerprint=excluded.fingerprint,state=excluded.state,payload=excluded.payload,updated=excluded.updated',(identity['id'],run,diagnosis['kind'],diagnosis['fingerprint'],phase,encoded(value),self.clock()))
  return value

 def observe_run(self,run,directory):
  with self.store.reader() as con:
   if not self.exists(con):return None
   row=con.execute('SELECT scope FROM pr_launches WHERE run=?',(run,)).fetchone()
   if not row:return None
   saved=self._scope(con,row['scope']);identity={k:saved[k] for k in ('id','project','episode','task_hash','stage','head','profile')}
  return self.observe(identity,run,directory)

 def wait(self,identity,state,reason):
  if state not in {'needs_codex','needs_harness_repair','waiting_for_controls','diagnosing'}:raise RecoveryHeld('Invalid recovery wait')
  with self.store.transaction() as con:
   row=con.execute('SELECT payload FROM pr_diagnoses WHERE scope=?',(identity['id'],)).fetchone()
   value=json.loads(row[0]) if row else {'scope':identity,'kind':'unknown'};value['next_action']=reason
   con.execute('INSERT INTO pr_diagnoses VALUES(?,?,?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET state=excluded.state,payload=excluded.payload,updated=excluded.updated',(identity['id'],value.get('run'),value['kind'],value.get('fingerprint',fingerprint(value)),state,encoded(value),self.clock()))

 def snapshot(self):
  import durability as D
  with self.store.reader() as con:
   if not self.exists(con):return {'available':True,'version':VERSION,'scopes':[],'local_counts_are_permission':False}
   # This snapshot is observational. Actual offer/reserve calls independently
   # reread current state and evidence; no snapshot can authorize a launch.
   observation={'state':D.read_json(self.runtime/'state.json',{}),'components':components()}
   scopes=[]
   for row in con.execute('SELECT * FROM pr_scopes WHERE project=? ORDER BY created DESC LIMIT 30',(self.project,)):
    identity={k:row[k] for k in ('id','project','episode','task_hash','stage','head','profile')}
    decision=self._admission(con,identity,observation=observation);diag=con.execute('SELECT payload,updated FROM pr_diagnoses WHERE scope=?',(row['id'],)).fetchone()
    saved=json.loads(row['payload'])
    scopes.append({'scope':identity,'task_id':saved['task_id'],'admission':decision,'diagnosis':json.loads(diag['payload']) if diag else None,'updated_at':diag['updated'] if diag else row['created'],
       'legacy_launches':saved['legacy_tries'],'legacy_runs':saved['legacy_runs'],'checkpoint':saved.get('checkpoint'),
       'historical_launches':con.execute('SELECT COUNT(*) FROM pr_launches WHERE scope=?',(row['id'],)).fetchone()[0]})
   return {'available':True,'version':VERSION,'scopes':scopes,'local_counts_are_permission':False}

def certificate_roots(coordinator_runtime):
 return [Path(coordinator_runtime),Path.home()/'Developer/operations/midnight']

def validate_qualification(certificate,*,require_installed=True):
 from worker_recovery import qualification
 if not isinstance(certificate,dict) or not isinstance(certificate.get('qualification'),dict) or not isinstance(certificate.get('model'),dict):raise RecoveryHeld('Repair qualification fields are unavailable')
 harness=certificate.get('kind')=='verified_harness_fix'
 if harness:
  old=certificate.get('from_components')
  if (not isinstance(old,dict) or not {'local_worker.py','structured_worker.py','ralphy/run.sh'}<=old.keys() or old==components()
      or any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v) for v in old.values())):raise RecoveryHeld('Harness certificate needs changed, recorded failure sources')
  if require_installed:
   if certificate.get('id')!=fingerprint({k:v for k,v in certificate.items() if k!='id'}):raise RecoveryHeld('Registered repair certificate changed')
   if (not isinstance(certificate.get('installed_source'),str) or not re.fullmatch('[a-f0-9]{40}',certificate['installed_source'])
       or not isinstance(certificate.get('coordinator_runtime'),str) or not Path(certificate['coordinator_runtime']).is_absolute()):raise RecoveryHeld('Harness repair needs an exact adopted generation')
 verified=qualification(certificate['qualification']['path'],certificate['model'])
 if verified!=certificate['qualification'] or certificate.get('worker_sources')!=verified['worker_sources']:raise RecoveryHeld('Repair qualification changed')
 if certificate.get('installed_source'):
  from action_store import Actions
  import git_delivery as G
  runtime=Path(certificate['coordinator_runtime']);health=Actions(runtime).meta('install_health') or {}
  if (health.get('state')!='verified' or health.get('installed')!=certificate['installed_source']
      or G.git(runtime.parent,'rev-parse','HEAD')!=certificate['installed_source']
      or not health.get('tree') or G.git(runtime.parent,'rev-parse','HEAD^{tree}')!=health['tree']):raise RecoveryHeld('Repair certificate is not the healthy installed generation')

def register_certificate(coordinator_runtime,certificate,*,maintenance=None):
 """Operator/Codex maintenance boundary, never available to a product worker."""
 if os.environ.get('MIDNIGHT_ACTION_ID'):
  from action_store import Actions
  import phase_runtime as P
  job=Actions(coordinator_runtime).get(os.environ['MIDNIGHT_ACTION_ID'])
  if not maintenance or not job or job['project']!='midnight' or job['state']!='running' or job['payload'].get('maintenance')!=maintenance or not P.alive(job.get('lease')):raise RecoveryHeld('Only reviewed Codex maintenance may register a harness certificate')
 if certificate.get('kind') not in {'verified_harness_fix','verified_precondition_fix','verified_checkpoint'} or certificate.get('review',{}).get('decision')!='accept' or certificate['review'].get('issues'):raise RecoveryHeld('A separate reviewed repair certificate is required')
 verify_refs(certificate['refs'],certificate_roots(coordinator_runtime))
 # A maintenance certificate is published before the 180-second health proof;
 # it remains unusable until that exact generation is verified.
 pending={k:v for k,v in certificate.items() if k not in {'installed_source','coordinator_runtime'}}
 validate_qualification(pending,require_installed=False)
 value=dict(certificate);value['id']=fingerprint(certificate)
 import durability as D
 path=Path(coordinator_runtime)/'progress-certificates'/(value['id']+'.json')
 D.atomic_write(path,(encoded(value)+'\n').encode());return value

def certificates(coordinator_runtime,kind,model):
 values=[];root=Path(coordinator_runtime)/'progress-certificates'
 for path in sorted(root.glob('*.json'))[:100]:
  if not E.safe_file(path,root):continue
  try:
   value=json.loads(path.read_text())
   if not isinstance(value,dict) or not isinstance(value.get('failure_kinds'),list):continue
   body={k:v for k,v in value.items() if k!='id'}
   if value['id']!=fingerprint(body) or kind not in value.get('failure_kinds',[]) or value['model']!=model:continue
   verify_refs(value['refs'],certificate_roots(coordinator_runtime));validate_qualification(value)
   if value.get('installed_source'):
    from action_store import Actions
    import git_delivery as G
    health=Actions(coordinator_runtime).meta('install_health') or {}
    if health.get('state')!='verified' or health.get('installed')!=value['installed_source'] or G.git(Path(coordinator_runtime).parent,'rev-parse','HEAD')!=value['installed_source']:continue
   values.append(value)
  except (ValueError,OSError,KeyError,TypeError):continue
 return values

def harness_request_status(root,request):
 """Read actual unresolved scope before any charged automatic maintenance."""
 import durability as D
 import git_delivery as G
 import active_state as AS
 import profile_manager as PM
 import quality_cycle as QC
 import worker_quality as Q
 root=Path(root).resolve()
 try:
  if not (root/'projects.json').is_file():return 'unavailable'
  identity=request['scope'];failure=request['failure'];registry=D.read_json(root/'projects.json',{})
  project=next((p for p in registry.get('projects',[]) if p['id']==identity['project'] and p.get('on_schedule')),None)
  if not project or G.git(root,'rev-parse','HEAD')!=request['source_head']:return 'superseded'
  runtime=Path(project['state_dir'])
  if not (runtime/'state.json').is_file():return 'unavailable'
  state=D.read_json(runtime/'state.json',{})
  if G.git(Path(project['path']),'rev-parse','HEAD')!=identity['head']:return 'superseded'
  ledger=Recovery(runtime,identity['project'],evidence_roots=[Path(project['path']),*certificate_roots(root/'runtime')])
  with ledger.store.reader() as con:
   saved=ledger._scope(con,identity['id'])
   if not saved:return 'unavailable'
   payload=json.loads(saved['payload']);task_id=payload['task_id']
   if task_id in state.get('done',[]) or AS.task_done(runtime,identity['project'],task_id):return 'superseded'
   latest=con.execute('SELECT state,payload FROM pr_diagnoses WHERE scope=?',(identity['id'],)).fetchone()
   if not latest:return 'unavailable'
   if latest['state']!='needs_harness_repair':return 'superseded'
   observed=json.loads(latest['payload'])
   if any(observed.get(k)!=failure.get(k) for k in ('run','kind','fingerprint','refs')):return 'superseded'
   if ledger._admission(con,identity)['allowed']:return 'superseded'
   launched=con.execute('SELECT run FROM pr_launches WHERE scope=? ORDER BY created DESC,rowid DESC LIMIT 1',(identity['id'],)).fetchone()
   if launched and launched[0]!=failure['run']:return 'superseded'
  # A newer completed verification or product result supersedes an old local
  # launch even before the backend has next refreshed its recovery projection.
  outcome=state.get('last_outcome',{})
  if outcome.get('task')==task_id and outcome.get('run')!=failure['run']:return 'superseded'
  active=state.get('active_attempt') or {}
  if active.get('task',{}).get('id')==task_id and active.get('run')!=failure['run']:return 'superseded'
  directory=runtime/'runs'/failure['run']
  if not (directory/'task.json').is_file():return 'unavailable'
  actual=classify(directory)
  if any(actual[k]!=failure[k] for k in ('kind','fingerprint','refs')):return 'superseded'
  task=D.read_json(directory/'task.json',{})
  if Q.task_fingerprint(task)!=identity['task_hash']:return 'superseded'
  config=D.read_json(Path(project['path'])/project['config'],{})
  static=config.get('quality_profile') or D.read_json(root/'quality_profiles'/(identity['project']+'.json'),{})
  if QC.profile_hash(PM.effective_profile(runtime,identity['project'],static))!=identity['profile']:return 'superseded'
  if identity['stage'].startswith('micro-'):
   import micro_recovery as M
   current=M.MicroRecovery(runtime,identity['project']).current(task,identity['episode'],identity['head'],identity['profile'])
   if identity['stage']!='micro-'+str(current['index']):return 'superseded'
  return 'current'
 except (ValueError,OSError,KeyError,TypeError,StopIteration,subprocess.SubprocessError):return 'unavailable'

def harness_request_current(root,request):
 return harness_request_status(root,request)=='current'

def publish_maintenance(root,identity):
 """Reconcile reviewed host records using the newly installed module bytes."""
 import durability as D
 import git_delivery as G
 from action_store import Actions
 from worker_recovery import qualification
 root=Path(root).resolve();runtime=root/'runtime'
 if not re.fullmatch(r'[A-Za-z0-9_-]{1,180}',identity):raise RecoveryHeld('Invalid maintenance identity')
 store=Actions(runtime);repair=store.repair(identity);retained_link=None;continuation=None
 if not repair and identity.startswith('continuation-'):
  from maintenance_recovery import session_for,reservation_link
  continuation=session_for(root,identity)
  repair=store.repair(continuation['payload'].get('repair','')) if continuation else None
  if continuation and continuation['payload'].get('retained_reservation'):
   retained_link=reservation_link(root,identity)
   if not retained_link or not repair or retained_link['repair']!=repair['id'] or continuation['payload'].get('action')!='verification':raise RecoveryHeld('Retained verification has no durable original-reservation relationship')
   source=Path(retained_link['source_directory'])
   if source.resolve()!=Path(repair['payload'].get('directory',runtime/'maintenance'/repair['id'])).resolve():raise RecoveryHeld('Original charged source directory changed')
   verify_refs(retained_link['payload']['refs'],[root])
   if ref(source/'candidate.patch')['sha256']!=retained_link['patch_sha256']:raise RecoveryHeld('Original charged candidate changed')
  elif not repair or repair['payload'].get('directory')!=str(runtime/'maintenance'/identity):raise RecoveryHeld('Continuation does not match its original charged maintenance launch')
 if not repair or repair['state'] not in ({'running','unknown','passed','failed'} if retained_link else {'running','unknown','passed'}):raise RecoveryHeld('No charged Codex maintenance record')
 directory=runtime/'maintenance'/identity
 verified=D.read_json(directory/'verified.json',{});installed=D.read_json(directory/'installation-complete.json',{})
 incident=D.read_json(directory/'incident.json',{})
 request=incident.get('payload',{}).get('progress_request')
 if not request or incident.get('code')!='worker_contract':return None
 if incident.get('reservation_incident',incident.get('id'))!=repair['incident'] or verified.get('incident_sha256')!=ref(directory/'incident.json')['sha256']:raise RecoveryHeld('Reviewed maintenance request changed')
 if retained_link:
  original=D.read_json(Path(retained_link['source_directory'])/'incident.json',{})
  if original.get('payload',{}).get('progress_request')!=request or ref(directory/'candidate.patch')['sha256']!=retained_link['patch_sha256']:raise RecoveryHeld('Retained repair no longer matches its original motivating request and candidate')
 delivery=D.read_json(directory/'delivery.json',{});review=D.read_json(directory/'review.json',{})
 patch=directory/'candidate.patch';regression=D.read_json(directory/'regression-completion.json',{})
 head=G.git(root,'rev-parse','HEAD')
 if (not installed or installed.get('commit')!=head or delivery.get('status')!='merged' or delivery.get('commit')!=head
     or installed.get('tree')!=G.git(root,'rev-parse','HEAD^{tree}') or installed.get('tree')!=verified.get('tree')
     or verified.get('patch_sha256')!=ref(patch)['sha256'] or verified.get('review')!=review
     or review.get('decision')!='accept' or review.get('issues')!=[]
     or regression.get('passed') is not False or not regression.get('failures')):raise RecoveryHeld('Installed maintenance is missing exact review/regression/delivery evidence')
 failure=request['failure'];old=failure.get('components')
 if not isinstance(old,dict) or old==components():raise RecoveryHeld('Maintenance did not change the diagnosed recovery components')
 q=qualification(verified['worker_qualification']['path'])
 if q!=verified['worker_qualification']:raise RecoveryHeld('Maintenance qualification changed')
 names=('verified.json','incident.json','review.json','independent-review-receipt.json','regression-completion.json','regression-on-base.log','candidate.patch','installation-complete.json','delivery.json')
 certificate={'kind':'verified_harness_fix','project':request['scope']['project'],'task_hash':request['scope']['task_hash'],
   'failure_kinds':[failure['kind']],'from_components':old,'model':q['model'],'worker_sources':q['worker_sources'],
   'qualification':q,'refs':[ref(directory/n) for n in names]+[ref(q['path'])], 'review':review,'installed_source':head,'coordinator_runtime':str(runtime),
   'instruction':'Apply the separately reviewed harness repair to the retained work unit. Preserve every original task obligation, frozen path, charge and final acceptance gate.'}
 return register_certificate(runtime,certificate,maintenance=identity)

def owner_release(root,project_id,task_id,reason):
 """Run by the owner (or an agent the owner directed) while the project is paused."""
 import durability as D
 root=Path(root).resolve();registry=D.read_json(root/'projects.json',{})
 project=next((p for p in registry.get('projects',[]) if p.get('id')==project_id),None)
 if not project or not task_id:raise RecoveryHeld('A registered project and task are required')
 runtime=Path(project['state_dir'])
 if not (runtime/'PAUSED').exists():raise RecoveryHeld('Pause the project before releasing a harness hold')
 ledger=Recovery(runtime,project_id,evidence_roots=[Path(project['path']),*certificate_roots(root/'runtime')])
 with ledger.store.reader() as con:
  rows=[r for r in con.execute('SELECT * FROM pr_scopes WHERE project=?',(project_id,)) if json.loads(r['payload']).get('task_id')==task_id] if ledger.exists(con) else []
  held=[{k:r[k] for k in ('id','project','episode','task_hash','stage','head','profile')} for r in rows
        if (con.execute('SELECT state FROM pr_diagnoses WHERE scope=?',(r['id'],)).fetchone() or {'state':None})['state']=='needs_harness_repair']
 if len(held)!=1:raise RecoveryHeld('Expected exactly one harness-repair hold for this task; found '+str(len(held)))
 proof=ledger.offer_owner_release(held[0],reason)
 with ledger.store.reader() as con:model=json.loads(con.execute('SELECT payload FROM pr_proofs WHERE id=?',(proof,)).fetchone()['payload'])['model']
 admission=ledger.admission(held[0],model)
 if not admission['allowed'] or admission['proof']!=proof:raise RecoveryHeld('Owner release recorded but not admitted: '+admission['reason'])
 return {'scope':held[0]['id'],'task':task_id,'proof':proof,'qualified':False,'admission':admission}

if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--publish-maintenance')
 parser.add_argument('--owner-release',metavar='PROJECT',help='Owner control: release one harness-repair hold after the harness changed');parser.add_argument('--task');parser.add_argument('--reason')
 args=parser.parse_args()
 if args.owner_release:print(json.dumps(owner_release(args.root,args.owner_release,args.task,args.reason),indent=2))
 elif args.publish_maintenance:print(json.dumps(publish_maintenance(args.root,args.publish_maintenance)))
 else:parser.error('--publish-maintenance or --owner-release is required')
