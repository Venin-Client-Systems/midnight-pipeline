"""Idle adoption of the exact independently reviewed private coordinator merge.

This installer changes code and the reviewed guardian bundle, never restores an
older ledger. Progress admission is activated only after generation health.
"""
import datetime as dt
from contextlib import closing, contextmanager
import fcntl
import hashlib
import io
import tarfile
import json
import os
import plistlib
import re
import stat
from pathlib import Path
import subprocess
import sys
import time
import threading
import uuid

ROOT=Path.home()/'Developer'/'midnight'
HERE=Path(__file__).resolve().parent


def frozen_dependency_path(digest):
    # Stdlib only: canonical imports may be broken after completed deployment.
    helper=Path(__file__).resolve();receipt=json.loads((HERE/'coordinator-resumer.json').read_text())
    review_path=HERE/'deployment-review.json';review=json.loads(review_path.read_text())
    if (hashlib.sha256(helper.read_bytes()).hexdigest()!=digest or receipt.get('helper')!=str(helper)
        or receipt.get('helper_sha256')!=digest or receipt.get('root')!=str(ROOT) or receipt.get('here')!=str(HERE)
        or review.get('decision')!='accept' or review.get('issues')!=[] or review.get('installer_sha256')!=digest
        or hashlib.sha256(review_path.read_bytes()).hexdigest()!=receipt.get('review_sha256')):
        raise RuntimeError('Frozen coordinator bootstrap binding changed')
    manifest_path=helper.parent/'dependency-manifest.json';raw=manifest_path.read_bytes();manifest=json.loads(raw)
    if (hashlib.sha256(raw).hexdigest()!=receipt.get('dependency_manifest_sha256')
        or manifest.get('source')!=receipt.get('installed') or manifest.get('tree')!=receipt.get('tree')):
        raise RuntimeError('Frozen coordinator dependency manifest changed')
    source=helper.parent/'source'
    actual={str(path.relative_to(source)) for path in source.rglob('*') if path.is_file() or path.is_symlink()}
    if source.is_symlink() or actual!=set(manifest.get('files',{})):raise RuntimeError('Frozen coordinator dependency inventory changed')
    for name,sha in manifest['files'].items():
        relative=Path(name);path=source/relative
        if relative.is_absolute() or '..' in relative.parts or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
            raise RuntimeError('Frozen coordinator dependency changed')
    return source


if __name__=='__main__':
    if len(sys.argv)==5 and sys.argv[1]=='--resume-coordinator':
        ROOT=Path(sys.argv[2]).resolve();HERE=Path(sys.argv[3]).resolve()
        sys.path.insert(0,str(frozen_dependency_path(sys.argv[4])))
    else:sys.path.insert(0,str(ROOT))
import durability as D
import phase_runtime as P
import git_delivery as G
from action_store import Actions
import maintenance_authority as A


def save(path,value):D.atomic_write(path,(json.dumps(value,indent=2)+'\n').encode())
def call(args,timeout=45):return subprocess.check_output(args,cwd=ROOT,text=True,stderr=subprocess.PIPE,timeout=timeout).strip()
def git(*args):return call(['git',*args])


def scheduled_controls_current(expected,registry=None):
    """Verify reviewed weekday controls without manufacturing a coding override."""
    if not isinstance(expected,dict):return False
    path=ROOT/'runtime/override.json';registry=registry if registry is not None else D.read_json(ROOT/'projects.json')
    wanted={'version':1,'mode':'scheduled','timezone':'Australia/Adelaide',
            'schedule':{'start':'00:00','end':'09:00','weekdays':['mon','tue','wed','thu','fri']},
            'override':None,'registry_sha256':expected.get('registry_sha256')}
    return bool(expected==wanted and type(expected.get('version')) is int
        and isinstance(expected.get('registry_sha256'),str) and re.fullmatch('[a-f0-9]{64}',expected['registry_sha256'])
        and not path.exists() and not path.is_symlink() and D.read_bytes(path,None) is None
        and registry.get('timezone')==expected['timezone'] and registry.get('schedule')==expected['schedule']
        and not (ROOT/'projects.json').is_symlink()
        and hashlib.sha256((ROOT/'projects.json').read_bytes()).hexdigest()==expected['registry_sha256'])


def activation_gate_current(binding):
    """An approved activation gate remains owned through source-fence release."""
    if not isinstance(binding,dict) or set(binding)!={'sha256','plan_sha256'}:return False
    if not all(isinstance(v,str) and re.fullmatch('[a-f0-9]{64}',v) for v in binding.values()):return False
    path=ROOT/'runtime/WEEKDAY_ACTIVATION_PENDING'
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size>65536:return False
        raw=path.read_bytes();value=json.loads(raw)
        return bool(hashlib.sha256(raw).hexdigest()==binding['sha256'] and isinstance(value,dict)
            and type(value.get('version')) is int and value['version']==1
            and value.get('plan_sha256')==binding['plan_sha256']
            and isinstance(value.get('owner'),str) and value['owner']
            and isinstance(value.get('generation'),str) and re.fullmatch('[a-f0-9]{32}',value['generation']))
    except (OSError,ValueError,TypeError):return False


def live_controls(expected,pauses,scheduled=None,activation_gate=None):
    return (D.read_json(ROOT/'runtime/override.json')==expected
        and all((Path(p).exists() or Path(p).is_symlink())==v for p,v in pauses.items())
        and not ((ROOT/'runtime/PAUSED').exists() or (ROOT/'runtime/PAUSED').is_symlink())
        and (scheduled is None or scheduled_controls_current(scheduled))
        and (activation_gate is None and scheduled is None or activation_gate_current(activation_gate)))


def locked(path,deadline):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    handle=path.open('a')
    while True:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB);return handle
        except BlockingIOError:
            if time.time()>=deadline:handle.close();raise RuntimeError('Healthy owner has not released its lock')
            time.sleep(.2)



_INSTALLATION_OWNER=threading.local()


@contextmanager
def installation_lock():
    """One helper owns adoption, restarts, activation and release as one flow."""
    key=(str(ROOT.resolve()),str(HERE.resolve()))
    prior=getattr(_INSTALLATION_OWNER,'key',None)
    if prior is not None:
        if prior!=key:raise RuntimeError('Nested coordinator installation context changed')
        yield
        return
    handle=locked(HERE/'installation.lock',time.time()+600)
    _INSTALLATION_OWNER.key=key
    try:yield
    finally:
        _INSTALLATION_OWNER.key=None
        handle.close()


def rejected_generation(commit,tree):
    records=Actions(ROOT/'runtime').meta('rejected_installations') or {}
    return commit in records or any(record.get('tree')==tree for record in records.values())

def adopt(base,commit,tree,bindings,validate,*,checkpoint=lambda stage:None):
    """Resume a committed source intent without touching earlier ledger state.

    Caller owns controller/supervisor/source locks. A failed process leaves the
    exact fence in place and this journal is the only admissible resume input.
    """
    intent_path=HERE/'installation-intent.json'
    intent=D.read_json(intent_path)
    wanted={'previous':base,'installed':commit,'tree':tree,'bindings':bindings}
    head=git('rev-parse','HEAD')
    if intent:
        if any(intent.get(k)!=v for k,v in wanted.items()):raise RuntimeError('Durable installation intent has different bindings')
    elif head!=base:raise RuntimeError('Changed source has no matching durable installation intent')
    if rejected_generation(commit,tree):raise RuntimeError('Rejected generation cannot be reinstalled from retained history')
    prior_health=Actions(ROOT/'runtime').meta('install_health') or {}
    if (prior_health.get('installed')==commit and prior_health.get('tree')==tree
        and prior_health.get('state') in {'rolling_back','rolled_back','held'}):
        raise RuntimeError('Rejected generation cannot be reinstalled after rollback or a health hold')
    if head not in {base,commit} or git('status','--porcelain') or not validate():
        raise RuntimeError('Source, fence or owner controls changed during adoption')
    if not intent:
        intent={**wanted,'at':dt.datetime.now(dt.timezone.utc).isoformat(),'idle_boundary':True,'owner_controls_unchanged':True}
        save(intent_path,intent)
    if head==base:
        git('update-ref','refs/midnight/coordinator/20261103-evidence-recovery-before',base)
        current_health=Actions(ROOT/'runtime').meta('install_health') or {}
        if (not validate() or git('rev-parse','HEAD')!=base or git('status','--porcelain') or rejected_generation(commit,tree)
            or current_health.get('installed')==commit and current_health.get('state') in {'rolling_back','rolled_back','held'}):
            raise RuntimeError('Source fence controls authority or health changed after intent before coordinator merge')
        git('merge','--ff-only',commit)
    checkpoint('source_adopted')
    if git('rev-parse','HEAD')!=commit or git('rev-parse','HEAD^{tree}')!=tree or not validate():
        raise RuntimeError('Adopted source or ownership changed before health registration')
    save(HERE/'installation.json',intent)
    checkpoint('installation_recorded')
    # The previous frozen guardian must never see armed health for this source.
    # Source adoption deliberately leaves the previous health record unchanged.
    return intent


def register_generation_health(intent,expected):
    store=Actions(ROOT/'runtime');health=store.meta('install_health') or {}
    activation_gate=intent.get('bindings',{}).get('activation_gate')
    if activation_gate is not None and not activation_gate_current(activation_gate):
        raise RuntimeError('Reviewed activation gate changed before generation health')
    same=all(health.get(k)==intent[k] for k in ('previous','installed','tree'))
    if same and health.get('state') in {'verifying','verified'}:
        if health.get('activation_gate')!=activation_gate:raise RuntimeError('Generation activation gate binding changed')
        return health
    if same and health.get('state') in {'rolling_back','rolled_back','held'}:
        raise RuntimeError('Generation health already requires rollback or reconciliation')
    epoch=uuid.uuid4().hex;armed=time.time()
    health={'previous':intent['previous'],'installed':intent['installed'],'tree':intent['tree'],'repair':'coordinator-20261103-evidence-recovery',
            'at':armed,'state':'verifying','installation_fence_sha256':hashlib.sha256(expected).hexdigest(),
            'restart_epoch':epoch,'restart_requirements':{label:{'epoch':epoch,'not_before':armed}
                for label in ('space.venin.midnight','space.venin.midnight-dashboard')},
            'controller_sha256':hashlib.sha256((ROOT/'midnight.py').read_bytes()).hexdigest(),
            'dashboard_sha256':hashlib.sha256((ROOT/'dashboard.py').read_bytes()).hexdigest()}
    if activation_gate is not None:health['activation_gate']=activation_gate
    store.meta('install_health',health,write=True)
    return health


class AuthorityEvidence:
    """Read one bounded, consistent set without repairing any conflicting bytes.

    Checkpoints remain authoritative for ordinary recovery. An effect guard
    requires their latest valid bytes AND the reviewed mirror to agree. File,
    parent and database identities bracket all reads, including the final seal
    check; a concurrent writer makes the whole observation unusable.
    """
    limit=128*1024

    def __init__(self):self.identities={}

    @staticmethod
    def identity(path, directory=False):
        try:value=Path(path).lstat()
        except FileNotFoundError:return None
        if directory:
            if not stat.S_ISDIR(value.st_mode):raise RuntimeError('Unsafe authority evidence directory')
            return (value.st_dev,value.st_ino,value.st_mode)
        if not stat.S_ISREG(value.st_mode) or value.st_nlink!=1:
            raise RuntimeError('Unsafe authority evidence file')
        return (value.st_dev,value.st_ino,value.st_mode,value.st_nlink,value.st_size,value.st_mtime_ns,value.st_ctime_ns)

    def watch(self,path,*,directory=False):
        key=(Path(path),directory);current=self.identity(*key)
        if key in self.identities and self.identities[key]!=current:
            raise RuntimeError('Authority evidence changed during validation')
        self.identities.setdefault(key,current)

    def raw(self,path,*,durable=True):
        path=Path(path);self.watch(path.parent,directory=True);self.watch(path)
        raw=A.fresh_bytes(path,self.limit)
        if durable:
            db=D.database(path)
            for candidate in (db,Path(str(db)+'-journal'),Path(str(db)+'-wal'),Path(str(db)+'-shm')):
                self.watch(candidate)
            if db.exists():
                # Do not materialize an oversized payload or fall back to an
                # earlier generation when the latest committed row is invalid.
                with closing(D.connect(path)) as con:
                    row=con.execute('SELECT version,length(payload),CASE WHEN length(payload)<=? THEN payload ELSE NULL END,digest,deleted '
                        'FROM snapshots WHERE name=? ORDER BY version DESC LIMIT 1',(self.limit,path.name)).fetchone()
                if row is not None:
                    version,size,payload,digest,deleted=row
                    if (type(version) is not int or version<1 or type(size) is not int or size>self.limit
                            or not isinstance(payload,bytes) or hashlib.sha256(payload).hexdigest()!=digest
                            or deleted!=0 or payload!=raw):
                        raise RuntimeError('Authority checkpoint and reviewed mirror conflict')
        return raw

    @staticmethod
    def parse(raw):
        def pairs(items):
            result={}
            for key,value in items:
                if key in result:raise ValueError('Duplicate authority evidence field')
                result[key]=value
            return result
        def constant(_):raise ValueError('Non-finite authority evidence value')
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=constant)
        if not isinstance(value,dict):raise ValueError('Authority evidence must be an object')
        return value

    def json(self,path):
        raw=self.raw(path);return self.parse(raw),hashlib.sha256(raw).hexdigest()

    def verify(self):
        # No more file/checkpoint reads follow this final identity pass.
        for (path,directory),expected in self.identities.items():
            if self.identity(path,directory)!=expected:
                raise RuntimeError('Authority evidence changed during validation')


def approved_authority(review):
    """Bind the reviewed policy upgrade or unchanged-permission record migration."""
    evidence=AuthorityEvidence()
    current_review,review_sha=evidence.json(HERE/'deployment-review.json')
    if current_review!=review:raise RuntimeError('Reviewed installation authority changed')
    proposal,proposal_sha=evidence.json(HERE/'authority-proposal.json')
    if review.get('authority_sha256')!=proposal_sha:
        raise RuntimeError('Approved authority proposal changed')
    before=proposal['before'];after=proposal['after']
    sealed,seal_sha=evidence.json(ROOT/'runtime/resilience/authority.json')
    record_raw=evidence.raw(A.REGISTER,durable=False)
    record_sha=hashlib.sha256(record_raw).hexdigest()
    migration=proposal.get('kind')=='venin-record-migration'
    if migration:
        retained=A.check(ROOT)
        identity=A.record_identity()
        if identity['sha256']!=record_sha:raise RuntimeError('Authority evidence changed during validation')
        if (type(proposal.get('schema_version')) is not int or proposal['schema_version']!=2 or before!=after
                or A.digest(before)!=retained.get('permission_sha256')
                or proposal.get('authority_record')!=str(A.REGISTER)
                or proposal.get('record_sha256')!=identity['sha256']
                or proposal.get('policy_path')!=str(A.POLICY)):
            raise RuntimeError('Authority migration is not the exact reviewed unchanged-permission record')
    else:
        changed={(sheet,cell) for sheet,cells in before.items() for cell,value in cells.items() if after.get(sheet,{}).get(cell)!=value}
        if changed!={('Automations','K5'),('Domains & Permissions','G10')} or set(before)!=set(after) or any(set(before[s])!=set(after[s]) for s in before):
            raise RuntimeError('Authority proposal is not the exact restricted permission delta')
    policy=Path(proposal['policy_path'])
    if review.get('policy_sha256')!=hashlib.sha256(evidence.raw(policy,durable=False)).hexdigest() or A.permissions()!=after:
        raise RuntimeError('Installed authority or policy differs from independent approval')
    receipt,receipt_sha=evidence.json(HERE/'authority-record-update.json')
    if receipt.get('permission_before')!=A.digest(before) or receipt.get('permission_after')!=A.digest(after) or receipt.get('policy_after')!=review['policy_sha256']:
        raise RuntimeError('Exact policy and permission migration receipt is missing')
    if A.check(ROOT)!=sealed:raise RuntimeError('Authority seal changed during validation')
    evidence.verify()
    return {'policy_sha256':review['policy_sha256'],'authority_sha256':review['authority_sha256'],
            'permission_digest':A.digest(after),'authority_record_sha256':record_sha,
            'authority_evidence_version':1,'authority_review_sha256':review_sha,
            'authority_receipt_sha256':receipt_sha,'authority_seal_sha256':seal_sha}



def authority_current(bindings):
    """Fresh exact policy/permission approval, including during long waits."""
    current=approved_authority(D.read_json(HERE/'deployment-review.json'))
    if any(bindings.get(key)!=value for key,value in current.items()):raise RuntimeError('Reviewed installation authority changed')
    return True

def bundle_install():
    intent=D.read_json(HERE/'installation-intent.json');expected=intent.get('bindings',{}).get('permission_digest')
    if not isinstance(expected,str) or not re.fullmatch(r'[a-f0-9]{64}',expected):raise RuntimeError('Bundle publication lacks its reviewed permission digest')
    authority_current(intent['bindings'])
    # Imports in this subprocess come from the newly adopted exact source.
    script="""import sys,json
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
import durability as D,phase_runtime as P,project_action as PA,install_guardian as I
from action_store import Actions
store=Actions(root/'runtime')
for job in store.jobs():
 if not P.alive(job.get('lease')) and PA.drain(root/'runtime',job['id']):store.finish(job['id'],'interrupted',{'reason':'verified_coordinator_idle_reconciliation'},lease=job.get('lease'))
print(json.dumps(I.install(root,expected_permission_sha256=sys.argv[2])))"""
    return json.loads(call([sys.executable,'-I','-B','-c',script,str(ROOT),expected],timeout=120))


def restart_service(label):
    call(['/bin/launchctl','kickstart','-k',f'gui/{os.getuid()}/{label}'],timeout=15)
    return {'requested':True,'at':dt.datetime.now(dt.timezone.utc).isoformat()}



def quiesce_guardian(*,checkpoint=lambda stage:None,validate=lambda:True):
    """Unload the old LaunchAgent and drain its exact native process identities."""
    target=f'gui/{os.getuid()}/space.venin.midnight-guardian'
    def inspect():
        result=subprocess.run(['/bin/launchctl','print',target],capture_output=True,text=True,timeout=10)
        if result.returncode and 'Could not find service' not in result.stderr:
            raise RuntimeError('Guardian service state is uncertain; preserve installation fence')
        return result
    installation=D.read_json(HERE/'installation-intent.json')
    binding={key:installation.get(key) for key in ('previous','installed','tree')}
    binding['fence_sha256']=installation.get('bindings',{}).get('fence_sha256')
    marker=ROOT/'runtime/INSTALLING'
    if (not all(binding.values()) or not marker.exists() or hashlib.sha256(marker.read_bytes()).hexdigest()!=binding['fence_sha256']
        or git('rev-parse','HEAD')!=binding['installed'] or git('rev-parse','HEAD^{tree}')!=binding['tree']):
        raise RuntimeError('Guardian quiescence lacks exact source and owned-fence bindings')
    journal=D.read_json(HERE/'guardian-quiescence-intent.json')
    if journal and journal.get('binding')!=binding:raise RuntimeError('Guardian quiescence intent belongs to another generation')
    leases=journal.get('processes',[]) if journal else []
    before=inspect()
    if before.returncode==0:
        pid=re.search(r'^\s*pid = (\d+)\s*$',before.stdout,re.M)
        if pid:
            leader=P.lease(int(pid.group(1)))
            if P.alive(leader):
                for process in (leader,*P.descendants(leader)):
                    if process not in leases:leases.append(process)
    # Native identities survive an interruption after service removal, including
    # children which outlive their LaunchAgent. Never replace them with an empty
    # list merely because the service is absent on replay.
    save(HERE/'guardian-quiescence-intent.json',{'binding':binding,'processes':leases,'at':dt.datetime.now(dt.timezone.utc).isoformat()})
    checkpoint('guardian_quiescence_intent')
    if before.returncode==0:
        if (not validate() or not marker.exists() or hashlib.sha256(marker.read_bytes()).hexdigest()!=binding['fence_sha256']
            or git('rev-parse','HEAD')!=binding['installed'] or git('rev-parse','HEAD^{tree}')!=binding['tree']):
            raise RuntimeError('Source, ownership, controls or authority changed before guardian unload')
        subprocess.run(['/bin/launchctl','bootout',target],capture_output=True,text=True,timeout=15)
    if inspect().returncode==0:raise RuntimeError('Previous guardian service is still loaded')
    if before.returncode==0:checkpoint('guardian_service_unloaded')
    for process in reversed(leases):
        if P.alive(process) and not P.stop_tree(process,grace=3):raise RuntimeError('Previous guardian process has not drained')
    if any(P.alive(process) for process in leases):raise RuntimeError('Previous guardian native identity remains alive')
    return {'unloaded':True,'binding':binding,'processes':leases,'at':dt.datetime.now(dt.timezone.utc).isoformat()}




def resumer_definition(intent,helper,digest):
    label='space.venin.midnight-coordinator-'+intent['installed'][:12]
    # Homebrew/site startup can report different aliases in the installer and
    # resumed child. Bind the same physical interpreter without relaxing the
    # complete definition comparison or accepting a different executable.
    interpreter=str(Path(sys.executable).resolve(strict=True))
    value={'Label':label,'ProgramArguments':[interpreter,'-I','-B',str(helper),'--resume-coordinator',str(ROOT),str(HERE),digest],
        'RunAtLoad':True,'StartInterval':60,'ProcessType':'Background','WorkingDirectory':str(HERE),
        'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:'+os.path.expanduser('~/.local/bin')+':/usr/bin:/bin:/usr/sbin:/sbin','PYTHONDONTWRITEBYTECODE':'1','TZ':'Australia/Adelaide'},
        'StandardOutPath':str(HERE/'coordinator-resumer.out.log'),'StandardErrorPath':str(HERE/'coordinator-resumer.err.log'),
        'ExitTimeOut':30,'ThrottleInterval':30}
    return label,plistlib.dumps(value)



def prepare_coordinator_recovery(base,commit,tree,bindings):
    plan={'previous':base,'installed':commit,'tree':tree,'bindings':bindings}
    prior=D.read_json(HERE/'coordinator-plan.json')
    if prior and prior!=plan:raise RuntimeError('Coordinator recovery plan changed')
    save(HERE/'coordinator-plan.json',plan)
    return arm_coordinator_resumer(plan)



def completed_installation(receipt,intent):
    """Recognize bound completion OR released dispatch, without live adoption."""
    complete=D.read_json(HERE/'installation-complete.json');release=D.read_json(HERE/'fence-release-intent.json')
    if not complete and not release:return False
    fields={'installed':receipt['installed'],'tree':receipt['tree'],'fence_sha256':intent.get('bindings',{}).get('fence_sha256')}
    if (not fields['fence_sha256'] or any(release.get(k)!=v for k,v in fields.items())
        or complete and (complete.get('state')!='complete' or any(complete.get(k)!=v for k,v in fields.items()))):
        raise RuntimeError('Terminal coordinator completion binding changed')
    for name in ('guardian-manifest.json','service-restarts.json','activation.json'):
        if release.get('receipts',{}).get(name)!=hashlib.sha256((HERE/name).read_bytes()).hexdigest():
            raise RuntimeError('Terminal coordinator completion receipt changed')
    if complete:return True
    # The journal was written only after exact source/health/authority checks.
    # A still-present original fence means its release effect is incomplete.
    # Absence or a distinct later owner's marker permits only old-resumer
    # retirement; neither path touches the current source, health or fence.
    marker=ROOT/'runtime/INSTALLING'
    if marker.exists() and not marker.is_symlink() and hashlib.sha256(marker.read_bytes()).hexdigest()==fields['fence_sha256']:return False
    return True


def retirement_binding(receipt):
    return {key:receipt.get(key) for key in ('installed','tree','helper_sha256','label','plist_sha256')}


def retired_resumer(receipt):
    retired=D.read_json(HERE/'coordinator-resumer-retired.json')
    if not retired:return False
    if any(retired.get(key)!=value for key,value in retirement_binding(receipt).items()):raise RuntimeError('Coordinator resumer retirement belongs to another generation')
    return retired.get('state') in {'retiring','retired'}



def freeze_resumer_dependencies(intent,helper):
    if git('rev-parse',intent['installed']+'^{tree}')!=intent['tree']:raise RuntimeError('Coordinator dependency tree differs from independent review')
    raw=subprocess.check_output(['git','archive',intent['installed']],cwd=ROOT);files={}
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive:
            if member.isdir():continue
            name=Path(member.name)
            if not member.isfile() or name.is_absolute() or '..' in name.parts:raise RuntimeError('Coordinator dependency archive has unsupported members')
            files[str(name)]=archive.extractfile(member).read()
    required={'durability.py','phase_runtime.py','action_store.py','history_store.py','git_delivery.py','maintenance_authority.py',
              'maintenance_recovery.py','core_repair.py','guardian.py','recovery_policy.json','coordinator_install.py'}
    if not required.issubset(files) or files['coordinator_install.py']!=Path(__file__).read_bytes():
        raise RuntimeError('Reviewed coordinator dependency closure is incomplete')
    manifest={'source':intent['installed'],'tree':intent['tree'],'files':{name:hashlib.sha256(value).hexdigest() for name,value in files.items()}}
    target=helper.parent/'source';manifest_path=helper.parent/'dependency-manifest.json';prior=D.read_json(manifest_path)
    if prior and prior!=manifest:raise RuntimeError('Frozen coordinator dependency source changed')
    for name,value in files.items():
        path=target/name
        if path.exists() and (path.is_symlink() or path.read_bytes()!=value):raise RuntimeError('Frozen coordinator dependency bytes changed')
        if not path.exists():D.atomic_write(path,value)
    actual={str(path.relative_to(target)) for path in target.rglob('*') if path.is_file() or path.is_symlink()}
    if actual!=set(files):raise RuntimeError('Unexpected frozen coordinator dependency file')
    save(manifest_path,manifest)
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def arm_coordinator_resumer(intent):
    """Install an independent, one-minute consumer before guardian unload."""
    raw=Path(__file__).read_bytes();digest=hashlib.sha256(raw).hexdigest();review=D.read_json(HERE/'deployment-review.json')
    verified=D.read_json(HERE/'verified.json');delivery=D.read_json(HERE/'delivery.json')
    if (review.get('decision')!='accept' or review.get('issues')!=[] or review.get('installer_sha256')!=digest
        or verified.get('base')!=intent['previous'] or verified.get('tree')!=intent['tree']
        or delivery.get('status')!='merged' or delivery.get('commit')!=intent['installed']):
        raise RuntimeError('Durable coordinator resumer requires the exact reviewed private installation')
    authority_current(intent['bindings'])
    helper=(HERE/'coordinator-resumer'/digest/'coordinator_install.py').resolve();label,plist_bytes=resumer_definition(intent,helper,digest)
    plist=Path.home()/'Library/LaunchAgents'/(label+'.plist');target=f'gui/{os.getuid()}/{label}'
    dependencies=freeze_resumer_dependencies(intent,helper)
    receipt={'version':1,'root':str(ROOT),'here':str(HERE),'previous':intent['previous'],'installed':intent['installed'],'tree':intent['tree'],
        'helper':str(helper),'helper_sha256':digest,'review_sha256':hashlib.sha256((HERE/'deployment-review.json').read_bytes()).hexdigest(),
        'label':label,'plist':str(plist),'plist_sha256':hashlib.sha256(plist_bytes).hexdigest(),'dependency_manifest_sha256':dependencies}
    prior=D.read_json(HERE/'coordinator-resumer.json')
    if prior and retired_resumer(prior):raise RuntimeError('Retired coordinator resumer cannot be rearmed')
    if prior and prior!=receipt:raise RuntimeError('A different coordinator resumer owns this installation')
    if helper.exists() and (helper.is_symlink() or helper.read_bytes()!=raw):raise RuntimeError('Frozen coordinator resumer changed')
    if plist.exists() and (plist.is_symlink() or plist.read_bytes()!=plist_bytes):raise RuntimeError('Coordinator resumer service definition changed')
    D.atomic_write(helper,raw);save(HERE/'coordinator-resumer.json',receipt);D.atomic_write(plist,plist_bytes)
    status=subprocess.run(['/bin/launchctl','print',target],capture_output=True,text=True,timeout=10)
    if status.returncode:
        if 'Could not find service' not in status.stderr:raise RuntimeError('Coordinator resumer service state is uncertain')
        subprocess.run(['/bin/launchctl','bootstrap',f'gui/{os.getuid()}',str(plist)],check=True,capture_output=True,text=True,timeout=15)
        status=subprocess.run(['/bin/launchctl','print',target],capture_output=True,text=True,timeout=10)
    if status.returncode:raise RuntimeError('Independent coordinator resumer is not loaded')
    save(HERE/'coordinator-resumer-ready.json',{'loaded':True,'intent_sha256':hashlib.sha256((HERE/'coordinator-resumer.json').read_bytes()).hexdigest(),
         'at':dt.datetime.now(dt.timezone.utc).isoformat()})
    return receipt


def resume_coordinator(digest,*,resume=None):
    """Consume only this approved journal, without model or new PR creation."""
    receipt=D.read_json(HERE/'coordinator-resumer.json');intent=D.read_json(HERE/'installation-intent.json') or D.read_json(HERE/'coordinator-plan.json')
    review=D.read_json(HERE/'deployment-review.json');helper=Path(__file__).resolve()
    if (receipt.get('version')!=1 or receipt.get('root')!=str(ROOT) or receipt.get('here')!=str(HERE)
        or receipt.get('helper')!=str(helper) or receipt.get('helper_sha256')!=digest or hashlib.sha256(helper.read_bytes()).hexdigest()!=digest
        or review.get('decision')!='accept' or review.get('issues')!=[] or review.get('installer_sha256')!=digest
        or hashlib.sha256((HERE/'deployment-review.json').read_bytes()).hexdigest()!=receipt.get('review_sha256')
        or any(receipt.get(k)!=intent.get(k) for k in ('previous','installed','tree'))):
        raise RuntimeError('Durable coordinator resumer binding changed')
    if retired_resumer(receipt):return {'state':'retired','installed':receipt['installed']}
    if completed_installation(receipt,intent):return {'state':'complete','installed':receipt['installed'],'terminal_receipts':True}
    label,plist_bytes=resumer_definition(intent,helper,digest);plist=Path(receipt.get('plist',''))
    if (receipt.get('label')!=label or receipt.get('plist_sha256')!=hashlib.sha256(plist_bytes).hexdigest()
        or plist.is_symlink() or plist.read_bytes()!=plist_bytes):raise RuntimeError('Durable coordinator resumer service changed')
    # A scheduled tick must yield promptly while a healthy installer owns flow.
    handle=(HERE/'installation.lock').open('a')
    try:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'state':'owner_active'}
        _INSTALLATION_OWNER.key=(str(ROOT.resolve()),str(HERE.resolve()))
        try:
            with P.source_lock(ROOT/'runtime',deadline=time.time()+15):
                ensure_guardian_evaluator(intent)
                head=git('rev-parse','HEAD');health=Actions(ROOT/'runtime').meta('install_health') or {}
                initial=(head==intent['previous'] and not D.read_json(HERE/'installation.json')
                    and health.get('state')=='verified' and health.get('installed')==intent['previous']
                    and not rejected_generation(intent['installed'],intent['tree']))
                if head!=intent['installed'] and not initial:
                    result={'state':'held','reason':'Source no longer matches the adopted generation; guardian reconciliation remains available and readoption is forbidden',
                        'at':dt.datetime.now(dt.timezone.utc).isoformat(),'guardian_available':True}
                    save(HERE/'coordinator-resumer-status.json',result)
                    return result
            (resume or (lambda:main(retire=False)))()
            complete=D.read_json(HERE/'installation-complete.json')
            result={'state':'complete' if complete.get('installed')==intent['installed'] else 'pending','at':dt.datetime.now(dt.timezone.utc).isoformat(),
                    'installed':intent['installed'],'guardian_available':True}
            save(HERE/'coordinator-resumer-status.json',result)
            return result
        except Exception as exc:
            result={'state':'held','error':type(exc).__name__,'detail':str(exc)[:500],'at':dt.datetime.now(dt.timezone.utc).isoformat()}
            save(HERE/'coordinator-resumer-status.json',result)
            return result
        finally:_INSTALLATION_OWNER.key=None
    finally:handle.close()


def disarm_coordinator_resumer():
    receipt=D.read_json(HERE/'coordinator-resumer.json');complete=D.read_json(HERE/'installation-complete.json')
    if not receipt:return False
    already=retired_resumer(receipt)
    intent=D.read_json(HERE/'installation-intent.json') or D.read_json(HERE/'coordinator-plan.json')
    if not already and not completed_installation(receipt,intent):return False
    helper=Path(receipt['helper']);label,raw=resumer_definition(intent,helper,receipt['helper_sha256']);plist=Path(receipt['plist'])
    if receipt.get('label')!=label or receipt.get('plist_sha256')!=hashlib.sha256(raw).hexdigest():raise RuntimeError('Preserve changed coordinator resumer binding')
    if plist.exists() and (plist.is_symlink() or plist.read_bytes()!=raw):raise RuntimeError('Preserve changed coordinator resumer service')
    retired=D.read_json(HERE/'coordinator-resumer-retired.json') or {**retirement_binding(receipt),'at':dt.datetime.now(dt.timezone.utc).isoformat(),'state':'retiring',
        'completion_basis':'completion_receipt' if complete else 'bound_release_journal','release_journal_sha256':hashlib.sha256((HERE/'fence-release-intent.json').read_bytes()).hexdigest()}
    save(HERE/'coordinator-resumer-retired.json',retired)
    # Remove the exact owned definition durably before bootout. Login cannot
    # reload it after termination; a still-running tick sees retirement first.
    if plist.exists():
        if plist.is_symlink() or plist.read_bytes()!=raw:raise RuntimeError('Preserve replaced coordinator resumer service')
        D.marker(plist,False)
    subprocess.run(['/bin/launchctl','bootout',f'gui/{os.getuid()}/{label}'],capture_output=True,text=True,timeout=15)
    save(HERE/'coordinator-resumer-retired.json',{**retired,'state':'retired','completed_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    return True


def ensure_guardian_evaluator(intent):
    """Restore the published hash-verified evaluator after a failed handoff.

    This only loads the existing observer service. It never republishes source,
    changes health or relaxes controls; the evaluator reads current pauses.
    """
    resilience=ROOT/'runtime/resilience';manifest=D.read_json(resilience/'manifest.json')
    source=manifest.get('source');bundle=Path(manifest.get('bundle',''))
    if (source not in {intent.get('previous'),intent.get('installed')} or manifest.get('canonical')!=str(ROOT)
        or bundle!=resilience/'bundles'/str(source) or bundle.is_symlink() or not manifest.get('files')):
        raise RuntimeError('Guardian availability recovery lacks the exact published evaluator')
    actual={str(p.relative_to(bundle)) for p in bundle.rglob('*') if p.is_file() or p.is_symlink()}
    if actual!=set(manifest['files']):raise RuntimeError('Published evaluator inventory changed')
    for name,digest in manifest['files'].items():
        relative=Path(name);path=bundle/relative
        if relative.is_absolute() or '..' in relative.parts or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Published evaluator files changed')
    bootstrap=resilience/'guardian_bootstrap.py'
    if (bootstrap.is_symlink() or hashlib.sha256(bootstrap.read_bytes()).hexdigest()!=manifest['files'].get('guardian_bootstrap.py')):
        raise RuntimeError('Guardian bootstrap differs from the published evaluator')
    health=Actions(ROOT/'runtime').meta('install_health') or {}
    if (health.get('installed')==intent['installed'] and health.get('state') in {'verifying','rolling_back','rolled_back','held'}
        and source!=intent['installed']):raise RuntimeError('Previous evaluator cannot observe armed candidate health')
    label='space.venin.midnight-guardian';target=f'gui/{os.getuid()}/{label}'
    plist=Path.home()/'Library/LaunchAgents'/(label+'.plist')
    if plist.is_symlink():raise RuntimeError('Guardian service definition changed')
    value=plistlib.loads(plist.read_bytes());args=value.get('ProgramArguments',[])
    if (value.get('Label')!=label or len(args)!=3 or Path(args[0]).resolve()!=Path(sys.executable).resolve()
        or args[1:]!=['-B',str(bootstrap)] or value.get('WorkingDirectory')!=str(resilience)):
        raise RuntimeError('Guardian service no longer points to its verified bootstrap')
    status=subprocess.run(['/bin/launchctl','print',target],capture_output=True,text=True,timeout=10)
    restored=False
    if status.returncode:
        if 'Could not find service' not in status.stderr:raise RuntimeError('Guardian availability is uncertain')
        subprocess.run(['/bin/launchctl','bootstrap',f'gui/{os.getuid()}',str(plist)],check=True,capture_output=True,text=True,timeout=15)
        restored=True
        status=subprocess.run(['/bin/launchctl','print',target],capture_output=True,text=True,timeout=10)
    if status.returncode:raise RuntimeError('Guardian evaluator remains unavailable')
    receipt={'available':True,'restored':restored,'source':source,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
    save(HERE/'guardian-availability.json',receipt)
    return receipt


def establish_guardian_generation(intent,expected,validate,*,install_bundle=bundle_install,quiesce=None,checkpoint=lambda stage:None):
    """The reviewed evaluator is established before new health becomes visible."""
    commit=intent['installed'];tree=intent['tree'];marker=ROOT/'runtime/INSTALLING'
    def exact():
        return (validate() and marker.exists() and marker.read_bytes()==expected
                and git('rev-parse','HEAD')==commit and git('rev-parse','HEAD^{tree}')==tree and not git('status','--porcelain'))
    manifest=D.read_json(HERE/'guardian-manifest.json');handoff=D.read_json(HERE/'guardian-handoff.json')
    health=Actions(ROOT/'runtime').meta('install_health') or {}
    if (handoff.get('installed')==commit and handoff.get('tree')==tree and handoff.get('health_armed') is True
        and handoff.get('manifest_sha256')==hashlib.sha256((HERE/'guardian-manifest.json').read_bytes()).hexdigest()
        and manifest.get('source')==commit and D.read_json(ROOT/'runtime/resilience/manifest.json')==manifest):
        if health.get('installed')!=commit or health.get('tree')!=tree or health.get('state') not in {'verifying','verified'}:
            raise RuntimeError('Reviewed guardian handoff has rejected or lost generation health')
        return manifest
    # The guardian's rollback also owns the source lock. Acquire it before
    # quiescence, then stop/drain the native evaluator before taking its audit
    # lock. This avoids unloading a guardian after it has restored source.
    with P.source_lock(ROOT/'runtime',deadline=time.time()+15):
        def current():
            if not exact():return False
            health=Actions(ROOT/'runtime').meta('install_health') or {}
            return not (health.get('installed')==commit and health.get('state') in {'rolling_back','rolled_back','held'})
        if not current():raise RuntimeError('Source or health changed before guardian handoff')
        arm_coordinator_resumer(intent)
        if not current():raise RuntimeError('Source or health changed after arming durable recovery')
        attempted=False
        try:
            attempted=True
            stopped=(quiesce or (lambda:quiesce_guardian(validate=current)))()
            if stopped.get('unloaded') is not True:raise RuntimeError('Previous guardian quiescence is unproven')
            save(HERE/'guardian-quiescence.json',stopped);checkpoint('guardian_quiesced')
            with locked(ROOT/'runtime/guardian.lock',time.time()+60):
                if not current():raise RuntimeError('Ownership changed during guardian handoff')
                # An interrupted publish reloads the reviewed agent before health.
                manifest=install_bundle()
                if not current() or manifest.get('source')!=commit or D.read_json(ROOT/'runtime/resilience/manifest.json')!=manifest:
                    raise RuntimeError('Reviewed guardian generation was not established')
                save(HERE/'guardian-manifest.json',manifest);checkpoint('bundle_published_before_health')
                if not current():raise RuntimeError('Authority or ownership changed before health registration')
                register_generation_health(intent,expected)
                checkpoint('health_armed_before_handoff_receipt')
                save(HERE/'guardian-handoff.json',{'installed':commit,'tree':tree,'health_armed':True,
                     'manifest_sha256':hashlib.sha256((HERE/'guardian-manifest.json').read_bytes()).hexdigest(),
                     'quiescence_sha256':hashlib.sha256((HERE/'guardian-quiescence.json').read_bytes()).hexdigest(),
                     'at':dt.datetime.now(dt.timezone.utc).isoformat()})
                checkpoint('health_registered')
        except BaseException:
            if attempted:
                try:ensure_guardian_evaluator(intent)
                except Exception as recovery:
                    save(HERE/'guardian-availability-held.json',{'error':type(recovery).__name__,'detail':str(recovery)[:500],
                         'source':git('rev-parse','HEAD'),'fence_retained':(ROOT/'runtime/INSTALLING').exists()})
            raise
    return manifest


def _finish_installation(bindings,expected,validate,*,install_bundle=bundle_install,restart=restart_service,
                        activate=None,checkpoint=lambda stage:None,deadline=None,quiesce=None):
    """Journal bundle, restarts, activation and exact owned-fence release.

    Recovery after release is bookkeeping only. It cannot restart live services,
    republish a bundle or reactivate a generation without the maintenance fence.
    """
    intent=D.read_json(HERE/'installation-intent.json');commit=intent.get('installed');tree=intent.get('tree')
    if intent.get('bindings')!=bindings or not commit or not validate():raise RuntimeError('Installation completion bindings changed')
    def exact():
        return (git('rev-parse','HEAD')==commit and git('rev-parse','HEAD^{tree}')==tree
                and not git('status','--porcelain') and validate())
    if not exact():raise RuntimeError('Installed source changed before completion')
    marker=ROOT/'runtime/INSTALLING';release=D.read_json(HERE/'fence-release-intent.json')
    release_fields={'installed':commit,'tree':tree,'fence_sha256':hashlib.sha256(expected).hexdigest()}
    if not marker.exists():
        if not release or any(release.get(k)!=v for k,v in release_fields.items()):raise RuntimeError('Missing owned fence without durable release intent')
        for name in ('guardian-manifest.json','service-restarts.json','activation.json'):
            if release.get('receipts',{}).get(name)!=hashlib.sha256((HERE/name).read_bytes()).hexdigest():raise RuntimeError('Released installation completion receipt changed')
        if D.read_json(ROOT/'runtime/resilience/manifest.json')!=D.read_json(HERE/'guardian-manifest.json') or Actions(ROOT/'runtime').meta('maintenance_recovery')!=D.read_json(HERE/'activation.json'):
            raise RuntimeError('Released installation guardian or activation binding changed')
        health=Actions(ROOT/'runtime').meta('install_health') or {}
        restart=D.read_json(HERE/'service-restarts.json')
        if (health.get('state')!='verified' or health.get('installed')!=commit or health.get('tree')!=tree
            or not restart.get('last_restart_epoch') or health.get('restart_epoch')!=restart['last_restart_epoch']
            or health.get('verified_restart_epoch')!=health['restart_epoch']):raise RuntimeError('Released installation no longer has exact healthy source and restart generation')
        complete={**release_fields,'state':'complete','recovered_after_release':True,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
        save(HERE/'installation-complete.json',complete);save(HERE/'installation-result.json',complete)
        return complete
    if marker.read_bytes()!=expected:raise RuntimeError('Foreign or replaced installation fence')
    manifest=establish_guardian_generation(intent,expected,validate,install_bundle=install_bundle,quiesce=quiesce,checkpoint=checkpoint)
    checkpoint('bundle_installed')
    restarts=D.read_json(HERE/'service-restarts.json')
    if restarts and restarts.get('installed')!=commit:raise RuntimeError('Restart receipt belongs to another generation')
    restarts= restarts or {'installed':commit,'services':{}}
    for label in ('space.venin.midnight-dashboard','space.venin.midnight'):
        if restarts['services'].get(label,{}).get('requested') is True:continue
        if not exact() or not marker.exists() or marker.read_bytes()!=expected:raise RuntimeError('Ownership changed before service restart')
        with P.source_lock(ROOT/'runtime',deadline=time.time()+15):
            if not exact() or not marker.exists() or marker.read_bytes()!=expected:raise RuntimeError('Ownership changed before restart health intent')
            # An intent can outlive both a successful kickstart and an unknown
            # subprocess outcome. Missing acknowledgement never authorises a
            # second destructive restart; preserve the original epoch/fence.
            for path in HERE.glob('service-restart-intent-*.json'):
                prior=D.read_json(path)
                if (path.is_symlink() or not isinstance(prior,dict) or prior.get('installed')!=commit
                    or prior.get('tree')!=tree or not prior.get('service')):
                    raise RuntimeError('Uncertain service restart intent; explicit reconciliation required')
                if prior['service']==label:
                    raise RuntimeError('Uncertain service restart for '+label+'; original intent retained, explicit reconciliation required')
            health=Actions(ROOT/'runtime').meta('install_health') or {}
            if health.get('installed')!=commit or health.get('tree')!=tree or health.get('state') not in {'verifying','verified'}:raise RuntimeError('Source health changed before service restart')
            epoch=uuid.uuid4().hex;armed=time.time()
            restart_intent={'installed':commit,'tree':tree,'service':label,'epoch':epoch,'not_before':armed,'previous_health':health}
            save(HERE/('service-restart-intent-'+epoch+'.json'),restart_intent)
            health=dict(health,state='verifying',at=armed,restart_epoch=epoch,
                        restart_requirements={**health.get('restart_requirements',{}),label:{'epoch':epoch,'not_before':armed}})
            for field in ('verified_at','verified_restart_epoch','verified_controller_process','verified_dashboard'):health.pop(field,None)
            Actions(ROOT/'runtime').meta('install_health',health,write=True)
            checkpoint('service_restart_intent_recorded')
            # Rollback shares this lock: source cannot change between the fresh
            # ownership/authority/health check and the actual service effect.
            if not exact() or not marker.exists() or marker.read_bytes()!=expected:
                raise RuntimeError('Ownership changed before service restart effect')
            current=Actions(ROOT/'runtime').meta('install_health') or {}
            if (current.get('installed')!=commit or current.get('tree')!=tree
                or current.get('state') not in {'verifying','verified'} or current.get('restart_epoch')!=epoch
                or current.get('restart_requirements',{}).get(label)!={'epoch':epoch,'not_before':armed}):
                raise RuntimeError('Source health changed before service restart effect')
            result=restart(label)
            checkpoint('service_restart_requested')
            restarts['services'][label]={**result,'health_epoch':epoch,'not_before':armed}
            restarts['last_restart_epoch']=epoch
            if restarts['services'][label].get('requested') is not True:raise RuntimeError('Service restart has no confirmed request')
            save(HERE/'service-restarts.json',restarts)
        checkpoint('service_restarted')
    deadline=deadline or time.time()+480
    while True:
        health=Actions(ROOT/'runtime').meta('install_health') or {}
        if not exact() or not marker.exists() or marker.read_bytes()!=expected:raise RuntimeError('Ownership changed while checking generation health')
        if health.get('installed')!=commit or health.get('tree')!=tree or health.get('state') not in {'verifying','verified'}:raise RuntimeError('Generation health rejected or lost the installed source')
        if health.get('restart_epoch')!=restarts.get('last_restart_epoch'):raise RuntimeError('Health observation belongs to another restart generation')
        if health['state']=='verified' and health.get('verified_restart_epoch')==health['restart_epoch']:break
        if time.time()>=deadline:raise RuntimeError('Exact generation health is still pending; resume this installation')
        time.sleep(5)
    with P.source_lock(ROOT/'runtime',deadline=time.time()+15):
        if not exact() or not marker.exists() or marker.read_bytes()!=expected:raise RuntimeError('Ownership changed before activation')
        def verified_generation():
            current=Actions(ROOT/'runtime').meta('install_health') or {}
            latest=D.read_json(HERE/'service-restarts.json')
            if (latest.get('installed')!=commit or not latest.get('last_restart_epoch')
                or current.get('state')!='verified' or current.get('installed')!=commit or current.get('tree')!=tree
                or current.get('restart_epoch')!=latest['last_restart_epoch']
                or current.get('verified_restart_epoch')!=current.get('restart_epoch')
                or any(latest.get('services',{}).get(label,{}).get('requested') is not True
                       or current.get('restart_requirements',{}).get(label,{}).get('epoch')!=latest['services'][label].get('health_epoch')
                       or current['restart_requirements'][label].get('not_before')!=latest['services'][label].get('not_before')
                       for label in ('space.venin.midnight','space.venin.midnight-dashboard'))):
                raise RuntimeError('Final source lock requires the exact verified current restart generation')
            return current,latest
        verified_generation()
        if activate is None:
            import maintenance_recovery as M
            activate=lambda:M.activate(ROOT)
        activation=Actions(ROOT/'runtime').meta('maintenance_recovery') or {}
        if activation.get('installed')!=commit or activation.get('tree')!=tree or activation.get('version')!=2:activation=activate()
        if activation.get('installed')!=commit or activation.get('tree')!=tree or activation.get('version')!=2:raise RuntimeError('Progress activation did not bind the installed generation')
        save(HERE/'activation.json',activation)
        verified_generation()
        if not exact() or not marker.exists() or marker.read_bytes()!=expected:raise RuntimeError('Ownership changed before fence release')
        release={**release_fields,'receipts':{name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in ('guardian-manifest.json','service-restarts.json','activation.json')}}
        save(HERE/'fence-release-intent.json',release)
        checkpoint('fence_release_intent_persisted')
        verified_generation()
        if not exact() or not marker.exists() or marker.read_bytes()!=expected:raise RuntimeError('Ownership changed after release intent before fence effect')
        D.marker(marker,False)
    checkpoint('fence_released')
    return finish_installation(bindings,expected,validate,install_bundle=install_bundle,restart=restart,
                               activate=activate,checkpoint=checkpoint,deadline=deadline,quiesce=quiesce)


def _main():
    verified=D.read_json(HERE/'verified.json');delivery=D.read_json(HERE/'delivery.json')
    helper_review=D.read_json(HERE/'deployment-review.json')
    if helper_review.get('decision')!='accept' or helper_review.get('issues')!=[] or helper_review.get('installer_sha256')!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise RuntimeError('The exact deployment helper has not passed separate review')
    if verified['review'].get('decision')!='accept' or verified['review'].get('issues')!=[] or any(c['code']!=0 or c['reason']!='exited' for c in verified['checks']):
        raise RuntimeError('Exact source lacks complete independent checks/review')
    if hashlib.sha256((HERE/'candidate.patch').read_bytes()).hexdigest()!=verified['patch_sha256']:
        raise RuntimeError('Reviewed patch changed')
    bindings=approved_authority(helper_review)
    base=verified['base'];commit=delivery.get('commit');tree=verified['tree']
    if delivery.get('status')!='merged' or not commit or git('rev-parse','HEAD') not in {base,commit} or git('status','--porcelain'):
        raise RuntimeError('Confirmed private merge or clean installed base changed')
    A.check(ROOT)
    registry=D.read_json(ROOT/'projects.json')
    controls=D.read_json(ROOT/'runtime/override.json')
    scheduled=helper_review.get('scheduled_controls');activation_gate=None
    if scheduled is not None:
        if verified['review'].get('scheduled_controls')!=scheduled or not scheduled_controls_current(scheduled,registry):
            raise RuntimeError('Reviewed scheduled controls changed; preserve current owner settings')
        bindings['scheduled_controls']=scheduled
        activation_gate={'sha256':helper_review.get('activation_gate_sha256'),'plan_sha256':helper_review.get('release_plan_sha256')}
        if (verified['review'].get('activation_gate_sha256')!=activation_gate['sha256']
            or verified['review'].get('release_plan_sha256')!=activation_gate['plan_sha256']
            or not activation_gate_current(activation_gate)):
            raise RuntimeError('Exact reviewed activation gate is missing or changed')
        gate=D.read_json(ROOT/'runtime/WEEKDAY_ACTIVATION_PENDING')
        if gate.get('base')!=base or gate.get('candidate_tree')!=tree:
            raise RuntimeError('Activation gate belongs to another source generation')
        bindings['activation_gate']=activation_gate
    elif controls.get('mode')!='continuous' or controls.get('expires_at') is not None or controls.get('created_at')!='2026-11-01T13:22:02.327074+00:00':
        raise RuntimeError('Owner run mode changed; preserve it and reconcile before adoption')
    pauses={str(p):p.exists() or p.is_symlink() for p in [ROOT/'runtime/PAUSED',*[Path(p['state_dir'])/'PAUSED' for p in registry['projects']]]}
    prior=D.read_json(HERE/'installation-intent.json')
    if prior:
        saved=prior.get('bindings',{})
        if saved.get('controls')!=controls or saved.get('pauses')!=pauses:raise RuntimeError('Original owner controls changed since installation intent')
        if saved.get('activation_gate')!=activation_gate:raise RuntimeError('Original activation gate binding changed since installation intent')
    bindings.update(controls=controls,pauses=pauses,fence_sha256=hashlib.sha256((HERE/'quiesce.json').read_bytes()).hexdigest(),
                    patch_sha256=verified['patch_sha256'],installer_sha256=helper_review['installer_sha256'])
    if rejected_generation(commit,tree):raise RuntimeError('Rejected generation retains its immutable rollback history')
    current_health=Actions(ROOT/'runtime').meta('install_health') or {}
    if (current_health.get('installed')==commit and current_health.get('tree')==tree
        and current_health.get('state') in {'rolling_back','rolled_back','held'}):
        raise RuntimeError('Rejected generation retains rollback ownership; the guardian must finalize restored-source proof')
    # Remote freshness is an initial-adoption gate. Already adopted source and
    # post-release bookkeeping reconcile only the bound local generation, even
    # if connectivity is lost or the remote branch has since advanced.
    if git('rev-parse','HEAD')==commit:
        wanted={'previous':base,'installed':commit,'tree':tree,'bindings':bindings}
        if not prior or any(prior.get(key)!=value for key,value in wanted.items()) or git('rev-parse','HEAD^{tree}')!=tree:
            raise RuntimeError('Adopted source lacks its exact approved local installation intent')
    else:
        call(['git','fetch','https://github.com/example-org/midnight.git','main'])
        if git('rev-parse',commit+'^{tree}')!=tree or git('rev-parse','FETCH_HEAD')!=commit:
            raise RuntimeError('Remote merge is not the exact independently reviewed tree')
    marker=ROOT/'runtime/INSTALLING';expected=(HERE/'quiesce.json').read_bytes()
    if not marker.exists() and D.read_json(HERE/'fence-release-intent.json'):
        print(json.dumps(finish_installation(bindings,expected,lambda:live_controls(controls,pauses,scheduled,activation_gate) and authority_current(bindings))),flush=True);return
    if not marker.exists() or marker.read_bytes()!=expected:raise RuntimeError('Source fence ownership changed')
    deadline=time.time()+900
    store=Actions(ROOT/'runtime')
    while True:
        if not live_controls(controls,pauses,scheduled,activation_gate):raise RuntimeError('Owner control changed during drain')
        active=[j for j in store.jobs() if P.alive(j.get('lease'))]
        executions=[e for e in store.executions() if P.live_tree(e['lease'])]
        if not active and not executions:break
        if time.time()>=deadline:raise RuntimeError('Healthy owned work has not reached an idle checkpoint')
        time.sleep(2)
    handles=[];adopted=False;idle_locked=False
    try:
        if git('rev-parse','HEAD')==base:
            with P.source_lock(ROOT/'runtime',deadline=min(deadline,time.time()+15)):
                if not marker.exists() or marker.read_bytes()!=expected or not live_controls(controls,pauses,scheduled,activation_gate) or not authority_current(bindings):
                    raise RuntimeError('Ownership, owner controls or authority changed before controller stop')
                if rejected_generation(commit,tree):raise RuntimeError('Rejected generation retains rollback ownership before controller stop')
                current_health=Actions(ROOT/'runtime').meta('install_health') or {}
                if (current_health.get('installed')==commit and current_health.get('tree')==tree
                    and current_health.get('state') in {'rolling_back','rolled_back','held'}):
                    raise RuntimeError('Rejected generation retains rollback ownership before controller stop')
                if git('rev-parse','HEAD')!=base:raise RuntimeError('Source changed before controller stop')
                prepare_coordinator_recovery(base,commit,tree,bindings)
                if not marker.exists() or marker.read_bytes()!=expected or not live_controls(controls,pauses,scheduled,activation_gate) or not authority_current(bindings):
                    raise RuntimeError('Ownership, owner controls or authority changed after arming recovery')
                armed_health=Actions(ROOT/'runtime').meta('install_health') or {}
                if (git('rev-parse','HEAD')!=base or git('status','--porcelain') or rejected_generation(commit,tree)
                    or armed_health.get('installed')==commit and armed_health.get('state') in {'rolling_back','rolled_back','held'}):
                    raise RuntimeError('Source or generation changed after arming recovery before controller stop')
                result=subprocess.run(['/bin/launchctl','kill','SIGTERM',f'gui/{os.getuid()}/space.venin.midnight'],capture_output=True,timeout=10)
            handles.append(locked(ROOT/'runtime/controller.lock',min(deadline,time.time()+60)))
            for project in registry['projects']:
                handles.append(locked(Path(project['state_dir'])/'supervisor.lock',min(deadline,time.time()+45)))
            idle_locked=True
        with P.source_lock(ROOT/'runtime',deadline=min(deadline,time.time()+15)):
            def validate():
                return (marker.exists() and marker.read_bytes()==expected and live_controls(controls,pauses,scheduled,activation_gate)
                    and not any(P.live_tree(e['lease']) for e in store.executions()) and authority_current(bindings))
            prepare_coordinator_recovery(base,commit,tree,bindings)
            adopt(base,commit,tree,bindings,validate)
            adopted=True
    finally:
        for handle in reversed(handles):handle.close()
        if not adopted and git('rev-parse','HEAD')==base:
            with P.source_lock(ROOT/'runtime',deadline=time.time()+10):
                health=Actions(ROOT/'runtime').meta('install_health') or {}
                never_adopted=(idle_locked and not (HERE/'coordinator-resumer-ready.json').exists()
                    and not (HERE/'installation-intent.json').exists() and not (HERE/'installation.json').exists()
                    and health.get('state')=='verified' and health.get('installed')==base
                    and health.get('tree')==git('rev-parse',base+'^{tree}') and live_controls(controls,pauses,scheduled,activation_gate))
                clean=(git('rev-parse','HEAD')==base and not git('status','--porcelain')
                       and git('write-tree')==git('rev-parse',base+'^{tree}'))
                released=bool(never_adopted and clean and marker.exists() and marker.read_bytes()==expected)
                if released:D.marker(marker,False)
                elif marker.exists() and marker.read_bytes()==expected:
                    save(HERE/'installation-held.json',{'reason':'Adoption may be partial; preserve source and reconcile the durable intent before release',
                         'previous':base,'installed':commit,'head':git('rev-parse','HEAD'),'status':git('status','--porcelain'),
                         'fence_retained':True,'controller_restart_requested':False})
            if released:subprocess.run(['/bin/launchctl','kickstart',f'gui/{os.getuid()}/space.venin.midnight'],capture_output=True,timeout=10)
    result=finish_installation(bindings,expected,lambda:live_controls(controls,pauses,scheduled,activation_gate) and authority_current(bindings))
    print(json.dumps(result),flush=True)


def finish_installation(*args,**kwargs):
    with installation_lock():return _finish_installation(*args,**kwargs)


def main(*,retire=True):
    with installation_lock():
        result=_main()
        if retire:disarm_coordinator_resumer()
        return result


if __name__=='__main__':
    if len(sys.argv)==5 and sys.argv[1]=='--resume-coordinator':
        ROOT=Path(sys.argv[2]).resolve();HERE=Path(sys.argv[3]).resolve()
        result=resume_coordinator(sys.argv[4]);print(json.dumps(result),flush=True)
        if result.get('state') in {'complete','retired'}:disarm_coordinator_resumer()
    else:
        if len(sys.argv)==3:ROOT=Path(sys.argv[1]).resolve();HERE=Path(sys.argv[2]).resolve()
        main()
