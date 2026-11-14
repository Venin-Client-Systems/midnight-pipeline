"""Reconcile already-authorised Midnight repository settings at a boundary."""
import datetime as dt
from pathlib import Path
import git_delivery as G


def reconcile(runtime,projects,*,api=None,force=False):
    api=api or G.gh;root=Path(runtime);receipt=root/'github-setup.json'
    previous=G.read(receipt);now=dt.datetime.now(dt.timezone.utc)
    if not force and previous.get('checked_at') and (now-dt.datetime.fromisoformat(previous['checked_at'])).total_seconds()<1800:return previous
    result={'checked_at':now.isoformat(),'identity':G.IDENTITY,'repositories':[]}
    try:
        if api('user').get('login')!=G.IDENTITY:raise ValueError('GitHub identity mismatch')
        org=api('orgs/'+G.ORGANISATION)
        if (org.get('plan') or {}).get('name')=='free':
            result.update(status='needs_github_plan',reason='Organisation is Free; private branch protection requires GitHub Team. Owner handles billing.')
        else:
            configs=[('midnight',{'repository':G.ORGANISATION+'/midnight','identity':G.IDENTITY,'base':'main'})]
            configs += [(p['id'],p['github']) for p in projects if p.get('github',{}).get('enabled')]
            for identity,config in configs:
                if config['repository'] not in {G.ORGANISATION+'/midnight',G.ORGANISATION+'/exampleapp'}:
                    raise ValueError('New repository protection requires coordinator admission')
                delivery=G.GitDelivery(root,identity,config,api=api);account=delivery.identity()
                if not account['permissions'].get('admin'):raise ValueError('Repository administration permission missing')
                api(delivery.endpoint,'PATCH',{'allow_auto_merge':True,'allow_squash_merge':True,'allow_merge_commit':False,'allow_rebase_merge':False,'delete_branch_on_merge':True})
                # These are the two newly created Midnight-managed repositories. Never alter unrelated repositories/org rules.
                if config['repository'] not in {G.ORGANISATION+'/midnight',G.ORGANISATION+'/exampleapp'}:
                    raise ValueError('New repository protection requires coordinator admission')
                try:old=api(delivery.endpoint+'/branches/main/protection')
                except RuntimeError as exc:
                    if '404' not in str(exc):raise
                    old={}
                required=old.get('required_status_checks') or {}
                checks={item['context']:{key:item[key] for key in ('context','app_id') if item.get(key) is not None}
                        for item in required.get('checks',[])}
                for context in set(G.CONTEXTS)|set(required.get('contexts',[])):
                    checks.setdefault(context,{'context':context})
                # GitHub's request schema accepts contexts OR checks, not both.
                # The checks form also preserves existing GitHub App bindings.
                payload={'required_status_checks':{'strict':True,'checks':[checks[key] for key in sorted(checks)]},'enforce_admins':True,
                    'required_pull_request_reviews':{'required_approving_review_count':0,'require_code_owner_reviews':False,'dismiss_stale_reviews':True,'require_last_push_approval':False},
                    'restrictions':None,'allow_force_pushes':False,'allow_deletions':False,'required_linear_history':True}
                restrictions=old.get('restrictions')
                if restrictions:payload['restrictions']={k:[x.get('slug') or x.get('login') for x in restrictions.get(k,[])] for k in ('users','teams','apps')}
                api(delivery.endpoint+'/branches/main/protection','PUT',payload)
                delivery.protection();result['repositories'].append({'repository':config['repository'],'status':'configured'})
            result['status']='configured'
    except (ValueError,RuntimeError,OSError) as exc:result.update(status='blocked',reason=str(exc)[:300])
    G.save(receipt,result)
    for project in projects:
        config=project.get('github',{})
        if config.get('enabled'):
            G.save(Path(project['state_dir'])/'github-status.json',{'status':result['status'],'reason':result.get('reason'),
                'identity':G.IDENTITY,'repository':config['repository'],'observed_at':result['checked_at']})
    return result
