"""Bounded, read-only evidence for managers without local action tools.

Only host-owned receipt directories and registered source paths are read.
Collection never runs commands, repairs mirrors or writes a cache.
"""
import hashlib
import json
import math
from pathlib import Path
import re

SAFE_ID = re.compile(r'[A-Za-z0-9_-]{1,160}')
SOURCE = re.compile(r'(?:src|tests|scripts|exampleapp)/[A-Za-z0-9_./-]+\.(?:py|tsx?|jsx?|cjs|mjs)')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def sanitize(text):
    text = re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9_.~-]{12,}', r'\1[REDACTED]', text)
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b', '[REDACTED]', text)
    text = re.sub(r'(?is)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----', '[REDACTED PRIVATE KEY]', text)
    text = re.sub(r'(?i)(https?://)[^/\s:@]+:[^@\s/]+@', r'\1[REDACTED]@', text)
    return re.sub(r'''(?ix)(?<![A-Z0-9_])((?:["']?(?:[A-Z_]{0,48}PASSWORD|[A-Z_]{0,48}SECRET(?:_ACCESS_KEY)?|API[_-]?KEY|[A-Z_]{0,48}ACCESS[_-]?TOKEN|AUTHORIZATION)["']?)\s*[=:]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;]+)''', r'\1"[REDACTED]"', text)


def sanitized(value, limit=8192, depth=0):
    if depth > 8:
        return {'truncated': True, 'reason': 'Context nesting limit'}
    if isinstance(value, str):
        raw = sanitize(value).encode()
        return raw[:limit].decode('utf8', errors='replace') + (' [truncated]' if len(raw)>limit else '')
    if isinstance(value, list):
        result = [sanitized(v,limit,depth+1) for v in value[:16]]
        if len(value)>16:result.append({'omitted_items':len(value)-16})
        return result
    if isinstance(value, dict):
        result = {str(k)[:160]:sanitized(v,limit,depth+1) for k,v in list(value.items())[:32]}
        if len(value)>32:result['omitted_fields']=len(value)-32
        # Redact structured secrets before serialization as well as text logs.
        for key in result:
            if re.search(r'(?i)(password|secret|api.?key|access.?token|authorization)',key):
                result[key]='[REDACTED]'
        return result
    if value is None or type(value) in (int,bool):return value
    if type(value) is float:return value if math.isfinite(value) else None
    return '[Unsupported evidence value]'


def safe_file(path, root):
    path, root = Path(path), Path(root)
    try:
        relative = path.relative_to(root)
        resolved = path.resolve().relative_to(root.resolve())
        excluded = lambda parts:any(p.startswith('.env') or p in {'.git','node_modules','..'} for p in parts)
        parents = [root.joinpath(*relative.parts[:n]) for n in range(1,len(relative.parts)+1)]
        return (path.is_file() and not any(p.is_symlink() for p in parents)
                and not excluded(relative.parts) and not excluded(resolved.parts))
    except (OSError, ValueError):
        return False


def read_json(path, root):
    if not safe_file(path, root) or path.stat().st_size > 1024 * 1024:
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def excerpt(path, root, budget):
    if budget <= 0 or not safe_file(path, root):
        return None
    size = path.stat().st_size
    if size > 32 * 1024 * 1024:
        return {'unavailable': 'Log exceeds bounded collection size', 'saved_bytes': size}
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    text = raw.decode('utf-8', errors='replace')
    match = re.search(r'(?m)^(?:FAIL\b|FAIL:|ERROR:|Error:|\s*●|Traceback|AssertionError)', text)
    focus = text[match.start():] if match else text
    encoded = sanitize(focus).encode()
    if len(encoded) > budget:
        first = budget // 2
        encoded = encoded[:first] + b'\n[excerpt truncated; full output retained]\n' + encoded[-max(0, budget-first-50):]
    return {'sha256': sha, 'saved_bytes': size, 'truncated': len(raw) > len(encoded),
            'combined_stdout_stderr': encoded[:budget].decode('utf-8', errors='replace')}


def collect(root, runtime, task, state, head, *, profile_hash=None, findings=(), max_bytes=64000):
    """Bind current canonical failures and actual attempt receipts to source."""
    if type(max_bytes) is not int or max_bytes<2048:
        raise ValueError('Evidence budget must be at least 2048 bytes')
    root, runtime = Path(root), Path(runtime)
    folders = []
    # A later passing assessment must not hide the retained receipt which
    # created an open finding. Only exact-source, exact-profile canonical
    # assessment folders are eligible; finding text never grants file access.
    def references(value, depth=0):
        if depth > 6:
            return
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for item in value[:32]:
                yield from references(item, depth+1)
        elif isinstance(value, dict):
            for item in list(value.values())[:32]:
                yield from references(item, depth+1)
    for finding in list(findings)[:16]:
        for reference in references(finding.get('evidence', [])):
            path = Path(reference)
            if not path.is_absolute():
                path = runtime / path
            try:
                relative = path.relative_to(runtime)
            except ValueError:
                continue
            if (len(relative.parts) != 3 or relative.parts[0] != 'assessments'
                    or not SAFE_ID.fullmatch(relative.parts[1]) or not safe_file(path, runtime)):
                continue
            folder = path.parent
            base = read_json(folder / 'assessment-base.json', runtime)
            if base.get('head') == head and (not profile_hash or base.get('profile_hash') == profile_hash):
                folders.append(('canonical', folder, base))
    assessment = state.get('last_assessment', {}).get('run', '')
    if isinstance(assessment, str) and SAFE_ID.fullmatch(assessment):
        folder = runtime / 'assessments' / assessment
        base = read_json(folder / 'assessment-base.json', runtime)
        if base.get('head') == head and (not profile_hash or base.get('profile_hash') == profile_hash):
            folders.append(('canonical', folder, base))
    identities = {task.get('id'), (task.get('retry_of') or {}).get('task_id')}
    for identity in sorted(i for i in identities if isinstance(i, str)):
        history = state.get('feedback_history', {}).get(identity, [])[-2:]
        retained = state.get('retry_candidates', {}).get(identity, {})
        runs = [x.get('run') for x in history] + [retained.get('run')]
        for run in dict.fromkeys(runs):
            if isinstance(run, str) and SAFE_ID.fullmatch(run):
                folder = runtime / 'runs' / run
                folders.append(('attempt', folder, read_json(folder / 'task.json', runtime)))
    checks, passed, receipts, seen, remaining = [], [], [], set(), max_bytes
    for kind, folder, binding in folders:
        if str(folder) in seen:
            continue
        seen.add(str(folder))
        for directory in (folder, folder / 'quality-checks'):
            recorded = read_json(directory / 'checks.json', runtime)
            if not isinstance(recorded, list):
                continue
            for index, check in enumerate(recorded[:32]):
                if not isinstance(check, dict):
                    continue
                if check.get('code') == 0 and check.get('reason') == 'exited':
                    if kind == 'canonical':
                        passed.append({'receipt': str((directory / 'checks.json').relative_to(runtime)),
                                       'binding': {k:binding.get(k) for k in ('head','profile_hash')},
                                       'command': check.get('command'), 'code': 0, 'reason': 'exited'})
                    continue
                output = excerpt(directory / f'check-{index}.log', runtime, min(8192, remaining))
                if output:
                    remaining -= len(output.get('combined_stdout_stderr', '').encode())
                checks.append({'origin': kind, 'receipt': str((directory / f'check-{index}.log').relative_to(runtime)),
                               'binding': {k:binding.get(k) for k in ('head','profile_hash','id')},
                               'command': check.get('command'), 'code': check.get('code'),
                               'reason': check.get('reason'), 'output': output or {'unavailable': 'Output unavailable or context budget exhausted'}})
        if kind == 'attempt':
            review = read_json(folder / 'review/result.json', runtime)
            response = excerpt(folder / 'worker-result.txt', runtime, min(2000, remaining))
            if response:
                remaining -= len(response.get('combined_stdout_stderr', '').encode())
            process = read_json(folder / 'worker-process.json', runtime)
            receipts.append({'run': folder.name, 'review': review, 'review_sha256':digest(review), 'worker_response': response,
                             'process':{k:process.get(k) for k in ('reason','code','seconds')}})
    value = {'head': head, 'profile_hash': profile_hash, 'task_sha256': digest(task),
             'canonical_or_attempt_failures': checks, 'attempt_receipts': receipts,
             'canonical_passed_checks': passed,
             'finding_ids': [f.get('id') for f in findings], 'full_logs_retained': True}
    observation=read_json(runtime/'coordinator-observations.json',runtime)
    if observation.get('head')==head and observation.get('profile_hash')==profile_hash:
        value['coordinator_observations']=observation
    # Enforce the final UTF-8 JSON size including metadata, nested review text,
    # truncation markers and hash overhead. Never forward raw structured values.
    for limit in (8192,4096,2048,1024,512,128):
        result = sanitized(value,limit)
        result['context_truncated'] = result != value
        result['evidence_hash'] = digest(result)
        if len(json.dumps(result).encode())<=max_bytes:
            return result
    result = {k:value[k] for k in ('head','profile_hash','task_sha256')}
    result.update(context_truncated=True,full_logs_retained=True,
                  collection_hash=digest(sanitized(value,128)),
                  finding_ids=[],canonical_or_attempt_failures=[],attempt_receipts=[],
                  explanation='Evidence exceeds this context budget; request a narrower approved check excerpt.')
    result=sanitized(result,64);result['evidence_hash']=digest(result)
    if len(json.dumps(result).encode())>max_bytes:
        raise ValueError('Evidence budget cannot contain the mandatory source binding')
    return result


def source_context(root, task, findings, proof, fallback=(), *, max_bytes=32000):
    root = Path(root)
    # Narrow repairs must show the actual writable code before frozen parent
    # files can consume the byte budget. All original paths remain evidence.
    candidates = list(task.get('local_worker_files', [])) + list(task.get('files', []))
    text = json.dumps([list(findings), proof], ensure_ascii=False)
    candidates += SOURCE.findall(text)
    if not candidates:
        candidates = [str(Path(p).relative_to(root)) for p in fallback]
    context, remaining = {}, max_bytes-2
    for name in dict.fromkeys(candidates):
        if not isinstance(name, str) or any(c in name for c in '*?[') or remaining <= 0:
            continue
        path = root / name
        if not safe_file(path, root):
            continue
        if path.stat().st_size>1024*1024:
            entry={'unavailable':'Source exceeds bounded collection size'}
            size=len(json.dumps({name:entry}).encode())
            if size>remaining:break
            context[name]=entry;remaining-=size+2
            continue
        raw = path.read_bytes()
        budget = min(remaining, 16000)
        content = sanitize(raw[:budget].decode('utf-8', errors='replace'))
        entry = {'sha256': hashlib.sha256(raw).hexdigest(), 'content': content, 'truncated': len(raw)>budget}
        while len(json.dumps({name:entry}).encode())>remaining and entry['content']:
            entry['content']=entry['content'][:len(entry['content'])//2];entry['truncated']=True
        size=len(json.dumps({name:entry}).encode())
        if size>remaining:break
        context[name]=entry;remaining-=size+2
    return context
