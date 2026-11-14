"""Read-only verification of an actual worker qualification and owner direction."""
import json
from pathlib import Path
import evidence_context as E
import structured_worker as S
import worker_probe_cases as C
import worker_probe_evidence as Q

SOURCES=('local_worker.py','structured_worker.py','ralphy/run.sh')


def qualification(path,model=None,*,source_root=None,allow_legacy=False):
 path=Path(path)
 if not E.safe_file(path,path.parent) or path.stat().st_size>128000:raise ValueError('Worker qualification is unavailable')
 proof=json.loads(path.read_text());root=Path(source_root).resolve() if source_root else Path(__file__).resolve().parent
 legacy=proof.get('schema_version')==1
 if legacy and not allow_legacy:raise ValueError('Legacy fixed-only qualification does not establish representative worker readiness')
 names=('label','range') if legacy else C.CASE_NAMES
 if (proof.get('schema_version') not in (1,2) or proof.get('passed') is not True or proof.get('tool_contract')!=S.CONTRACT
     or proof.get('product_attempts')!=0 or proof.get('cloud_calls')!=0
     or proof.get('worker_sources')!={n:S.digest(root/n) for n in SOURCES}
     or model is not None and proof.get('model')!=model
     or not isinstance(proof.get('results'),list) or len(proof['results'])!=len(names)
     or {r.get('case') for r in proof['results']}!=set(names)):raise ValueError('Exact worker, model and complete fixed qualification cases are required')
 sources=('worker_probe.py','worker_probe_runtime.py','worker_probe_client.py','worker_recovery.py') if legacy else Q.QUALIFICATION_SOURCES
 if (not legacy or proof.get('qualification_sources') is not None) and proof.get('qualification_sources')!={n:S.digest(root/n) for n in sources}:raise ValueError('Trusted qualification source changed')
 if not legacy and (proof.get('inference_policy')!=S.INFERENCE_POLICY or proof.get('execution_dependencies')!={n:S.digest(root/n) for n in Q.EXECUTION_DEPENDENCIES}):raise ValueError('Worker inference policy or execution dependency changed')
 for result in proof['results']:
  directory=Path(result['directory'])
  if (not directory.resolve().is_relative_to(path.parent.resolve()) or result.get('passed') is not True
      or result.get('ready_handoff') is not True or result.get('scope_ok') is not True
      or result.get('host_check_code')!=0 or result.get('host_check_reason')!='exited'
      or type(result.get('actual_tool_events')) is not int or result['actual_tool_events']<2):raise ValueError('Actual edit, fixed tests, scope and handoff were not qualified')
  files=result.get('files',{})
  required={'work/solution.py','work/test_solution.py','work/.midnight-ready.json','worker-manifest.json','worker-events.jsonl','worker-tools/submission.json','independent-host-check/check.log'}
  if not legacy:
   required.update({'work/AGENTS.md','work/PRD.md'})
   if result['case'] in C.REPRESENTATIVE_CASES:required.update({'work/contracts.md','work/fixtures.json','work/reference.py'})
  if set(files)!=required or any(not E.safe_file(directory/n,path.parent) or S.digest(directory/n)!=sha for n,sha in files.items()):raise ValueError('Worker qualification evidence changed')
  manifest=json.loads((directory/'worker-manifest.json').read_text())
  if not legacy and (manifest.get('task_id')!='typed-worker-qualification-'+result['case']
      or manifest.get('workspace')!=str(directory/'work') or manifest.get('allowed_files')!=['solution.py']
      or manifest.get('test_command')!=S.syntax_command(['solution.py'],'/opt/homebrew/bin:/usr/bin:/bin')):raise ValueError('Fixed qualification manifest changed')
  if not S.submission_complete(directory,directory/'work',manifest['task_id'],proof['model']):raise ValueError('Qualified handoff no longer verifies')
  if not legacy and not Q.terminal_handoff_complete(directory,directory/'work',manifest['task_id'],proof['model']):raise ValueError('Representative terminal publication is incomplete')
  if not legacy:
   representative=result['case'] in C.REPRESENTATIVE_CASES
   if (result.get('protected_inputs_unchanged') is not True or result.get('unrelated_source_unchanged') is not True
       or result.get('source_existed_before') is not (result['case']!='summary_creation')):raise ValueError('Representative source or protected input preservation is unverified')
   if representative:
    oracle=next(c[2] for c in C.cases() if c[0]==result['case'])
    if ((directory/'work/test_solution.py').read_text()!=oracle or result.get('brief_bytes')!=(directory/'work/PRD.md').stat().st_size
        or result['brief_bytes']<C.MIN_BRIEF_BYTES):raise ValueError('Representative oracle or production brief changed')
   elif S.digest(directory/'work/test_solution.py')!=C.LEGACY_ORACLE_SHA256[result['case']]:raise ValueError('Original fixed qualification oracle changed')
   if result.get('phase_evidence')!=Q.phase_evidence(directory,proof['model'],representative):raise ValueError('Representative inference/tool receipts changed')
 return {'path':str(path.resolve()),'sha256':S.digest(path),'model':proof['model'],'worker_sources':proof['worker_sources'],'contract':S.CONTRACT,
         'representative':not legacy,'inference_policy':proof.get('inference_policy')}


def owner_direction(path,project,episode,task,step):
 path=Path(path)
 if not E.safe_file(path,path.parent) or path.stat().st_size>16000:raise ValueError('Owner direction is unavailable')
 value=json.loads(path.read_text())
 if (value.get('project')!=project or value.get('episode')!=episode or value.get('task')!=task
     or value.get('microstep_index')!=step or value.get('additional_local_launches')!=2
     or value.get('owner_instruction')!='two further local attempts after a verified worker/tool-call fix. Proceed'
     or any(value.get(k) is not True for k in ('cloud_limits_unchanged','all_previous_charges_retained','full_acceptance_and_frozen_paths_retained'))):
  raise ValueError('Owner direction does not authorize this exact two-launch extension')
 return {'path':str(path.resolve()),'sha256':S.digest(path),'instruction':value['owner_instruction']}
