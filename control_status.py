"""Pure explanation of project control state; observing never starts recovery."""
import datetime as dt
from evidence_context import sanitize
from profile_manager import missing_evidence


def project_control(status, state, *, instant=None, process_running=False, supervisor_running=False, activity=None, paused=False,owned_phase=None):
    instant = instant or dt.datetime.now(dt.timezone.utc)
    def future(value):
        try:
            parsed = dt.datetime.fromisoformat(value)
            return value if parsed.tzinfo and parsed > instant else None
        except (TypeError, ValueError):
            return None
    result = {'state': 'idle', 'stage': 'Idle', 'code': 'no_active_phase',
              'explanation': 'No project phase is active.', 'recovery_owner': 'automatic',
              'next_retry_at': None, 'recovery_state': 'none', 'last_progress_at': None}
    def blocked(code, stage, explanation, retry=None, owner='codex'):
        result.update(state='blocked', code=code, stage=stage, explanation=sanitize(explanation)[:1000],
                      next_retry_at=retry, recovery_owner=owner, recovery_state='pending')
        return result
    if paused:
        return blocked('owner_pause', 'Paused', 'The owner pause must be cleared before dispatch.', owner='owner')
    if supervisor_running and owned_phase and owned_phase.get('state') in {'waiting','running'} and owned_phase.get('deadline',0)<=instant.timestamp():
        return blocked('phase_deadline','Recovering · expired phase','The owned phase reached its absolute deadline and needs deterministic cleanup.',owner='automatic')
    explicit=status.get('control')
    if isinstance(explicit,dict) and explicit.get('state') in {'waiting','recovering','blocked'}:
        try:age=(instant-dt.datetime.fromisoformat(status.get('updated_at',''))).total_seconds()
        except (TypeError,ValueError):age=float('inf')
        waiting_lease=bool(owned_phase and owned_phase.get('state')=='waiting' and owned_phase.get('deadline',0)>instant.timestamp())
        if supervisor_running and (age<=300 or waiting_lease):
            for key in result:
                if key in explicit:result[key]=explicit[key]
            result['explanation']=sanitize(str(result['explanation']))[:1000]
            return result
    if process_running:
        stage = {'local_worker':'Coding', 'cloud_implementation':'Cloud implementation', 'host_checks':'Checking',
                 'local_review':'Independent local review','local_planning':'Local planning',
                 'independent_review':'Independent review', 'github_delivery':'Delivery'}.get(activity, 'Preparing or managing task')
        result.update(state='active', stage=stage, code=activity or 'management_active', explanation='A verified project process is active.')
        return result
    if status.get('phase')=='github_delivery' and supervisor_running:
        try:age=(instant-dt.datetime.fromisoformat(status.get('updated_at',''))).total_seconds()
        except (TypeError,ValueError):age=float('inf')
        if age<=300:
            result.update(state='active',stage='GitHub delivery',code='github_delivery',explanation='The owned supervisor is reconciling the exact checked private PR.')
            return result
    if status.get('phase') == 'preparing_task':
        try:
            age = (instant-dt.datetime.fromisoformat(status.get('updated_at',''))).total_seconds()
        except (ValueError,TypeError):age = float('inf')
        if supervisor_running and 0<=age<=300:
            result.update(state='active', stage='Preparing workspace', code='preparing_workspace',
                          explanation='Preparing an isolated workspace and validating dispatch eligibility.')
            return result
        return blocked('stale_preparation','Blocked · preparation needs recovery',
                       'No fresh verified supervisor proves this workspace preparation is progressing.',owner='automatic')
    detail = status.get('detail', '')
    message = detail if isinstance(detail, str) else detail.get('reason', '') if isinstance(detail, dict) else ''
    scope=status.get('scope_wait') or {}
    if status.get('phase')=='quality_wait' and future(scope.get('until')):
        result.update(state='waiting',stage='Scope clear · reassessment scheduled',code='scope_clear',explanation=message,
                      next_retry_at=scope['until'],recovery_owner='automatic')
        return result
    cooldown = future(state.get('cooldown_until'))
    if 'separately journaled guided successor' in message:
        return blocked('guided_retry_blocked', 'Blocked · guided retry', 'The failed task needs a journaled guided successor before another implementation.', cooldown)
    if status.get('phase') == 'needs_attention':
        return blocked('orchestration_error', 'Blocked · orchestration error', message or 'The last project operation requires recovery.', cooldown)
    if status.get('phase')=='manager_hold':
        return blocked('rescue_manager_held','Blocked · manager hold',message or 'A manager decision is needed for this issue.')
    if status.get('phase')=='profile_needs_manager':
        missing=missing_evidence(message)
        return blocked('profile_evidence_missing' if missing else 'profile_manager_hold','Blocked · profile coverage',
            message or 'The accepted capability needs a supported additive quality profile.',owner='automatic' if missing else 'codex')
    waiting = state.get('planning_wait') or {}
    retry = future(waiting.get('until'))
    current_head = (state.get('quality_decision') or {}).get('head')
    if retry and status.get('phase') in {'quality_wait','no_useful_work'} and (not current_head or not waiting.get('head') or waiting['head']==current_head):
        reason = waiting.get('reason', 'No supported next change was planned.')
        missing = any(x in reason.lower() for x in ('cannot read', 'no available tool', 'failure evidence', 'failure log'))
        return blocked('missing_failure_evidence' if missing else 'planner_hold',
                       'Blocked · missing failure evidence' if missing else 'Waiting · planner hold', reason, retry)
    if cooldown:
        return blocked('failure_backoff', 'Waiting · failure backoff', message or 'Bounded failure backoff is active.', cooldown, 'automatic')
    episodes = {f.get('episode_id') for f in (state.get('quality_status') or {}).get('material_findings', [])}
    selected = (state.get('quality_decision') or {}).get('task',{}).get('id')
    if selected:
        episodes.add(state.get('issue_episodes',{}).get(selected,{}).get('episode'))
    for episode, hold in state.get('rescue_decisions', {}).items():
        if episode in episodes and hold.get('decision') == 'hold':
            return blocked('manager_hold', 'Blocked · manager hold', hold.get('summary', 'The manager held this issue.'))
    if state.get('quality_decision',{}).get('action')=='needs_manager':
        return blocked('coordinator_required','Blocked · manager action required',
                       'The selected task needs a coordinator decision before another attempt.')
    if status.get('phase') in {'waiting_for_midnight','finished_at_09:00','finished_at_managed_deadline'}:
        result.update(stage='Scheduled', code='outside_window', explanation='Waiting for the next permitted work window.')
    elif state.get('pending'):
        result.update(stage='Waiting for review or delivery', code='pending_continuation',
                      explanation='Saved work is awaiting its next checked continuation.')
    return result
