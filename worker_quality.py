"""Shared worker-quality controls for every Midnight project backend."""
import hashlib
import json
from pathlib import Path
import re
import durability as D
import evidence_context as E
from candidate_hygiene import quarantine_artifacts

API_VERSION = 4
BACKEND_PROTOCOL = 5
INSTRUCTION = """Give the local worker one observable behaviour per task: a named function or small edit, concrete valid/invalid examples, explicit allowed files and a focused test command. Prefer one implementation file and one test file. Split storage, validation, presentation and CLI wiring into dependency-linked increments. Keep the complete integration acceptance gate on the final increment. Repairs address one numbered defect at a time when independent. Do not create backup or temporary files outside the task's allowed scope. Use saved numbered reviewer findings as a repair checklist. Preserve scoped candidate code where valid. A retry must change the rejected candidate before another review. Compare actual changes and pass unresolved findings to independent review. Diagnose no-change exits and unfinished tool calls. Worker claims and checkpoints are not acceptance. Preserve each project's checks, isolation, deadlines, schedule and history. Use the host-supplied MIDNIGHT_LOCAL_CODE_BIN for every local worker invocation so all projects receive the selected qualified model; never choose or download a model from a product task."""

def policy():
    return {"id": "midnight-worker-quality", "version": API_VERSION, "instruction": INSTRUCTION,
            "local_admission":"verified_recovery_evidence_v1","harness_repairs":"codex_only",
            "progress_recovery_sha256":hashlib.sha256(Path(__file__).with_name('progress_recovery.py').read_bytes()).hexdigest(),
            "evidence_context_sha256":hashlib.sha256(Path(__file__).with_name('evidence_context.py').read_bytes()).hexdigest(),
            "sha256": hashlib.sha256(INSTRUCTION.encode()).hexdigest(),
            "candidate_hygiene_sha256":hashlib.sha256(Path(__file__).with_name('candidate_hygiene.py').read_bytes()).hexdigest(),
            "project_backend_sha256":hashlib.sha256(Path(__file__).with_name('project_backend.py').read_bytes()).hexdigest(),
            "local_supervisor_sha256":hashlib.sha256(Path(__file__).with_name('local_supervisor.py').read_bytes()).hexdigest(),
            "quality_monitor_sha256":hashlib.sha256(Path(__file__).with_name('quality_monitor.py').read_bytes()).hexdigest(),
            "micro_recovery_sha256":hashlib.sha256(Path(__file__).with_name('micro_recovery.py').read_bytes()).hexdigest(),
            "worker_recovery_sha256":hashlib.sha256(Path(__file__).with_name('worker_recovery.py').read_bytes()).hexdigest(),
            "agent_workspace_sha256":hashlib.sha256(Path(__file__).with_name('agent_workspace.py').read_bytes()).hexdigest(),
            "agent_discussion_sha256":hashlib.sha256(Path(__file__).with_name('agent_discussion.py').read_bytes()).hexdigest(),
            "module_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}

def model_contract():
    return {'version':4,'entry_env':'MIDNIGHT_LOCAL_CODE_BIN','receipt':'worker-model.json','completion_receipt':'worker-completion.json',
            'manifest_env':'MIDNIGHT_LOCAL_WORKER_MANIFEST','worker_contract':'typed-edits-chat-v3',
            'worker_sources':{name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
                              for name in ('local_worker.py','structured_worker.py','colibri_provider.py','ralphy/run.sh')}}

def read(path, default=None):
    try:return json.loads(Path(path).read_text())
    except FileNotFoundError:return {} if default is None else default

def write(path, value):
    D.atomic_write(path, (json.dumps(value, indent=2, allow_nan=False)+"\n").encode())

def task_fingerprint(task):
    return hashlib.sha256(json.dumps(task,sort_keys=True).encode()).hexdigest()

def source_candidate(task,state):
    """A coordinator may bind a narrow successor to one exact prior task."""
    # After its first attempt a successor owns newer work. Re-seeding its parent
    # every time silently discards that progress and repeats the original patch.
    current=state.get('retry_candidates',{}).get(task['id'])
    if current and current.get('task_sha256')==task_fingerprint(task):
        return current
    source=task.get('retry_of')
    identity=source['task_id'] if source else task['id']
    expected=source['task_sha256'] if source else task_fingerprint(task)
    receipt=state.get('retry_candidates',{}).get(identity)
    return receipt if receipt and receipt.get('task_sha256')==expected else None


def substantive_failure(directory, feedback, *, interrupted=False):
    """Classify only actual local implementation attempts from host receipts.

    A ready marker is a successful submission even when the host's deliberate
    process stop returns 130/-15. Verification of an older draft is not a new
    local attempt. Neither worker prose nor a clone alone proves inference.
    """
    directory=Path(directory)
    backend=read(directory/'implementation-backend.json')
    process=read(directory/'worker-process.json')
    if interrupted or backend.get('kind') in {'cloud','saved_candidate'} or not process:
        return None
    completion=read(directory/'worker-completion.json')
    submitted=process.get('reason')=='ready_for_review' or (process.get('reason')=='exited' and process.get('code')==0)
    checks=read(directory/'checks.json',[])+read(directory/'quality-checks/checks.json',[])
    review=read(directory/'review/result.json')
    if any(c.get('reason')=='exited' and c.get('code',0)!=0 for c in checks):
        return 'acceptance'
    if review.get('decision')=='revise' and review.get('issues'):
        return 'review'
    if submitted and any(marker in feedback for marker in ('Unchanged retry candidate','No product changes','Changed a protected','Unsupported candidate','Candidate patch is too large')):
        return 'worker_no_change' if 'Unchanged' in feedback or 'No product changes' in feedback else 'implementation'
    if process.get('reason') in {'task_timeout','worker_stalled'} and completion.get('response_observed') is True:
        return 'implementation'
    return None


def repair_preamble(directory):
    """Lead with the current candidate's host status, ahead of old task prose."""
    if read(Path(directory)/'retry-seed.json'):
        return ('The staged candidate is retained REJECTED work. It is not a completed fix. '
                'Address the unresolved findings below and change the candidate before resubmitting.')
    return 'Fresh task: implement the current task and verify it.'


def attempt_evidence(directory):
    """Small host receipts for a manager that cannot inspect local files."""
    folder=Path(directory);task=read(folder/'task.json');review=read(folder/'review/result.json')
    backend=read(folder/'implementation-backend.json');process=read(folder/'worker-process.json')
    completion=read(folder/'worker-completion.json')
    return {'task_id':task.get('id'),'task_sha256':task_fingerprint(task) if task else None,
            'backend':backend.get('kind'),'guidance_id':backend.get('guidance_id'),
            'process':{k:process.get(k) for k in ('reason','code','seconds')},
            'response_observed':completion.get('response_observed'),
            'review':{k:review.get(k) for k in ('decision','summary','issues')},
            'failed_checks':[{'reason':c.get('reason'),'code':c.get('code')}
                for c in read(folder/'checks.json',[]) if c.get('reason')=='exited' and c.get('code',0)!=0]}

def repair_plan(task, state, directory):
    """Retain source and successor reviews with complete provenance."""
    source_id=(task.get('retry_of') or {}).get('task_id',task['id'])
    identities=list(dict.fromkeys([source_id,task['id']]))
    reviews=[];items=[]
    for identity in identities:
        recorded=state.get('review_findings',{}).get(identity)
        if not recorded:continue
        review=recorded['review']
        if not isinstance(review,dict) or not isinstance(review.get('issues',[]),list) or any(not isinstance(x,str) for x in review.get('issues',[])):
            raise ValueError('Saved review findings require inspection')
        provenance={'task':identity,'run':recorded['run'],'review':review}
        reviews.append(provenance)
        for finding in review.get('issues',[]):
            items.append({'id':f'R{len(items)+1}','finding':finding,'source_task':identity,'source_run':recorded['run']})
    if not reviews:
        history=state.get('feedback_history',{}).get(task['id']) or state.get('feedback_history',{}).get(source_id,[])
        text=history[-1]['summary'] if history else state.get('feedback',{}).get(task['id'],state.get('feedback',{}).get(source_id,''))
        try:feedback=json.loads(text)
        except (ValueError,TypeError):feedback={}
        if not isinstance(feedback,dict):feedback={}
        issues=feedback.get('issues',[])
        if not isinstance(issues,list) or any(not isinstance(x,str) for x in issues):issues=[]
        run=history[-1]['run'] if history else None
        reviews=[{'task':source_id,'run':run,'review':feedback}] if feedback else []
        for finding in issues or ([text] if text else []):
            items.append({'id':f'R{len(items)+1}','finding':finding,'source_task':source_id,'source_run':run})
    latest=reviews[-1] if reviews else {'run':None,'review':{}}
    plan={'task':task['id'],'source_task':source_id,'source_run':latest['run'],'items':items,
          'complete_review':latest['review'],'complete_reviews':reviews,'worker_claim_is_acceptance':False}
    write(directory/'repair-plan.json',plan)
    if len(items)>30 or len(json.dumps(plan).encode())>32000:
        raise ValueError('Complete repair findings exceed the bounded worker context; Codex must split this task. Full findings retained in repair-plan.json')
    return plan


def retry_progress(directory, patch, changes, delta="", *, first_verification=False):
    """Do not spend another review on byte-identical rejected work."""
    seed = read(directory / "retry-seed.json")
    unchanged = bool(seed) and hashlib.sha256(patch.encode()).hexdigest() == seed.get("patch_sha256")
    result = {"seed_run": seed.get("run"), "unchanged_candidate": unchanged, "files": changes,
              "patch_sha256": hashlib.sha256(patch.encode()).hexdigest()}
    result["delta"] = delta
    result['first_verification']=first_verification is True
    result['blocked']=unchanged and not result['first_verification']
    write(directory / "retry-progress.json", result)
    return result['blocked']


def worker_inference_receipts(directory):
    """Bounded numeric action order across phases, including readable legacy receipts."""
    directory=Path(directory);found=[]
    for path in (directory/'worker-tools').glob('*.json'):
        match=re.fullmatch(r'(decision|edit|chat)-([0-9]{1,6})\.json',path.name)
        if match:found.append((int(match[2]),{'decision':0,'edit':1,'chat':2}[match[1]],path))
    receipts=[]
    for _,_,path in sorted(found)[-32:]:
        value=E.read_json(path,directory)
        if isinstance(value,dict) and value:receipts.append((path,value))
    return receipts


def worker_failure(directory):
    """Facts only; a wrapper exit never substitutes for a verified handoff."""
    directory=Path(directory)
    def object_(name):
        value=E.read_json(directory/name,directory);return value if isinstance(value,dict) else {}
    def bounded_object(value):
        if not isinstance(value,dict):return None
        value=E.sanitized(value,limit=2000)
        return value if len(json.dumps(value).encode())<=8192 else None
    failed=[(p,v) for p,v in worker_inference_receipts(directory) if v.get('status')=='failed']
    client=None;handoff=None;events=directory/'worker-events.jsonl'
    if E.safe_file(events,directory):
        with events.open('rb') as stream:
            stream.seek(max(0,events.stat().st_size-2*1024*1024))
            for line in stream.read(2*1024*1024).splitlines():
                try:value=json.loads(line)
                except ValueError:continue
                if isinstance(value,dict) and value.get('type')=='worker.client_result' and value.get('schema_version')==1 and type(value.get('exit_code')) is int and 0<=value['exit_code']<=255:
                    client={'exit_code':value['exit_code'],'schema_version':1}
                if isinstance(value,dict) and value.get('type')=='worker.result_handoff' and type(value.get('schema_version')) is int and value['schema_version']==1 and value.get('status')=='failed':
                    handoff={'schema_version':1,'status':'failed'}
    if not failed and not handoff and (client is None or client['exit_code']==0):return None
    path,receipt=failed[-1] if failed else (None,{})
    process=object_('worker-process.json');completion=object_('worker-completion.json')
    text=lambda value: E.sanitized(value,limit=2000) if isinstance(value,str) else None
    budget=receipt.get('requested_budget')
    return {'schema_version':1,'phase':text(receipt.get('phase')) or (path.name.split('-')[0] if path else 'result_handoff' if handoff else None),
        'completion_reason':text(receipt.get('completion_reason')) or ('failed' if handoff else None),'requested_budget':budget if type(budget) is int and 0<budget<=1000000 else None,
        'metrics':bounded_object(receipt.get('metrics')) or {},'error_class':text(receipt.get('error_class')),
        'error':text(receipt.get('error')),'worker_contract':text(receipt.get('worker_contract',completion.get('contract'))),
        'model':bounded_object(receipt.get('model')) or bounded_object(object_('worker-model.json')) or None,'inference_policy':bounded_object(receipt.get('inference_policy')),
        'client_result':client,'wrapper_result':{'code':process.get('code') if type(process.get('code')) is int else None,'reason':text(process.get('reason'))},
        'receipt':{'name':str(path.relative_to(directory)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} if path else None,
        **({'result_handoff':handoff} if handoff else {})}


def worker_diagnostic(directory, problem):
    result = directory / "worker-result.txt"
    text = ''
    if result.is_file() and not result.is_symlink():
        with result.open('rb') as stream:
            stream.seek(max(0,result.stat().st_size-4000))
            text = stream.read(4000).decode('utf8',errors='replace')
    fragment = bool(re.search(r"<tool_call>\s*$", text))
    events = ''
    path = directory / 'worker-events.jsonl'
    if path.is_file() and not path.is_symlink():
        with path.open('rb') as stream:
            stream.seek(max(0,path.stat().st_size-65536))
            events = stream.read(65536).decode('utf8',errors='replace')
    native = '"type":"command_execution"' in events or '"type": "command_execution"' in events
    printed = not native and bool(re.search(r'<(?:tool_call|function=|parameter=)', text))
    failure=worker_failure(directory)
    diagnosis = {"problem": problem, "unfinished_tool_call": fragment, 'unexecuted_tool_markup': printed,'worker_failure':failure}
    write(directory / "worker-diagnostic.json", diagnosis)
    advice = '; worker printed tool markup as text; use the actual command tool API, do not simulate calls or stop after promising to read files' if printed else ''
    detail=''
    if failure:
        detail='; worker '+str(failure['phase'] or 'unknown phase')+' ended '+str(failure['completion_reason'] or failure['error_class'] or 'unknown')
        if failure['requested_budget'] is not None:detail+='; requested '+str(failure['requested_budget'])+' tokens, observed '+str((failure.get('metrics') or {}).get('eval_count'))
        detail+='; client exit '+str((failure['client_result'] or {}).get('exit_code'))+', wrapper exit '+str(failure['wrapper_result']['code'])
    return problem + detail + ("; worker ended with an unfinished tool-call fragment" if fragment else "") + advice


def check_evidence(directory, *, max_total=64000, max_each=16000):
    """Actual bounded host stdout/stderr alongside commands and exit receipts."""
    directory=Path(directory);result=[];remaining=max_total
    for index,receipt in enumerate(read(directory/'checks.json',[])):
        path=directory/f'check-{index}.log'
        size=path.stat().st_size if path.exists() else 0
        budget=max(0,min(max_each,remaining))
        if path.exists() and budget:
            with path.open('rb') as stream:
                stream.seek(max(0,size-budget));raw=stream.read(budget)
            output=raw.decode('utf8',errors='replace')
        else:output=''
        result.append({'command':receipt.get('command'),'code':receipt.get('code'),'reason':receipt.get('reason'),
                       'combined_stdout_stderr':output,'output_is_tail':size>budget,'saved_bytes':size,
                       'receipt':f'check-{index}.log'})
        remaining-=min(size,budget)
    write(directory/'review-check-evidence.json',result)
    return result


def missing_declared_files(work, task):
    """Declared writable task files absent from the candidate tree."""
    names=task.get('local_worker_files') or task.get('files',[])
    return [n for n in names if isinstance(n,str) and n and not any(c in n for c in '*?[')
            and not (Path(work)/n).exists() and not (Path(work)/n).is_symlink()]


def contract_feedback(work, task):
    """Name an uncreated required file plainly instead of an opaque import error."""
    missing=missing_declared_files(work,task)
    if not missing:return ''
    return ('Required task file(s) were never created: '+', '.join(missing)
            +'. The task contract requires creating them; checks that import them cannot pass until they exist.\n')


def check_feedback(directory):
    """Worker sees failing checks only; complete host receipts remain inspectable."""
    checks=read(directory/'checks.json',[])
    failures=[]
    for index,result in enumerate(checks):
        if result.get('code')==0 and result.get('reason')=='exited':continue
        path=directory/f'check-{index}.log'
        raw=path.read_text(errors='replace') if path.exists() else 'No check log recorded.'
        # unittest and traceback failures occur at the end; skip verbose passing cases.
        marker=raw.find('======================================================================')
        if marker>=0:raw=raw[marker:]
        failures.append({'check':index,'command':result.get('command'),
            'reason':result.get('reason'),'code':result.get('code'),'log_tail':raw[-4000:]})
    write(directory/'check-feedback.json',{'failed_checks':failures,'full_receipts':'checks.json and check-N.log'})
    return json.dumps(failures,ensure_ascii=False)[:6000] if failures else 'Host verification did not complete; inspect checks.json.'

