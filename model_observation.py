"""File-only model comparison observations; no discovery or inference."""
import datetime as dt
import json
from pathlib import Path
import re
import model_manager as M
import durability as D
import colibri_observation as C

SUMMARY_FIELDS=('case','side','passed','seconds','stop_reason','host_error','tool_calls','peak_model_bytes','peak_pressure','swap_growth_mib','at')

def selection_snapshot():
    view=M.snapshot()
    view['selected_next']=view.get('active')
    view['active_field_scope']='selection for new actions; not native residency'
    view['native_activity']=C.snapshot(M.RUNTIME.parent)
    return view


def snapshot():
    view=selection_snapshot()
    try:
        state=M.load();comparison=state.get('comparison')
        operation=state.get('operation') or {}
        if comparison:
            rows=[{k:r.get(k) for k in SUMMARY_FIELDS} for r in comparison.get('results',[])]
            view['probe_results']=rows
            phase=operation.get('phase')
            if phase=='acquiring':phase='qualification_queued'
            elif phase=='between_probes':phase='qualification_queued'
            view['display_phase']=phase or 'qualification_queued'
            view['scores']={side:{'completed':sum(r.get('side')==side for r in rows),
                'passed':sum(r.get('side')==side and r.get('passed') is True for r in rows),
                'seconds':round(sum(r.get('seconds',0) or 0 for r in rows if r.get('side')==side),2)} for side in ('incumbent','challenger')}
        else:
            view['probe_results']=[];view['scores']={}
            view['display_phase']='maintenance_backoff' if state.get('error') else 'incumbent_retained'
        # Persistent helper can observe a transfer before a comparison is created.
        if operation.get('phase')=='acquiring' and not comparison:
            checked=dt.datetime.fromisoformat(operation['at'])
            if (dt.datetime.now(dt.timezone.utc)-checked).total_seconds()<900:view['display_phase']='acquiring'
        if view.get('selection_kind') in {'owner_trial','owner_default','owner_pinned'}:
            view['display_phase']=view['selection_kind']+'_selected'
        view['qualification_history']=[{'key':k,**v} for k,v in state.get('evaluated',{}).items()][-30:]
        view['checkpoint_health']={name:D.health(M.RUNTIME/name) for name in ('model-state.json','model-probe.json')}
    except (OSError,ValueError,KeyError,TypeError) as exc:
        view['observation_error']='Detailed model evidence unavailable: '+type(exc).__name__
    return view

def inspect_model(url):
    from urllib.parse import parse_qs,urlsplit
    parsed=urlsplit(url);query=parse_qs(parsed.query,strict_parsing=True)
    if parsed.path!='/api/model-comparison' or set(query)!={'id'} or len(query['id'])!=1:raise ValueError('Invalid model comparison query')
    identity=query['id'][0]
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}',identity):raise ValueError('Invalid model comparison identity')
    root=M.RUNTIME.resolve();folder=M.RUNTIME/'comparisons'/identity
    for item in (M.RUNTIME,M.RUNTIME/'comparisons',folder):
        if item.is_symlink():raise ValueError('Unsafe model evidence root')
    if not folder.is_dir() or not folder.resolve().is_relative_to(root):raise ValueError('Comparison unavailable')
    records={};errors=[]
    def receipt(path):
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file() or path.stat().st_size>512000:
            errors.append(path.name+': unavailable');return None
        try:return json.loads(path.read_text())
        except (OSError,ValueError):errors.append(path.name+': invalid');return None
    for name in ('plan.json','verdict.json'):
        if (folder/name).exists():records[name]=receipt(folder/name)
    runs=M.RUNTIME/'runs'/identity
    if (M.RUNTIME/'runs').is_symlink() or runs.is_symlink():raise ValueError('Unsafe model run root')
    results=[]
    for entry in sorted(runs.glob('*'))[:100]:
        if entry.is_symlink() or not re.fullmatch(r'[0-9]{2}-(incumbent|challenger)(-retry-[0-9]+)?',entry.name):continue
        value=receipt(entry/'result.json')
        if isinstance(value,dict):
            value={k:value.get(k) for k in SUMMARY_FIELDS};value['side']=entry.name.split('-')[1];value['probe']=entry.name
            results.append(value)
    live=(M.load().get('comparison') or {}).get('id')==identity
    return {'comparison':identity,'live':live,'records':records,'results':results,'errors':errors}
