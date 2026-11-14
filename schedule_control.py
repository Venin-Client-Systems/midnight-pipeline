"""Shared wall-clock schedule; weekdays refer to the window's start date."""
import datetime as dt
from zoneinfo import ZoneInfo

DAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')


def weekdays(schedule):
    values = schedule.get('weekdays', list(DAYS))
    if (not isinstance(values, list) or not values or
            any(not isinstance(day, str) or day not in DAYS for day in values) or
            len(set(values)) != len(values)):
        raise ValueError('Schedule weekdays must be distinct mon–sun names, with at least one day')
    return tuple(day for day in DAYS if day in values)


def window(instant, registry, override=None):
    if instant.tzinfo is None:
        raise ValueError('Schedule observation requires an aware timestamp')
    tz = ZoneInfo(registry['timezone']); local = instant.astimezone(tz)
    schedule = registry['schedule']; days = weekdays(schedule)
    start = dt.time.fromisoformat(schedule['start']); end = dt.time.fromisoformat(schedule['end'])
    if start.tzinfo is not None or end.tzinfo is not None:
        raise ValueError('Schedule times must be local wall-clock times')
    intervals = []
    # Yesterday covers a crossing-midnight window; +7 covers a sole weekday.
    for offset in range(-1, 8):
        date = local.date() + dt.timedelta(days=offset)
        if DAYS[date.weekday()] not in days: continue
        a = dt.datetime.combine(date, start, tz)
        b = dt.datetime.combine(date + dt.timedelta(days=end <= start), end, tz)
        intervals.append((a.astimezone(dt.timezone.utc), b.astimezone(dt.timezone.utc)))
    active = next(((a, b) for a, b in intervals if a <= instant < b), None)
    upcoming = next(a for a, _ in intervals if a > instant)
    persistent = bool(override and override.get('mode') == 'continuous' and override.get('expires_at') is None)
    expiry = dt.datetime.fromisoformat(override['expires_at']) if override and not persistent else None
    if expiry is not None and expiry.tzinfo is None:
        raise ValueError('Override expiry must include a timezone')
    extended = expiry is not None and instant < expiry
    candidates = ([active[1]] if active else []) + ([expiry] if extended else [])
    cutoff = instant + dt.timedelta(hours=24) if persistent else max(candidates, default=None)
    return {'active': bool(active or extended or persistent), 'cutoff': cutoff,
            'mode': 'continuous' if persistent else 'override' if extended else 'scheduled',
            'override_active': extended, 'next_start': upcoming, 'scheduled_active': bool(active)}


def performance_review(job):
    """The only metadata job exempt from ordinary schedule closure."""
    return bool(job.get('project') == 'midnight-performance-review' and
                job.get('payload', {}).get('kind') == 'performance_review')
