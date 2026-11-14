"""Read-only launch observations, never a resource lease or admission proof.

A scheduled start, a selected model and an idle healthy service do not establish
that local work can start. In particular, one memory sample cannot establish the
continuous quiet window enforced by memory_runtime.
"""
import datetime as dt
import math
import subprocess

import memory_admission as MA


def memory_observation(sample, instant):
    """Use the admission parser/rules without keeping or consuming a quiet gate."""
    unavailable = {'state': 'unknown', 'reason': 'memory_observation_unavailable',
                   'summary': 'Current memory readiness is unavailable.',
                   'observed_at': None, 'free_inactive_bytes': None,
                   'required_bytes': MA.MIN_FREE_INACTIVE_BYTES, 'deficit_bytes': None,
                   'quiet_window_seconds': MA.WINDOW_SECONDS, 'admission_verified': False}
    if not isinstance(sample, dict): return unavailable
    at = sample.get('at')
    if type(at) not in (int, float) or not math.isfinite(at) or not 0 <= instant.timestamp() - at <= MA.MAX_GAP_SECONDS:
        return dict(unavailable, reason='memory_observation_stale', summary='The memory observation is stale or invalid.')
    observed = MA.QuietGate(probe=lambda: sample).observe()
    latest = observed.get('latest')
    if not latest: return unavailable
    free = latest['free_inactive_bytes']; required = MA.MIN_FREE_INACTIVE_BYTES
    state = 'blocked_now' if observed['reason'] in {'insufficient_free_inactive_memory', 'memory_pressure'} else 'quiet_window_not_observed'
    if state == 'blocked_now' and observed['reason'] == 'insufficient_free_inactive_memory':
        summary = f'GLM memory admission is blocked now: {free / 1e9:.1f} GB free plus inactive; {required / 1e9:.1f} GB required.'
    elif state == 'blocked_now':
        summary = 'GLM memory admission is blocked by current memory pressure.'
    else:
        summary = 'This memory sample meets the initial conditions; the continuous quiet window has not been observed.'
    return {'state': state, 'reason': observed['reason'], 'summary': summary,
            'observed_at': dt.datetime.fromtimestamp(at, dt.timezone.utc).isoformat(),
            'free_bytes': latest['free_bytes'], 'inactive_bytes': latest['inactive_bytes'],
            'free_inactive_bytes': free, 'required_bytes': required,
            'deficit_bytes': max(0, required - free), 'pressure': latest['pressure'],
            'swap_out_counter': latest['swap_out_counter'],
            'quiet_window_seconds': MA.WINDOW_SECONDS, 'admission_verified': False}


def observe(models, commission, instant, *, probe=None, profile=None, clock=None):
    """Observe selected configuration and current memory, without invoking models."""
    import colibri_provider as GP
    selected = models.get('selected_next') or models.get('active') or {}
    result = {'schema_version': 1, 'observed_at': instant.isoformat(),
              'scope': 'read_only_observation_not_admission', 'model': selected.get('model'),
              'state': 'unverified', 'admission_verified': False, 'checks': [], 'notes': []}
    if (commission.get('job_state') or commission.get('state')) in {'failed', 'interrupted', 'unknown'}:
        result['notes'].append('The last supervised commissioning action did not complete successfully. Installed source health is separate from a successful model run.')
    if not GP.matches(selected):
        result['summary'] = 'Scheduled eligibility is separate from model, resource and task admission.'
        return result
    try:
        if profile:
            checked = profile(selected)
        else:
            GP.phase_profile(selected)
            checked = GP.config()
        if checked.get('version') != 2:
            result['summary'] = 'The selected profile has no version-two quiet-memory observation.'
            return result
    except (OSError, ValueError, KeyError, TypeError):
        result.update(state='blocked_now', summary='The selected GLM profile cannot be verified against its current source and settings.')
        result['checks'].append({'id': 'model_profile', 'state': 'blocked_now', 'summary': result['summary']})
        return result
    result['checks'].append({'id': 'model_profile', 'state': 'verified', 'summary': 'Selected GLM source and profile match their saved bindings.'})
    try: sample = (probe or MA.sample)()
    except (OSError, ValueError, TypeError, subprocess.SubprocessError): sample = None
    # The sample timestamp is captured at the end of its native probes.
    observed_at = (clock or (lambda: dt.datetime.now(dt.timezone.utc)))()
    memory = memory_observation(sample, observed_at)
    result['observed_at'] = observed_at.isoformat()
    result.update(state=memory['state'], summary=memory['summary'], memory=memory)
    result['checks'].append({'id': 'memory', **memory})
    result['notes'].append('These are current observations. Dispatch must still acquire its leases, verify the original task and controls, and pass the continuous quiet-memory check before charging inference.')
    return result
