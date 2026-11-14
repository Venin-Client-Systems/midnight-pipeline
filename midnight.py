#!/usr/bin/env python3
"""Midnight: local overnight development orchestration and evidence-based progress."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import fcntl
import hashlib
import io
import json
import os
import plistlib
from pathlib import Path
import re
import signal
import subprocess
import process_observer as PO
import sys
import time
import uuid
from zoneinfo import ZoneInfo
import durability as D
import worker_quality as Q
import model_manager as MM
import colibri_provider as GP
import model_observation as MO
import session_history as SH
import active_state as AS
import quality_cycle as QC
import quality_monitor as QM
import cloud_policy as CP
import cloud_rescue as CR
import git_delivery as GD
import action_store as AW
import phase_runtime as PR
import project_action as PA
import progress_recovery as RP
import schedule_control as SC
import commissioning as COM

ROOT = Path(__file__).resolve().parent
LOADED_CODE_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
CONTROLLER_LEASE=None
REGISTRY = ROOT / "projects.json"
ENGINEERING_POLICY = ROOT / "OPEN_SOURCE.md"
STATE = ROOT / "runtime"
LABEL = "space.venin.midnight"
PYTHON = "/opt/homebrew/bin/python3"
STOP = False
CSV_FIELDS = SH.FIELDS
LIMIT_KEYS = {"max_attempts_per_night", "max_cloud_calls_per_night", "max_failures_per_night"}
CONTINUOUS_LIMITS = {"max_attempts_per_night": None, "max_cloud_calls_per_night": None,
                     "max_failures_per_night": None}


def now():
    return dt.datetime.now(dt.timezone.utc)


def read(path, default=None):
    if Path(path).name in D.CRITICAL_JSON:
        return D.read_json(path, default)
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {} if default is None else default


def write(path, value):
    path = Path(path)
    if path.name in D.CRITICAL_JSON:
        D.save_json(path, value)
    else:
        D.atomic_write(path, (json.dumps(value, indent=2, allow_nan=False) + "\n").encode(), durable=path.name != "status.json")


def engineering_policy():
    raw = ENGINEERING_POLICY.read_bytes()
    if not raw.strip() or len(raw) > 8000:
        raise ValueError("Midnight engineering policy must contain 1-8000 bytes")
    return {"id": "open-source-reuse", "source": "OPEN_SOURCE.md",
            "sha256": hashlib.sha256(raw).hexdigest(), "instruction": raw.decode("utf-8")}


def registry():
    if not REGISTRY.exists():
        raise ValueError("Midnight is not set up yet: projects.json is missing. Run `midnight setup`.")
    data = read(REGISTRY)
    if data.get("schema_version") != 1 or not isinstance(data.get("projects"), list):
        raise ValueError("Invalid Midnight project register")
    ZoneInfo(data["timezone"])
    SC.weekdays(data["schedule"])
    for key in ("start", "end"):
        dt.time.fromisoformat(data["schedule"][key])
    ids = set()
    for project in data["projects"]:
        identity = project["id"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", identity) or identity in ids:
            raise ValueError("Project IDs must be unique slugs")
        ids.add(identity)
        if not isinstance(project.get("on_schedule"), bool):
            raise ValueError("on_schedule must be a boolean")
        if type(project.get("backend_protocol")) is not int or project["backend_protocol"] != Q.BACKEND_PROTOCOL:
            raise ValueError("Every Midnight project must declare backend_protocol 5; see BACKENDS.md")
        for key in ("path", "state_dir"):
            if not Path(project[key]).is_absolute():
                raise ValueError("Project paths must be absolute")
        for key in ("controller", "backlog", "config", "brief"):
            p = Path(project[key])
            if p.is_absolute() or ".." in p.parts:
                raise ValueError("Project file references must remain inside the project")
    return data


def selected(data, identity=None):
    result = [p for p in data["projects"] if identity is None or p["id"] == identity]
    if identity and not result:
        raise ValueError("Unknown Midnight project: " + identity)
    return result


def window(instant, data, override=None):
    return SC.window(instant, data, override)


def parse_expiry(value, instant, timezone):
    result = dt.datetime.fromisoformat(value)
    if result.tzinfo is None:
        zone = ZoneInfo(timezone)
        result = result.replace(tzinfo=zone)
        roundtrip = result.astimezone(dt.timezone.utc).astimezone(zone)
        if roundtrip.replace(tzinfo=None) != result.replace(tzinfo=None):
            raise ValueError("That local time does not exist; use an explicit UTC offset")
        if result.utcoffset() != result.replace(fold=1).utcoffset():
            raise ValueError("That local time is ambiguous; include a UTC offset")
    if result <= instant:
        raise ValueError("Override expiry must be in the future")
    return result.astimezone(dt.timezone.utc)


def service():
    try:
        r = subprocess.run(["/bin/launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], capture_output=True, text=True, timeout=5)
        return {"loaded": r.returncode == 0, "label": LABEL,
                "running": bool(re.search(r"^\s*state = running$", r.stdout, re.M)),
                "last_exit": (re.search(r"last exit code = (\d+)", r.stdout) or [None, None])[1]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"loaded": None, "label": LABEL, "error": str(exc)}


def queue(project, state):
    tasks = read(Path(project["path"]) / project["backlog"], []) + state.get("planned", [])
    done = set(state.get("done", []))
    done.update(AS.tasks_done(Path(project["state_dir"]),project["id"],(t['id'] for t in tasks)))
    in_flight = {identity for identity in [(state.get("active_attempt") or {}).get("task", {}).get("id"),
                 (state.get("pending") or {}).get("task", {}).get("id")] if identity}
    replacements={t['id']:t['superseded_by'] for t in tasks if t.get('superseded_by')}
    replacements.update(state.get('superseded_tasks',{}))
    superseded = [dict(t,superseded_by=replacements[t['id']]) for t in tasks if t['id'] in replacements]
    retired=state.get('retired_repair_contracts',{})
    remaining = [t for t in tasks if t["id"] not in done and t['id'] not in replacements and t['id'] not in retired]
    eligible = [t for t in remaining if t["id"] not in in_flight and state.get("tries", {}).get(t["id"], 0) < 2
                and all(d in done for d in t.get("depends_on", []))]
    exhausted = [t["id"] for t in remaining if t["id"] not in in_flight and state.get("tries", {}).get(t["id"], 0) >= 2]
    recovery=RP.Recovery(project['state_dir'],project['id'],evidence_roots=[project['path'],*RP.certificate_roots(STATE)]).snapshot()
    by_task={}
    for scope in recovery['scopes']:by_task.setdefault(scope['task_id'],scope)
    for task in remaining:
        info=by_task.get(task['id'])
        if not info or task['id'] in in_flight or not all(d in done for d in task.get('depends_on',[])):continue
        if task['id'] in exhausted:exhausted.remove(task['id'])
        if info['admission']['state'] in {'ready','diagnosing'} and task not in eligible:eligible.append(task)
    return {"known": len(tasks), "completed": len(done), "remaining": len(remaining),
            "superseded": [{"id": t["id"], "by": t["superseded_by"]} for t in superseded],
            "retired":sorted(retired),"exhausted": exhausted, "in_flight": sorted(in_flight), "next": [{"id": t["id"], "title": t["title"]} for t in eligible[:5]]}


def project_snapshot(project):
    directory = Path(project["state_dir"])
    errors = []
    def observation(path, default):
        try:
            return read(path, default)
        except (ValueError, OSError) as exc:
            errors.append(f"{path.name}: {exc}")
            return default
    state = observation(directory / "state.json", {})
    status = observation(directory / "status.json", {})
    config = observation(Path(project["path"]) / project["config"], {})
    try:
        backlog = queue(project, state)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        errors.append("Backlog unavailable: " + str(exc))
        backlog = None
    pending = state.get("pending") or {}
    active = (state.get("active_attempt") or {}).get("task") or pending.get("task") or {}
    active_run = (state.get("active_attempt") or {}).get("run") or pending.get("run")
    activity = None
    if active_run and re.fullmatch(r"[A-Za-z0-9_-]+", active_run):
        run_dir = directory / "runs" / active_run
        backend = observation(run_dir / "implementation-backend.json", {})
        finished = observation(run_dir / "worker-process.json", {})
        activity = ("github_delivery" if pending.get("phase") == "delivery" else "independent_review" if pending
                    else "host_checks" if finished or backend.get('kind')=='saved_candidate' else "local_worker" if backend.get("kind") == "local"
                    else "cloud_implementation" if backend.get("kind") == "cloud" else "task_management")
    from control_status import project_control
    child = observation(directory / 'active-child.json', {})
    child_live = bool(child.get('pid') and child.get('boot_id') == D.boot_id()
                      and child.get('stamp') and stamp(child['pid']) == child['stamp'])
    supervisor = observation(STATE/'active-child.json',{})
    supervisor_live = bool(supervisor.get('project') == project['id'] and supervisor.get('pid')
                           and supervisor.get('boot_id') == D.boot_id() and supervisor.get('stamp')
                           and stamp(supervisor['pid']) == supervisor['stamp'])
    project_jobs=[j for j in AW.observed_jobs(STATE) if j['project']==project['id']]
    if not supervisor_live:
        supervisor_live=any(PR.alive(j.get('lease')) for j in project_jobs)
    owned=next(iter(AW.observed_phases(STATE,project['id'])),None)
    from colibri_observation import project_models
    model_view=project_models(MM.snapshot().get('active') or {'model':project['local_model']},project_jobs,owned)
    if owned and owned.get('kind')=='local' and owned.get('payload',{}).get('role'):
        activity='local_review' if owned['payload']['role']=='review' else 'local_planning'
    control = project_control(status, state, process_running=child_live, activity=activity,
                              supervisor_running=supervisor_live,
                              owned_phase=owned,
                              paused=(directory / 'PAUSED').exists())
    last_task = {k: active[k] for k in ('id', 'title') if k in active}
    micro=(state.get('active_attempt') or {}).get('microstep')
    if micro and active:
        active=dict(active,title='Step '+str(micro['index']+1)+'/'+str(micro['total'])+': '+micro['title'])
    if control['state'] == 'blocked' and not pending:
        active = {};activity = None
    repair_status = RP.Recovery(directory,project['id'],evidence_roots=[project['path'],*RP.certificate_roots(STATE)]).snapshot()
    from productive_status import observe as productive_observation
    productivity = productive_observation(directory, project['id'], state, repair=repair_status,
        control=control, instant=now(), active=child_live)
    return {"id": project["id"], "name": project["name"], "on_schedule": project["on_schedule"],
            "path": project["path"], "why": project["why"], "instruction": project["instruction"],
            **model_view, "cloud_model": CP.CloudPolicy(STATE).primary()["model"], "cloud_reasoning_effort":CP.CloudPolicy(STATE).primary()["reasoning_effort"], "cloud_providers":CP.CloudPolicy(STATE).provider_snapshot(),
            "phase": "paused" if (directory / "PAUSED").exists() else status.get("phase", "unobserved"),
            "observed_at": status.get("updated_at"), "accepted": state.get("accepted", [])[-50:],
            "accepted_total":AS.accepted_count(directory,project["id"],state), "accepted_truncated":AS.accepted_count(directory,project["id"],state)>50,
            "improvement": improvement_snapshot(project),
            'quality_monitor':QM.QualityMonitor(directory,project['id']).snapshot(),
            'repair_status':repair_status, 'productivity':productivity,
            'routine_provider':'local','local_supervision_calls_today':state.get('local_supervision_calls_today',0),
            "queue": backlog, "attempts_today": state.get("attempts_today", 0),
            "failures_today": state.get("failures_today", 0), "cloud_calls_today": state.get("cloud_calls_today", 0),
            "interruptions_today": state.get("interruptions_today", 0),
            "active_task": {k: active[k] for k in ("id", "title") if k in active},
            "activity": activity, "retry_after": control['next_retry_at'], 'control': control, 'last_task': last_task,
            "pending_review": pending.get("task", {}).get("id"),
            "recent_feedback": {identity: feedback for identity, feedback in state.get("feedback", {}).items()
                                if identity not in state.get("done", [])},
            "effective_limits": effective_limits(now()),
            "report": str(directory / "morning-report.md"), "evidence": str(directory / "runs"), "errors": errors}


def fair_projects(projects):
    last=read(STATE/'fairness.json').get('last_project')
    ids=[p['id'] for p in projects]
    if last in ids:
        n=ids.index(last)+1;return projects[n:]+projects[:n]
    return projects


def improvement_snapshot(project,view='quality'):
    from improvement_observation import snapshot as observe
    return observe(project,ROOT,view=view)


def owner_action(kind,detail,via):
    """Append-only record of every owner control change (CLI or dashboard)."""
    entry={'at':now().isoformat(),'action':kind,'detail':detail,'via':via}
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=STATE/'owner-actions.jsonl'
    with path.open('a') as handle:
        handle.write(json.dumps(entry)+'\n');handle.flush();os.fsync(handle.fileno())
    store=AW.Actions(STATE)
    with store.write() as con:store.event(con,'midnight','owner_action',uuid.uuid4().hex,entry)
    return entry


def owner_control(action, detail=None, *, via='cli', data=None):
    detail=detail or {};data=data or registry()
    fields={'set_primary_provider':{'primary'},'overrule_decision':{'decision_id'},
            'unretire_task':{'project','task'},'pause':set(),'resume':set()}
    if action not in fields or set(detail)!=fields[action]:raise ValueError('Unknown action or invalid control fields')
    if any(not isinstance(v,str) or not v or len(v)>128 for v in detail.values()):raise ValueError('Invalid control value')
    import coordinator as K
    if action=='set_primary_provider':
        result=CP.CloudPolicy(STATE).select(detail['primary'],via)
    elif action=='overrule_decision':result=K.overrule(ROOT,detail['decision_id'],registry=data)
    elif action=='unretire_task':
        project=selected(data,detail['project'])[0]
        result=K.unretire(project,detail['task'])
    else:
        result=set_pause(action,data=data,via=via,log=False)
    owner_action(action,detail,via)
    return result


def set_pause(action, *, data=None, project=None, via='cli', log=True):
    data=data or registry()
    target=Path(selected(data,project)[0]['state_dir']) if project else STATE
    target.mkdir(parents=True,exist_ok=True)
    D.marker(target/'PAUSED',action=='pause')
    if action=='resume':kick()
    if log:owner_action(action,{'project':project},via)
    return {'paused':action=='pause','project':project}


def maintain_history(project):
    from history_maintenance import maintain
    maintain(project)


def snapshot(data, identity=None):
    instant = now()
    override = read(STATE / "override.json")
    timing = window(instant, data, override)
    commission = COM.snapshot(STATE, instant)
    controller = read(STATE / "status.json")
    phase = controller.get("phase", "unobserved")
    if (STATE / "PAUSED").exists():
        phase = "paused"
    elif ((STATE/'WEEKDAY_ACTIVATION_PENDING').exists() or (STATE/'WEEKDAY_ACTIVATION_PENDING').is_symlink()):
        phase = 'activation_pending'
    elif not timing["active"] and not commission['active'] and phase not in {"needs_attention"}:
        phase = "waiting_for_midnight"
    policy = engineering_policy()
    value = {"schema_version": 2, "pipeline": data["name"], "checked_at": instant.isoformat(),
            "engineering_policy": {k: v for k, v in policy.items() if k != "instruction"},
            "timezone": data["timezone"], "schedule": data["schedule"], "phase": phase,
            "controller": controller, "service": service(), "override": override,
            "override_active": timing["override_active"],
            'run_mode':timing['mode'],'run_mode_label':'24/7 — no expiry' if timing['mode']=='continuous' else 'Saved schedule' if timing['mode']=='scheduled' else 'Expiring override',
            "effective_cutoff": timing["cutoff"].isoformat() if timing["cutoff"] else None,
            "next_start": timing["next_start"].astimezone(ZoneInfo(data["timezone"])).isoformat(),
            "ledger": str(STATE / "sessions.csv"), "registry": str(REGISTRY),
            "models": MO.selection_snapshot(),
            'workflow':AW.observe(STATE),
            "recent_sessions": [r for r in ledger_rows(100) if identity is None or r["project"] == identity][-5:],
            "projects": [project_snapshot(p) for p in selected(data, identity)]}
    value['commissioning'] = commission
    from readiness import observe
    value['readiness'] = observe(value['models'], commission, instant)
    if commission['active']:
        value.update(run_mode='commissioning',run_mode_label='One retained action',
                     effective_cutoff=dt.datetime.fromtimestamp(commission['deadline'],dt.timezone.utc).isoformat())
    try:
        from agent_workspace import Workspace
        value['agent_workspace']=Workspace(STATE/'agent-workspace').overview()
        value['agent_workspace']['active_discussions']=[{'actor':j['payload'].get('actor'),'kind':j['payload'].get('discussion_kind'),
            'phase_state':next((p['state'] for p in value['workflow'].get('phases',[]) if p['job']==j['id'] and p['kind']=='local'),'preparing'),
            'work':j['payload'].get('work')} for j in AW.observed_jobs(STATE) if j['payload'].get('kind')=='agent_discussion' and j['state']=='running' and PR.alive(j.get('lease'))]
    except Exception:value['agent_workspace']={'available':False,'active_discussions':[]}
    from operation_status import describe
    value['operation'] = describe(value)
    if commission['active'] and phase not in {'paused','needs_attention'}:
        value['operation'].update(next_run_label='Commissioning deadline',next_run_at=value['effective_cutoff'])
    if phase=='activation_pending':
        value['operation'].update(state='waiting',answer='NO',work_active=False,local_worker_active=False,
            headline='Preparing weekday activation',happening='The reviewed runtime settings are being reconciled; work remains held.',next_run_blocked=True)
    return value


def paragraph(value):
    return " ".join(str(value).split())


def ledger_rows(limit=None):
    return SH.Sessions(STATE).rows(limit)


def csv_rows(payload):
    return SH.parse(payload)


def append_rows(path, rows):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        SH.Sessions(path.parent).append(rows)


def finish(session, reason, data,*,preserve_current=False):
    if session.get("finished"):
        return
    end = now()
    rows = []
    ledger = SH.Sessions(STATE)
    for identity, baseline in session["projects"].items():
        saved = ledger.receipt(session["id"], identity)
        if saved is not None:
            # The ledger commit precedes the finished checkpoint. Recovery must
            # preserve that decision even if the restart has a different reason
            # or later project state; it cannot rewrite the completed session.
            rows.append(saved)
            continue
        project = baseline.get("project") or selected(data, identity)[0]
        state = read(Path(project["state_dir"]) / "state.json")
        receipts={a['run']:a for a in state.get('accepted',[]) if a['run'] not in baseline['accepted_runs']}
        cursor=None
        while True:
            page=AS.accepted_since(Path(project['state_dir']),identity,session['started_at'],cursor=cursor)
            for a in page:
                if a['run'] not in baseline['accepted_runs']:receipts[a['run']]=a
            if len(page)<500:break
            cursor=(page[-1]['at'],page[-1]['run'])
        accepted=sorted((a for a in receipts.values() if not a.get('session_id') or a['session_id']==session['id']),key=lambda a:a.get('at',''))
        attempts = max(0, AS.local_attempt_total(state) - baseline["attempts"])
        charges=AW.Actions(STATE).session_charges(session['id'],identity)
        durable=session.get('durable_actions') and not session.get('legacy_dispatch')
        if durable:attempts=charges.get('local',0)
        titles = "; ".join(paragraph(a["title"]) for a in accepted)
        achieved = (f"Accepted {len(accepted)} change(s): {titles}." if accepted else "No new changes passed acceptance during this session.")
        concise = "; ".join(paragraph(a["title"])[:100] for a in accepted[:3])
        summary_achieved = achieved if len(accepted) <= 3 else f"Accepted {len(accepted)} changes, including {concise}. Further accepted changes are listed in the achievement field."
        detail = paragraph(baseline.get("error", ""))
        action = "worked on" if attempts or accepted else "scheduled"
        summary = f"Midnight {action} {project['name']} because {paragraph(baseline['why']).rstrip('.')}. {summary_achieved} It made {attempts} local attempt(s); the session ended with {reason.replace('_', ' ')}."
        cloud_attempts=charges.get('cloud_implementation',0) if durable else max(0,state.get("cloud_implementation_count",0)-baseline.get("cloud_implementation_count",0))
        if cloud_attempts:
            summary += f" Exceptional cloud implementation attempts: {cloud_attempts}."
        if detail:
            summary += " Controller detail: " + detail[:500]
        if state.get("pending"):
            summary += " A candidate remains pending independent review."
        recovery=AW.Actions(STATE).session_summary(identity,dt.datetime.fromisoformat(session['started_at']).timestamp(),end.timestamp())
        if recovery:
            summary+=' Recovery record: '+', '.join(f'{count} {key.replace("_"," ")}' for key,count in sorted(recovery.items()))+'.'
        rows.append({"date": session["date"], "session_id": session["id"], "started_at": session["started_at"],
                     "ended_at": end.isoformat(), "mode": session["mode"], "project": identity,
                     "why": baseline["why"], "achieved": achieved, "accepted_count": len(accepted),
                     "attempt_count": attempts, "cloud_implementation_count": cloud_attempts, "schema_version": 2, "outcome": reason, "summary": summary,
                     "evidence": str(Path(project["state_dir"]) / "runs")})
    append_rows(STATE / "sessions.csv", rows)
    committed = [ledger.receipt(session["id"], identity) for identity in session["projects"]]
    if any(row is None for row in committed):
        raise RuntimeError("Session ledger is incomplete after commit")
    outcomes = {row["outcome"] for row in committed}
    ended = max((dt.datetime.fromisoformat(row["ended_at"]) for row in committed), default=end)
    session.update(finished=True, ended_at=ended.isoformat(),
                   outcome=next(iter(outcomes)) if len(outcomes)==1 else reason, rows=committed)
    write(STATE / "sessions" / (session["id"] + ".json"), session)
    if not preserve_current:write(STATE / "session.json", session)


def queue_closing_session(session):
    store=AW.Actions(STATE);closing=store.meta('closing_sessions') or []
    if not any(s['id']==session['id'] for s in closing):
        closing.append(session);store.meta('closing_sessions',closing,write=True)
        write(STATE/'sessions'/(session['id']+'.json'),session)


def close_ready_sessions(data):
    store=AW.Actions(STATE);closing=store.meta('closing_sessions') or [];remaining=[]
    for session in closing:
        owned=[j for j in store.jobs() if j['payload'].get('session')==session['id']]
        live=[]
        for execution in store.executions():
            job=store.get(execution['job'])
            if job and job['payload'].get('session')==session['id'] and PR.live_tree(execution['lease']):live.append(execution)
        if owned or live:remaining.append(session);continue
        finish(session,'daily_checkpoint',data,preserve_current=True)
    if remaining!=closing:store.meta('closing_sessions',remaining,write=True)


def publish(phase, **detail):
    global CONTROLLER_LEASE
    if CONTROLLER_LEASE is None:CONTROLLER_LEASE=PR.lease()
    write(STATE / "status.json", {"updated_at": now().isoformat(), "phase": phase,
        'code_sha256':LOADED_CODE_SHA256,'process':CONTROLLER_LEASE,**detail})


def begin_session(data, timing):
    session = {"id": now().strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8],
               "date": now().astimezone(ZoneInfo(data["timezone"])).date().isoformat(),
               "started_at": now().isoformat(), "mode": timing["mode"], 'durable_actions':True,
               "override": read(STATE / "override.json"), "projects": {}}
    for project in data["projects"]:
        if project["on_schedule"]:
            state = read(Path(project["state_dir"]) / "state.json")
            session["projects"][project["id"]] = {
                "accepted_runs": [a["run"] for a in state.get("accepted", [])],
                "attempts": AS.local_attempt_total(state), "cloud_implementation_count": state.get("cloud_implementation_count",0), "why": project["why"], "project": project}
    write(STATE / "session.json", session)
    return session


def stamp(pid):
    r = PO.run(['/bin/ps', "-p", str(pid), "-o", "lstart="], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def terminate(proc):
    if proc.poll() is None:
        proc.terminate()  # Backend handles SIGTERM, then kills its worker group.
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def effective_limits(instant):
    override = read(STATE / "override.json")
    if not override or (not (override.get('mode')=='continuous' and override.get('expires_at') is None) and instant >= dt.datetime.fromisoformat(override["expires_at"])):
        return {}
    limits = override.get("limits", {})
    if not isinstance(limits, dict) or set(limits) - LIMIT_KEYS or any(v is not None and (type(v) is not int or v <= 0) for v in limits.values()):
        raise ValueError("Invalid temporary override limits")
    return limits


def eligible(project, instant):
    if (STATE/'INSTALLING').exists() or ((STATE/'WEEKDAY_ACTIVATION_PENDING').exists() or (STATE/'WEEKDAY_ACTIVATION_PENDING').is_symlink()):return False
    if COM.snapshot(STATE,instant)['blocks_dispatch']:return False
    if not project["on_schedule"] or (Path(project["state_dir"]) / "PAUSED").exists():
        return False
    if any(job['project']==project['id'] for job in AW.Actions(STATE).jobs()):
        return False
    state = read(Path(project["state_dir"]) / "state.json")
    config = read(Path(project["path"]) / project["config"])
    config.update(effective_limits(instant))
    scope=read(Path(project['state_dir'])/'status.json').get('scope_wait',{})
    if scope and not queue(project,state)['next']:
        key=AW.fingerprint({'why':project.get('why'),'instruction':project.get('instruction'),'source':Q.policy()['project_backend_sha256']})
        try:
            until=dt.datetime.fromisoformat(scope['until'])
            if until.tzinfo and until>instant and scope.get('key')==key:return False
        except (ValueError,KeyError,TypeError):pass
    # The backend resets per-day budgets when it starts on a new local day.
    today = instant.astimezone(ZoneInfo("Australia/Adelaide")).date().isoformat()
    # Evidence admission owns local continuations. Paid phases enforce their
    # own unchanged budgets in the backend; a spent cloud quota cannot stop
    # otherwise eligible local work.
    if state.get("day") == today and config.get('routine_provider')!='local':
        if any(config[limit] is not None and state.get(field, 0) >= config[limit] for field, limit in [
            ("attempts_today", "max_attempts_per_night"), ("cloud_calls_today", "max_cloud_calls_per_night"),
            ("failures_today", "max_failures_per_night")]):
            return False
    cooldown = state.get("cooldown_until")
    return not cooldown or instant >= dt.datetime.fromisoformat(cooldown)


def run_project(project, cutoff, session):
    session['legacy_dispatch']=True
    with MM.task_selection() as descriptor:
        return run_pinned_project(project,cutoff,session,descriptor)


def project_brief(project,worker_model,session):
    return {"project":project['id'],"why":project['why'],"instruction":project['instruction'],
            "worker_model":worker_model,"engineering_policy":engineering_policy(),"worker_quality_policy":Q.policy(),
            "quality_cycle_policy":QC.policy(),"cloud_policy":CP.policy(),"cloud_rescue_policy":CR.policy(),
            "git_delivery_policy":GD.policy(),"action_policy":AW.policy(),
            "quality_profile":read(ROOT/project['quality_profile']) if project.get('quality_profile') else {},
            "github":project.get('github',{}),"cloud_model_descriptor":CP.CloudPolicy(STATE).descriptor(),
            'routine_provider':'local','quality_monitor_policy':QM.policy(),
            "source":"Codex-maintained Midnight project register","session_id":session['id']}


def submit_project(project,cutoff,session):
    # The independently owned action validates capabilities again immediately
    # before product launch. Do not duplicate that cold import in the controller:
    # a slow idle project's admission must not take down other active work.
    with MM.task_selection() as descriptor:
        if GP.matches(descriptor):
            profile=GP.phase_profile(descriptor)
            seconds=profile['worker_timeout_seconds']+profile['supervisor_timeout_seconds']
            cutoff=GP.selection_cutoff(min(cutoff,now()+dt.timedelta(seconds=seconds)))
        else:cutoff=min(cutoff,now()+dt.timedelta(minutes=90))
        return PA.launch(STATE,project,descriptor,project_brief(project,descriptor,session),session['id'],cutoff)


def reconcile_actions():
    """An owned live action is not an orphan just because a controller restarted."""
    store=AW.Actions(STATE)
    for job in store.jobs():
        if job['state']=='queued':
            if job['deadline']<=time.time():store.finish(job['id'],'interrupted',{'reason':'deadline_before_launch'})
            elif time.time()-job['updated']>10:PA.spawn_pending(STATE,job)
            continue
        if PR.alive(job.get('lease')):continue
        if PA.drain(STATE,job['id']):store.finish(job['id'],'interrupted',{'reason':'dead_owned_action'})
    # Also reap an active phase whose job was terminalised by an older process.
    for phase in store.phases():
        job=store.get(phase['job'])
        if not job or job['state'] not in {'queued','running'}:PA.drain(STATE,phase['job'])
    for execution in store.executions():
        job=store.get(execution['job'])
        if not job or job['state'] not in {'queued','running'} or not PR.alive(job.get('lease')):PA.drain(STATE,execution['job'])
    cursor=store.meta('history_cursor') or 0
    for item in store.events(after=cursor):
        if item['kind'] in {'action_succeeded','action_failed','action_interrupted'}:
            project=next((p for p in registry()['projects'] if p['id']==item['project']),None)
            if project:maintain_history(project)
        store.meta('history_cursor',item['sequence'],write=True)


def stop_actions(*,schedule_only=False):
    def targets():
        jobs=AW.Actions(STATE).jobs()
        if schedule_only and not any((STATE/name).exists() or (STATE/name).is_symlink() for name in ('PAUSED','INSTALLING','WEEKDAY_ACTIVATION_PENDING')):
            jobs=[job for job in jobs if not SC.performance_review(job) and not COM.permits(STATE,job,now())]
        return jobs
    for job in targets():
        if job['state']=='queued':
            AW.Actions(STATE).finish(job['id'],'interrupted',{'reason':'control_before_start'});continue
        if PR.alive(job.get('lease')):
            try:os.kill(job['lease']['pid'],signal.SIGTERM)
            except ProcessLookupError:pass
    deadline=time.monotonic()+30
    while targets() and time.monotonic()<deadline:
        reconcile_actions();time.sleep(.25)
    return not targets()


def run_commissioning(data):
    """Dispatch the one retained action; never enter planning/chat/maintenance."""
    grant=COM.snapshot(STATE,now())
    if not grant['active']:return 0
    project=selected(data,grant['project'])[0]
    timing={'mode':'commissioning','override_active':False,
            'cutoff':dt.datetime.fromtimestamp(grant['deadline'],dt.timezone.utc)}
    session=read(STATE/'session.json')
    if not session or session.get('finished') or session.get('commissioning')!=grant['id']:
        if session and not session.get('finished'):queue_closing_session(session)
        session=begin_session(data,timing);session['commissioning']=grant['id'];write(STATE/'session.json',session)
    awake=subprocess.Popen(['/usr/bin/caffeinate','-is','-w',str(os.getpid())])
    try:
        while not STOP:
            if any((STATE/name).exists() or (STATE/name).is_symlink() for name in ('PAUSED','INSTALLING','WEEKDAY_ACTIVATION_PENDING')) or (Path(project['state_dir'])/'PAUSED').exists():break
            grant=COM.snapshot(STATE,now())
            if not grant['active']:break
            if grant['state']=='armed':
                with MM.task_selection() as descriptor:
                    PA.launch(STATE,project,descriptor,project_brief(project,descriptor,session),
                              session['id'],timing['cutoff'],commissioning=True)
            reconcile_actions()
            publish('working',active_projects=[project['id']],session_id=session['id'],
                    commissioning=COM.snapshot(STATE,now()))
            time.sleep(3)
    finally:
        awake.terminate();awake.wait(timeout=10)
        grant=COM.snapshot(STATE,now())
        if not STOP or any((STATE/name).exists() or (STATE/name).is_symlink() for name in ('PAUSED','INSTALLING','WEEKDAY_ACTIVATION_PENDING')):
            if stop_actions(schedule_only=True):
                if not any(j['payload'].get('session')==session['id'] for j in AW.Actions(STATE).jobs()):
                    finish(session,'commissioning_finished',data)
        publish('paused' if (STATE/'PAUSED').exists() else 'waiting_for_midnight',commissioning=grant)
    return 0


def maintenance_preempt():
    if any(p['kind']=='local' and p['state']=='waiting' for p in AW.Actions(STATE).phases()):return True
    return any(eligible(p,now()) for p in registry()['projects'])


def schedule_coordinator():
    """Start a detached coordinator; its own lock serializes long passes."""
    import coordinator as K
    if any((STATE/name).exists() or (STATE/name).is_symlink() for name in ('PAUSED','INSTALLING','WEEKDAY_ACTIVATION_PENDING')):
        return False
    config=K.settings(STATE)
    if config['mode']=='off':return False
    state=read(STATE/'maintenance-cadence.json',{})
    instant=now().timestamp()
    if instant-state.get('coordinator',0)<config['cadence_seconds']:return False
    directory=STATE/'coordinator';directory.mkdir(parents=True,exist_ok=True)
    with (directory/'coordinator.log').open('ab') as log:
        subprocess.Popen([PYTHON,'-B',str(ROOT/'coordinator.py'),'--root',str(ROOT)],
            stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,cwd=ROOT)
    state['coordinator']=instant;write(STATE/'maintenance-cadence.json',state)
    return True


def coordinator_command(args, data):
    import coordinator as K
    if args.mode:
        K.set_mode(STATE,args.mode);owner_action('coordinator_mode',{'mode':args.mode},'cli')
    if args.overrule:
        result=K.overrule(ROOT,args.overrule,registry=data)
        owner_action('overrule_decision',result,'cli')
    if args.run:
        owner_action('coordinator_run',{'mode':K.settings(STATE)['mode']},'cli')
        K.run(ROOT,registry=data)
    return K.status(ROOT,registry=data)


def routine_maintenance(data,cutoff,*,spare=False):
    """Discovery/probes are due work, never a prerequisite for every dispatch."""
    if any((STATE/name).exists() or (STATE/name).is_symlink() for name in ('PAUSED','INSTALLING','WEEKDAY_ACTIVATION_PENDING')) or COM.snapshot(STATE,now())['blocks_dispatch']:return
    state=read(STATE/'maintenance-cadence.json',{})
    instant=now().timestamp()
    if instant-state.get('models',0)>=3600 and not any(p['kind']=='local' for p in AW.Actions(STATE).phases()):
        state['models']=instant;write(STATE/'maintenance-cadence.json',state)
        try:
            with PR.resource(STATE,'local',time.time()+.05,stopped=lambda:STOP or (STATE/'PAUSED').exists()):
                maintain_models(data,cutoff)
        except PR.PhaseInterrupted:pass
    if not spare and not maintenance_preempt() and instant-state.get('cloud',0)>=600:
        state['cloud']=instant;write(STATE/'maintenance-cadence.json',state);maintain_cloud(data,cutoff)


def run_pinned_project(project, cutoff, session,worker_model):
    validate_backend(project)
    import shutil
    if shutil.disk_usage(STATE).free < 5*1024**3:
        publish('low_disk_hold',project=project['id'],detail='Preserving evidence; fewer than 5 GiB free')
        return 75
    identity = project["id"]
    directory = Path(project["state_dir"])
    if identity not in session["projects"]:
        state = read(directory / "state.json")
        session["projects"][identity] = {"accepted_runs": [a["run"] for a in state.get("accepted", [])],
            "attempts": AS.local_attempt_total(state), "cloud_implementation_count": state.get("cloud_implementation_count",0), "why": project["why"], "project": project}
        write(STATE / "session.json", session)
    log = STATE / "sessions" / session["id"] / (identity + ".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    brief = STATE / "instructions" / (identity + ".json")
    write(brief,project_brief(project,worker_model,session))
    command = [PYTHON, str(Path(project["path"]) / project["controller"]),
               "managed-once", "--until", cutoff.isoformat(), "--brief", str(brief)]
    if effective_limits(now()):
        command += ["--limits", str(STATE / "override.json")]
    with log.open("a") as output:
        env = os.environ.copy()
        env['MIDNIGHT_SHARED_ROOT'] = str(ROOT)
        env['MIDNIGHT_LOCAL_CODE_BIN'] = str(ROOT / 'local_worker.py')
        env['MIDNIGHT_WORKER_DESCRIPTOR']=json.dumps(worker_model)
        proc = subprocess.Popen(command, env=env,
                                cwd=project["path"], stdout=output, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)
        write(STATE / "active-child.json", {"pid": proc.pid, "stamp": stamp(proc.pid), "boot_id": D.boot_id(), "project": identity})
        begin = time.monotonic()
        while proc.poll() is None:
            if STOP or (STATE / "PAUSED").exists() or now() >= cutoff:
                terminate(proc)
                break
            current = window(now(), registry(), read(STATE / "override.json"))
            if not current["active"]:
                terminate(proc)
                break
            publish("working", project=identity, session_id=session["id"], mode=current["mode"],
                    cutoff=current["cutoff"].isoformat(), elapsed_seconds=round(time.monotonic() - begin))
            if time.monotonic() - begin > 5400:
                terminate(proc)
                session["projects"][identity]["error"] = "Backend exceeded the bounded cycle deadline"
                break
            time.sleep(1)
        code = proc.wait(timeout=5)
    write(STATE / "active-child.json", {})
    if code != 0:
        session["projects"][identity]["error"] = f"Backend exit {code}; inspect {log}"
    write(STATE / "session.json", session)
    return code


def maintain_models(data, cutoff):
    """One recoverable qualification step at a worker boundary, never on status."""
    if 'unittest' in sys.modules:
        raise RuntimeError('Tests must mock model maintenance; live models are forbidden in tests')
    if cutoff-now()<dt.timedelta(minutes=5):return
    log=STATE/'models'/'maintenance.log';log.parent.mkdir(mode=0o700,exist_ok=True)
    token=uuid.uuid4().hex
    write(STATE/'model-maintenance.json',{'token':token,'controller_pid':os.getpid(),
        'controller_stamp':stamp(os.getpid()),'boot_id':D.boot_id(),'cutoff':cutoff.isoformat()})
    with log.open('a') as output:
        proc=subprocess.Popen([PYTHON,'-B',str(ROOT/'model_manager.py'),'--managed-token',token],
            stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        write(STATE/'active-child.json',{'pid':proc.pid,'stamp':stamp(proc.pid),'boot_id':D.boot_id(),'project':'__model_maintenance__'})
        started=time.monotonic()
        while proc.poll() is None:
            current=window(now(),registry(),read(STATE/'override.json'))
            if STOP or (STATE/'PAUSED').exists() or not current['active'] or now()>=cutoff or time.monotonic()-started>900 or maintenance_preempt():
                terminate(proc);break
            publish('model_maintenance',detail='Discovery or one serialized local qualification step',cutoff=cutoff.isoformat())
            time.sleep(1)
        proc.wait(timeout=10)
    write(STATE/'active-child.json',{})


def maintain_cloud(data, cutoff):
    """Account/model maintenance runs in an interruptible owned child at a boundary."""
    if 'unittest' in sys.modules:raise RuntimeError('Tests must mock cloud maintenance')
    current=window(now(),registry(),read(STATE/'override.json'))
    if STOP or (STATE/'PAUSED').exists() or not current['active'] or now()>=cutoff:return
    cutoff=min(cutoff,current['cutoff'])
    log=STATE/'cloud-maintenance.log'
    with log.open('a') as output:
        proc=subprocess.Popen([PYTHON,'-B',str(ROOT/'cloud_maintenance_entry.py'),str(STATE),cutoff.isoformat()],
            stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        write(STATE/'active-child.json',{'pid':proc.pid,'stamp':stamp(proc.pid),'boot_id':D.boot_id(),'project':'__cloud_maintenance__'})
        started=time.monotonic()
        while proc.poll() is None:
            current=window(now(),registry(),read(STATE/'override.json'))
            if STOP or (STATE/'PAUSED').exists() or not current['active'] or now()>=min(cutoff,current['cutoff']) or time.monotonic()-started>900 or maintenance_preempt():
                terminate(proc);break
            publish('cloud_model_maintenance',detail='Official release discovery and bounded strongest-profile qualification')
            time.sleep(1)
        proc.wait(timeout=10)
    write(STATE/'active-child.json',{})


def tick():
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE, 0o700)
    with (STATE / "controller.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        data = registry()
        while ((STATE/'WEEKDAY_ACTIVATION_PENDING').exists() or (STATE/'WEEKDAY_ACTIVATION_PENDING').is_symlink()):
            drained=stop_actions()
            publish('paused' if (STATE/'PAUSED').exists() else 'activation_pending',
                    detail='Reviewed weekday activation is held until settings and campaign reconcile',drained=drained)
            if STOP:return 0
            time.sleep(3)
        if STOP:return 0
        data=registry()
        try:
            repair_controller_state()
        except (OSError, ValueError) as exc:
            publish("needs_attention", detail="Runtime recovery unavailable: " + str(exc))
            return 1
        child = read(STATE / "active-child.json")
        if child.get("boot_id") == D.boot_id() and child.get("stamp") and stamp(child["pid"]) == child["stamp"]:
            os.kill(child["pid"], signal.SIGTERM)
            for _ in range(16):
                if stamp(child["pid"]) != child["stamp"]:
                    break
                time.sleep(1)
            else:
                publish("needs_attention", detail="Prior backend has not stopped; preserved for recovery")
                return 1
        write(STATE / "active-child.json", {})
        AW.Actions(STATE).recover()
        reconcile_actions()
        close_ready_sessions(data)
        commission=COM.snapshot(STATE,now())
        recovered = recover_projects(data,commission['project']) if commission['active'] else recover_projects(data)
        if any(r["returncode"] != 0 for r in recovered):
            publish("needs_attention", detail="Project recovery is busy or requires inspection", recovery=recovered)
            return 0 if all(r["returncode"] in (0, 75) for r in recovered) else 1
        session = read(STATE / "session.json")
        timing = window(now(), data, read(STATE / "override.json"))
        paused = (STATE / "PAUSED").exists()
        if not paused and not (STATE/'INSTALLING').exists() and commission['active']:
            return run_commissioning(data)
        if not timing["active"] or paused or (STATE/'INSTALLING').exists() or commission['blocks_dispatch']:
            drained=stop_actions(schedule_only=not paused and not (STATE/'INSTALLING').exists())
            if session and not session.get("finished"):
                if drained:
                    close_ready_sessions(data);finish(session, "paused" if paused else "window_ended", data)
                else:publish('needs_attention',detail='Session retained until owned processes finish draining')
            publish("paused" if paused else "waiting_for_midnight")
            return 0
        if not session or session.get("finished"):
            session = begin_session(data, timing)
        awake = subprocess.Popen(["/usr/bin/caffeinate", "-is", "-w", str(os.getpid())])
        reason = "window_ended"
        try:
            while not STOP and not (STATE / "PAUSED").exists():
                if ((STATE/'WEEKDAY_ACTIVATION_PENDING').exists() or (STATE/'WEEKDAY_ACTIVATION_PENDING').is_symlink()):
                    reason='activation_pending';break
                if COM.snapshot(STATE,now())['blocks_dispatch']:
                    reason='commissioning_pending';break
                data = registry()
                timing = window(now(), data, read(STATE / "override.json"))
                if not timing["active"]:
                    break
                if session["date"] != now().astimezone(ZoneInfo(data["timezone"])).date().isoformat():
                    queue_closing_session(session)
                    session = begin_session(data, timing)
                    close_ready_sessions(data)
                if timing["override_active"]:
                    if session["mode"] == "scheduled":
                        session["mode"] = "scheduled+override"
                    session["override"] = read(STATE / "override.json")
                    write(STATE / "session.json", session)
                import shutil
                if shutil.disk_usage(STATE).free<5*1024**3:
                    publish('low_disk_hold',detail='Preserving evidence; fewer than 5 GiB free')
                    time.sleep(10);continue
                reconcile_actions()
                close_ready_sessions(data)
                timing=window(now(),registry(),read(STATE/'override.json'))
                if STOP or (STATE/'PAUSED').exists() or not timing['active']:break
                jobs=AW.Actions(STATE).jobs()
                publish('working' if jobs else 'waiting_for_project',
                        active_projects=[j['project'] for j in jobs],session_id=session['id'])
                available = fair_projects([p for p in data["projects"] if eligible(p, now())])
                try:
                    import agent_discussion as AD
                    product_work=bool(available or any(j['project'] in {p['id'] for p in data['projects']} for j in jobs))
                    AD.schedule(ROOT,data,now(),timing['cutoff'],session['id'],product_work=product_work)
                    schedule_coordinator()
                except Exception as exc:
                    write(STATE/'agent-workspace-health.json',{'at':now().isoformat(),'state':'unavailable','reason':type(exc).__name__})
                if not available:
                    jobs=AW.Actions(STATE).jobs()
                    if jobs:
                        publish('working',active_projects=[j['project'] for j in jobs],session_id=session['id'])
                        routine_maintenance(data,timing['cutoff'],spare=True)
                    else:
                        publish("waiting_for_project", session_id=session["id"])
                        routine_maintenance(data,timing['cutoff'])
                    time.sleep(3)
                    continue
                for project in available:
                    timing = window(now(), data, read(STATE / "override.json"))
                    if STOP or (STATE / "PAUSED").exists() or not timing["active"]:
                        break
                    action=submit_project(project, timing["cutoff"], session)
                    if action:write(STATE/"fairness.json",{"last_project":project["id"]})
                    # Avoid rapid retries if an external backend lock is occupied.
                    time.sleep(3)
            if STOP:
                reason = "controller_stopped"
            elif (STATE / "PAUSED").exists():
                reason = "paused"
        except Exception as exc:
            reason = "controller_error"
            publish("needs_attention", detail=str(exc))
            raise
        finally:
            awake.terminate()
            awake.wait(timeout=10)
            if reason in {'paused','window_ended','activation_pending'} or not window(now(),data,read(STATE/'override.json'))['active']:
                if stop_actions(schedule_only=reason!='paused'):
                    close_ready_sessions(data);finish(session, reason, data)
                else:publish('needs_attention',detail='Session retained until owned processes finish draining')
            elif not AW.Actions(STATE).jobs():
                finish(session, reason, data)
        publish("paused" if reason == "paused" else "waiting_for_midnight", last_session=session["id"])
    return 0


def kick():
    r = subprocess.run(["/bin/launchctl", "kickstart", f"gui/{os.getuid()}/{LABEL}"], capture_output=True, text=True)
    return r.returncode == 0


def repair_controller_state():
    for name in ("session.json", "override.json"):
        D.read_json(STATE / name, repair=True)
    if D.read_bytes(STATE / "sessions.csv", None) is not None:
        csv_rows(D.read_bytes(STATE / "sessions.csv"))
        D.read_bytes(STATE / "sessions.csv", repair=True)


def power_observation():
    result = {"automatic_restart": None, "filevault": "unknown", "automatic_login": None}
    try:
        text = subprocess.check_output(["/usr/bin/pmset", "-g", "custom"], text=True, timeout=5)
        match = re.search(r"^\s*autorestart\s+(\d+)\s*$", text, re.M)
        result["automatic_restart"] = bool(int(match[1])) if match else None
        text = subprocess.check_output(["/usr/bin/fdesetup", "status"], text=True, timeout=5)
        result["filevault"] = "on" if "FileVault is On" in text else "off" if "FileVault is Off" in text else "unknown"
        result["automatic_login"] = bool(plistlib.loads(Path("/Library/Preferences/com.apple.loginwindow.plist").read_bytes()).get("autoLoginUser"))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return result


def doctor(data, identity=None):
    root = [D.health(STATE / n) for n in ("session.json", "override.json", "sessions.csv")]
    projects = [{"id": p["id"], "checkpoints": [D.health(Path(p["state_dir"]) / n)
                for n in ("state.json", "promotion-intent.json")]} for p in selected(data, identity)]
    rows = root + [c for p in projects for c in p["checkpoints"]]
    workflow=AW.observe(STATE)
    return {"checked_at": now().isoformat(), "boot_id": D.boot_id(), "service": service(),
            "power": power_observation(), "controller_checkpoints": root, "projects": projects,
            'guardian':workflow.get('guardian',{}),'workflow_available':workflow.get('available'),
            'workflow_error':workflow.get('error'),'guardian_error':read(STATE/'guardian-error.json'),
            "status": "needs_attention" if workflow.get('available') is False or any(c["status"] == "unavailable" for c in rows) else
                      "repair_needed" if any(c["status"] == "repair_needed" for c in rows) else "healthy",
            "cold_boot_requirement": "With FileVault enabled, unlock and log in before the user LaunchAgent and model services resume."}


def recover_runtime(data, identity=None):
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / "controller.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy", "detail": "The active controller owns recovery; no worker was interrupted."}
        AW.Actions(STATE).recover()
        if any(j['project'] in {p['id'] for p in selected(data,identity)} for j in AW.Actions(STATE).jobs()):
            return {'status':'busy','detail':'Owned project actions remain active; no worker was interrupted.'}
        try:
            repair_controller_state()
        except (ValueError, OSError) as exc:
            return {"status": "needs_attention", "detail": "Checkpoint recovery unavailable: " + str(exc)}
        results = recover_projects(data, identity)
        return {"status": "recovered" if all(r["returncode"] == 0 for r in results) else "needs_attention", "projects": results}


def recover_projects(data, identity=None):
    results = []
    for project in selected(data, identity):
        owned=next((j for j in AW.Actions(STATE).jobs() if j['project']==project['id']),None)
        if owned:
            results.append({'project':project['id'],'returncode':0,'detail':'Owned durable action retained','error':''})
            continue
        if project.get('on_schedule') is False and not Path(project.get('path','')).is_dir():
            # An archived or relocated unscheduled project cannot block the
            # scheduled ones. Its registry entry and history stay untouched.
            results.append({'project':project['id'],'returncode':0,'error':'',
                            'detail':'Unscheduled project directory is absent; recovery skipped'})
            continue
        try:
            validate_backend(project)
            proc = subprocess.run([PYTHON, str(Path(project["path"]) / project["controller"]), "recover"],
                                  cwd=project["path"], capture_output=True, text=True, timeout=30,
                                  env=dict(os.environ,MIDNIGHT_SHARED_ROOT=str(ROOT)))
            results.append({"project": project["id"], "returncode": proc.returncode,
                            "detail": proc.stdout[-3000:], "error": proc.stderr[-1000:]})
        except (OSError, subprocess.TimeoutExpired) as exc:
            results.append({"project": project["id"], "returncode": 1, "detail": "", "error": type(exc).__name__})
    return results


def validate_backend(project):
    if type(project.get('backend_protocol')) is not int or project['backend_protocol'] != Q.BACKEND_PROTOCOL:
        raise ValueError('Backend protocol declaration is required: '+project['id'])
    proc = subprocess.run([PYTHON, str(Path(project['path'])/project['controller']), 'capabilities'],
                          cwd=project['path'], capture_output=True, text=True, timeout=10,
                          env=dict(os.environ,MIDNIGHT_SHARED_ROOT=str(ROOT)))
    try:
        if len(proc.stdout.encode()) > 16000:
            raise ValueError('Oversized capabilities')
        value = json.loads(proc.stdout)
    except ValueError:
        raise ValueError('Backend capabilities unavailable: '+project['id']) from None
    if (proc.returncode != 0 or not isinstance(value, dict) or value.get('backend_protocol') != Q.BACKEND_PROTOCOL
            or value.get('worker_quality_policy') != Q.policy() or value.get('recovery') != 'model-free'
            or value.get('worker_model_contract')!=Q.model_contract()
            or value.get('quality_cycle_policy')!=QC.policy() or value.get('cloud_policy')!=CP.policy()
            or value.get('cloud_rescue_policy')!=CR.policy() or value.get('git_delivery_policy')!=GD.policy()
            or value.get('action_policy')!=AW.policy()):
        raise ValueError('Backend must adopt current shared Midnight quality and recovery controls: '+project['id'])


def render(view, detailed=False):
    from operation_status import describe
    operation=view.get('operation') or describe(view)
    print(operation['answer']+' — '+operation['headline'])
    print('What is happening: '+operation['happening'])
    print(operation['next_run_label']+': '+str(operation['next_run_at']))
    if operation['next_run_blocked']:
        print('Work remains subject to the displayed hold and admission controls.')
    days = view['schedule'].get('weekdays')
    day_label = ', '.join(day.title() for day in days) if days and len(days) < 7 else 'every day'
    print(f"Schedule: {view['schedule']['start']}–{view['schedule']['end']} {view['timezone']}, {day_label}")
    if view.get('readiness'):
        print('Next launch observation: ' + view['readiness']['summary'])
    print(f"Scheduler loaded: {view['service']['loaded']}; checked {view['checked_at']}")
    print(f"Next scheduled start: {view['next_start']}")
    if view.get('run_mode')=='continuous':
        print('Operating mode: 24/7 — no expiry. Saved schedule: 00:00–09:00 Adelaide.')
    elif view["override_active"]:
        print("Override expires: " + view["override"]["expires_at"])
    print()
    for p in view["projects"]:
        q = p["queue"] or {}
        print(f"{p['name']} [{p['id']}] — {'on schedule' if p['on_schedule'] else 'off schedule'}; {p['phase']}")
        print(f"  Progress: {p.get('accepted_total',len(p['accepted']))} accepted; {q.get('remaining', '?')} known tasks remaining; {len(q.get('exhausted', []))} exhausted")
        print(f"  Today: {p['attempts_today']} local attempts, {p['cloud_calls_today']} cloud calls, {p['failures_today']} failed attempts")
        if p.get('productivity'):
            from productive_status import summary as productive_summary
            print('  Productive progress: ' + productive_summary(p['productivity']))
        if p["accepted"]:
            a = p["accepted"][-1]
            print(f"  Last accepted: {a['id']} — {a['title']} ({a['commit'][:12]})")
        if q.get("next"):
            print("  Next eligible: " + q["next"][0]["id"] + " — " + q["next"][0]["title"])
        if p["pending_review"]:
            print("  Pending review: " + p["pending_review"])
        print(f"  Last project observation: {p['observed_at'] or 'unavailable'}")
        if detailed:
            print("  Why: " + p["why"])
            print("  Instruction: " + p["instruction"])
            print(f"  Models: {p['local_model']} / {p['cloud_model']}")
            for identity, message in list(p["recent_feedback"].items())[-5:]:
                print("  Feedback " + identity + ": " + paragraph(message)[:500])
            print("  Evidence: " + p["evidence"])
        for error in p["errors"]:
            print("  Observation error: " + error)
        print()
    print("Session CSV: " + view["ledger"])
    if detailed and view["recent_sessions"]:
        print("\nRecent completed sessions:")
        for row in view["recent_sessions"][-3:]:
            print(f"  {row['date']} [{row['mode']}]: {row['summary']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    for command in ("status", "projects", "report", "performance", "doctor", "recover", "models", "quality", "findings", "improvement-history", "rescue",'incidents','recovery-history','repair-status'):
        p = sub.add_parser(command)
        p.add_argument("project", nargs="?")
        p.add_argument("--json", action="store_true")
        if command == "performance":
            p.add_argument("--since", help="ISO timestamp; defaults to this override or the last 24 hours")
            p.add_argument("--until", help="ISO timestamp; defaults to now or the expired override's endpoint")
        if command == 'models':
            controls=p.add_mutually_exclusive_group()
            controls.add_argument('--trial',choices=['glm'],help='Enable an explicit experimental real-work provider trial')
            controls.add_argument('--end-trial',action='store_true',help='Return new task selection to the retained incumbent')
            controls.add_argument('--default',dest='owner_default',choices=['glm'],help='Use the owner-selected GLM default until explicitly changed')
            controls.add_argument('--restore-incumbent',action='store_true',help='Restore the retained qualified incumbent for new work')
            controls.add_argument('--pin',action='store_true',help='Pin the current verified local worker: no automatic qualification may replace it')
            controls.add_argument('--unpin',action='store_true',help='Release the owner pin and resume automatic qualification')
            p.add_argument('--provider-config',help='Reviewed local provider configuration; requires --trial or --default')
            p.add_argument('--until',dest='trial_until',help='Absolute trial expiry, Adelaide if no offset; at most 24 hours')
    sub.add_parser("tick", help="Installed scheduler entry point")
    p = sub.add_parser("dashboard", help="Serve the read-only dashboard on this Mac")
    p.add_argument("--port", type=int, default=8766)
    for command in ("pause", "resume"):
        p = sub.add_parser(command)
        p.add_argument("project", nargs="?", help="Omit to control the whole Midnight pipeline")
    p = sub.add_parser("override", help="Inspect, set or cancel an expiring override")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--until", help="ISO date/time; local Adelaide time if offset omitted")
    group.add_argument("--for", dest="duration", help="Duration such as 2h or 30m")
    group.add_argument("--cancel", action="store_true")
    p.add_argument("--reason", default="Manual override via Midnight CLI")
    p.add_argument("--continuous", action="store_true", help="Use an expiring extended workload allowance")
    p=sub.add_parser('mode',help='Select persistent 24/7 operation or the saved schedule')
    p.add_argument('value',nargs='?',choices=['24x7','scheduled'])
    p=sub.add_parser('commissioning',help='Inspect or explicitly activate one retained action, at most nine hours')
    p.add_argument('--activate',action='store_true')
    p.add_argument('--project');p.add_argument('--task');p.add_argument('--until');p.add_argument('--reason')
    p.add_argument('--scope',help='Exact retained recovery scope when the task is a saved manager continuation')
    p=sub.add_parser('chat',help='Inspect or operate the private persistent agent workspace')
    p.add_argument('arguments',nargs=argparse.REMAINDER)
    p=sub.add_parser('cloud',help='Show or choose the main cloud model; the other provider is the fallback')
    p.add_argument('--primary',choices=['claude','codex'])
    p=sub.add_parser('oversight',help='Read the same evidence and explanations as the dashboard')
    p.add_argument('--json',action='store_true')
    p=sub.add_parser('tasks',help='Restore a coordinator-retired task')
    p.add_argument('--project',required=True);p.add_argument('--unretire',required=True)
    p=sub.add_parser('coordinator',help='Inspect and control automated escalation decisions')
    p.add_argument('--run',action='store_true')
    p.add_argument('--mode',choices=['shadow','live','off'])
    p.add_argument('--overrule')
    p=sub.add_parser('reconcile-merge',help='Record a repair the owner merged outside Midnight delivery')
    p.add_argument('project');p.add_argument('--task',required=True);p.add_argument('--commit',required=True)
    p=sub.add_parser('scrum',help='Read the daily Adelaide scrum schedule and history')
    p.add_argument('--json',action='store_true')
    args = parser.parse_args()
    data = registry()
    command = args.command or "status"
    if command=='oversight':
        import oversight
        value=oversight.collect()
        if args.json:print(json.dumps(value,indent=2))
        else:
            print(value['operation']['happening'])
            for project in value['projects']:
                print(project['name']+': '+project['explain'])
                for task in project['tasks']:print('  '+task['id']+' — '+task['state']+': '+task['explain'])
        return 0
    if command=='tasks':
        print(json.dumps(owner_control('unretire_task',{'project':args.project,'task':args.unretire},data=data),indent=2));return 0
    if command=='coordinator':
        print(json.dumps(coordinator_command(args,data),indent=2));return 0
    if command=='cloud':
        manager=CP.CloudPolicy(STATE)
        if args.primary:
            owner_control('set_primary_provider',{'primary':args.primary},data=data)
        print(json.dumps(manager.provider_snapshot(),indent=2));return 0
    if command=='reconcile-merge':
        project=selected(data,args.project)[0]
        proc=subprocess.run([PYTHON,str(Path(project['path'])/project['controller']),'reconcile-merge','--task',args.task,'--commit',args.commit],
                            cwd=project['path'],capture_output=True,text=True,timeout=300,env=dict(os.environ,MIDNIGHT_SHARED_ROOT=str(ROOT)))
        sys.stdout.write(proc.stdout);sys.stderr.write(proc.stderr[-2000:])
        return proc.returncode
    if command=='commissioning':
        if args.activate:
            if not all((args.project,args.task,args.until,args.reason)):
                raise ValueError('Activation requires --project, --task, --until and --reason')
            instant=now();deadline=parse_expiry(args.until,instant,data['timezone'])
            base=window(instant,data)
            if not base['scheduled_active']:
                # Commissioning dispatches only inside the base schedule. Activated
                # outside it, the one-use grant was consumed with no attempt and
                # blocked dispatch until the next window. Refuse before writing it.
                raise ValueError('Commissioning runs only inside the base schedule window; activate it after '+base['next_start'].isoformat())
            with PR.source_lock(STATE):
                with MM.task_selection() as descriptor:
                    result=COM.activate(STATE,data,args.project,args.task,deadline,PA.source_head(),descriptor,args.reason,instant,scope_id=args.scope)
            kick()
        else:
            if any((args.project,args.task,args.until,args.reason,args.scope)):raise ValueError('Grant fields require --activate')
            result=COM.snapshot(STATE,now())
        print(json.dumps(result,indent=2));return 0
    if command=='chat':
        from agent_workspace_cli import run as chat_cli
        return chat_cli(args.arguments,STATE/'agent-workspace',data['projects'])
    if command=='scrum':
        from agent_workspace import Workspace
        print(json.dumps(Workspace(STATE/'agent-workspace').overview(),indent=2));return 0
    if command == "dashboard":
        from dashboard import serve
        return serve(args.port)
    if command == "tick":
        return tick()
    if command in {'quality','findings','improvement-history','rescue'}:
        values=[improvement_snapshot(p,command) for p in selected(data,args.project)]
        print(json.dumps({'schema_version':1,'projects':values},indent=2));return 0
    if command == 'models':
        if args.provider_config and not (args.trial or args.owner_default):raise ValueError('--provider-config requires an explicit --trial or --default')
        if args.trial_until and not args.trial:raise ValueError('--until requires an explicit --trial')
        if args.trial or args.owner_default:
            with MM.transaction():
                if args.provider_config:GP.configure(json.loads(Path(args.provider_config).read_text()))
                if args.owner_default:GP.enable_default()
                else:
                    expiry=parse_expiry(args.trial_until,now(),data['timezone']) if args.trial_until else now()+dt.timedelta(hours=24)
                    GP.enable(expiry)
        elif args.end_trial or args.restore_incumbent:
            with MM.transaction():GP.disable()
        elif args.pin:MM.pin()
        elif args.unpin:MM.unpin()
        print(json.dumps(MM.snapshot(),indent=2));return 0
    if command=='repair-status':
        values=[{'project':p['id'],**RP.Recovery(p['state_dir'],p['id'],evidence_roots=[p['path'],*RP.certificate_roots(STATE)]).snapshot()} for p in selected(data,args.project)]
        print(json.dumps({'schema_version':1,'projects':values},indent=2));return 0
    if command in {'incidents','recovery-history'}:
        store=AW.Actions(STATE)
        rows=store.incidents() if command=='incidents' else store.events(project=args.project)
        if command=='incidents':rows=[{k:r[k] for k in ('id','project','code','state','created','updated','attempts')} for r in rows if not args.project or r['project']==args.project]
        print(json.dumps({'schema_version':1,'items':rows},indent=2));return 0
    if command in {"doctor", "recover"}:
        value = doctor(data, args.project) if command == "doctor" else recover_runtime(data, args.project)
        print(json.dumps(value, indent=2))
        return 75 if value["status"] == "busy" else 1 if value["status"] == "needs_attention" else 0
    if command == "performance":
        from performance import collect, render_performance
        override = read(STATE / "override.json")
        since = dt.datetime.fromisoformat(args.since) if args.since else (
            dt.datetime.fromisoformat(override["created_at"]) if override else now() - dt.timedelta(hours=24))
        if since.tzinfo is None:
            since = since.replace(tzinfo=ZoneInfo(data["timezone"]))
        end = dt.datetime.fromisoformat(args.until) if args.until else min(
            now(), dt.datetime.fromisoformat(override["expires_at"])) if override and override.get('expires_at') else now()
        if end.tzinfo is None:
            end = end.replace(tzinfo=ZoneInfo(data["timezone"]))
        if end < since:
            raise ValueError("Performance endpoint must not precede its start")
        value = collect(selected(data, args.project), since, end)
        if args.json:
            print(json.dumps(value, indent=2))
        else:
            render_performance(value)
        return 0
    if command in {"status", "projects", "report"}:
        view = snapshot(data, getattr(args, "project", None))
        if getattr(args, "json", False):
            print(json.dumps(view, indent=2))
        else:
            render(view, detailed=command == "report")
        return 0
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if command in {"pause", "resume"}:
        set_pause(command,data=data,project=args.project)
        print(f"{args.project or 'Midnight'} {command} requested; active workers observe controls within a few seconds.")
        return 0
    if command=='mode':
        if args.value=='24x7':
            write(STATE/'override.json',{'mode':'continuous','created_at':now().isoformat(),'expires_at':None,
                'reason':'Owner selected continuous development until manually changed','limits':dict(CONTINUOUS_LIMITS)})
            kick()
        elif args.value=='scheduled':
            D.delete(STATE/'override.json');kick()
        value=read(STATE/'override.json');timing=window(now(),data,value)
        print(json.dumps({'mode':timing['mode'],'label':'24/7 — no expiry' if timing['mode']=='continuous' else 'Saved schedule',
            'expires_at':None if timing['mode']=='continuous' else (value or {}).get('expires_at'),'saved_schedule':data['schedule'],
            'paused':(STATE/'PAUSED').exists()},indent=2))
        return 0
    if command == "override":
        if args.cancel:
            D.delete(STATE / "override.json")
            print("Override cancelled; the normal midnight–09:00 schedule applies.")
        elif args.until or args.duration:
            instant = now()
            if args.until:
                expiry = parse_expiry(args.until, instant, data["timezone"])
            else:
                match = re.fullmatch(r"([1-9][0-9]*)([mh])", args.duration)
                if not match:
                    raise ValueError("Use a positive duration such as 30m or 2h")
                expiry = instant + dt.timedelta(seconds=int(match[1]) * (60 if match[2] == "m" else 3600))
            value = {"created_at": instant.isoformat(), "expires_at": expiry.isoformat(), "reason": paragraph(args.reason)}
            if args.continuous:
                value["limits"] = dict(CONTINUOUS_LIMITS)
            write(STATE / "override.json", value)
            started = kick()
            print(f"Override saved until {expiry.astimezone(ZoneInfo(data['timezone'])).isoformat()}; scheduler signalled: {started}.")
            print("Any pause remains in force. At expiry the normal schedule applies automatically.")
        else:
            value = read(STATE / "override.json")
            print(json.dumps({"active": window(now(), data, value)["override_active"], "override": value}, indent=2))
        return 0


def stopping(_sig, _frame):
    global STOP
    STOP = True


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stopping)
    signal.signal(signal.SIGINT, stopping)
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError) as exc:
        print("Midnight: " + str(exc), file=sys.stderr)
        sys.exit(2)
