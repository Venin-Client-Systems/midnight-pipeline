"""One exact reviewed release, installed after real commissioning evidence.

Run from a frozen tested checkout via a one-minute user LaunchAgent. This is a
deterministic handoff, not an open-ended agent or permission to implement work.
The manifest is pinned by SHA-256 in the LaunchAgent arguments. No account or
model calls occur here; the checked remote merge is prepared before arming.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager, nullcontext
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import uuid

# This entry is also invoked under python -I from its frozen trusted bundle.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import durability as D
from action_store import Actions
import guardian as GU
import phase_runtime as P
from maintenance_authority import check as check_authority


def read(path):
    path = Path(path)
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 2 * 1024**2:
        raise ValueError('Unsafe or oversized release evidence')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Release evidence must be an object')
    return value


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(root, *args):
    return subprocess.check_output(['git', *map(str, args)], cwd=root, text=True,
                                   stderr=subprocess.PIPE, timeout=20).strip()


def manifest(path, expected):
    if not re.fullmatch(r'[a-f0-9]{64}', expected) or digest(path) != expected:
        raise ValueError('Release manifest changed')
    value = read(path)
    for key in ('before', 'commit', 'tree', 'tested_head'):
        if not re.fullmatch(r'[a-f0-9]{40}', value.get(key, '')):
            raise ValueError('Release source identity is invalid')
    for key in ('checks', 'review', 'merge'):
        item = value[key]
        if digest(item['path']) != item['sha256']:
            raise ValueError('Reviewed release evidence changed')
        proof = read(item['path'])
        if key == 'merge':
            if proof.get('state') != 'MERGED' or proof.get('headRefOid') != value['tested_head'] or proof.get('mergeCommit', {}).get('oid') != value['commit']:
                raise ValueError('Remote merge receipt differs from reviewed release')
        else:
            if proof.get('head') != value['tested_head'] or proof.get('tree') != value['tree']:
                raise ValueError('Verification is not bound to the exact source')
            if key == 'review' and (proof.get('decision') != 'approve' or proof.get('issues') != []):
                raise ValueError('Independent release review did not approve')
            if key == 'checks' and (proof.get('passed') is not True or not proof.get('commands') or any(c.get('returncode') != 0 for c in proof['commands'])):
                raise ValueError('Release checks did not pass')
    return value


def validation_ready(value, proof):
    if not proof or proof.get('state') != 'passed':
        return False
    latest = proof.get('latest', {})
    if (type(proof.get('continuous_healthy_seconds')) is not int or proof['continuous_healthy_seconds'] < 14400
            or proof.get('required_seconds') != 14400 or latest.get('issues') != []
            or latest.get('actual_commit') != value['before'] or latest.get('expected_commit') != value['before']
            or latest.get('installed_commit') != value['before'] or latest.get('installation') != 'verified'):
        raise ValueError('Commissioning proof does not establish four healthy hours on the baseline')
    if any(proof.get('maximum_resource_ownership', {}).get(k, 10**9) > cap for k, cap in {'local': 1, 'host': 1, 'cloud': 2}.items()):
        raise ValueError('Commissioning resource limits were exceeded')
    return True


@contextmanager
def file_lock(path, seconds=5):
    with Path(path).open('a') as handle:
        end = time.monotonic() + seconds
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    raise P.PhaseInterrupted('Owned process has not reached an idle boundary')
                time.sleep(.1)
        yield


def eligible(root, instant):
    return not (root / 'runtime/PAUSED').exists() and GU.window(read(root / 'projects.json'), D.read_json(root / 'runtime/override.json'), instant)


def service_started(root, name, after):
    """Reconcile an interrupted restart intent using boot-bound live identity."""
    try:
        if name == 'controller':
            value = D.read_json(root / 'runtime/status.json') or {}
            recent = (dt.datetime.fromisoformat(value['updated_at']).timestamp() > after
                      and value.get('process', {}).get('birth_us', 0) / 1e6 >= after)
        else:
            with urllib.request.urlopen('http://127.0.0.1:8766/api/health', timeout=3) as response:
                value = json.load(response)
            recent = value.get('process', {}).get('birth_us', 0) / 1e6 >= after
        return bool(recent and value.get('code_sha256') == digest(root / ('midnight.py' if name == 'controller' else 'dashboard.py'))
                    and P.alive(value.get('process')))
    except (OSError, ValueError, KeyError):
        return False


def other_owners(store, identity):
    return [j for j in store.jobs() if j['id'] != identity] or [e for e in store.executions() if e['job'] != identity]


def own_action(store, release):
    identity = os.environ.get('MIDNIGHT_ACTION_ID')
    if identity:
        job = store.get(identity)
        if (not job or job.get('state') != 'running' or job.get('payload') != {'kind': 'prepared_release', 'release': release}
                or not P.alive(job.get('lease'))):
            raise ValueError('Prepared release has no matching live owning action')
    return identity


def restart_services(root, directory, release):
    path = directory / 'restart-receipt.json'
    receipt = D.read_json(path, {}) or {}
    requests = receipt.setdefault('requests', {})
    for name, request in (('controller', GU.restart_controller), ('dashboard', GU.restart_dashboard)):
        saved = requests.get(name, {})
        if saved.get('state') == 'confirmed':
            continue
        if saved.get('state') == 'intent' and service_started(root, name, saved['at']):
            requests[name] = {**saved, 'state': 'confirmed', 'reconciled': True}
            D.save_json(path, receipt)
            continue
        # Never blindly restart a live controller after an uncertain request
        # while independently owned work is active.
        store = Actions(root / 'runtime')
        if name == 'controller' and other_owners(store, own_action(store, release)):
            raise P.PhaseInterrupted('Controller restart waits for owned work')
        requests[name] = {'state': 'intent', 'at': time.time()}
        D.save_json(path, receipt)
        try:
            okay = request()
            requests[name]['state'] = 'confirmed' if okay else 'failed'
        except (OSError, subprocess.SubprocessError) as exc:
            # Preserve intent: a timeout can occur after launchctl succeeded.
            requests[name]['error'] = type(exc).__name__
            D.save_json(path, receipt)
            raise
        D.save_json(path, receipt)
    if any(requests.get(name, {}).get('state') != 'confirmed' for name in ('controller', 'dashboard')):
        raise RuntimeError('Source installed; service restart is not yet confirmed')
    return receipt


def sensor_health():
    with urllib.request.urlopen('http://127.0.0.1:8766/api/telemetry', timeout=5) as response:
        value = json.load(response)
    hardware = value.get('hardware', {})
    age = time.time() - dt.datetime.fromisoformat(hardware['sampled_at']).timestamp()
    cpu = hardware.get('temperature', {}).get('cpu_celsius')
    fans = hardware.get('fans', [])
    return {'passed': value.get('available') is True and hardware.get('available') is True
            and -5 <= age <= 25 and type(cpu) in (int, float) and 0 < cpu < 150
            and any(type(f.get('rpm')) in (int, float) and 0 <= f['rpm'] <= 30000 for f in fans),
            'sampled_at': hardware.get('sampled_at'), 'cpu_celsius': cpu,
            'gpu_celsius': hardware.get('temperature', {}).get('gpu_celsius'), 'fans': fans}


def release_marker(runtime, identity, *, locked=False):
    """Release only this exact installer's marker, including aborted replays."""
    store = Actions(runtime)
    if (store.meta('deferred_install_owner') or {}).get('id') != identity:
        return
    with nullcontext() if locked else P.source_lock(runtime):
        if (store.meta('deferred_install_owner') or {}).get('id') == identity:
            D.marker(Path(runtime) / 'INSTALLING', False)
            store.meta('deferred_install_owner', {}, write=True)


def completion_record(value, directory):
    """Retire personal reporting; original release evidence stays in VENIN."""
    if not value.get('completion_record'):
        return {'status': 'not_configured'}
    receipt = {'schema_version': 1, 'status': 'retired_personal_reporting',
        'reasonCode': 'venin_personal_separation_20261114', 'release_id': value['id'],
        'scope': 'Reporting destination retirement only; not product acceptance',
        'records': 'Existing local release receipts, GitHub and the Site / Project Map'}
    path = directory / 'venin-completion-record.json'
    missing = object()
    prior = D.read_json(path, missing)
    if prior is not missing:
        if prior != receipt: raise ValueError('VENIN completion record conflicts with retained evidence')
        return prior
    # Never read, replay, rewrite or remove the historical personal outbox/result.
    D.save_json(path, receipt)
    return receipt


def tick(root, value, directory, *, instant=None):
    root, directory = Path(root), Path(directory)
    runtime = root / 'runtime'
    instant = instant or dt.datetime.now(dt.timezone.utc)
    journal_path = directory / 'state.json'
    journal = D.read_json(journal_path, {}) or {}
    source_locked = False

    def clear_marker():
        release_marker(runtime, value['id'], locked=source_locked)

    @contextmanager
    def installation_lock():
        nonlocal source_locked
        with P.source_lock(runtime):
            source_locked = True
            try:
                yield
            finally:
                source_locked = False

    def report(state, **extra):
        if state in {'complete', 'rolled_back', 'held'}:
            clear_marker()
        journal.update(state=state, checked_at=instant.isoformat(), expected_commit=value['commit'], **extra)
        D.save_json(journal_path, journal)
        return journal

    if journal.get('state') in {'complete', 'rolled_back', 'held'}:
        clear_marker()
        return journal
    if instant >= dt.datetime.fromisoformat(value['expires_at']):
        return report('held', reason='Prepared release expired; retained for manager review')
    current = git(root, 'rev-parse', 'HEAD')
    if current not in {value['before'], value['commit']} or git(root, 'status', '--porcelain'):
        return report('held', reason='Canonical source changed; no automatic overwrite')
    store = Actions(runtime)
    identity = own_action(store, value['id'])
    health = store.meta('install_health') or {}
    owner = store.meta('deferred_install_owner') or {}

    if value.get('mode')=='observe_installed':
        if current!=value['before'] or value['commit']!=value['before']:
            return report('held',reason='The commissioned recovery source changed')
        if digest(value['observer_path'])!=value['observer_sha256'] or digest(value['monitor_path'])!=value['monitor_sha256']:
            return report('held',reason='Commissioning observer identity or implementation changed')
        proof=read(value['validation_path'])
        if not validation_ready(value,proof):return report('waiting_validation',reason='Waiting for four healthy hours on the recovery source')
        if health.get('installed')!=current or health.get('state')!='verified':
            return report('held',reason='Recovery source adoption health changed')
        sensors=sensor_health();D.save_json(directory/'sensor-health.json',sensors)
        if not sensors['passed']:return report('verifying_health',reason='Waiting for live source-gated native readings')
        D.save_json(directory/'commissioning-proof.json',proof)
        return report('complete',completed_at=instant.isoformat(),reason='Four healthy recovery hours and live native readings verified')

    if current == value['commit']:
        intent = journal.get('adoption_intent') or {}
        saved_proof = directory / 'commissioning-proof.json'
        if (intent.get('before') != value['before'] or intent.get('commit') != value['commit'] or intent.get('tree') != value['tree']
                or not saved_proof.is_file() or digest(saved_proof) != intent.get('proof_sha256')
                or not validation_ready(value, D.read_json(saved_proof))):
            return report('held', reason='Installed source has no matching durable commissioning/adoption intent')
    if journal.get('state') in {'verifying_health', 'rollback_requested'}:
        if health.get('installed') == value['commit'] and health.get('state') == 'rolling_back':
            return report('rollback_requested', reason='Independent guardian is restoring the previous source')
        if health.get('installed') == value['commit'] and health.get('state') in {'rolled_back', 'held'}:
            return report('rolled_back' if health['state'] == 'rolled_back' else 'held', reason='Independent adoption health did not accept the release')
        if current == value['before']:
            return report('rolled_back', reason='Previous source restored; failed release will not be reapplied')
        if health.get('installed') == value['commit'] and health.get('state') == 'verified':
            try:
                proof = sensor_health()
            except (OSError, ValueError, KeyError) as exc:
                proof = {'passed': False, 'error': type(exc).__name__}
            if proof['passed']:
                D.save_json(directory / 'sensor-health.json', proof)
                return report('complete', completed_at=instant.isoformat(), reason='Exact reviewed source installed and native sensor health verified')
            D.save_json(directory / 'sensor-health.json', proof)
            if instant.timestamp() - health['at'] > 360:
                with installation_lock():
                    if git(root, 'rev-parse', 'HEAD') != value['commit'] or git(root, 'status', '--porcelain'):
                        return report('held', reason='Owner source changes prevent sensor-health rollback')
                    health.update(state='rolling_back', feature_failure='native_sensor_contract_failed')
                    store.meta('install_health', health, write=True)
                return report('rollback_requested', reason='Native sensor contract failed its six-minute verification; independent guardian owns rollback')
        return report('verifying_health', reason='Waiting for independent adoption and live native sensor verification')

    if current == value['before']:
        if digest(value['observer_path']) != value['observer_sha256'] or digest(value['monitor_path']) != value['monitor_sha256']:
            return report('held', reason='Commissioning observer identity or implementation changed')
        try:
            passing_proof = read(value['validation_path'])
            ready = validation_ready(value, passing_proof)
        except (ValueError, OSError):
            clear_marker()
            return report('held', reason='Commissioning evidence was revoked or no longer validates')
        if not ready:
            clear_marker()
            return report('waiting_validation', reason='Waiting for the actual four-hour passing result; no source change')
        if health.get('installed') != value['before'] or health.get('state') != 'verified':
            return report('held', reason='Baseline adoption health changed')
        D.save_json(directory / 'commissioning-proof.json', passing_proof)
    if not eligible(root, instant):
        clear_marker()
        return report('waiting_window', reason='Owner pause or normal schedule controls prevent adoption')
    if (runtime / 'INSTALLING').exists() and owner.get('id') != value['id']:
        return report('waiting_installer', reason='Another verified source installer owns the boundary')
    if current == value['before'] and owner.get('id') == value['id'] and time.time() - owner['since'] > 3600:
        clear_marker()
        return report('held', reason='Healthy work did not reach an idle boundary within one hour')

    stopped_controller = False
    try:
        with installation_lock():
            check_authority(root)
            if not eligible(root, dt.datetime.now(dt.timezone.utc)) or dt.datetime.now(dt.timezone.utc) >= dt.datetime.fromisoformat(value['expires_at']):
                clear_marker()
                return report('waiting_window', reason='Owner controls changed before adoption')
            owner = store.meta('deferred_install_owner') or {}
            if (runtime / 'INSTALLING').exists() and owner.get('id') != value['id']:
                return report('waiting_installer', reason='Another source installer acquired the boundary')
            if owner.get('id') != value['id']:
                store.meta('deferred_install_owner', {'id': value['id'], 'since': time.time()}, write=True)
            D.marker(runtime / 'INSTALLING', True)
            if current == value['before'] and other_owners(store, identity):
                return report('draining', reason='Current healthy owned work finishes before source adoption')
            if git(root, 'rev-parse', value['commit'] + '^{tree}') != value['tree']:
                raise ValueError('Prepared merge tree differs from reviewed source')
            if git(root, 'rev-parse', 'HEAD') not in {value['before'], value['commit']} or git(root, 'status', '--porcelain'):
                raise ValueError('Source changed before adoption')
            if current == value['before']:
                report('adopting', intent_at=instant.isoformat(), adoption_intent={
                    'before': value['before'], 'commit': value['commit'], 'tree': value['tree'],
                    'proof_sha256': digest(directory / 'commissioning-proof.json')})
                subprocess.run(['/bin/launchctl', 'kill', 'SIGTERM', f'gui/{os.getuid()}/space.venin.midnight'], capture_output=True, timeout=10)
                stopped_controller = True
                with ExitStack() as stack:
                    stack.enter_context(file_lock(runtime / 'controller.lock'))
                    for project in read(root / 'projects.json')['projects']:
                        if not re.fullmatch(r'[a-zA-Z0-9_-]+', project['id']):
                            raise ValueError('Invalid registered project identity')
                        stack.enter_context(file_lock(runtime / 'projects' / project['id'] / 'supervisor.lock'))
                    fresh = dt.datetime.now(dt.timezone.utc)
                    if (other_owners(store, identity) or not eligible(root, fresh)
                            or fresh >= dt.datetime.fromisoformat(value['expires_at'])
                            or git(root, 'rev-parse', 'HEAD') != value['before'] or git(root, 'status', '--porcelain')):
                        raise P.PhaseInterrupted('Ownership or controls changed before mutation')
                    git(root, 'update-ref', 'refs/midnight/deferred/' + value['id'], value['before'])
                    git(root, 'merge', '--ff-only', value['commit'])
            adopted = D.read_json(directory / 'installation.json')
            if not adopted:
                adopted = {'previous': value['before'], 'installed': value['commit'], 'tree': value['tree'], 'at': dt.datetime.now(dt.timezone.utc).isoformat()}
                D.save_json(directory / 'installation.json', adopted)
            if health.get('installed') != value['commit']:
                store.meta('install_health', {**adopted, 'at': time.time(), 'state': 'verifying', 'repair': value['id'],
                    'controller_sha256': digest(root / 'midnight.py'), 'dashboard_sha256': digest(root / 'dashboard.py')}, write=True)
            if not journal.get('guardian_installed'):
                command = "import sys;sys.path.insert(0,sys.argv[1]);import install_guardian;install_guardian.install(sys.argv[1])"
                result = subprocess.run([sys.executable, '-I', '-B', '-c', command, str(root)],
                                        capture_output=True, text=True, timeout=30)
                if result.returncode:
                    raise RuntimeError('Independent guardian installation failed')
                report('resuming', guardian_installed=True)
        if dt.datetime.now(dt.timezone.utc) >= dt.datetime.fromisoformat(value['expires_at']):
            clear_marker()
            return report('held', reason='Prepared release expired before service adoption')
        restart_services(root, directory, value['id'])
        clear_marker()
        return report('verifying_health', reason='Services restarted; independent health and native readings must pass')
    except Exception:
        if git(root, 'rev-parse', 'HEAD') == value['before']:
            clear_marker()
            if stopped_controller:
                GU.restart_controller()
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--release-id', required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if root != Path.home()/'Developer'/'midnight' or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', args.release_id):
        raise ValueError('Unadmitted deferred release target')
    directory = root / 'runtime/deferred-releases' / args.release_id
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with file_lock(directory / 'watcher.lock', seconds=.2):
        try:
            value = manifest(Path(args.manifest), args.sha256)
            if Path(value['root']).resolve() != root or value['id'] != args.release_id:
                raise ValueError('Pinned release target differs from invocation')
            result = tick(root, value, directory)
            if result.get('state') == 'complete':
                receipt = completion_record(value, directory)
                result = {**result, 'completion_record_status': receipt.get('status'), 'completion_record_reason': receipt.get('reasonCode')}
                D.save_json(directory / 'state.json', result)
        except Exception as exc:
            # A rejected manifest or a pre-install failure must never leave a
            # permanent dispatch hold. Installed restart intents retain their
            # marker until their individually journalled requests reconcile.
            current = git(root, 'rev-parse', 'HEAD')
            installed = locals().get('value', {}).get('commit')
            if current != installed:
                release_marker(root / 'runtime', args.release_id)
            result = D.read_json(directory / 'state.json', {}) or {}
            result.update(last_error=type(exc).__name__, error_at=dt.datetime.now(dt.timezone.utc).isoformat())
            D.save_json(directory / 'state.json', result)
        print(json.dumps(result))


if __name__ == '__main__':
    main()
