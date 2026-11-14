"""Bounded, read-only project/run inspection for every registered backend."""
import datetime as dt
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit


def receipt(path, errors):
    if not path.exists():return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size>512000:
        errors.append(path.name+': unsupported receipt');return None
    try:return json.loads(path.read_text())
    except (ValueError,OSError):errors.append(path.name+': unavailable');return None


def inspect_request(url):
    import midnight as M
    from performance import collect
    parsed=urlsplit(url);query=parse_qs(parsed.query,strict_parsing=True)
    if parsed.path not in {'/api/project','/api/run'}:raise ValueError('Unknown inspection route')
    if any(len(values)!=1 for values in query.values()):raise ValueError('Duplicate query field')
    fields={key:values[0] for key,values in query.items()}
    allowed={'project','before'} if parsed.path=='/api/project' else {'project','run'}
    if set(fields)-allowed or 'project' not in fields:raise ValueError('Invalid inspection query')
    project=M.selected(M.registry(),fields['project'])[0];root=Path(project['state_dir'])
    if root.is_symlink() or (root/'runs').is_symlink():raise ValueError('Unsupported evidence root')
    state=M.read(root/'state.json');errors=[]
    if parsed.path=='/api/project':
        tasks=M.read(Path(project['path'])/project['backlog'],[])+state.get('planned',[])
        active=(state.get('active_attempt') or {}).get('task',{}).get('id')
        pending=(state.get('pending') or {}).get('task',{}).get('id')
        output=[]
        for task in tasks:
            identity=task['id'];tries=state.get('tries',{}).get(identity,0)
            status=('accepted' if identity in state.get('done',[]) or M.AS.task_done(root,project['id'],identity) else 'superseded' if task.get('superseded_by') else
                    'awaiting review' if identity==pending else 'working' if identity==active else 'exhausted' if tries>=2 else
                    'blocked' if any(d not in state.get('done',[]) and not M.AS.task_done(root,project['id'],d) for d in task.get('depends_on',[])) else 'queued')
            output.append(dict(task,status=status,attempts=tries,feedback=state.get('feedback',{}).get(identity),review_findings=state.get('review_findings',{}).get(identity)))
        from history_store import HistoryStore
        store=HistoryStore(root)
        if store.path.exists():
            before=fields.get('before')
            cursor=(dt.datetime.strptime(before.split('-')[0],'%Y%m%dT%H%M%SZ').replace(tzinfo=dt.timezone.utc),before) if before else None
            indexed=store.page_runs(project['id'],limit=51,before=cursor)
            history=[r['payload'] for r in indexed];page=history[:50]
        else:
            history=collect([project],dt.datetime(1970,1,1,tzinfo=dt.timezone.utc),M.now())['projects'][0]['details']
            history=sorted(history,key=lambda r:r['run'],reverse=True)
            if fields.get('before'):history=[r for r in history if r['run']<fields['before']]
            page=history[:50]
        return {'schema_version':2,'history_coverage':M.read(root/'history-migration.json') or {'complete':False},'improvement':M.improvement_snapshot(project),'project':project['id'],'name':project['name'],'checked_at':M.now().isoformat(),
                'tasks':output,'runs':page,'next_cursor':page[-1]['run'] if len(history)>50 else None,
                'cooldown_until':state.get('cooldown_until'),'consecutive_failures':state.get('consecutive_failures',0),
                'local_model':project['local_model'],'review_model':M.CP.CloudPolicy(M.STATE).descriptor()['model'], 'review_reasoning_effort':M.CP.CloudPolicy(M.STATE).descriptor()['reasoning_effort'],
                'backend_protocol':project['backend_protocol'],'errors':errors}
    identity=fields.get('run','')
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9_-]+',identity):raise ValueError('Invalid run identity')
    folder=root/'runs'/identity
    if not folder.is_dir() or folder.is_symlink() or folder.resolve().parent!=(root/'runs').resolve():raise ValueError('Run not found')
    names=('task.json','worker-model.json','worker-completion.json','worker-process.json','checks.json','check-feedback.json','candidate-hygiene.json','repair-plan.json','retry-progress.json','worker-diagnostic.json','review/result.json','review/process.json','accepted.json','outcome.json','candidate-checkpoint.json','github-delivery.json','cloud-profile.json','archive.json')
    records={}
    for name in names:
        path=folder/name
        if not path.resolve().is_relative_to(folder.resolve()):errors.append(name+': unsafe receipt path');continue
        value=receipt(path,errors)
        if value is not None:records[name]=value
    logs=[]
    for path in sorted(folder.glob('check-*.log'))[:10]:
        if not re.fullmatch(r'check-[0-9]+\.log',path.name) or path.is_symlink() or not path.is_file():continue
        with path.open('rb') as handle:
            size=path.stat().st_size;handle.seek(max(0,size-12000));text=handle.read(12000).decode('utf-8','replace')
        logs.append({'name':path.name,'text':text,'truncated':size>12000})
    return {'project':project['id'],'run':identity,'checked_at':M.now().isoformat(),'records':records,'check_logs':logs,'errors':errors}
