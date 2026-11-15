"""Independent, bounded Midnight health audit. No inference on read paths.

Installed from a verified frozen source bundle, independent of controller
imports. Stable incidents retain their charged repair budget across releases.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.request
import uuid
from zoneinfo import ZoneInfo
import durability as D
from action_store import Actions, fingerprint
import phase_runtime as P
import project_action as PA
import evidence_context as E
from maintenance_authority import check as check_authority
import schedule_control as SC
import commissioning as COM

ROOT=Path(os.environ.get('MIDNIGHT_CANONICAL_ROOT',Path(__file__).resolve().parent))
INSTALL_ROOT=Path.home()/'Developer'/'midnight'
POLICY=json.loads((Path(__file__).resolve().parent/'recovery_policy.json').read_text())


def window(registry,override,instant):
    return SC.window(instant, registry, override)['active']


def read(path,default=None):return D.read_json(path,{} if default is None else default)


def expected_project_wait(project,instant):
    status=project.get('status',{});typed=status.get('control',{})
    retry=typed.get('next_retry_at') or status.get('retry_after') or project.get('state',{}).get('cooldown_until')
    if retry:
        try:
            if instant<dt.datetime.fromisoformat(retry):return True
        except (ValueError,TypeError):pass
    return bool(project.get('paused') or status.get('phase') in {
        'waiting_for_midnight','paused','nightly_allowance_reached','finishing_before_09:00',
        'waiting_for_provider','pending_delivery',
        'manager_hold','quality_wait','no_useful_work','quality_profile_pending',
        'quality_profile_needs_manager','profile_needs_manager','profile_update_pending'}
        or typed.get('recovery_owner') in {'owner','provider'}
        or typed.get('code') in {'feature_gate_held','rescue_manager_held','planning_no_change','quality_gate','manager_hold','planner_hold'})


# Expected waits that are fine for minutes but mean "nothing is happening"
# when they persist through a work window. Schedule/pause/allowance waits are
# deliberate and excluded.
STALE_HOLD_PHASES={'manager_hold','quality_wait','no_useful_work','quality_profile_pending','quality_profile_needs_manager',
    'profile_needs_manager','profile_update_pending','waiting_for_provider','pending_delivery','needs_attention','resource_wait'}


def stale_holds(value,meta,instant):
    """Track how long each project (and the controller) has stayed held.

    Owner-visible notice only: never an incident or an orchestrator repair,
    because these waits are explained and may need an owner decision. The
    clock keeps running across ordinary re-ticks; it restarts only when the
    held phase or its detail changes, and clears outside the work window.
    """
    if value['paused'] or not value['active_window'] or value['installing']:
        meta['held_projects']={};return []
    now=instant.timestamp();held=meta.setdefault('held_projects',{});seen=set()
    from control_status import backlog_finished
    subjects=[(p['id'],p.get('status',{}).get('phase',''),str(p.get('status',{}).get('detail',''))) for p in value['projects']
              if not p['paused'] and not backlog_finished(p.get('status',{}).get('phase',''),p.get('status',{}).get('planning'),instant)]
    controller=value.get('controller') or {}
    subjects.append(('midnight',controller.get('phase',''),str(controller.get('detail',''))))
    for ident,phase,detail in subjects:
        if phase not in STALE_HOLD_PHASES:continue
        seen.add(ident);signature=fingerprint({'phase':phase,'detail':detail[:600]})
        record=held.get(ident)
        if not record or record.get('signature')!=signature:
            held[ident]={'since':now,'phase':phase,'detail':E.sanitize(detail)[:300],'signature':signature,'notified_at':None}
    for ident in [k for k in held if k not in seen]:held.pop(ident)
    threshold=POLICY.get('stale_hold_seconds',3600);renotify=POLICY.get('stale_hold_renotify_seconds',21600)
    due=[]
    for ident,record in held.items():
        if now-record['since']<threshold:continue
        record['stale']=True
        if record.get('notified_at') is None or now-record['notified_at']>=renotify:due.append(ident)
    return due


def notify_owner(title,message):
    """Local macOS notification; arguments are passed as data, never script."""
    try:
        subprocess.run(['/usr/bin/osascript','-e','on run argv','-e','display notification (item 2 of argv) with title (item 1 of argv)','-e','end run',
                        title[:120],message[:400]],capture_output=True,timeout=10)
        return True
    except (OSError,subprocess.SubprocessError):
        return False


def stale_hold_message(ident,record,instant):
    minutes=int((instant.timestamp()-record['since'])//60)
    since=dt.datetime.fromtimestamp(record['since'],ZoneInfo('Australia/Adelaide')).strftime('%H:%M %a')
    return ('Midnight stalled: '+ident,
            f"{record['phase']} for {minutes//60}h{minutes%60:02d}m (since {since}). {record.get('detail') or ''}".strip())


def observation(root,instant):
    runtime=root/'runtime';registry=read(root/'projects.json');override=read(runtime/'override.json')
    if not registry.get('projects'):raise ValueError('Project register is unavailable')
    store=Actions(runtime);jobs=store.jobs();phases=store.phases();controller=read(runtime/'status.json')
    if not controller:controller=read(runtime/'controller.json')
    projects=[]
    for p in registry['projects']:
        if not p.get('on_schedule'):continue
        status=read(Path(p['state_dir'])/'status.json');state=read(Path(p['state_dir'])/'state.json')
        from productive_status import observe as productive_observation
        from control_status import project_control
        control=project_control(status,state,instant=instant,paused=(Path(p['state_dir'])/'PAUSED').exists())
        productivity=productive_observation(p['state_dir'],p['id'],state,control=control,instant=instant)
        projects.append({'id':p['id'],'paused':(Path(p['state_dir'])/'PAUSED').exists(),
            'status':status,'state':state,'state_dir':p['state_dir'],'productivity':productivity})
    dashboard=dashboard_probe() if root.resolve()==ROOT.resolve() else {'available':None}
    traceback=''
    log=runtime/'launchd.err.log'
    if E.safe_file(log,runtime):
        with log.open('rb') as handle:handle.seek(max(0,log.stat().st_size-6000));traceback=E.sanitize(handle.read(6000).decode(errors='replace'))
    import progress_recovery as RP
    requests=[dict(r,current=(status:=RP.harness_request_status(root,r))=='current',currency=status) for r in store.harness_requests()]
    commission=COM.snapshot(runtime,instant)
    return {'at':instant.isoformat(),'active_window':window(registry,override,instant) or commission['active'],
        'commissioning':commission,'dashboard':dashboard,'controller_traceback':traceback,
        'paused':(runtime/'PAUSED').exists(),'installing':(runtime/'INSTALLING').exists() or ((runtime/'WEEKDAY_ACTIVATION_PENDING').exists() or (runtime/'WEEKDAY_ACTIVATION_PENDING').is_symlink()),
        'controller':controller,'jobs':jobs,'phases':phases,'executions':store.executions(),'phase_history':store.phases(active=False),'projects':projects,'harness_requests':requests}


def diagnoses(value,meta,instant):
    """Pure classification: known external/control waits are never core bugs."""
    now=instant.timestamp();issues=[]
    if value.get('dashboard',{}).get('available') is False and not value['installing']:
        issues.append(('dashboard_unresponsive','midnight','dashboard',{'reason':value['dashboard'].get('reason','Unavailable')}))
    if value['paused'] or not value['active_window'] or value['installing']:return issues
    for request in value.get('harness_requests',[]):
        if not request.get('current'):continue
        if value.get('commissioning',{}).get('blocks_dispatch') and request['scope']['project']!=value['commissioning']['project']:continue
        project=next((p for p in value['projects'] if p['id']==request['scope']['project']),None)
        if not project or project['paused']:continue
        issues.append(('worker_contract','midnight','progress-'+request['failure']['fingerprint'],{'progress_request':request}))
    for execution in value.get('executions',[]):
        phase=next((p for p in value.get('phase_history',[]) if p['id']==execution['phase']),None)
        if P.live_tree(execution['lease']) and (not phase or phase['state'] in {'succeeded','failed','interrupted'}):
            issues.append(('orphan_execution','midnight',execution['kind'],{'phase':execution['phase'],'job':execution['job']}))
    for phase in value['phases']:
        if phase['state'] in {'waiting','running'} and phase['deadline']<now:
            issues.append(('phase_deadline',phase['project'],phase['kind'],{'phase':phase['id'],'deadline':phase['deadline']}))
    for job in value['jobs']:
        if job['project']=='midnight' and job.get('payload',{}).get('kind')!='agent_discussion':continue
        if job['state']=='running' and not P.alive(job.get('lease')):
            issues.append(('orphan_action',job['project'],'owned-action',{'action':job['id']}))
        elif job['state']=='running' and not any(p['job']==job['id'] for p in value['phases']):
            registered=next((p for p in value['projects'] if p['id']==job['project']),{})
            if registered.get('state',{}).get('pending',{}).get('phase')=='delivery':continue
            progress=max([job['created']]+[p['progress'] for p in value.get('phase_history',[]) if p['job']==job['id']])
            if now-progress>120:issues.append(('action_no_progress',job['project'],'action-progress',{'action':job['id'],'progress':progress}))
    controller=value.get('controller',{})
    stamp=controller.get('updated_at')
    age=(instant-dt.datetime.fromisoformat(stamp)).total_seconds() if stamp else 999999
    if age>120:
        issues.append(('controller_unresponsive','midnight','controller',{'age_seconds':round(age),'traceback':value.get('controller_traceback','')}))
    elif controller.get('phase')=='needs_attention':
        detail=str(controller.get('detail',''))
        if not any(s in detail.lower() for s in ('owner changes','uncommitted','authentication','quota','permission','disk space')):
            issues.append(('controller_error','midnight','controller',{'detail':E.sanitize(detail)[:1200],'traceback':value.get('controller_traceback','')}))
    available=[p for p in value['projects'] if not p['paused'] and
        (not value.get('commissioning',{}).get('blocks_dispatch') or p['id']==value['commissioning']['project'])]
    unexplained=[]
    for p in available:
        status=p['status'];phase=status.get('phase','');detail=str(status.get('detail',''))
        if any(s in detail.lower() for s in ('truncated','no repository-reading','no available tool','no tools are available')):
            issues.append(('missing_manager_evidence',p['id'],'manager-handoff',{'phase':phase,'detail':detail[:1200]}))
        if phase=='needs_attention' and not expected_project_wait(p,instant):
            lower=detail.lower()
            if not any(x in lower for x in ('authentication','quota','rate limit','owner changes','uncommitted','allowance','scope','permission','disk','profile needs')):
                issues.append(('backend_error',p['id'],'backend-control',{'phase':phase,'detail':detail[:1200]}))
        owned=any(j['project']==p['id'] for j in value['jobs'])
        known=owned or expected_project_wait(p,instant)
        if not known:unexplained.append(p['id'])
    if unexplained:
        threshold=POLICY['unexplained_block_seconds'] if len(unexplained)==len(available) else POLICY['runnable_idle_seconds']
        stalled=[p for p in unexplained if now-(meta.get('idle_projects',{}).get(p) or now)>=threshold]
        if stalled:issues.append(('runnable_idle','midnight','dispatch',{'projects':stalled,'idle_seconds':round(max(now-meta['idle_projects'][p] for p in stalled))}))
    return issues


def restart_controller():
    return subprocess.run(['/bin/launchctl','kickstart','-k',f'gui/{os.getuid()}/space.venin.midnight'],
        capture_output=True,timeout=10).returncode==0


def restart_dashboard():
    return subprocess.run(['/bin/launchctl','kickstart','-k',f'gui/{os.getuid()}/space.venin.midnight-dashboard'],capture_output=True,timeout=10).returncode==0


def dashboard_probe():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8766/api/health',timeout=2) as response:value=json.load(response)
        observation=value.get('status_observation')
        responsive=observation is None or observation.get('available') is True or observation.get('warming') is True
        return {'available':value.get('service')=='midnight-dashboard' and P.alive(value.get('process')) and responsive}
    except (OSError,ValueError):return {'available':False,'reason':'Read-only health endpoint unavailable'}


def dashboard_health(after,expected,*,not_before=None,details=None):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8766/api/status',timeout=2) as response:
            value=json.load(response)
        process=value.get('dashboard_process') or {}
        valid=bool(value.get('observation',{}).get('available',True) and value.get('workflow',{}).get('version')==1 and dt.datetime.fromisoformat(value['checked_at']).timestamp()>after
            and value.get('dashboard_code_sha256')==expected and P.alive(process)
            and (not_before is None or isinstance(process.get('birth_us'),int) and process['birth_us']>=not_before*1000000))
        if valid and details is not None:details.update(process=process,checked_at=value['checked_at'],code_sha256=expected)
        return valid
    except (OSError,ValueError,KeyError):return False


def fenced_installation_verification(root,health):
    """An exact adopted generation may prove health while coding is closed.

    Both installer-owned fences remain in place. This grants no work, repair,
    rollback or fence-release permission; the installer owns finalisation.
    """
    runtime=root/'runtime'
    if health.get('state')!='verifying' or (runtime/'PAUSED').exists() or (runtime/'PAUSED').is_symlink():return False
    marker=runtime/'INSTALLING';gate=runtime/'WEEKDAY_ACTIVATION_PENDING'
    try:
        if not all(E.safe_file(path,runtime) and path.stat().st_size<=65536 for path in (marker,gate)):return False
        raw=marker.read_bytes();fence=json.loads(raw);gate_raw=gate.read_bytes();activation=json.loads(gate_raw)
        binding=health.get('activation_gate')
        if (not isinstance(binding,dict) or set(binding)!={'sha256','plan_sha256'}
            or not all(isinstance(v,str) and re.fullmatch('[a-f0-9]{64}',v) for v in binding.values())
            or hashlib.sha256(gate_raw).hexdigest()!=binding['sha256']):return False
        if (hashlib.sha256(raw).hexdigest()!=health.get('installation_fence_sha256')
            or not isinstance(fence,dict) or not isinstance(activation,dict)
            or not isinstance(fence.get('owner'),str) or not fence['owner']
            or not re.fullmatch('[a-f0-9]{32}',fence.get('generation',''))
            or activation.get('version')!=1 or activation.get('owner')!=fence['owner']
            or activation.get('generation')!=fence['generation']
            or activation.get('base')!=health.get('previous') or fence.get('source')!=health.get('previous')
            or activation.get('candidate_tree')!=health.get('tree')
            or activation.get('plan_sha256')!=binding['plan_sha256']):return False
        import git_delivery as G
        return bool(G.git(root,'rev-parse','HEAD')==health.get('installed')
            and G.git(root,'rev-parse','HEAD^{tree}')==health.get('tree') and not G.git(root,'status','--porcelain')
            and hashlib.sha256((root/'midnight.py').read_bytes()).hexdigest()==health.get('controller_sha256')
            and hashlib.sha256((root/'dashboard.py').read_bytes()).hexdigest()==health.get('dashboard_sha256')
            and (root/'guardian.py').read_bytes()==Path(__file__).read_bytes())
    except (OSError,ValueError,TypeError,subprocess.SubprocessError):return False


def installation_health(root,value,instant):
    """Watch an adopted patch; rollback code only, never its runtime history."""
    store=Actions(root/'runtime');health=store.meta('install_health') or {}
    outside_window=not value['active_window']
    fenced=outside_window or health.get('activation_gate') is not None
    if (health.get('state') not in {'verifying','rolling_back','rolled_back'} or value['paused']
        or (outside_window or health.get('state')=='verifying' and fenced)
        and not fenced_installation_verification(root,health)):return
    if health.get('state')=='rolled_back' and health.get('finalized') and not rollback_fence(root,health,create=False):return
    elapsed=instant.timestamp()-health['at']
    stamp=value.get('controller',{}).get('updated_at')
    responsive=bool(stamp and dt.datetime.fromisoformat(stamp).timestamp()>health['at']
        and instant.timestamp()-dt.datetime.fromisoformat(stamp).timestamp()<90
        and value['controller'].get('code_sha256')==health.get('controller_sha256')
        and P.alive(value['controller'].get('process')))
    requirements=health.get('restart_requirements') or {};controller_requirement=requirements.get('space.venin.midnight')
    controller_process=value.get('controller',{}).get('process') or {}
    if controller_requirement:
        responsive=responsive and isinstance(controller_process.get('birth_us'),int) and controller_process['birth_us']>=controller_requirement['not_before']*1000000
    bad=value.get('controller',{}).get('phase')=='needs_attention'
    dashboard_proof={};dashboard_requirement=requirements.get('space.venin.midnight-dashboard')
    dashboard_ok=False
    if health['state']=='verifying' and elapsed>=180 and responsive and not bad:
        dashboard_ok=(dashboard_health(health['at'],health.get('dashboard_sha256'),not_before=dashboard_requirement['not_before'],details=dashboard_proof)
                      if dashboard_requirement else dashboard_health(health['at'],health.get('dashboard_sha256')))
    if dashboard_ok:
        with P.source_lock(root/'runtime',deadline=time.time()+10,stopped=lambda:(root/'runtime'/'PAUSED').exists()):
            if store.meta('install_health')!=health:return
            if fenced and not fenced_installation_verification(root,health):return
            owned=health.get('rollback_fence')
            if owned and owned.get('owner')=='guardian-install-health' and owned.get('generation') and owned.get('installed')==health['installed'] and owned.get('repair')==health['repair']:
                import git_delivery as G
                if G.git(root,'rev-parse','HEAD')!=health['installed']:return
                marker=root/'runtime'/'INSTALLING'
                expected=(json.dumps(owned,sort_keys=True)+'\n').encode()
                if E.safe_file(marker,root/'runtime') and marker.read_bytes()==expected:
                    D.marker(marker,False);health['rollback_fence_cleared_at']=instant.isoformat()
                else:health['rollback_fence_retired_at']=instant.isoformat()
                health.pop('rollback_fence',None)
            health.update(state='verified',verified_at=instant.isoformat())
            if health.get('restart_epoch'):
                health.update(verified_restart_epoch=health['restart_epoch'],verified_controller_process=controller_process,
                              verified_dashboard=dashboard_proof)
            store.meta('install_health',health,write=True)
        return
    if outside_window:return
    if health['state']=='verifying' and elapsed<180:return
    import git_delivery as G
    with P.source_lock(root/'runtime',deadline=time.time()+10,stopped=lambda:(root/'runtime'/'PAUSED').exists()):
        if store.meta('install_health')!=health:return
        if health['state']=='verifying' and fenced and not fenced_installation_verification(root,health):return
        current=G.git(root,'rev-parse','HEAD')
        replay=health['state'] in {'rolling_back','rolled_back'} and current==health['previous']
        if current!=health['installed'] and not replay or G.git(root,'status','--porcelain'):
            health.update(state='held',reason='Owner source changes prevent automatic rollback');store.meta('install_health',health,write=True);return
        if replay:
            finalize_rollback(root,health);return
        if not rollback_fence(root,health):
            health.update(state='held',reason='Foreign or replaced installation fence prevents rollback');store.meta('install_health',health,write=True);return
        for job in store.jobs():
            if job['project']=='midnight' and job.get('payload',{}).get('kind')!='agent_discussion':continue
            if P.alive(job.get('lease')):os.kill(job['lease']['pid'],signal.SIGTERM)
            if not P.alive(job.get('lease')) and PA.drain(root/'runtime',job['id'],grace=3):
                store.finish(job['id'],'interrupted',{'reason':'rollback_drain'})
        if any(j['project']!='midnight' or j.get('payload',{}).get('kind')=='agent_discussion' for j in store.jobs()):return
        if (root/'runtime'/'PAUSED').exists() or not window(read(root/'projects.json'),read(root/'runtime'/'override.json'),dt.datetime.now(dt.timezone.utc)):return
        health.update(state='rolling_back',rollback_started=health.get('rollback_started',instant.isoformat()),
                      rejected_controller=health.get('rejected_controller') or value.get('controller',{}).get('process'))
        store.meta('install_health',health,write=True)
        G.git(root,'update-ref','refs/midnight/failed-install/'+health['repair'],health['installed'])
        if current!=health['previous']:G.git(root,'reset','--hard',health['previous'])
        health.update(state='rolled_back',reason='Installed controller failed its bounded health observation')
        store.meta('install_health',health,write=True);finalize_rollback(root,health)


def rollback_fence(root,health,*,create=True):
    """Never claim an existing marker from another installation."""
    runtime=root/'runtime';marker=runtime/'INSTALLING';owned=health.get('rollback_fence') or {}
    expected=(json.dumps(owned,sort_keys=True)+'\n').encode()
    if marker.exists():
        if not E.safe_file(marker,runtime):return False
        value=marker.read_bytes()
        return bool(owned.get('owner')=='guardian-install-health' and owned.get('generation')
            and owned.get('installed')==health['installed'] and owned.get('repair')==health['repair'] and value==expected
            or health.get('installation_fence_sha256') and hashlib.sha256(value).hexdigest()==health['installation_fence_sha256'])
    if not create:return False
    health['rollback_fence']={'installed':health['installed'],'repair':health['repair'],'owner':'guardian-install-health','generation':uuid.uuid4().hex}
    Actions(runtime).meta('install_health',health,write=True)
    D.atomic_write(marker,(json.dumps(health['rollback_fence'],sort_keys=True)+'\n').encode())
    return True


def restored_generation_proof(root,health):
    """A restart request alone cannot release rejected loaded code."""
    try:
        import git_delivery as G
        if G.git(root,'rev-parse','HEAD')!=health['previous'] or G.git(root,'status','--porcelain'):return None
        rejected=health.get('rejected_controller')
        if rejected and P.alive(rejected):return None
        intents=health['rollback_restart_intents'];controller_intent=intents['controller'];dashboard_intent=intents['dashboard']
        controller=read(root/'runtime/status.json') or read(root/'runtime/controller.json');process=controller.get('process') or {}
        stamp=dt.datetime.fromisoformat(controller['updated_at']).timestamp();now=time.time()
        if (not P.alive(process) or not isinstance(process.get('birth_us'),int) or process['birth_us']<controller_intent['not_before']*1000000
            or stamp<=controller_intent['not_before'] or now-stamp>90 or controller.get('phase')=='needs_attention'
            or controller.get('code_sha256')!=hashlib.sha256((root/'midnight.py').read_bytes()).hexdigest()):return None
        dashboard={}
        if not dashboard_health(max(controller_intent['not_before'],dashboard_intent['not_before']),
            hashlib.sha256((root/'dashboard.py').read_bytes()).hexdigest(),not_before=dashboard_intent['not_before'],details=dashboard):return None
        return {'source':health['previous'],'tree':G.git(root,'rev-parse','HEAD^{tree}'),'controller':process,
                'controller_updated_at':controller['updated_at'],'dashboard':dashboard,'observed_at':dt.datetime.now(dt.timezone.utc).isoformat()}
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):return None


def finalize_rollback(root,health):
    runtime=root/'runtime';store=Actions(runtime)
    if (runtime/'PAUSED').exists() or not window(read(root/'projects.json'),read(runtime/'override.json'),dt.datetime.now(dt.timezone.utc)):return
    if not rollback_fence(root,health):
        health.update(finalized=False,rollback_waiting='foreign_or_replaced_fence');store.meta('install_health',health,write=True);return
    authority=check_authority(root)
    requests=health.setdefault('restart_requests',{});intents=health.setdefault('rollback_restart_intents',{})
    def restart_current():
        import git_delivery as G
        return (not (runtime/'PAUSED').exists()
            and window(read(root/'projects.json'),read(runtime/'override.json'),dt.datetime.now(dt.timezone.utc))
            and check_authority(root)==authority and store.meta('install_health')==health
            and rollback_fence(root,health,create=False) and G.git(root,'rev-parse','HEAD')==health['previous']
            and not G.git(root,'status','--porcelain'))
    for name,request in (('controller',restart_controller),('dashboard',restart_dashboard)):
        if requests.get(name):continue
        if (runtime/'PAUSED').exists() or not rollback_fence(root,health,create=False):return
        # A durable intent may have already caused a native restart even if its
        # acknowledgement is absent or false. Keep that epoch and the fence;
        # another audit does not authorise a second destructive kickstart.
        if name in intents:
            if not restart_current():return
            health.update(finalized=False,rollback_waiting='uncertain_restart_'+name)
            store.meta('install_health',health,write=True);return
        intents[name]={'epoch':uuid.uuid4().hex,'not_before':time.time()}
        health.update(state='rolled_back',finalized=False);store.meta('install_health',health,write=True)
        if not restart_current():return
        try:requests[name]=bool(request())
        except (OSError,subprocess.SubprocessError) as exc:requests[name]=False;health['restart_error']=type(exc).__name__
        store.meta('install_health',health,write=True)
        if not requests[name]:
            if not restart_current():return
            health.update(finalized=False,rollback_waiting='uncertain_restart_'+name)
            store.meta('install_health',health,write=True);return
    if not requests.get('controller') or not requests.get('dashboard'):return
    if (runtime/'PAUSED').exists() or not rollback_fence(root,health,create=False):return
    rejected=health.get('rejected_controller')
    if rejected and P.alive(rejected):
        health['rollback_waiting']='drain_rejected_controller';store.meta('install_health',health,write=True)
        if not restart_current():return
        if not P.stop_tree(rejected,grace=3):return
    proof=restored_generation_proof(root,health)
    if not proof:
        health.update(finalized=False,rollback_waiting='fresh_restored_generation_process_proof');store.meta('install_health',health,write=True);return
    if (runtime/'PAUSED').exists() or not window(read(root/'projects.json'),read(runtime/'override.json'),dt.datetime.now(dt.timezone.utc)) or not rollback_fence(root,health,create=False):return
    health.update(state='rolled_back',finalized=True,restored_generation_proof=proof);health.pop('rollback_waiting',None)
    store.meta('install_health',health,write=True)
    import maintenance_recovery as M
    if M.configured(root):
        if not M.restore_rollback_admission(root,health) and store.meta('install_health')==health:
            health.update(finalized=False,rollback_waiting='restored_admission_awaits_owned_drainage_or_fresh_proof')
            store.meta('install_health',health,write=True)
        return
    # The caller owns the source lock. Check the saved generation and exact
    # marker once more after proof persistence, then release only these bytes.
    fresh=restored_generation_proof(root,health)
    same_processes=bool(fresh and all(fresh.get(key)==proof.get(key) for key in ('source','tree','controller'))
        and fresh.get('dashboard',{}).get('process')==proof.get('dashboard',{}).get('process'))
    if not same_processes or not restart_current():
        if store.meta('install_health')==health:
            health.update(finalized=False,rollback_waiting='restored_generation_changed_before_final_release')
            store.meta('install_health',health,write=True)
        return
    D.marker(runtime/'INSTALLING',False)


def repair_running(store,spawn=None):
    import maintenance_recovery as M
    if M.configured(store.runtime.parent) and M.running(store.runtime.parent,spawn=spawn):return True
    for repair in store.repairs():
        progress=repair.get('payload',{}).get('progress_recovery')
        if progress:
            session=M.session_for(store.runtime.parent,progress['session'])
            if session and session['state']=='complete':
                M.MaintenanceRecovery(store.runtime.parent,session['incident']).complete(session['id'],session['result'])
                continue
            # A version-two repair is reconciled through its original
            # continuation directory, never the legacy reservation path.
            return True
        if repair['state']=='reserved':
            if store.clock()-repair['updated']>120:
                store.repair(repair['id'],state='unknown')
            return True
        if repair['state']=='running' and P.alive(repair.get('lease')):return True
        if repair['state']=='running':
            store.repair(repair['id'],state='unknown',expected_lease=repair.get('lease'),payload=repair['payload'])
        # Unknown execution is charged. The recovery executor must reconcile
        # its source/delivery receipts before another writer can start.
        current=store.repair(repair['id'])
        if P.alive(current.get('lease')):return True
        for job in store.jobs():
            if job['project']=='midnight' and job.get('payload',{}).get('kind')!='agent_discussion' and not P.alive(job.get('lease')):
                if PA.drain(store.runtime,job['id'],grace=3):store.finish(job['id'],'interrupted',{'reason':'maintenance_owner_lost'})
        log=store.runtime/'maintenance'/repair['id']/'reconcile.log';log.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with log.open('a') as out:
            (spawn or subprocess.Popen)([sys.executable,'-B',str(Path(__file__).resolve().parent/'core_repair.py'),
                '--root',str(store.runtime.parent),'--repair',repair['id'],'--reconcile'],
                stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
        return True
    return False


def launch_repair(root,store,incident,*,spawn=None):
    if (store.meta('install_health') or {}).get('state') in {'verifying','rolling_back'}:return None
    if repair_running(store,spawn=spawn):return None
    fault=store.get_incident(incident);request=(fault or {}).get('payload',{}).get('progress_request')
    if (fault or {}).get('code')=='dashboard_unresponsive' and dashboard_probe().get('available') is True:
        store.incident_state(incident,'resolved','Fresh dashboard health supersedes the old incident; no new maintenance charge');return None
    if request:
        import progress_recovery as RP
        status=RP.harness_request_status(root,request)
        if status=='unavailable':return None
        if status=='superseded':
            store.retire_harness(request['id']);store.incident_state(incident,'resolved','Retained harness request superseded; original history and charges retained');return None
    import maintenance_recovery as M
    if M.configured(root):return M.launch(root,store,incident,spawn=spawn)
    identity=store.reserve_repair(incident,{'source':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'incident_binding':fault})
    if not identity:return None
    log=root/'runtime'/'maintenance'/identity/'supervisor.log';log.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    try:
        with log.open('a') as out:
            (spawn or subprocess.Popen)([sys.executable,'-B',str(Path(__file__).resolve().parent/'core_repair.py'),
                '--root',str(root),'--repair',identity],stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
    except BaseException:
        store.repair(identity,state='unknown');raise
    return identity


def audit(root=ROOT,*,instant=None,restart=restart_controller,spawn=None,notify=None):
    root=Path(root);runtime=root/'runtime';runtime.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (runtime/'guardian.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'state':'busy'}
        instant=instant or dt.datetime.now(dt.timezone.utc);store=Actions(runtime)
        store.recover()
        value=observation(root,instant);meta=store.meta('guardian') or {}
        if value['active_window'] and not value['paused']:
            repair_running(store,spawn=spawn)
            value=observation(root,instant)
            installation_health(root,value,instant)
            import maintenance_recovery as M
            if M.configured(root):
                try:M.publish_generation(root,store.meta('install_health') or {})
                except (OSError,ValueError,subprocess.SubprocessError) as exc:
                    store.meta('maintenance_bundle_publication',{'state':'held','reason':str(exc)[:500]},write=True)
        elif not value['paused']:
            # Health-only observation leaves the ordinary schedule, recovery
            # and all dispatch decisions closed outside the coding window.
            installation_health(root,value,instant)
        issues=diagnoses(value,meta,instant)
        for request in value.get('harness_requests',[]):
            if request.get('currency')=='superseded':store.retire_harness(request['id'])
        if value['paused'] or not value['active_window']:
            meta['idle_projects']={}
        else:
            idle=meta.setdefault('idle_projects',{})
            for project in value['projects']:
                active=any(p['project']==project['id'] and p['state']=='running' and P.live_tree(p.get('lease')) for p in value['phases'])
                owned=any(j['project']==project['id'] for j in value['jobs'])
                if active or owned or expected_project_wait(project,instant):idle.pop(project['id'],None)
                else:idle.setdefault(project['id'],instant.timestamp())
        seen=[]
        for code,project,key,evidence in issues:
            identity=store.incident(project,code,key,fingerprint(evidence),evidence);seen.append(identity)
            incident=store.get_incident(identity)
            import maintenance_recovery as M
            if incident['state']=='held' and not M.configured(root):continue
            repair_key='deterministic:'+identity
            if not store.meta(repair_key):
                store.meta(repair_key,{'at':instant.isoformat(),'evidence':fingerprint(evidence)},write=True)
                ok=False
                if code=='orphan_execution':
                    for execution in store.executions():
                        if execution['phase']==evidence['phase'] and P.stop_tree(execution['lease'],grace=3):
                            store.clear_execution(execution['phase'],execution['lease']);ok=True
                if code in {'orphan_action','phase_deadline','action_no_progress'}:
                    jobs=[j for j in value['jobs'] if j['project']==project]
                    for job in jobs:
                        if code in {'phase_deadline','action_no_progress'} and P.alive(job.get('lease')):
                            os.kill(job['lease']['pid'],signal.SIGTERM)
                        if not P.alive(job.get('lease')) and PA.drain(runtime,job['id'],grace=3):
                            store.finish(job['id'],'interrupted',{'reason':'guardian_recovery'});ok=True
                elif code in {'controller_unresponsive','controller_error','runnable_idle'}:ok=restart()
                elif code=='dashboard_unresponsive':ok=restart_dashboard()
                store.incident_state(identity,'recovering','Bounded deterministic recovery attempted' if ok else 'Deterministic recovery unavailable')
            elif value['active_window'] and not value['paused'] and instant.timestamp()-incident['created']>=60 and POLICY['enabled'] and not repair_running(store,spawn=spawn):
                launch_repair(root,store,identity,spawn=spawn)
        for incident in store.incidents():
            if incident['state'] in {'open','recovering'} and incident['id'] not in seen:
                # Absence during a pause/window end is not proof of a repair.
                if value['active_window'] and not value['paused'] and not value['installing'] and (store.meta('install_health') or {}).get('state') not in {'verifying','rolling_back'}:
                    store.incident_state(incident['id'],'resolved','The exact fault is no longer observed by the independent audit')
        # Owner-visible stall notice, independent of incidents and repairs.
        # Only the installed pipeline may raise real desktop notifications.
        send=notify or (notify_owner if root.resolve()==INSTALL_ROOT.resolve() else (lambda *_:False))
        for ident in stale_holds(value,meta,instant):
            record=meta['held_projects'][ident]
            if send(*stale_hold_message(ident,record,instant)):record['notified_at']=instant.timestamp()
        meta['stale_holds']=sorted(k for k,r in meta.get('held_projects',{}).items() if r.get('stale'))
        meta['productive_progress']={p['id']:p.get('productivity',{}) for p in value['projects']}
        meta.update(at=instant.isoformat(),issues=len(issues),state='paused' if value['paused'] else 'outside_window' if not value['active_window'] else 'attention' if issues else 'observing')
        store.meta('guardian',meta,write=True)
        return {**meta,'incidents':seen}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',default=str(ROOT));args=parser.parse_args()
    if Path(args.root).resolve()!=INSTALL_ROOT.resolve():raise ValueError('Guardian CLI requires the installed canonical pipeline')
    try:result=audit(Path(args.root))
    except Exception as exc:
        result={'state':'unavailable','at':dt.datetime.now(dt.timezone.utc).isoformat(),'reason':type(exc).__name__+': '+str(exc)[:240]}
        D.atomic_write(Path(args.root)/'runtime'/'guardian-error.json',(json.dumps(result)+'\n').encode())
    print(json.dumps(result))


if __name__=='__main__':main()
