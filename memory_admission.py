"""Read-only, fail-closed quiet-host measurement for the admitted GLM profile.

The caller owns resource admission and calls observe every five seconds outside
the shared admission lock. This module never sleeps, writes, unloads or launches.
"""
from collections import deque
import math
import subprocess
import time

import durability as D
from telemetry import memory_values

WINDOW_SECONDS = 120
SAMPLE_SECONDS = 5
MAX_GAP_SECONDS = 7.5
MIN_FREE_INACTIVE_BYTES = 24_000_000_000
MAX_SWAP_OUT_BYTES = 64 * 1024**2


def sample():
    vm = subprocess.check_output(['/usr/bin/vm_stat'], text=True, timeout=3)
    sysctl = subprocess.check_output(['/usr/sbin/sysctl', 'hw.memsize', 'vm.swapusage',
                                     'kern.memorystatus_vm_pressure_level'], text=True, timeout=3)
    return dict(memory_values(vm, sysctl), at=time.time(), boot_id=D.boot_id())


class QuietGate:
    def __init__(self, *, probe=sample, clock=time.monotonic):
        self.probe, self.clock = probe, clock
        self.samples = deque(maxlen=64)

    def observe(self):
        instant = self.clock()
        try:
            value = self.probe()
            for key in ('at', 'total_bytes', 'free_bytes', 'inactive_bytes', 'swap_out_counter'):
                number = value.get(key)
                if type(number) not in (int, float) or not math.isfinite(number) or number < 0:
                    raise ValueError('Incomplete native memory observation')
            if (not isinstance(value.get('boot_id'), str) or not value['boot_id']
                    or value.get('pressure') not in {'normal', 'warning', 'critical'}
                    or value['free_bytes'] + value['inactive_bytes'] > value['total_bytes']):
                raise ValueError('Invalid native memory observation')
            latest = {key: value.get(key) for key in ('at', 'boot_id', 'pressure', 'total_bytes',
                'free_bytes', 'inactive_bytes', 'wired_bytes', 'compressed_bytes', 'swap_used_bytes',
                'swap_in_counter', 'swap_out_counter')}
            latest['free_inactive_bytes'] = value['free_bytes'] + value['inactive_bytes']
        except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
            self.samples.clear()
            return {'ready': False, 'reason': 'memory_observation_unavailable', 'quiet_seconds': 0,
                    'sample_count': 0, 'latest': None, 'minimum_free_inactive_bytes': None, 'swap_out_bytes': None}
        reason = None
        if latest['pressure'] != 'normal': reason = 'memory_pressure'
        elif latest['free_inactive_bytes'] < MIN_FREE_INACTIVE_BYTES: reason = 'insufficient_free_inactive_memory'
        if self.samples:
            before, previous = self.samples[-1]
            if (not 0 < instant - before <= MAX_GAP_SECONDS or previous['boot_id'] != latest['boot_id']
                    or latest['at'] <= previous['at'] or latest['swap_out_counter'] < previous['swap_out_counter']):
                self.samples.clear()
                reason = reason or 'memory_observation_gap'
        if reason:
            self.samples.clear()
            # A valid but discontinuous sample can begin a new quiet window.
            if reason == 'memory_observation_gap': self.samples.append((instant, latest))
        else:
            self.samples.append((instant, latest))
            while len(self.samples) > 1 and instant - self.samples[1][0] >= WINDOW_SECONDS:
                self.samples.popleft()
        duration = instant - self.samples[0][0] if self.samples else 0
        swapped = latest['swap_out_counter'] - self.samples[0][1]['swap_out_counter'] if self.samples else None
        if swapped is not None and swapped > MAX_SWAP_OUT_BYTES:
            self.samples.clear(); self.samples.append((instant, latest))
            duration = 0; reason = 'recent_swap_out'
        minimum = min((v['free_inactive_bytes'] for _, v in self.samples), default=None)
        return {'ready': reason is None and duration >= WINDOW_SECONDS,
                'reason': reason or ('ready' if duration >= WINDOW_SECONDS else 'quiet_window_pending'),
                'quiet_seconds': duration, 'sample_count': len(self.samples), 'latest': latest,
                'minimum_free_inactive_bytes': minimum, 'swap_out_bytes': swapped}
