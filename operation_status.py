"""Plain-language running state from existing observations; no I/O or actions."""
import datetime as dt


def describe(view):
    phase = view.get('phase', 'unobserved')
    service = view.get('service') or {}
    checked = dt.datetime.fromisoformat(view['checked_at']) if view.get('checked_at') else None
    stamp = (view.get('controller') or {}).get('updated_at')
    try:
        stale = bool(checked and stamp and (checked - dt.datetime.fromisoformat(stamp)).total_seconds() > 90)
    except (ValueError, TypeError):
        stale = True
    running = bool(service.get('running'))
    result = {'running': running, 'work_active': False, 'local_worker_active': False, 'state': 'waiting', 'answer': 'NO',
              'headline': 'Waiting for the next run', 'happening': 'The scheduler is waiting.',
              'next_run_at': view.get('next_start'), 'next_run_label': 'Next scheduled run',
              'window_end': view.get('effective_cutoff'), 'next_run_blocked': False}
    if phase == 'paused':
        result.update(running=False, state='paused', headline='Midnight is paused',
                      happening='Work will resume when the pause is cleared.', next_run_blocked=True,
                      next_run_label='Scheduled window · currently paused')
    elif not service.get('loaded'):
        result.update(running=False, state='offline', headline='Midnight is offline',
                      happening='The scheduler service is not loaded.', next_run_blocked=True,
                      next_run_label='Scheduled window · service offline')
    elif phase == 'needs_attention' or (phase not in ('waiting_for_midnight', 'unobserved') and (stale or not running)):
        result.update(running=False, answer='CHECK', state='attention', headline='Midnight needs attention',
                      happening='The controller is not responding normally. Open the recovery details.',
                      next_run_label='Next scheduled window')
    elif phase == 'waiting_for_midnight':
        result.update(running=False, headline='Waiting for the next run',
                      happening='Outside scheduled hours. The next window permits work subject to resource and task admission.',
                      next_run_label='Next eligible window')
        readiness = view.get('readiness') or {}
        if readiness.get('state') == 'blocked_now':
            result['happening'] += ' ' + readiness.get('summary', 'A current launch prerequisite is blocked.')
    elif running:
        result.update(state='running', answer='YES', headline='Midnight is running')
        descriptions = {'model_maintenance': 'Checking and qualifying local models.',
                        'cloud_model_maintenance': 'Checking cloud model availability and GitHub delivery.',
                        'waiting_for_project': 'The scheduler is on; projects are waiting for eligible work.',
                        'low_disk_hold': 'Work is held because available disk space is low.'}
        result['happening'] = descriptions.get(phase, 'Working through the current project task.')
        if phase == 'working':
            result['work_active'] = True
            priority={'local_worker':0,'local_review':1,'local_planning':2,'host_checks':3,'independent_review':4,'cloud_implementation':5,'github_delivery':6}
            for project in sorted(view.get('projects', []),key=lambda p:priority.get(p.get('activity'),5)):
                task = project.get('active_task') or {}
                if task.get('title') or project.get('activity') in {'local_review','local_planning'}:
                    activity = project.get('activity')
                    labels = {'local_worker': 'Local coding','local_review':'Independent local review','local_planning':'Local planning', 'cloud_implementation': 'Exceptional cloud implementation',
                              'host_checks': 'Running host checks', 'independent_review': 'Independent review',
                              'github_delivery': 'Waiting for GitHub delivery', 'task_management': 'Preparing task or checking escalation'}
                    prefix = labels.get(activity)
                    result['happening'] = (prefix + ' · ' if prefix else '') + str(project['name']) + (': ' + str(task['title']) if task.get('title') else '')
                    result['local_worker_active'] = activity == 'local_worker' and (project.get('control') or {}).get('state','active')=='active'
                    break
        if phase == 'waiting_for_project' and checked:
            waiting=[]
            scheduled=[p for p in view.get('projects',[]) if p.get('on_schedule',True) and p.get('phase')!='paused']
            for project in scheduled:
                try:retry=dt.datetime.fromisoformat(project.get('retry_after') or '')
                except (TypeError,ValueError):continue
                if retry>checked:waiting.append((retry,project))
            if scheduled and len(waiting)==len(scheduled):
                retry,project=min(waiting,key=lambda item:item[0])
                if not result['window_end'] or retry<dt.datetime.fromisoformat(result['window_end']):
                    result.update(happening='Projects are in failure backoff; no coding worker is running.',
                                  next_work_at=retry.isoformat(),next_work_label='Next retry eligibility')
        if view.get('run_mode')=='continuous':
            result.update(next_run_label='24/7 — no expiry',next_run_at=None,window_end=None)
        elif view.get('override_active'):
            result.update(next_run_label='Running continuously until', next_run_at=view.get('override_expires') or (view.get('override') or {}).get('expires_at'))
        else:
            result.update(next_run_label='Current run window ends', next_run_at=view.get('effective_cutoff'))
        blocked=[p for p in view.get('projects',[]) if (p.get('control') or {}).get('state')=='blocked']
        result['blocked_projects']=len(blocked)
        if blocked and not result['local_worker_active'] and not any((p.get('control') or {}).get('state')=='active' for p in view.get('projects',[])):
            result.update(state='attention',answer='CHECK',work_active=False,
                          headline='Midnight is on; project work is blocked',
                          happening='; '.join(str(p.get('name',p.get('id')))+': '+p['control']['explanation'][:180] for p in blocked[:3]))
        waiting=[p for p in view.get('projects',[]) if (p.get('control') or {}).get('state')=='waiting']
        if waiting and not blocked and not result['local_worker_active'] and not any((p.get('control') or {}).get('state')=='active' for p in view.get('projects',[])):
            result.update(work_active=False,happening='; '.join(str(p.get('name',p.get('id')))+': '+p['control']['explanation'] for p in waiting[:3]))
    else:
        result.update(state='unobserved', answer='CHECK', headline='Checking Midnight',
                      happening='Waiting for a fresh controller observation.')
    discussions=(view.get('agent_workspace') or {}).get('active_discussions',[])
    if result['running'] and phase not in {'paused','needs_attention'} and discussions:
        discussion=discussions[0]
        label=('Daily scrum check-in' if discussion.get('kind')=='scrum' else 'Local agent conversation')+' · '+str(discussion.get('actor','local participant'))
        label+=' · '+('processing' if discussion.get('phase_state')=='running' else 'queued/preparing')
        result['discussion_activity']=label
        result['happening']+='; '+label
        if discussion.get('phase_state')=='running':result['work_active']=True
    if phase!='paused' and (view.get('workflow') or {}).get('available') is False:
        result.update(answer='CHECK',state='attention',work_active=False,local_worker_active=False,
            headline='Midnight needs checking',happening='The durable action store is unavailable; current work cannot be verified.',
            next_run_blocked=True)
    return result
