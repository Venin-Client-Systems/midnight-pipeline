"""Operator-only PR checks from retained, source-bound host evidence.

This entry point publishes statuses only. It cannot merge, adopt source, alter
protection, or turn a worker's claimed success into a host receipt. Its input
directory is populated by the trusted operator after complete tests and a
separate review. The hashes bind that evidence; they are not reviewer signatures.
"""
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

import git_delivery as G

REPOSITORY = G.ORGANISATION + '/midnight'
REVIEWER = 'Codex independent cross-review'
SUITE = ['-m', 'unittest', 'discover', '-s', 'tests', '-v']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_bytes(root, relative, limit=1024 * 1024, *, allow_empty=False):
    """Read one bounded regular evidence file without following links."""
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('Evidence path must be relative')
    parts = relative.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise ValueError('Evidence path escapes its directory')
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory); directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not (0 if allow_empty else 1) <= before.st_size <= limit:
                raise ValueError('Evidence file is unsafe or oversized')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                data = stream.read(limit + 1)
            after = os.fstat(fd)
            path_state = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
            identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
            if identity(before) != identity(after) or identity(after) != identity(path_state):
                raise ValueError('Evidence file changed while reading')
            if len(data) != before.st_size:
                raise ValueError('Evidence bytes changed')
            return data
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def checked_bytes(root, relative, expected, limit=1024 * 1024):
    if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('Evidence digest missing')
    data = safe_bytes(root, relative, limit)
    if digest(data) != expected: raise ValueError('Evidence bytes changed')
    return data


def checked_checkout(checkout, head):
    flags = G.git(checkout, 'ls-files', '-v', '-z').split('\0')
    if any(row and not row.startswith('H ') for row in flags):
        raise ValueError('Hidden or unresolved Git index flags prevent attestation')
    entries = G.git(checkout, 'ls-tree', '-r', '-z', '--full-tree', head).split('\0')
    for row in filter(None, entries):
        metadata, relative = row.split('\t', 1)
        mode, kind, blob = metadata.split()
        if mode not in {'100644', '100755'} or kind != 'blob':
            raise ValueError('Unsupported source entry requires operator review')
        data = safe_bytes(checkout, relative, 32 * 1024 * 1024, allow_empty=True)
        actual = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if actual != blob:
            raise ValueError('Actual tracked bytes differ from reviewed source')
        executable = bool((checkout / relative).lstat().st_mode & 0o111)
        if executable != (mode == '100755'):
            raise ValueError('Actual tracked mode differs from reviewed source')


def object_json(data):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value: raise ValueError('Duplicate evidence field')
            value[key] = item
        return value
    def invalid(_): raise ValueError('Non-finite evidence value')
    value = json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(value, dict): raise ValueError('Evidence must be an object')
    return value


def artifact(root, descriptor, limit=1024 * 1024):
    if not isinstance(descriptor, dict): raise ValueError('Evidence descriptor missing')
    return checked_bytes(root, descriptor.get('path'), descriptor.get('sha256'), limit)


def local_evidence(root, request):
    if type(request.get('schema_version')) is not int or request['schema_version'] != 1:
        raise ValueError('Unsupported attestation request')
    if request.get('kind') != 'codex-pr-attestation' or request.get('repository') != REPOSITORY:
        raise ValueError('Unapproved attestation repository or kind')
    if type(request.get('pr')) is not int or request['pr'] <= 0:
        raise ValueError('Invalid PR identity')
    source = {key: request.get(key) for key in ('base', 'head', 'tree')}
    if any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value) for value in source.values()):
        raise ValueError('Exact source identity missing')
    source_path = request.get('source_root')
    if not isinstance(source_path, str) or not Path(source_path).is_absolute():
        raise ValueError('Isolated source directory missing')
    checkout = Path(source_path)
    if G.git(checkout, 'rev-parse', 'HEAD') != source['head'] or G.git(checkout, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('Reviewed checkout changed')
    if G.git(checkout, 'rev-parse', 'HEAD^{tree}') != source['tree']:
        raise ValueError('Reviewed source tree changed')
    if G.git(checkout, 'symbolic-ref', 'HEAD') == 'refs/heads/main':
        raise ValueError('PR attestation requires an isolated branch')
    G.git(checkout, 'merge-base', '--is-ancestor', source['base'], source['head'])
    checked_checkout(checkout, source['head'])
    checks = object_json(artifact(root, request.get('checks')))
    review = object_json(artifact(root, request.get('review')))
    for evidence in (checks, review):
        if type(evidence.get('schema_version')) is not int or evidence['schema_version'] != 2 or evidence.get('source') != source:
            raise ValueError('Receipt is not bound to exact base/head/tree')
    if checks.get('passed') is not True or not isinstance(checks.get('commands'), list) or not checks['commands']:
        raise ValueError('Complete host checks missing')
    full_suite = False
    for command in checks['commands']:
        if not isinstance(command, dict) or type(command.get('returncode')) is not int or command['returncode'] != 0:
            raise ValueError('Host command did not pass')
        argv = command.get('command')
        if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or not arg.strip() for arg in argv):
            raise ValueError('Host command missing')
        log = artifact(root, command.get('log'), 32 * 1024 * 1024).decode('utf-8')
        if argv[1:] == SUITE and re.fullmatch(r'python3(?:\.\d+)?', Path(argv[0]).name):
            terminal = re.search(r'\nRan ([1-9][0-9]*) tests? in [0-9.]+s\n\nOK\s*\Z', log)
            if not terminal or type(command.get('tests')) is not int or int(terminal[1]) != command['tests']:
                raise ValueError('Complete suite terminal evidence missing')
            if command.get('skips') != 0 or type(command.get('skips')) is not int:
                raise ValueError('Skipped suite cannot attest')
            full_suite = True
    if not full_suite: raise ValueError('Complete unittest discovery is required')
    if (review.get('decision') != 'accept' or review.get('issues') != [] or review.get('reviewer') != REVIEWER
            or review.get('checks_sha256') != request['checks']['sha256']
            or not isinstance(review.get('evidence'), list) or not review['evidence']
            or any(not isinstance(item, str) or not item.strip() for item in review['evidence'])):
        raise ValueError('Independent accepted review of these checks is required')
    return source


def remote_evidence(api, request, source):
    delivery = G.GitDelivery(Path(request['source_root']), 'midnight', {'repository': REPOSITORY}, api=api)
    delivery.identity(); delivery.protection()
    endpoint = 'repos/' + REPOSITORY
    pr = api(endpoint + '/pulls/' + str(request['pr']))
    if (pr.get('number') != request['pr'] or pr.get('state') != 'open' or pr.get('merged')
            or pr.get('head', {}).get('sha') != source['head']
            or pr.get('head', {}).get('repo', {}).get('full_name') != REPOSITORY
            or pr.get('base', {}).get('ref') != 'main' or pr.get('base', {}).get('sha') != source['base']
            or pr.get('base', {}).get('repo', {}).get('full_name') != REPOSITORY):
        raise ValueError('PR identity, head or main base changed')
    if api(endpoint + '/git/ref/heads/main').get('object', {}).get('sha') != source['base']:
        raise ValueError('Remote main advanced')
    if api(endpoint + '/git/commits/' + source['head']).get('tree', {}).get('sha') != source['tree']:
        raise ValueError('Remote head tree differs from review')


def controls(runtime, cutoff):
    try: (runtime / 'PAUSED').lstat()
    except FileNotFoundError: pass
    else: raise ValueError('Pipeline paused or pause marker requires inspection')
    if cutoff is not None and dt.datetime.now(dt.timezone.utc) >= cutoff:
        raise ValueError('Attestation cutoff reached')


def statuses(api, endpoint, head):
    rows = []
    for page in range(1, 1001):
        batch = api(endpoint + '/commits/' + head + '/statuses?per_page=100&page=' + str(page))
        if not isinstance(batch, list): raise ValueError('Status readback is malformed')
        rows.extend(batch)
        if len(batch) < 100: return rows
    raise ValueError('Status readback exceeds bounded pagination; held')


def matching_status(rows, context, description, target):
    same_context = lambda row: isinstance(row, dict) and isinstance(row.get('context'), str) and row['context'].casefold() == context.casefold()
    matching = [row for row in rows if same_context(row)
        and row.get('description') == description and row.get('target_url') == target
        and row.get('state') == 'success' and isinstance(row.get('creator'), dict) and row['creator'].get('login') == G.IDENTITY
        and type(row.get('id')) is int]
    if len(matching) > 1: raise ValueError('Duplicate status publications require inspection')
    if matching:
        latest = next((row for row in rows if same_context(row)), None)
        if latest is not matching[0]: raise ValueError('A newer status supersedes this attestation')
    return matching[0] if matching else None


def journal_state(raw, request_sha256, request):
    if raw is None:
        return {'schema_version': 1, 'request_sha256': request_sha256, 'effects': {}}
    state = object_json(raw)
    if (type(state.get('schema_version')) is not int or state['schema_version'] != 1
            or state.get('request_sha256') != request_sha256 or not isinstance(state.get('effects'), dict)
            or state.get('status') not in {'publishing', 'held', 'complete'}):
        raise ValueError('Attestation journal identity or schema changed')
    for context, effect in state['effects'].items():
        if context not in G.CONTEXTS or not isinstance(effect, dict):
            raise ValueError('Attestation effect is malformed')
        if effect == {'state': 'intent'}: continue
        if (set(effect) != {'state', 'id'} or effect.get('state') != 'confirmed'
                or type(effect.get('id')) is not int or effect['id'] <= 0):
            raise ValueError('Attestation effect lost its uncertainty or confirmation')
    source = {key: request.get(key) for key in ('base', 'head', 'tree')}
    if (('source' in state and state['source'] != source)
            or ('repository' in state and state['repository'] != REPOSITORY)
            or ('pr' in state and state['pr'] != request['pr'])):
        raise ValueError('Attestation journal source changed')
    if state['status'] == 'complete' and (set(state['effects']) != set(G.CONTEXTS)
            or any(effect['state'] != 'confirmed' for effect in state['effects'].values())
            or state.get('source') != source or state.get('repository') != REPOSITORY
            or state.get('pr') != request['pr']):
        raise ValueError('Completed attestation journal is incomplete')
    return state


def read_journal(folder, name):
    try: return safe_bytes(folder, name, 65536)
    except FileNotFoundError:
        # A dangling link is present evidence, never a new empty journal.
        try: (folder / name).lstat()
        except FileNotFoundError: return None
        raise ValueError('Attestation journal cannot be read safely')


def attest(runtime, request_path, *, request_sha256, api=None, cutoff=None):
    """Journal and reconcile two checks; never merge or install anything."""
    runtime = Path(runtime).absolute(); request_path = Path(request_path).absolute()
    evidence = runtime / 'coordinator-release-evidence'
    relative = request_path.relative_to(evidence).as_posix()
    api = api or G.gh
    raw = checked_bytes(evidence, relative, request_sha256)
    request = object_json(raw); root = request_path.parent
    # Lock all requests, not just a digest, so different operators cannot race
    # publication for the same PR using different receipt bundles.
    folder = runtime / 'coordinator-attestations'
    try:
        folder.mkdir(exist_ok=True)
        folder_info = folder.lstat()
        if not stat.S_ISDIR(folder_info.st_mode): raise ValueError('Unsafe attestation journal directory')
        fd = os.open(folder / 'writer.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd); raise ValueError('Unsafe attestation writer lock')
    except (ValueError, OSError) as exc:
        return {'status': 'held', 'reason': str(exc)[:300]}
    with os.fdopen(fd, 'r+') as lock:
        lock_info = os.fstat(fd)
        def writer_identity():
            current_folder = folder.lstat(); current_lock = (folder / 'writer.lock').lstat()
            same = lambda before, after: (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
            if (not stat.S_ISREG(current_lock.st_mode) or current_lock.st_nlink != 1
                    or not same(folder_info, current_folder) or not same(lock_info, current_lock)):
                raise ValueError('Attestation writer identity changed')
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return {'status': 'held', 'reason': 'Attestation writer is busy'}
        path = folder / (request_sha256 + '.json')
        try:
            writer_identity()
            saved_bytes = read_journal(folder, path.name)
            state = journal_state(saved_bytes, request_sha256, request)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            # Never replace malformed/unsafe evidence with a synthetic journal.
            return {'status': 'held', 'reason': str(exc)[:300]}
        def journal_unchanged():
            writer_identity()
            if read_journal(folder, path.name) != saved_bytes:
                raise ValueError('Attestation journal changed concurrently; preserve it')
        def persist():
            nonlocal saved_bytes
            journal_unchanged()
            G.save(path, state)
            saved_bytes = (json.dumps(state, indent=2, allow_nan=False) + '\n').encode()
        endpoint = 'repos/' + REPOSITORY
        target = 'https://github.com/' + REPOSITORY + '/pull/' + str(request.get('pr'))
        description = 'Reviewed source/evidence ' + request_sha256 + '\nMidnight automation'
        def validate():
            journal_unchanged()
            if checked_bytes(evidence, relative, request_sha256) != raw:
                raise ValueError('Request changed')
            source = local_evidence(root, request)
            remote_evidence(api, request, source)
            # External reads can race a local control or evidence change.
            checked_bytes(evidence, relative, request_sha256)
            local_evidence(root, request); controls(runtime, cutoff)
            journal_unchanged()
            return source
        try:
            source = validate()
            for context in G.CONTEXTS:
                rows = statuses(api, endpoint, source['head'])
                found = matching_status(rows, context, description, target)
                effect = state['effects'].get(context)
                if found:
                    state['effects'][context] = {'state': 'confirmed', 'id': found['id']}
                    persist(); continue
                if effect:
                    raise ValueError('Uncertain or missing prior status remains held; no repeat publication')
                validate()
                state['effects'][context] = {'state': 'intent'}
                state['status'] = 'publishing'; persist()
                # Intent persistence itself is a crash/control-change boundary.
                validate()
                api(endpoint + '/statuses/' + source['head'], 'POST',
                    {'state': 'success', 'context': context, 'description': description, 'target_url': target})
                found = matching_status(statuses(api, endpoint, source['head']), context, description, target)
                if not found: raise ValueError('Status publication is unconfirmed')
                state['effects'][context] = {'state': 'confirmed', 'id': found['id']}
                persist()
            validate()
            # Success requires both exact receipts still present on the final read.
            rows = statuses(api, endpoint, source['head'])
            if not all(matching_status(rows, context, description, target) for context in G.CONTEXTS):
                raise ValueError('Final status readback incomplete')
            validate()
            state.update(status='complete', source=source, repository=REPOSITORY, pr=request['pr'],
                reason='Both exact-source checks confirmed; merge and installation remain separate')
        except (ValueError, RuntimeError, OSError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
            state.update(status='held', reason=str(exc)[:300])
        try: persist()
        except InterruptedError: raise
        except (ValueError, OSError) as exc:
            state.update(status='held', reason=str(exc)[:300])
        return state
