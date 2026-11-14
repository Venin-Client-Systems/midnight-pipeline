"""Finish reviewed coordinator source releases after GitHub protection is ready.

A source release may already be installed locally. A confirmed squash merge is
adopted only when both source trees are byte-identical, retaining the old commit
under a recovery ref. This never adopts a product worker's unreviewed changes.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess
import git_delivery as G


def attest_pr(runtime, request_path, *, request_sha256, api=None, cutoff=None):
    """Explicit operator-only checks for an isolated, independently reviewed PR."""
    from coordinator_attestation import attest
    return attest(runtime, request_path, request_sha256=request_sha256, api=api, cutoff=cutoff)


def reconcile(runtime,*,api=None,cutoff=None):
    runtime=Path(runtime);path=runtime/'coordinator-releases.json';state=G.read(path)
    if not state:return {'status':'none'}
    api=api or G.gh
    for item in state.get('releases',[]):
        if item.get('status')=='complete':continue
        root=Path(item['canonical']);repo=item['repository'];base=item['base'];head=item['head']
        if repo not in {G.ORGANISATION+'/midnight',G.ORGANISATION+'/exampleapp'}:raise ValueError('Unadmitted coordinator repository')
        try:
            delivery=G.GitDelivery(runtime,root.name,{'repository':repo,'identity':G.IDENTITY,'base':'main'},api=api)
            delivery.identity();delivery.protection()
            for key in ('checks','review'):
                evidence=Path(item[key+'_path'])
                if hashlib.sha256(evidence.read_bytes()).hexdigest()!=item[key+'_sha256']:raise ValueError('Coordinator evidence changed')
            review=json.loads(Path(item['review_path']).read_text())
            checks=json.loads(Path(item['checks_path']).read_text())
            for evidence in (review,checks):
                if not isinstance(evidence,dict):raise ValueError('Coordinator evidence must be an object')
                if evidence.get('schema_version')!=1 or evidence.get('head')!=head or evidence.get('tree')!=item['tree']:
                    raise ValueError('Coordinator evidence is not bound to the exact source')
            if review.get('decision')!='accept' or review.get('issues')!=[] or review.get('reviewer')!='Codex independent cross-review' or not isinstance(review.get('evidence'),list) or not review['evidence']:
                raise ValueError('Coordinator review is not accepted')
            commands=checks.get('commands')
            if checks.get('passed') is not True or not isinstance(commands,list) or not commands or any(
                not isinstance(c,dict) or type(c.get('returncode')) is not int or c['returncode']!=0 or
                not isinstance(c.get('command'),list) or not c['command'] or any(not isinstance(x,str) or not x.strip() for x in c['command']) or not re.fullmatch('[0-9a-f]{64}',c.get('log_sha256','')) for c in commands):
                raise ValueError('Coordinator checks did not pass')
            if G.git(root,'symbolic-ref','HEAD')!='refs/heads/main':raise ValueError('Coordinator canonical branch changed')
            if G.git(root,'status','--porcelain'):raise ValueError('Coordinator source has local edits')
            if G.git(root,'rev-parse',head+'^{tree}')!=item['tree']:raise ValueError('Coordinator source tree does not match evidence')
            current=G.git(root,'rev-parse','HEAD')
            if current not in {head,item.get('merge_commit')}:raise ValueError('Coordinator source advanced; keep pending for manager')
            pr=api('repos/'+repo+'/pulls/'+str(item['pr']))
            if pr['head']['sha']!=head or pr['base'].get('ref')!='main':raise ValueError('Coordinator PR head or target changed')
            if not pr.get('merged'):
                if pr['base']['sha']!=base:raise ValueError('Coordinator PR base changed; reverify')
                for context in G.CONTEXTS:
                    api('repos/'+repo+'/statuses/'+head,'POST',{'state':'success','context':context,'description':'Coordinator source tests and independent review passed'})
                if cutoff and dt.datetime.now(dt.timezone.utc)>=cutoff:raise ValueError('Schedule cutoff reached')
                if (runtime/'PAUSED').exists():raise ValueError('Pipeline paused')
                if G.git(root,'rev-parse','HEAD')!=head or G.git(root,'status','--porcelain'):raise ValueError('Coordinator source changed before merge')
                delivery.protection()
                fresh=api('repos/'+repo+'/pulls/'+str(item['pr']))
                if fresh['head']['sha']!=head or fresh['base'].get('ref')!='main' or fresh['base']['sha']!=base:raise ValueError('Coordinator PR changed before merge')
                item['status']='merge_intent';G.save(path,state)
                api('repos/'+repo+'/pulls/'+str(item['pr'])+'/merge','PUT',{'sha':head,'merge_method':'squash',
                    'commit_title':item['title'],'commit_message':'Reviewed Midnight coordinator release.\n\nMidnight automation'})
                pr=api('repos/'+repo+'/pulls/'+str(item['pr']))
            if not pr.get('merged'):raise ValueError('Coordinator merge not confirmed')
            merged=pr['merge_commit_sha']
            if not re.fullmatch('[0-9a-f]{40}',merged):raise ValueError('Invalid coordinator merge receipt')
            item.update(status='remote_merged',merge_commit=merged);G.save(path,state)
            G.git(root,'fetch','https://github.com/'+repo+'.git','main')
            if G.git(root,'rev-parse',merged+'^{tree}')!=item['tree'] or G.git(root,'rev-parse',head+'^{tree}')!=item['tree']:
                raise ValueError('Coordinator merge tree differs from installed source')
            G.git(root,'merge-base','--is-ancestor',merged,'FETCH_HEAD')
            if G.git(root,'symbolic-ref','HEAD')!='refs/heads/main':raise ValueError('Coordinator canonical branch changed')
            if G.git(root,'status','--porcelain'):raise ValueError('Local edits prevent coordinator adoption')
            if G.git(root,'rev-parse','HEAD')!=current:raise ValueError('Coordinator source advanced during adoption')
            if current!=merged:G.git(root,'update-ref','refs/midnight/coordinator-backup/'+head,head)
            G.git(root,'update-ref','refs/heads/main',merged,current)
            item.update(status='complete',reason='Confirmed squash tree adopted; previous source commit retained')
        except (ValueError,RuntimeError,OSError,KeyError,subprocess.SubprocessError) as exc:
            item['reason']=str(exc)[:300]
        G.save(path,state)
    state['status']='complete' if all(r.get('status')=='complete' for r in state.get('releases',[])) else 'pending'
    G.save(path,state);return state


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Publish reviewed PR checks without merging or installing')
    parser.add_argument('action', choices=['attest'])
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--request', required=True)
    parser.add_argument('--request-sha256', required=True)
    parser.add_argument('--cutoff', required=True, help='Absolute ISO-8601 timestamp with timezone')
    args = parser.parse_args()
    try:
        cutoff = dt.datetime.fromisoformat(args.cutoff)
        if cutoff.tzinfo is None: raise ValueError('Cutoff must include a timezone')
        result = attest_pr(args.runtime, args.request, request_sha256=args.request_sha256, cutoff=cutoff)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
        result = {'status': 'held', 'reason': str(error)[:300]}
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'complete' else 1)
