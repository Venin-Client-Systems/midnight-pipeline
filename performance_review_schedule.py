"""Owned local schedules for one finite seven-day evidence review.

Collection and qualified review run in separate launchd processes. This module
does not change Midnight's model, product code, budgets, pause or work schedule.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import time

import performance_review as F
import durability as D


def specifications(root, directory, python, agents):
    root, directory, agents = Path(root).resolve(), Path(directory).absolute(), Path(agents)
    if not re.fullmatch('[a-z0-9-]{1,60}', directory.name):
        raise ValueError('Use a simple unique review identifier')
    result = {}
    for mode, interval in [('observe', 60), ('review', 300)]:
        label = 'space.venin.midnight-review-' + directory.name + '-' + mode
        value = {'Label': label, 'ProgramArguments': [str(python), '-B', str(root / 'performance_review_schedule.py'),
                 '--root', str(root), '--review-dir', str(directory), mode], 'WorkingDirectory': str(root),
                 'StartInterval': interval, 'RunAtLoad': True, 'ProcessType': 'Standard',
                 'StandardOutPath': str(directory / (mode + '.out.log')),
                 'StandardErrorPath': str(directory / (mode + '.err.log')),
                 'EnvironmentVariables': {'PYTHONUNBUFFERED': '1', 'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'}}
        raw = plistlib.dumps(value, sort_keys=True)
        result[mode] = {'label': label, 'path': str(agents / (label + '.plist')), 'sha256': F.digest(raw), 'definition': value, 'raw': raw}
    return result


def native(value, uid, run):
    """Read the exact current service, allowing legitimate later boot generations."""
    target = f'gui/{uid}/{value["label"]}'
    result = run(['/bin/launchctl', 'print', target], capture_output=True, text=True, timeout=5)
    if result.returncode:
        if 'Could not find service' in result.stderr: return None
        raise ValueError('Native service absence is unconfirmed')
    text = result.stdout; definition = value['definition']
    if not text.startswith(target + ' = {') or len(text) > 131072: raise ValueError('Unexpected native service response')
    def field(name):
        match = re.search(r'^\t' + re.escape(name) + r' = (.+)$', text, re.M)
        return match.group(1) if match else None
    arguments = re.search(r'^\targuments = \{\n(.*?)^\t\}', text, re.M | re.S)
    args = [line.strip() for line in arguments.group(1).splitlines()] if arguments else None
    environment = re.search(r'^\tenvironment = \{\n(.*?)^\t\}', text, re.M | re.S)
    values = dict(line.strip().split(' => ', 1) for line in environment.group(1).splitlines()) if environment else {}
    values.pop('OSLogRateLimit', None); values.pop('XPC_SERVICE_NAME', None)
    if (field('path') != value['path'] or field('program') != definition['ProgramArguments'][0]
            or args != definition['ProgramArguments'] or field('working directory') != definition['WorkingDirectory']
            or field('stdout path') != definition['StandardOutPath'] or field('stderr path') != definition['StandardErrorPath']
            or values != definition['EnvironmentVariables'] or field('spawn type') != 'daemon (3)'):
        raise ValueError('Preserve a different loaded service despite matching plist bytes')
    coalition = re.search(r'^\tresource coalition = \{\n\s*ID = (\d+)', text, re.M)
    if not coalition: raise ValueError('Native service generation is unavailable')
    return {'boot_id': D.boot_id(), 'coalition': int(coalition.group(1))}


def install(root, directory, *, python=sys.executable, agents=None, run=subprocess.run):
    """Install only these two new schedules after exact activation and plan sealing."""
    root, directory = Path(root).resolve(), Path(directory).absolute()
    from performance_review_cloud import review_plan
    baseline = F.Review(root, directory).baseline()
    if F.unseal(directory / 'cloud-review-plan.json') != review_plan(root, directory):
        raise ValueError('Explicit seven-review plan is missing or changed')
    agents = Path.home() / 'Library/LaunchAgents' if agents is None else Path(agents)
    specs = specifications(root, directory, python, agents)
    receipt = {'baseline_sha256': F.digest(F.encoded(baseline)), 'entrypoint_sha256': F.digest(F.read_bytes(__file__)),
               'uid': os.getuid(), 'schedules': {key: {k: v for k, v in value.items() if k != 'raw'} for key, value in specs.items()}}
    previous = (directory / 'schedule.json').exists()
    if not previous:
        for value in specs.values():
            path = Path(value['path'])
            if native(value, os.getuid(), run) is not None or path.exists() or path.is_symlink():
                raise ValueError('Preserve an existing service; select a new review identifier')
    F.write_once(directory / 'schedule.json', receipt)
    for value in specs.values():
        path = Path(value['path'])
        if path.is_symlink(): raise ValueError('Preserve a linked schedule')
        if path.exists() and F.read_bytes(path) != value['raw']:
            raise ValueError('Preserve another schedule at this path')
        F.write_raw_once(path, value['raw'])
        target = f'gui/{os.getuid()}/{value["label"]}'
        if native(value, os.getuid(), run) is None:
            run(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(path)], check=True, capture_output=True, text=True, timeout=15)
        if native(value, os.getuid(), run) is None: raise ValueError('Schedule registration is unconfirmed')
    return {'state': 'scheduled', 'started_at': F.iso(baseline['started_at']), 'ends_at': F.iso(baseline['ends_at']),
            'daily_due': [F.iso(baseline['started_at'] + day * F.DAY) for day in range(1, 8)], 'schedules': receipt['schedules']}


def ownership(root, directory):
    observer = F.Review(root, directory); baseline = observer.baseline(); amendments = observer.amendments()
    saved = F.unseal(directory / 'schedule.json')
    expected = (amendments[-1]['intent']['next']['source_files']['performance_review_schedule.py']
                if amendments else saved['entrypoint_sha256'])
    if (expected != F.digest(F.read_bytes(__file__)) or saved['uid'] != os.getuid()
            or saved['baseline_sha256'] != F.digest(F.encoded(baseline))):
        raise ValueError('Schedule ownership or source changed')
    expected = specifications(root, directory, sys.executable, Path.home() / 'Library/LaunchAgents')
    retiring = (directory / 'retirement-intent.json').exists()
    if retiring and F.unseal(directory / 'retirement-intent.json')['schedule_sha256'] != F.digest(F.encoded(saved)):
        raise ValueError('Retirement belongs to another schedule')
    for mode, value in saved['schedules'].items():
        if value != {key: item for key, item in expected[mode].items() if key != 'raw'}:
            raise ValueError('Unexpected schedule identity')
        if Path(value['path']).is_symlink(): raise ValueError('Preserve a linked schedule')
        if retiring and not Path(value['path']).exists():
            continue
        if F.digest(F.read_bytes(value['path'])) != value['sha256']:
            raise ValueError('Schedule replaced; preserve it')
    return saved


def notify(directory, day, state, *, run=subprocess.run):
    """Local OS notification request; a receipt is not proof the user saw it."""
    path = directory / 'notifications' / f'day-{day:02}.json'
    if path.exists():
        return
    title = 'Midnight GLM weekly review'
    body = f'Day {day} review {state}. Reports: {directory.name}'
    F.write_once(path, {'day': day, 'state': state, 'requested_at': time.time(), 'visible_delivery': 'unverified'})
    script = 'on run argv\ndisplay notification (item 1 of argv) with title (item 2 of argv)\nend run'
    try:
        run(['/usr/bin/osascript', '-e', script, body, title], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def retire(directory, saved, *, run=subprocess.run, spawn=subprocess.Popen):
    """One shared retirement lock; only exact current owned generations unload."""
    lock = directory / '.retirement.lock'
    with os.fdopen(os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600), 'a') as handle:
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return {'state': 'busy'}
        intent = directory / 'retirement-intent.json'; binding = F.digest(F.encoded(saved))
        if intent.is_symlink(): raise ValueError('Preserve linked retirement evidence')
        if intent.exists() and F.unseal(intent)['schedule_sha256'] != binding:
            raise ValueError('Retirement belongs to another schedule')
        observed = {}
        for mode, value in saved['schedules'].items():
            path = Path(value['path'])
            if path.is_symlink(): raise ValueError('Preserve a linked schedule')
            if path.exists():
                if F.digest(F.read_bytes(path)) != value['sha256']: raise ValueError('Replaced schedule must remain untouched')
            elif not intent.exists(): raise ValueError('Missing schedule before retirement')
            observed[mode] = native(value, saved['uid'], run)
        if not intent.exists():
            F.write_once(intent, {'schedule_sha256': binding, 'requested_at': time.time()})
        for value in saved['schedules'].values():
            path = Path(value['path'])
            if path.is_symlink() or (path.exists() and F.digest(F.read_bytes(path)) != value['sha256']):
                raise ValueError('Schedule changed before retirement publication')
            path.unlink(missing_ok=True); D.sync_directory(path.parent)
        for mode in ('observe', 'review'):
            value = saved['schedules'][mode]; generation = native(value, saved['uid'], run)
            if generation is None: continue
            if generation != observed[mode]: raise ValueError('Native generation changed during retirement; inspect again')
            request = {'schedule_sha256': binding, 'mode': mode, 'generation': generation, 'definition_sha256': value['sha256']}
            F.write_once(directory / 'retirement-requests' / (mode + '-' + F.digest(F.encoded(generation)) + '.json'), request)
            if native(value, saved['uid'], run) != generation: raise ValueError('Native generation changed before unload')
            target = f'gui/{saved["uid"]}/{value["label"]}'
            if mode == 'review':
                spawn(['/bin/launchctl', 'bootout', target], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, start_new_session=True)
                return {'state': 'retirement_requested', 'native_exit_verified': False}
            run(['/bin/launchctl', 'bootout', target], capture_output=True, timeout=15)
            if native(value, saved['uid'], run) is not None:
                return {'state': 'retirement_requested', 'native_exit_verified': False}
        F.write_once(directory / 'schedule-retired.json', {'owned_plists_removed': True, 'native_exit_verified': True})
        return {'state': 'retired', 'native_exit_verified': True}


def tick(root, directory, mode):
    root, directory = Path(root).resolve(), Path(directory).absolute()
    saved = ownership(root, directory)
    if (directory / 'retirement-intent.json').exists():
        return retire(directory, saved) if mode == 'review' else {'state': 'retirement_pending'}
    path = directory / ('.schedule-' + mode + '.lock')
    with os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600), 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'state': 'busy'}
        observer = F.Review(root, directory)
        if mode == 'observe':
            return observer.tick()
        from performance_review_cloud import reconcile_due, review_due, review_status
        reconcile_due(root, directory)
        status = review_status(root, directory)
        for day in range(1, 8):
            report = directory / 'reports' / f'day-{day:02}.json'
            if day not in status['spent_days'] and report.exists():
                result = review_due(root, directory, report)
                if result.get('state') in {'completed', 'failed', 'unknown', 'spent'}:
                    notify(directory, day, result['state'])
                return result
        if observer.status()['complete'] and status['complete']:
            return retire(directory, saved)
        return {'state': 'waiting', 'reviews': status}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--review-dir', type=Path, required=True)
    parser.add_argument('command', choices=['install', 'observe', 'review', 'status'])
    args = parser.parse_args()
    try:
        if args.command == 'install':
            result = install(args.root, args.review_dir)
        elif args.command == 'status':
            from performance_review_cloud import review_status
            result = {'evidence': F.Review(args.root, args.review_dir).status(), 'reviews': review_status(args.root, args.review_dir)}
        else:
            result = tick(args.root, args.review_dir, args.command)
        print(json.dumps(result, indent=2)); return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({'state': 'unavailable', 'reason': type(error).__name__, 'detail': str(error)[:200]})); return 1


if __name__ == '__main__':
    raise SystemExit(main())
