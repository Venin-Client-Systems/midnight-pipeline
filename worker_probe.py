"""Fixed disposable worker qualification, invoked by the reviewed host only.

The caller owns the local phase/resource. This script never edits source, product
workspaces, accounts or provider configuration. Qualification outputs are not
product candidates and cannot be promoted.
"""
from pathlib import Path
import argparse,datetime as dt,hashlib,json,os,subprocess,sys,time
parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--output',required=True);parser.add_argument('--model-file',required=True);args=parser.parse_args()
WORK=Path(args.source).resolve();B=Path(args.output).resolve();ROOT=Path(__file__).resolve().parent
B.mkdir(parents=True,exist_ok=True,mode=0o700)
sys.path.insert(0,str(ROOT))
import phase_runtime as P,structured_worker as S
import worker_probe_cases as C,worker_probe_evidence as Q
from worker_probe_runtime import Broker,confined_worker
model=json.loads(Path(args.model_file).read_text());results=[]
qualification_deadline=time.monotonic()+580
worker_sources={n:S.digest(WORK/n) for n in ('local_worker.py','structured_worker.py','ralphy/run.sh')}
qualification_sources={n:S.digest(ROOT/n) for n in Q.QUALIFICATION_SOURCES}
execution_dependencies={n:S.digest(WORK/n) for n in Q.EXECUTION_DEPENDENCIES}
if (WORK/'ralphy/run.sh').read_bytes()!=(ROOT/'ralphy/run.sh').read_bytes():raise ValueError('Qualification requires the immutable trusted Ralphy entry')
cases=[('label','def normalize_label(value):\n    return value\n',
 'from solution import normalize_label\nassert normalize_label("  HeLLo ")=="hello"\nassert normalize_label(" ")==""\ntry: normalize_label(3)\nexcept TypeError: pass\nelse: raise AssertionError("requires TypeError")\n',
 'Implement normalize_label(value): accept strings, strip surrounding whitespace and return lowercase; raise TypeError for every non-string. Keep the function name.'),
 ('range','def within(value, low, high):\n    return True\n',
 'from solution import within\nassert within(3,1,4) is True\nassert within(1,1,4) is True\nassert within(4,1,4) is True\nassert within(5,1,4) is False\nassert within(-1,1,4) is False\ntry: within(2,4,1)\nexcept ValueError: pass\nelse: raise AssertionError("requires ValueError")\n',
 'Implement within(value, low, high): return a bool indicating low<=value<=high, including endpoints. Raise ValueError when low>high. Keep the function name.')]
cases+=C.cases()
padding=''.join('# Existing unrelated implementation record '+str(i).zfill(4)+' must remain unchanged.\n' for i in range(200))
standards=('Preserve existing behavior, exact file scope, fixed host checks, unrelated code and evidence. Make one bounded edit for the requested behavior. No network, dependencies, accounts, Git operations or expanded permissions.\n'*50)
for name,source,oracle,task in cases:
 representative=name in C.REPRESENTATIVE_CASES
 original_function=source;source=padding+source+'\n# End of existing source.\n' if source is not None else None
 directory=B/'probes'/name;work=directory/'work';work.mkdir(parents=True,exist_ok=False)
 inputs={'test_solution.py':oracle,'progress.txt':'','AGENTS.md':'Disposable host-managed qualification. Edit only solution.py, run fixed tests, finish. No accounts, network or Git changes.',
   'PRD.md':'# One bounded editing probe\n\n- [ ] '+task+'\n\nRead solution.py and test_solution.py. Use edit_file for ONE exact snippet in solution.py, then run_tests, then finish. Preserve every unrelated line. The host owns metadata, tests, progress and the ready marker. No product files or cloud inference are involved.\n\nEngineering constraints:\n'+standards}
 if source is not None:inputs['solution.py']=source
 if representative:
  extra=C.context_files(task,oracle);context=extra.pop('host_context');inputs.update(extra)
  inputs['PRD.md']='# Production-sized bounded qualification\n\n- [ ] '+task+'\n\nBefore editing, read each of '+', '.join(C.REQUIRED_READS)+'. The solution.py file may be absent; read its absence, then create it using empty old_text. Read every other named contract before editing. Use exact typed edits, run the fixed syntax check, then finish. The host owns semantic verification. Do not edit any other file.\n\nRetained host context, advisory only:\n'+context+'\n\nCurrent task remains: '+task+'\n'
 for file,text in inputs.items():(work/file).write_text(text)
 protected_inputs={n:S.digest(work/n) for n in inputs if n not in {'solution.py','progress.txt'}}
 subprocess.run(['git','init','--quiet'],cwd=work,check=True);subprocess.run(['git','add','.'],cwd=work,check=True)
 subprocess.run(['git','-c','user.name=Qualification','-c','user.email=qualification@localhost','-c','commit.gpgsign=false','commit','-qm','Disposable qualification baseline'],cwd=work,check=True)
 task_id='typed-worker-qualification-'+name
 worker_seconds=min(240,int(qualification_deadline-time.monotonic())-30)
 if worker_seconds<20:raise TimeoutError('Fixed qualification total deadline before next case')
 manifest={'version':1,'workspace':str(work),'task_id':task_id,'allowed_files':['solution.py'],
  'test_command':S.syntax_command(['solution.py'],'/opt/homebrew/bin:/usr/bin:/bin'),'check_kind':'syntax','timeout_seconds':worker_seconds}
 (directory/'worker-manifest.json').write_text(json.dumps(manifest))
 broker=Broker(directory,manifest,S.run_tests)
 (directory/'worker-tools').mkdir(mode=0o700)
 _,env=confined_worker(WORK,directory,[str(WORK/'local_worker.py')])
 command=[str(ROOT/'ralphy/run.sh'),'--prd','PRD.md']
 env.update(HOME=str(Path.home()),PYTHONDONTWRITEBYTECODE='1',MIDNIGHT_PROBE_SOURCE=str(WORK),MIDNIGHT_LOCAL_CODE_BIN=str(ROOT/'worker_probe_client.py'),MIDNIGHT_LOCAL_WORKER_MANIFEST=str(directory/'worker-manifest.json'),MIDNIGHT_WORKER_DESCRIPTOR=json.dumps(model),
   MIDNIGHT_WORKER_PROBE_BROKER=str(broker.root),
   EXAMPLEAPP_AGENT_EVENTS=str(directory/'worker-events.jsonl'),EXAMPLEAPP_AGENT_RESULT=str(directory/'worker-result.txt'),EXAMPLEAPP_RALPHY_STATE_DIR=str(directory/'ralphy-state'))
 for key in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','LOCAL_CODE_EVALUATION_LOCK_HELD'):env.pop(key,None)
 started=time.monotonic();ready=False
 guards=B/'native-guards';guards.mkdir(exist_ok=True,mode=0o700)
 launch=guards/(name+'.json');launch.write_text(json.dumps({'command':command,'cwd':str(work),'env':env,'parent':P.lease(),'deadline':time.time()+worker_seconds+30}))
 with __import__('contextlib').nullcontext():
  with (directory/'ralphy.log').open('w') as output:
   proc=subprocess.Popen([sys.executable,'-I','-B',str(ROOT/'worker_probe_runtime.py'),'--guard',str(launch)],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
   lease=dict(P.lease(proc.pid),group=True,members=[])
   try:
    while proc.poll() is None:
     lease['members']=P.group_members(lease)+P.descendants(lease)
     (guards/(name+'-lease.json')).write_text(json.dumps(lease))
     broker.step(min(qualification_deadline,time.monotonic()+20))
     ready=Q.terminal_handoff_complete(directory,work,task_id,model)
     if ready:break
     if launch.with_suffix('.exit.json').exists():break
     if time.monotonic()-started>worker_seconds+10:raise TimeoutError('Typed worker qualification deadline')
     time.sleep(.5)
   finally:
    leader_alive=P.alive(lease)
    lease['members']=lease.get('members',[])+P.group_members(lease)+P.descendants(lease)
    drained=P.stop_tree(lease,grace=3) and not any(P.alive(p) for p in lease['members'])
    (guards/(name+'-drained.json')).write_text(json.dumps({'leader_was_alive':leader_alive,'drained':drained,'lease':lease}))
    proc.wait(timeout=10)
    if not leader_alive or not drained:raise RuntimeError('Candidate qualification group ownership/drain was not verified')
  ready=Q.terminal_handoff_complete(directory,work,task_id,model)
  if not ready:raise RuntimeError('Confined worker did not produce a verified handoff; semantic qualification not started')
  host=S.run_tests(work,directory/'independent-host-check',dict(manifest,test_command=['/opt/homebrew/bin/python3','-B','test_solution.py']),min(qualification_deadline,time.monotonic()+20))
 events_path=directory/'worker-events.jsonl';events=[]
 for line in events_path.read_text().splitlines():
  try:events.append(json.loads(line))
  except ValueError:pass
 tools=[e for e in events if e.get('type')=='item.completed' and e.get('item',{}).get('type') in ('file_change','command_execution')]
 names=set(subprocess.check_output(['git','diff','--name-only'],cwd=work,text=True).splitlines()+subprocess.check_output(['git','ls-files','--others','--exclude-standard'],cwd=work,text=True).splitlines())
 scope_ok=names=={'solution.py','.midnight-ready.json'} and (work/'test_solution.py').read_text()==oracle
 unrelated_unchanged=source is None or ((work/'solution.py').read_text().startswith(padding) and (work/'solution.py').read_text().endswith('\n# End of existing source.\n'))
 protected_unchanged=all((work/n).is_file() and S.digest(work/n)==sha for n,sha in protected_inputs.items())
 phases=Q.phase_evidence(directory,model,representative)
 context_ok=not representative or (work/'PRD.md').stat().st_size>=C.MIN_BRIEF_BYTES
 passed=bool(ready and host['code']==0 and host['reason']=='exited' and tools and scope_ok and unrelated_unchanged and protected_unchanged and context_ok and (work/'solution.py').read_text()!=source)
 files={str(p.relative_to(directory)):S.digest(p) for p in (work/'solution.py',work/'.midnight-ready.json',directory/'worker-manifest.json',events_path,directory/'worker-tools/submission.json',directory/'independent-host-check/check.log',*[work/n for n in protected_inputs]) if p.is_file()}
 result={'case':name,'passed':passed,'ready_handoff':ready,'host_check_code':host['code'],'host_check_reason':host['reason'],'actual_tool_events':len(tools),'scope_ok':scope_ok,
   'seconds':round(time.monotonic()-started,2),'directory':str(directory),'files':files,'initial_source_bytes':len(source.encode()) if source is not None else 0,'brief_bytes':(work/'PRD.md').stat().st_size,'unrelated_source_unchanged':unrelated_unchanged,
   'source_existed_before':source is not None,'protected_inputs_unchanged':protected_unchanged,'phase_evidence':phases}
 (directory/'qualification.json').write_text(json.dumps(result,indent=2)+'\n');results.append(result);print(json.dumps(result),flush=True)
 if not passed:break
proof={'schema_version':2,'at':dt.datetime.now(dt.timezone.utc).isoformat(),'passed':len(results)==len(C.CASE_NAMES) and all(r['passed'] for r in results),'model':model,
 'worker_sources':worker_sources,'qualification_sources':qualification_sources,'tool_contract':S.CONTRACT,
 'execution_dependencies':execution_dependencies,'inference_policy':S.INFERENCE_POLICY,
 'production_entry':'frozen ralphy/run.sh -> confined candidate local_worker.py -> structured_worker.py; fixed check broker',
 'production_worker_entry_exercised':True,'qualification_proxy':True,'native_production_client':False,'product_attempts':0,'cloud_calls':0,'results':results}
assert worker_sources=={n:S.digest(WORK/n) for n in worker_sources},'Worker source changed during qualification'
assert qualification_sources=={n:S.digest(ROOT/n) for n in qualification_sources},'Trusted qualification source changed during execution'
assert execution_dependencies=={n:S.digest(WORK/n) for n in execution_dependencies},'Worker execution dependency changed during qualification'
(B/'typed-worker-qualification.json').write_text(json.dumps(proof,indent=2)+'\n')
if not proof['passed']:sys.exit(1)
import worker_recovery as W
(B/'qualification-receipt.json').write_text(json.dumps(W.qualification(B/'typed-worker-qualification.json',model,source_root=WORK),indent=2)+'\n')
