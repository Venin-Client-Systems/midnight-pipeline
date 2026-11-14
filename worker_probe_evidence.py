"""Read-only checks of the fixed representative worker's actual tool receipts."""
import json,re
from pathlib import Path

import evidence_context as E
import structured_worker as S
import worker_probe_cases as C

QUALIFICATION_SOURCES = ('worker_probe.py', 'worker_probe_runtime.py', 'worker_probe_client.py',
                         'worker_recovery.py', 'worker_probe_cases.py', 'worker_probe_evidence.py')
EXECUTION_DEPENDENCIES = ('local_supervisor.py', 'model_bench.py', 'model_manager.py',
                        'colibri_provider.py', 'phase_runtime.py', 'durability.py',
                        'worker_probe_runtime.py', 'verification_sandbox.py')


def read(path, root):
    if not E.safe_file(path, root) or path.stat().st_size > 256000:
        raise ValueError('Representative qualification receipt is unavailable')
    return json.loads(path.read_text())


def terminal_handoff_complete(directory, work, task_id, model):
    """Observe the complete publication boundary before stopping owned workers."""
    directory = Path(directory)
    if not S.submission_complete(directory, work, task_id, model):
        return False
    try:
        decisions = sorted((directory / 'worker-tools').glob('decision-*.json'),
                           key=lambda p: int(p.stem.split('-')[1]))
        if not decisions or len(decisions) > 32:
            return False
        decision = read(decisions[-1], directory)
        index = decisions[-1].stem.split('-')[1]
        result = read(directory / 'worker-tools' / ('tool-result-' + index + '.json'), directory)
        if (decision.get('status') != 'completed' or decision.get('value', {}).get('action') != 'finish'
                or result.get('finished') is not True or 'error' in result):
            return False
        path = directory / 'worker-events.jsonl'
        if not E.safe_file(path, directory) or path.stat().st_size > 4 * 1024 * 1024:
            return False
        events = [json.loads(line) for line in path.read_text().splitlines()]
        if any(not isinstance(e, dict) or e.get('type') in ('error', 'turn.failed', 'worker.inference_failed') for e in events):
            return False
        completed = [i for i, e in enumerate(events) if e.get('type') == 'turn.completed']
        handoffs = [(i, e) for i, e in enumerate(events) if e.get('type') == 'worker.result_handoff']
        clients = [(i, e) for i, e in enumerate(events) if e.get('type') == 'worker.client_result']
        return (len(completed) == len(handoffs) == len(clients) == 1
                and completed[0] < handoffs[0][0] < clients[0][0]
                and type(handoffs[0][1].get('schema_version')) is int
                and handoffs[0][1] == {'type': 'worker.result_handoff', 'schema_version': 1, 'status': 'completed'}
                and type(clients[0][1].get('schema_version')) is int
                and clients[0][1].get('schema_version') == 1
                and type(clients[0][1].get('exit_code')) is int and clients[0][1]['exit_code'] == 0)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def phase_evidence(directory, model, representative=False):
    directory = Path(directory)
    tools = directory / 'worker-tools'
    decisions = sorted(tools.glob('decision-*.json'), key=lambda p: int(p.stem.split('-')[1]))
    if not decisions or len(decisions) > 32:
        raise ValueError('Representative qualification requires bounded actual decisions')
    reads = set()
    first_edit = None
    finished = False
    tested_after_edit = False
    edited = False
    max_prompt = 0
    hashes = {}
    policy = None
    for path in decisions:
        finished = False
        index = path.stem.split('-')[1]
        decision = read(path, directory)
        phase_paths = [(path, decision, 'decision', 1024)]
        action = decision.get('value', {})
        result_path = tools / ('tool-result-' + index + '.json')
        result = read(result_path, directory)
        hashes[str(result_path.relative_to(directory))] = S.digest(result_path)
        if action.get('action') == 'edit_file':
            edit_path = tools / ('edit-' + index + '.json')
            # edit_snapshot may reject the action before inference is called.
            # A handled precondition error has no edit receipt to invent. Any
            # existing inference receipt, including a failed one, is validated.
            precondition_error = isinstance(result.get('error'), str) and bool(result['error'])
            if edit_path.exists() or edit_path.is_symlink() or not precondition_error:
                phase_paths.append((edit_path, read(edit_path, directory), 'edit', 4096))
        for receipt_path, receipt, phase, budget in phase_paths:
            actual_policy = receipt.get('inference_policy', {})
            if (receipt.get('schema_version') != 2 or receipt.get('status') != 'completed'
                    or receipt.get('completion_reason') != 'stop'
                    or receipt.get('phase') != phase or receipt.get('requested_budget') != budget
                    or receipt.get('worker_contract') != S.CONTRACT or receipt.get('model') != model
                    or actual_policy.get('version') != S.INFERENCE_POLICY
                    or actual_policy.get('model') != model
                    or type(actual_policy.get('thinking_capable')) is not bool
                    or not isinstance(actual_policy.get('metadata_sha256'), str)
                    or not re.fullmatch('[a-f0-9]{64}', actual_policy['metadata_sha256'])):
                raise ValueError('Incomplete or changed representative inference policy receipt')
            if policy is not None and policy != actual_policy:
                raise ValueError('Representative inference policy changed within the worker')
            policy = actual_policy
            metrics = receipt.get('metrics', {})
            count = metrics.get('prompt_eval_count')
            if type(count) is not int or count <= 0:
                raise ValueError('Representative prompt usage is unavailable')
            if (type(metrics.get('eval_count')) is not int or not 0 < metrics['eval_count'] <= budget
                    or metrics.get('done_reason') != 'stop'):
                raise ValueError('Representative inference usage or completion is invalid')
            if phase == 'decision':
                max_prompt = max(max_prompt, count)
            hashes[str(receipt_path.relative_to(directory))] = S.digest(receipt_path)
        if 'error' in result:
            continue
        name = action.get('action')
        if name == 'read_file':
            if result.get('path') != action.get('path'):
                raise ValueError('Representative read receipt has a different path')
            reads.add(action['path'])
        elif name == 'edit_file':
            if result.get('written') != 'solution.py':
                raise ValueError('Representative edit did not write its fixed source')
            if first_edit is None:
                first_edit = sorted(reads)
            edited = True
            tested_after_edit = False
        elif name == 'run_tests':
            tested_after_edit = edited and result.get('code') == 0 and result.get('reason') == 'exited'
        elif name == 'finish':
            finished = result.get('finished') is True and tested_after_edit
    if not finished or not edited:
        raise ValueError('Representative read/edit/check/finish sequence is incomplete')
    if representative and (not set(C.REQUIRED_READS) <= set(first_edit or []) or max_prompt < C.MIN_PROMPT_TOKENS):
        raise ValueError('Representative accumulated reads or production context are insufficient')
    return {'files': hashes, 'inference_policy': policy, 'first_edit_reads': first_edit,
            'max_decision_prompt_tokens': max_prompt, 'verified_sequence': True}
