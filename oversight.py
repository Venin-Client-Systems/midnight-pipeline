"""Read-only oversight assembled from Midnight's existing evidence collectors."""
import datetime as dt
import json
from pathlib import Path


def journal(path, limit=200):
    """Bound retained display history; original journals are never changed."""
    try:
        from collections import deque
        with Path(path).open() as handle:lines=deque(handle,maxlen=limit)
    except FileNotFoundError:return []
    rows=[]
    for line in lines:
        try:rows.append(json.loads(line))
        except ValueError:continue
    return rows


def task_view(task, state, project, scopes, retired, attempts):
    import midnight as M
    identity=task['id'];hold=state.get('task_holds',{}).get(identity)
    recovery=scopes.get(identity,{})
    accepted=identity in state.get('done',[]) or M.AS.task_done(Path(project['state_dir']),project['id'],identity)
    active=(state.get('active_attempt') or {}).get('task',{}).get('id')
    pending=(state.get('pending') or {}).get('task',{}).get('id')
    if accepted:status,reason='accepted','Accepted commit evidence is retained.'
    elif identity in retired:status,reason='retired',retired[identity].get('rationale','Retired by the coordinator; the owner can restore it.')
    elif task.get('superseded_by') or identity in state.get('superseded_tasks',{}):status,reason='superseded','A recorded successor carries this work; the original history is retained.'
    elif identity==pending:status,reason='awaiting review','A retained candidate is waiting for checked review or delivery.'
    elif identity==active:status,reason='running','An attempt is retained; use the current project phase to confirm activity.'
    elif hold:status,reason='held',hold.get('reason','Task-specific hold needs inspection.')
    elif recovery.get('admission',{}).get('state') in {'needs_codex','needs_harness_repair'}:
        status,reason='waiting for coordinator',str((recovery.get('diagnosis') or {}).get('next_action') or recovery['admission'].get('reason','A bounded coordinator decision is needed.'))
    elif any(d not in state.get('done',[]) and not M.AS.task_done(Path(project['state_dir']),project['id'],d) for d in task.get('depends_on',[])):
        status,reason='waiting for dependency','An earlier required task has not been accepted.'
    elif recovery and not recovery.get('admission',{}).get('allowed'):
        status,reason='held',recovery.get('admission',{}).get('reason','No verified continuation is available.')
    else:status,reason='ready','Eligible for selection when the schedule, resources and project quality gates permit.'
    last=next((a for a in attempts if a.get('task_id')==identity or a.get('task')==identity or (isinstance(a.get('task'),dict) and a['task'].get('id')==identity)),None)
    return {'id':identity,'title':task.get('title',identity),'state':status,'explain':reason,'last_attempt':last,'hold':hold,
            'recovery':recovery.get('admission'),'retirement':retired.get(identity)}


def collect(base=None):
    import midnight as M
    import coordinator as K
    import dashboard as D
    from dashboard_details import inspect_request
    from urllib.parse import urlencode
    view=base if base is not None else D.collect()
    data=M.registry();by_id={p['id']:p for p in data['projects']}
    view['oversight_version']=1
    view['cloud_providers']=M.CP.CloudPolicy(M.STATE).provider_snapshot()
    view['models']=M.MM.snapshot()
    view['coordinator']={'mode':K.settings(M.STATE)['mode'],'decisions':K.decisions(M.STATE)}
    view['owner_actions']=journal(M.STATE/'owner-actions.jsonl')
    view['guardian']=view.get('workflow',{}).get('guardian',{'state':'unobserved'})
    view['incidents']=view.get('workflow',{}).get('incidents',[])
    for project in view['projects']:
        config=by_id[project['id']];directory=Path(config['state_dir']);state=M.read(directory/'state.json')
        project['task_holds']=state.get('task_holds',{})
        project['hold_tracker']=M.read(directory/'hold-tracker.json')
        project['explain']=project.get('control',{}).get('explanation') or 'No current project control explanation is available.'
        project['events']=journal(directory/'events.jsonl',150)
        try:
            details=inspect_request('/api/project?'+urlencode({'project':project['id']}))
            project['attempts']=details['runs'];project['history_coverage']=details.get('history_coverage')
            project['next_history_cursor']=details.get('next_cursor')
            backend=K.project_backend(config)
            tasks=backend.all_tasks(state)
            scopes={}
            for scope in project.get('repair_status',{}).get('scopes',[]):scopes.setdefault(scope['task_id'],scope)
            retired=M.read(directory/'coordinator-retired.json')
            project['tasks']=[task_view(t,state,config,scopes,retired,project['attempts']) for t in tasks]
        except (OSError,ValueError,KeyError,TypeError) as exc:
            project['tasks']=[];project['oversight_error']=type(exc).__name__+': '+str(exc)[:300]
    view['history_limits']={'decisions':200,'owner_actions':200,'events_per_project':150,'attempts_per_project':50,
                            'sessions':len(view.get('recent_sessions',[]))}
    return view
