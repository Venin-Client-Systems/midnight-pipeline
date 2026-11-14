"""Verify the frozen prepared release before importing any coordinator code."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import types


CLEANUP_NAMES = ('durability.py', 'history_store.py', 'action_store.py')
CANONICAL = Path.home()/'Developer'/'midnight'


def host_helpers(root, hashes):
    """Drop only our dispatch hold using separately pinned minimal host code.

    A damaged staged feature must not keep a healthy baseline idle. Cleanup
    hashes are pinned independently in the LaunchAgent arguments so this path
    can run even when the release manifest or its full bundle is rejected.
    """
    expected = hashes.split(',')
    if len(expected) != len(CLEANUP_NAMES):
        raise ValueError('Incomplete cleanup pins')
    source_bytes = {}
    for name, digest in zip(CLEANUP_NAMES, expected):
        path = root / name
        raw = path.read_bytes()
        if path.is_symlink() or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('Minimal host cleanup source changed')
        source_bytes[name] = raw
    # Do not add this partially trusted directory to import search. Only these
    # exact named modules may come from it; their other imports use stdlib.
    sys.path[:] = [p for p in sys.path if p and Path(p).resolve() != root]
    loaded = {}
    for name in CLEANUP_NAMES:
        module_name = name[:-3]
        module = types.ModuleType(module_name)
        module.__file__ = str(root / name)
        module.__package__ = ''
        sys.modules[module_name] = module
        exec(compile(source_bytes[name], str(root / name), 'exec', dont_inherit=True), module.__dict__)
        loaded[module_name] = module
    return loaded['durability'], loaded['action_store'].Actions


def cleanup(root, identity, hashes):
    root = Path(root).resolve()
    if root != CANONICAL:
        raise ValueError('Unadmitted cleanup target')
    D, Actions = host_helpers(root, hashes)
    runtime = root / 'runtime'
    with (runtime / 'source-adoption.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = Actions(runtime)
        if (store.meta('deferred_install_owner') or {}).get('id') == identity:
            D.marker(runtime / 'INSTALLING', False)
            store.meta('deferred_install_owner', {}, write=True)


def verify(path, expected):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 2 * 1024**2 or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError('Deferred release manifest changed')
    manifest = json.loads(path.read_text())
    bundle = Path(manifest['bundle'])
    actual = {str(p.relative_to(bundle)) for p in bundle.rglob('*') if p.is_file() or p.is_symlink()}
    if actual != set(manifest['bundle_files']):
        raise ValueError('Unexpected file in frozen deferred-release bundle')
    for name, expected_hash in manifest['bundle_files'].items():
        member = Path(name)
        if member.is_absolute() or '..' in member.parts:
            raise ValueError('Unsafe frozen-bundle member')
        source = bundle / member
        if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != expected_hash:
            raise ValueError('Frozen deferred-release source changed')
    return manifest


def dispatch(value, args):
    """Reuse qualified durable action/phase fencing for the prepared command."""
    sys.path.insert(0, value['bundle'])  # All members were verified above.
    import durability as D
    from action_store import Actions
    import phase_runtime as P
    import project_action as PA
    import core_repair as CR
    import deferred_release as R
    root = Path(args.root).resolve()
    if root != CANONICAL or value['root'] != str(root) or value['id'] != args.release_id:
        raise ValueError('Pinned deferred target differs from invocation')
    runtime = root / 'runtime'; directory = runtime / 'deferred-releases' / value['id']
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with R.file_lock(directory / 'bootstrap.lock', seconds=.2):
        state = D.read_json(directory / 'state.json', {}) or {}
        store = Actions(runtime)
        old = next((j for j in store.jobs() if j['project'] == 'midnight'), None)
        payload = {'kind': 'prepared_release', 'release': value['id']}
        if old:
            if old['payload'] != payload or P.alive(old.get('lease')):
                R.release_marker(runtime, value['id'])
                print(json.dumps({'state': 'waiting_shared_maintenance'})); return 0
            if not PA.drain(runtime, old['id'], grace=3):
                print(json.dumps({'state': 'waiting_owned_drain'})); return 0
            store.finish(old['id'], 'interrupted', {'reason': 'prepared_release_parent_lost'}, lease=old.get('lease'))
        if dt.datetime.now(dt.timezone.utc) >= dt.datetime.fromisoformat(value['expires_at']):
            R.release_marker(runtime, value['id'])
            state.update(state='held', reason='Prepared release expired; no source mutation')
            D.save_json(directory / 'state.json', state); print(json.dumps(state)); return 0
        if state.get('state') in {'held', 'rolled_back'}:
            R.release_marker(runtime, value['id'])
            print(json.dumps(state)); return 0
        if state.get('state') == 'complete':
            receipt = R.completion_record(value, directory)
            updated = {**state, 'completion_record_status': receipt.get('status'),
                       'completion_record_reason': receipt.get('reasonCode')}
            if updated != state:D.save_json(directory / 'state.json', updated)
            print(json.dumps(updated)); return 0
        # Waiting alone uses no host slot, action, model or service operation.
        if (state.get('state') in {None, 'waiting_validation'} and (store.meta('deferred_install_owner') or {}).get('id') != value['id']
                and not R.validation_ready(value, R.read(value['validation_path']))):
            state.update(state='waiting_validation', checked_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                         expected_commit=value['commit'], reason='Waiting for actual four-hour passing evidence; no source change')
            D.save_json(directory / 'state.json', state); print(json.dumps(state)); return 0
        deadline = min(time.time() + 100, dt.datetime.fromisoformat(value['expires_at']).timestamp())
        if not CR.permitted(root, deadline):
            R.release_marker(runtime, value['id'])
            print(json.dumps({'state': 'waiting_window'})); return 0
        with P.source_lock(runtime):
            job = store.submit('midnight', payload, deadline)
            if not job or job['payload'] != payload:
                print(json.dumps({'state': 'waiting_shared_maintenance'})); return 0
            mine = P.lease()
            if not store.claim(job['id'], mine):return 0
        command = [sys.executable, '-I', '-B', str(Path(value['bundle']) / 'deferred_release.py'),
                   '--manifest', args.manifest, '--sha256', args.sha256, '--root', args.root, '--release-id', args.release_id]
        previous = os.environ.get('MIDNIGHT_ACTION_ID'); os.environ['MIDNIGHT_ACTION_ID'] = job['id']
        outcome = 'interrupted'
        try:
            CR.command(root, job['id'], 'host', command, Path(value['bundle']), directory / 'runner.log',
                       90, deadline, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
            outcome = 'succeeded'
            return 0
        finally:
            if previous is None:os.environ.pop('MIDNIGHT_ACTION_ID', None)
            else:os.environ['MIDNIGHT_ACTION_ID'] = previous
            if PA.drain(runtime, job['id'], grace=3):
                store.finish(job['id'], outcome, {'reason': 'prepared_release_guarded_tick'}, lease=mine)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--cleanup-hashes', required=True)
    args = parser.parse_args()
    try:
        value = verify(args.manifest, args.sha256)
    except Exception:
        cleanup(args.root, args.release_id, args.cleanup_hashes)
        raise
    return dispatch(value, args)


if __name__ == '__main__':
    sys.exit(main())
