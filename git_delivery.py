"""Host-owned, journalled GitHub delivery. No worker may call this module.

Every delivery is a PR. Only a confirmed remote squash merge is acceptance.
The account, repository, exact head, strict base protection and both attestations
are checked afresh. Uncertain writes are reconciled by their deterministic key.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess
import durability as D

IDENTITY = 'example-delivery-account'
ORGANISATION = 'example-org'
CONTEXTS = ('midnight/host-checks', 'midnight/codex-review')


def policy():
    return {'id':'midnight-github-delivery','version':1,'identity':IDENTITY,
            'organisation':ORGANISATION,'merge_method':'squash','required_contexts':list(CONTEXTS),
            'module_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def gh(endpoint, method='GET', payload=None,*,timeout=60):
    command=['/opt/homebrew/bin/gh','api','--hostname','github.com',endpoint,'--method',method]
    if payload is not None:command+=['--input','-']
    result=subprocess.run(command,input=json.dumps(payload) if payload is not None else None,
                          capture_output=True,text=True,timeout=timeout)
    if result.returncode:
        # Never persist credential-bearing stderr; retain HTTP status only.
        code=re.search(r'HTTP (\d+)',result.stderr)
        raise RuntimeError('GitHub API unavailable'+(' HTTP '+code.group(1) if code else ''))
    return json.loads(result.stdout) if result.stdout.strip() else {}


def git(root,*args):
    return subprocess.check_output(['git',*map(str,args)],cwd=root,text=True,stderr=subprocess.PIPE,timeout=90).strip()


def read(path):
    try:return json.loads(path.read_text())
    except FileNotFoundError:return {}


def save(path,value):
    D.atomic_write(path,(json.dumps(value,indent=2,allow_nan=False)+'\n').encode())


class GitDelivery:
    def __init__(self,runtime,project_id,config,*,api=None):
        self.runtime=Path(runtime);self.project_id=project_id;self.config=config;self.api=api or gh
        self.repo=config.get('repository','');self.base=config.get('base','main')
        if not re.fullmatch(r'example-org/[A-Za-z0-9_.-]+',self.repo):raise ValueError('Unapproved GitHub repository')
        if config.get('identity',IDENTITY)!=IDENTITY or self.base!='main':raise ValueError('Unapproved GitHub identity or branch')
        self.endpoint='repos/'+self.repo

    def identity(self):
        user=self.api('user')
        if user.get('login')!=IDENTITY:raise ValueError('GitHub identity mismatch; delivery held')
        repo=self.api(self.endpoint)
        if repo.get('full_name','').lower()!=self.repo.lower() or not repo.get('private'):
            raise ValueError('GitHub repository identity or private visibility mismatch')
        if not repo.get('permissions',{}).get('push'):raise ValueError('GitHub write permission missing')
        return {'login':user['login'],'repository':repo['full_name'],'permissions':repo['permissions']}

    def protection(self):
        rule=self.api(self.endpoint+'/branches/'+self.base+'/protection')
        checks=rule.get('required_status_checks') or {}
        contexts=set(checks.get('contexts',[]))|{x['context'] for x in checks.get('checks',[])}
        approvals=rule.get('required_pull_request_reviews')
        if not checks.get('strict') or not set(CONTEXTS)<=contexts or not rule.get('enforce_admins',{}).get('enabled'):
            raise ValueError('Strict required checks and admin enforcement are required')
        if approvals is None or approvals.get('required_approving_review_count',1)!=0 or approvals.get('require_code_owner_reviews'):
            raise ValueError('Repository needs the authorised zero-human-approval PR policy')
        if rule.get('allow_force_pushes',{}).get('enabled') or rule.get('allow_deletions',{}).get('enabled'):
            raise ValueError('Force-push/deletion protection is required')
        return rule

    def snapshot(self):
        """Local journal only: dashboard/status must never contact GitHub."""
        return read(self.runtime/'github-status.json') or {'status':'unobserved','repository':self.repo,'identity':IDENTITY}

    def _save(self,path,value):
        value['observed_at']=dt.datetime.now(dt.timezone.utc).isoformat()
        save(path,value);save(self.runtime/'github-status.json',value)

    def deliver(self,work,task,run_id,base,review,checks,*,canonical,directory,patch_sha256=None,pre_merge=None,pre_prepare=None):
        directory=Path(directory);canonical=Path(canonical)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,150}',run_id):raise ValueError('Invalid delivery identity')
        journal=directory/'github-delivery.json';receipt=read(journal)
        branch='midnight/'+run_id;repo_url='https://github.com/'+self.repo+'.git'
        reviewer='independent local model' if (directory/'review/local-decision-receipt.json').is_file() else 'independent cloud Codex'
        try:
            account=self.identity();self.protection()
            patch=(directory/'candidate.patch').read_bytes();digest=hashlib.sha256(patch).hexdigest()
            if digest!=patch_sha256 or review.get('decision')!='accept' or review.get('issues')!=[]:
                raise ValueError('Independent acceptance or patch attestation missing')
            if not checks or any(c.get('code')!=0 or c.get('reason')!='exited' for c in checks):
                raise ValueError('Full host checks must pass before delivery')
            if receipt and (receipt.get('run')!=run_id or receipt.get('patch_sha256')!=digest or receipt.get('base')!=base):
                raise ValueError('Delivery journal no longer matches candidate')
            receipt.update(run=run_id,repository=self.repo,identity=account['login'],branch=branch,base=base,patch_sha256=digest)
            # Query before every possible creation; uncertain previous writes never duplicate PRs.
            prs=self.api(self.endpoint+'/pulls?state=all&head='+ORGANISATION+':'+branch+'&base='+self.base+'&per_page=100')
            if len(prs)>1:raise ValueError('Multiple PRs match the delivery key')
            pr=self.api(self.endpoint+'/pulls/'+str(prs[0]['number'])) if prs else None
            if pr and pr.get('merged'):
                return self._confirm_merged(pr,receipt,journal,canonical,repo_url)
            if pre_prepare:pre_prepare()
            if pr and pr.get('state')=='closed':raise ValueError('Delivery PR closed without merge; manager needed')
            remote_base=self.api(self.endpoint+'/git/ref/heads/'+self.base)['object']['sha']
            if remote_base!=base:raise ValueError('Remote base changed; new full checks and review required')
            delivery=directory/'delivery-work'
            if not receipt.get('head'):
                if delivery.exists():
                    # Never overwrite an ambiguous clone. A committed matching marker is recoverable.
                    marker=git(delivery,'log','-1','--format=%B')
                    if 'Midnight-Run: '+run_id not in marker:raise ValueError('Incomplete delivery clone needs reconciliation')
                else:
                    subprocess.run(['git','clone','--quiet','--no-hardlinks',str(canonical),str(delivery)],check=True,capture_output=True)
                    git(delivery,'checkout','--quiet','-b',branch,base)
                    git(delivery,'apply','--check',str(directory/'candidate.patch'))
                    git(delivery,'apply','--index',str(directory/'candidate.patch'))
                    worker=read(directory/'worker-model.json')
                    message=(task['id']+': '+task['title']+'\n\nMidnight-Run: '+run_id+'\n'+
                             self.project_id.capitalize()+'-Run: '+run_id+'\nWorker: '+worker.get('model','recorded in private receipt')+
                             '\nWorker-Kind: '+worker.get('kind','local')+'\nReview: '+reviewer)
                    git(delivery,'-c','user.name=Midnight','-c','user.email=midnight@localhost','commit','-m',message)
                expected=git(delivery,'diff','--binary',base,'HEAD')
                expected_path=directory/'delivery-observed.patch'
                D.atomic_write(expected_path,(expected+'\n').encode())
                # Byte-format may differ, so prove exact trees using an independent index.
                import os, tempfile
                fd,index_name=tempfile.mkstemp(prefix='delivery-index-',dir=directory);os.close(fd);Path(index_name).unlink()
                env=dict(os.environ,GIT_INDEX_FILE=index_name)
                try:
                    subprocess.run(['git','read-tree',base],cwd=delivery,env=env,check=True,capture_output=True)
                    subprocess.run(['git','apply','--cached',str(directory/'candidate.patch')],cwd=delivery,env=env,check=True,capture_output=True)
                    expected_tree=subprocess.check_output(['git','write-tree'],cwd=delivery,env=env,text=True).strip()
                    if git(delivery,'rev-parse','HEAD^{tree}')!=expected_tree:raise ValueError('Delivery tree differs from attested candidate')
                finally:Path(index_name).unlink(missing_ok=True)
                receipt['candidate_tree']=expected_tree
                receipt['head']=git(delivery,'rev-parse','HEAD');receipt['status']='prepared'
                self._save(journal,receipt)
            head=receipt['head']
            if pr and pr['head']['sha']!=head:raise ValueError('PR head changed; independent re-review required')
            if not pr:
                opened=self.api(self.endpoint+'/pulls?state=open&base='+self.base+'&per_page=100')
                if any(p.get('head',{}).get('ref','').startswith('midnight/') for p in opened):
                    receipt.update(status='pending',reason='Another Midnight implementation PR is open');self._save(journal,receipt);return receipt
                # Non-force push is idempotent; remote rejection never overwrites someone else's branch.
                self._save(journal,dict(receipt,status='push_intent'))
                git(delivery,'push',repo_url,head+':refs/heads/'+branch)
                self._save(journal,dict(receipt,status='pr_intent'))
                body=(f"Midnight proposes {task['title']}.\n\nFull host checks passed and the {reviewer} accepted the exact candidate. "
                      f"Base: `{base}`. Candidate: `{head}`. Run: `{run_id}`.\n\n"
                      "Private run evidence retains the task, implementation worker, checks and complete review.\n\nMidnight automation")
                pr=self.api(self.endpoint+'/pulls','POST',{'title':task['id']+': '+task['title'],'head':branch,'base':self.base,'body':body})
            receipt.update(pr=pr['number'],pr_url=pr['html_url'],status='pending');self._save(journal,receipt)
            # Attest only after fetching the exact immutable candidate again.
            pr=self.api(self.endpoint+'/pulls/'+str(pr['number']))
            if pr['head']['sha']!=head or pr['base']['sha']!=base:raise ValueError('PR head/base changed before checks')
            for context in CONTEXTS:
                self.api(self.endpoint+'/statuses/'+head,'POST',{'state':'success','context':context,'description':'Trusted Midnight host verified this exact candidate'})
            self.protection()
            pr=self.api(self.endpoint+'/pulls/'+str(pr['number']))
            if pr['head']['sha']!=head or pr['base']['sha']!=base:raise ValueError('PR head/base changed before merge')
            status=self.api(self.endpoint+'/commits/'+head+'/status')
            contexts={s['context']:s['state'] for s in reversed(status.get('statuses',[]))}
            if status.get('state')!='success' or any(contexts.get(c)!='success' for c in CONTEXTS):
                receipt.update(status='pending',reason='Waiting for required checks');self._save(journal,receipt);return receipt
            # GitHub enforces strict current-base checks, extra checks/rules and PR-only branch policy.
            if git(canonical,'rev-parse','HEAD')!=base or git(canonical,'status','--porcelain'):
                raise ValueError('Canonical source changed before remote merge; fresh assessment/review required')
            if pre_merge is not None:pre_merge()
            self._save(journal,dict(receipt,status='merge_intent'))
            worker=read(directory/'worker-model.json')
            message=(f"Midnight-Run: {run_id}\n{self.project_id.capitalize()}-Run: {run_id}\n"
                     f"Worker: {worker.get('model','recorded')}\nWorker-Kind: {worker.get('kind','local')}\n"
                     f"Review: {reviewer}\n\nMidnight automation")
            self.api(self.endpoint+'/pulls/'+str(pr['number'])+'/merge','PUT',{'sha':head,'merge_method':'squash','commit_title':task['id']+': '+task['title'],'commit_message':message})
            pr=self.api(self.endpoint+'/pulls/'+str(pr['number']))
            if not pr.get('merged'):
                receipt.update(status='pending',reason='Remote merge not confirmed');self._save(journal,receipt);return receipt
            return self._confirm_merged(pr,receipt,journal,canonical,repo_url)
        except (OSError,RuntimeError,ValueError,KeyError,subprocess.SubprocessError) as exc:
            receipt.update(status='blocked' if isinstance(exc,ValueError) else 'pending',reason=str(exc)[:240] if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__)
            self._save(journal,receipt);return receipt

    def _confirm_merged(self,pr,receipt,journal,canonical,url):
        if pr['head']['sha']!=receipt.get('head') or not re.fullmatch(r'[a-f0-9]{40}',pr.get('merge_commit_sha','')):
            raise ValueError('Remote merge does not match pinned candidate')
        commit=pr['merge_commit_sha']
        receipt.update(status='remote_merged',commit=commit,pr=pr['number'],pr_url=pr['html_url'])
        self._save(journal,receipt)
        if git(canonical,'status','--porcelain'):raise ValueError('Canonical tree dirty; remote merge retained for recovery')
        git(canonical,'fetch',url,self.base)
        git(canonical,'merge-base','--is-ancestor',commit,'FETCH_HEAD')
        git(canonical,'merge','--ff-only','FETCH_HEAD')
        receipt.update(status='merged',reason='Remote merge confirmed and canonical source synchronized')
        self._save(journal,receipt);return receipt
