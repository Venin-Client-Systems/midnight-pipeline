"""Trusted discovery and paired local coding qualification for all projects."""
import datetime as dt
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import uuid
import time
import urllib.parse
import urllib.request
import durability as D
import colibri_provider as GP
import model_bench as B
from model_cases import CASES

ROOT=Path(__file__).resolve().parent
RUNTIME=ROOT/'runtime/models'
CONFIG=ROOT/'models.json'
SUITE=hashlib.sha256(b'\0'.join((ROOT/name).read_bytes() for name in
    ('model_cases.py','model_bench.py','model_manager.py','models.json'))).hexdigest()
CONTROL_CHECK=lambda:None

@contextmanager
def transaction():
    RUNTIME.mkdir(parents=True,mode=0o700,exist_ok=True);os.chmod(RUNTIME,0o700)
    with (RUNTIME/'maintenance.lock').open('a') as mutex:
        fcntl.flock(mutex,fcntl.LOCK_EX)
        yield

def now():return dt.datetime.now(dt.timezone.utc)
def save(state):D.save_json(RUNTIME/'model-state.json',state)
def load():return D.read_json(RUNTIME/'model-state.json',{})
def config():return json.loads(CONFIG.read_text())
def ram():return int(subprocess.check_output(['/usr/sbin/sysctl','-n','hw.memsize'],text=True))
def public_json(url):
    # URLs are constructed solely from the version-controlled trusted catalogue.
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Midnight-local-model-scout/1'}),timeout=10) as r:
        raw=r.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:raise ValueError('Discovery response exceeds bound')
        return json.loads(raw),hashlib.sha256(raw).hexdigest()

def metadata(path,body=None):
    """Read local model metadata; a cold Ollama listing can exceed the first bound at window start."""
    try:return B.api(path,body)
    except TimeoutError:pass
    try:return B.api(path,body,timeout=40)
    except TimeoutError:raise TimeoutError('Local model metadata timed out twice: '+path) from None


def local_descriptor(name, context=32768):
    if name==GP.MODEL:
        value=GP.descriptor(verify=True)
        if context!=value['context']:raise ValueError('Colibri context profile changed')
        return value
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.:/-]{1,100}',name):raise ValueError('Invalid local model name')
    entry=next((x for x in metadata('/api/tags').get('models',[]) if x['name']==name),None)
    if not entry:raise ValueError('Selected local model is missing')
    show=B.api('/api/show',{'model':name})
    if 'tools' not in show.get('capabilities',[]):raise ValueError('Selected model has no tool support')
    return {'model':name,'digest':entry['digest'],'context':context,'bytes':entry['size'],
        'parameters_sha256':hashlib.sha256(show.get('parameters','').encode()).hexdigest()}

def active(verify=False):
    trial=GP.active(verify=verify)
    if trial:return trial
    cfg=config();state=load();item=state.get('active')
    if item is None:
        # Fresh installations must explicitly attest their incumbent before work.
        if not verify:return dict(cfg['baseline'],status='awaiting_initial_attestation')
        item=local_descriptor(cfg['baseline']['model'],cfg['baseline']['context'])
        if item['digest']!=cfg['baseline']['digest']:raise ValueError('Incumbent digest changed; qualification required')
    if verify and local_descriptor(item['model'],item['context'])!=item:
        raise ValueError('Selected model digest/parameters changed; refusing silent replacement')
    return item

def dispatch_descriptor():
    item=active(verify=True)
    if GP.matches(item):return dict(item,promotion_generation=None)
    canary=load().get('canary')
    return dict(item,promotion_generation=canary['generation'] if canary else None)

@contextmanager
def task_selection():
    """Selection cannot change until the dispatched project cycle finishes."""
    with transaction():
        B.recover_probe(RUNTIME)
        yield dispatch_descriptor()

def discover(cfg):
    observations=[]
    physical=ram()
    for candidate in cfg['candidates']:
        CONTROL_CHECK()
        record=dict(candidate,checked_at=now().isoformat())
        try:
            card,card_hash=public_json('https://huggingface.co/api/models/'+candidate['card'])
            license=(card.get('cardData') or {}).get('license')
            record.update(card_sha256=card_hash,observed_license=license)
            if license!=candidate['license']:raise ValueError('Publisher licence requires Codex assessment')
            family,tag=candidate['model'].split(':')
            manifest,digest=public_json('https://registry.ollama.ai/v2/library/'+family+'/manifests/'+tag)
            size=sum(layer['size'] for layer in manifest['layers'])
            model_layers=[x for x in manifest['layers'] if x['mediaType']=='application/vnd.ollama.image.model']
            if not model_layers:raise ValueError('No local weights; remote-only models are ineligible')
            record.update(source_digest=digest,bytes=size)
            record['eligible']=size<=physical*cfg['weight_ram_fraction']
            record['reason']='awaiting_local_qualification' if record['eligible'] else 'weights_exceed_ram_fit'
        except InterruptedError:raise
        except (OSError,ValueError,KeyError,TypeError) as exc:
            record.update(eligible=False,reason=type(exc).__name__+': '+str(exc)[:180])
        observations.append(record)
    releases=[]
    for publisher in cfg['publishers']:
        CONTROL_CHECK()
        try:
            entries,_=public_json('https://huggingface.co/api/models?'+urllib.parse.urlencode(
                {'author':publisher,'sort':'lastModified','direction':-1,'limit':30}))
            releases.extend({'id':x['id'],'modified':x.get('lastModified'),
                'status':'catalogued' if any(c['card']==x['id'] for c in cfg['candidates']) else 'requires_compatibility_assessment'}
                for x in entries if x.get('pipeline_tag')=='text-generation' or 'coder' in x['id'].lower() or 'devstral' in x['id'].lower())
        except InterruptedError:raise
        except (OSError,ValueError,KeyError,TypeError) as exc:
            releases.append({'id':publisher,'status':'discovery_unavailable','reason':type(exc).__name__})
    return {'checked_at':now().isoformat(),'ram_bytes':physical,'candidates':observations,'publisher_releases':releases}

def result_key(record):return record['model']+'@'+record['source_digest']+'@'+SUITE

def compare(results):
    """Promotion needs all six challenger passes and a measurable paired win."""
    if len(results)!=len(CASES)*4:return {'promote':False,'reason':'comparison_incomplete'}
    baseline=[r for r in results if r['side']=='incumbent'];challenger=[r for r in results if r['side']=='challenger']
    a=sum(r['passed'] for r in baseline);b=sum(r['passed'] for r in challenger)
    t1=sum(r['seconds'] for r in baseline);t2=sum(r['seconds'] for r in challenger)
    fit=all(r['stop_reason'] is None and r['swap_growth_mib']<=4096 and r['peak_pressure']<4 for r in challenger)
    win=b==len(CASES)*2 and fit and (b>a or (a==b and t2<=t1*.85))
    return {'promote':win,'reason':'all_checks_and_paired_win' if win else 'no_proven_quality_or_speed_win',
        'incumbent_passed':a,'challenger_passed':b,'total_each':len(CASES)*2,
        'incumbent_seconds':t1,'challenger_seconds':t2,'memory_fit':fit,
        'scope':'three synthetic coding behaviours, each repeated twice; not a universal model ranking'}

def history(state, kind, **values):
    event={'at':now().isoformat(),'kind':kind,**values}
    state.setdefault('history',[]).append(event);state['history']=state['history'][-100:]
    with (RUNTIME/'events.jsonl').open('a') as f:f.write(json.dumps(event)+'\n');f.flush();os.fsync(f.fileno())

def alias_name(candidate, cfg):
    identity={'source':candidate['source_digest'],'context':cfg['context'],'temperature':candidate['temperature']}
    digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    return 'midnight-'+candidate['model'].split(':')[0]+':'+digest[:20]

def same_configuration(left,right):
    # Ollama's manifest digest covers weights, template and parameter blobs.
    # Alias names and the presentation order of /api/show are not model changes.
    return left['digest']==right['digest'] and left['context']==right['context']

@contextmanager
def acquisition_window(seconds=600):
    """Hard deadline/liveness checks also interrupt a stalled or trickling read."""
    previous=signal.getsignal(signal.SIGALRM);timer=signal.getitimer(signal.ITIMER_REAL)
    deadline=time.monotonic()+seconds
    def check(_sig,_frame):
        CONTROL_CHECK()
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Resumable model acquisition deadline')
        signal.setitimer(signal.ITIMER_REAL,min(1,remaining))
    signal.signal(signal.SIGALRM,check);signal.setitimer(signal.ITIMER_REAL,min(1,seconds))
    try:yield
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)
        if timer[0]:signal.setitimer(signal.ITIMER_REAL,*timer)

def install(candidate, cfg):
    # Ollama resumes content-addressed layer downloads; no remote code or packages.
    if shutil.disk_usage(RUNTIME).free<candidate['bytes']+10*1024**3:raise ValueError('Insufficient free disk headroom')
    CONTROL_CHECK()
    deadline=time.monotonic()+600
    req=urllib.request.Request('http://127.0.0.1:11434/api/pull',data=json.dumps({'model':candidate['model'],'stream':True}).encode(),headers={'Content-Type':'application/json'})
    complete=False
    with acquisition_window(),urllib.request.urlopen(req,timeout=30) as response:
        while line:=response.readline(1024*1024):
            CONTROL_CHECK()
            if time.monotonic()>=deadline:raise TimeoutError('Resumable model acquisition deadline')
            value=json.loads(line)
            if value.get('error'):raise ValueError(value['error'])
            if value.get('status')=='success':complete=True
    if not complete:raise ValueError('Download did not complete')
    CONTROL_CHECK()
    source=local_descriptor(candidate['model'])
    if source['digest']!=candidate['source_digest']:raise ValueError('Upstream changed during acquisition; rediscover before qualification')
    alias=alias_name(candidate,cfg)
    existing=next((x for x in B.api('/api/tags').get('models',[]) if x['name']==alias),None)
    protected=[x['model'] for x in (load().get('active'),load().get('previous')) if x]
    if alias in protected:
        return local_descriptor(alias,cfg['context'])
    if existing:
        # Immutable alias reuse is allowed, rewriting existing artifacts is not.
        return local_descriptor(alias,cfg['context'])
    B.api('/api/create',{'model':alias,'from':candidate['model'],'parameters':{
        'num_ctx':cfg['context'],'temperature':candidate['temperature']},'stream':False},timeout=120)
    return local_descriptor(alias,cfg['context'])

def _step():
    if GP.active():return {'status':'owner_colibri_default_active' if GP.trial_status().get('mode')=='owner_default' else 'owner_colibri_trial_active','benchmarks_suspended':True}
    CONTROL_CHECK()
    cfg=config()
    if not cfg.get('enabled'):return {'status':'disabled'}
    RUNTIME.mkdir(parents=True,mode=0o700,exist_ok=True);os.chmod(RUNTIME,0o700)
    B.recover_probe(RUNTIME)
    state=load()
    if state.get('retry_after') and now()<dt.datetime.fromisoformat(state['retry_after']):
        return {'status':'maintenance_backoff','retry_after':state['retry_after']}
    if not state.get('active'):
        state.update(schema_version=1,active=active(verify=True),evaluated={},history=[])
        history(state,'incumbent_attested',model=state['active']['model']);save(state)
    if state.get('owner_pin'):
        # The owner chose this worker explicitly: no discovery, benchmark or
        # speed-biased promotion may replace it until it is unpinned.
        return {'status':'owner_pinned','model':state['active']['model'],
                'matches_active':state['owner_pin'].get('digest')==state['active'].get('digest')}
    discovery=state.get('discovery',{})
    if not discovery or now()-dt.datetime.fromisoformat(discovery['checked_at'])>=dt.timedelta(hours=cfg['discovery_hours']):
        state['discovery']=discover(cfg);save(state)
    # Discovery is cheap and does not own the GPU. Every acquisition/probe does.
    with B.GPU_LOCK.open('a') as gpu:
        try:fcntl.flock(gpu,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'gpu_busy'}
        pending=state.get('comparison')
        if not pending:
            candidate=next((c for c in state['discovery']['candidates'] if c.get('eligible')
                and result_key(c) not in state['evaluated'] and c['source_digest']!=state['active']['digest']
                and (not state.get('acquisition_failures',{}).get(result_key(c))
                    or (state['acquisition_failures'][result_key(c)]['count']<2 and now()>=dt.datetime.fromisoformat(state['acquisition_failures'][result_key(c)]['retry_after'])))),None)
            if not candidate:return {'status':'current','model':state['active']['model']}
            state['operation']={'phase':'acquiring','candidate':candidate['model'],'at':now().isoformat()};save(state)
            try:challenger=install(candidate,cfg)
            except InterruptedError:raise
            except (OSError,ValueError,KeyError,TypeError) as exc:
                key=result_key(candidate);old=state.setdefault('acquisition_failures',{}).get(key,{'count':0})
                state['acquisition_failures'][key]={'count':old['count']+(0 if isinstance(exc,OSError) else 1),'reason':type(exc).__name__+': '+str(exc)[:180],
                    'retry_after':(now()+dt.timedelta(hours=6)).isoformat(),'candidate':candidate['model']}
                history(state,'acquisition_held',model=candidate['model']);save(state)
                return {'status':'candidate_acquisition_held','candidate':candidate['model']}
            if same_configuration(challenger,state['active']):
                state['evaluated'][result_key(candidate)]={'verdict':{'promote':False,'reason':'same_configuration'},'at':now().isoformat()}
                save(state);return {'status':'same_configuration_skipped'}
            pending={'id':now().strftime('%Y%m%dT%H%M%SZ')+'-'+candidate['source_digest'][:8],
                'key':result_key(candidate),'source':candidate,'incumbent':active(verify=True),
                'challenger':challenger,'suite':SUITE,'results':[]}
            D.atomic_write(RUNTIME/'comparisons'/pending['id']/'plan.json',(json.dumps(pending,indent=2)+'\n').encode())
            state['comparison']=pending;history(state,'qualification_started',model=challenger['model']);save(state)
            return {'status':'acquired','candidate':challenger['model']}
        if pending['suite']!=SUITE or pending['incumbent']!=state['active']:
            history(state,'comparison_abandoned',reason='suite_or_incumbent_changed');state.pop('comparison');save(state)
            return {'status':'restarting_comparison'}
        if same_configuration(pending['challenger'],pending['incumbent']):
            state['evaluated'][pending['key']]={'verdict':{'promote':False,'reason':'same_configuration'},'comparison':pending['id'],'at':now().isoformat()}
            history(state,'same_configuration_skipped',model=pending['challenger']['model']);state.pop('comparison');save(state)
            return {'status':'same_configuration_skipped'}
        index=len(pending['results']);side=('incumbent','challenger')[index%2]
        descriptor=pending[side]
        if local_descriptor(descriptor['model'],descriptor['context'])!=descriptor:raise ValueError('Qualification model changed')
        directory=RUNTIME/'runs'/pending['id']/f'{index:02}-{side}'
        # An interrupted probe has no result and is retained; next attempt gets a fresh repo.
        if directory.exists():directory=directory.with_name(directory.name+'-retry-'+now().strftime('%H%M%S%f'))
        state['operation']={'phase':'benchmarking','candidate':pending['challenger']['model'],
            'side':side,'probe':index+1,'total':len(CASES)*4,'at':now().isoformat()};save(state)
        try:
            result=B.probe(directory,descriptor,CASES[(index//2)%len(CASES)],cfg['probe_seconds'],gpu.fileno(),ram(),lease_path=RUNTIME/'model-probe.json',cancel=CONTROL_CHECK)
        finally:
            B.api('/api/generate',{'model':descriptor['model'],'keep_alive':0},timeout=30)
        result['side']=side;result['evidence']=str(directory/'result.json');pending['results'].append(result)
        state['operation']={'phase':'between_probes','completed':len(pending['results']),'total':len(CASES)*4,'at':now().isoformat()}
        failed_absolute_gate=side=='challenger' and not result['passed']
        if failed_absolute_gate or len(pending['results'])==len(CASES)*4:
            verdict=({'promote':False,'reason':'challenger_failed_required_probe','completed_probes':len(pending['results'])}
                if failed_absolute_gate else compare(pending['results']))
            state['evaluated'][pending['key']]={'verdict':verdict,'comparison':pending['id'],'at':now().isoformat()}
            D.atomic_write(RUNTIME/'comparisons'/pending['id']/'verdict.json',
                (json.dumps({'verdict':verdict,'results':pending['results']},indent=2)+'\n').encode())
            if verdict['promote']:
                state['previous']=state['active'];state['active']=pending['challenger']
                state['canary']={'observed_runs':[],'failures':0,'generation':uuid.uuid4().hex,'started_at':now().isoformat()}
                history(state,'promoted',model=state['active']['model'],previous=state['previous']['model'],verdict=verdict)
            else:history(state,'retained_incumbent',model=state['active']['model'],challenger=pending['challenger']['model'],verdict=verdict)
            state.pop('comparison')
        state.pop('error',None);state.pop('retry_after',None);save(state)
        return {'status':'probe_complete','passed':result['passed'],'model':state['active']['model']}

def _pin(state,reason,current=None):
    current=current or active(verify=True)
    if state.get('active')!=current:raise ValueError('Pin requires the verified current selected model')
    if state.get('comparison'):
        history(state,'comparison_abandoned',reason='owner_pin',challenger=state['comparison'].get('challenger',{}).get('model'));state.pop('comparison')
    if state.get('canary'):
        # Owner selection replaces the automatic canary and its rollback.
        history(state,'canary_retired',model=current['model'],reason='owner_pin',observed_runs=len(state['canary'].get('observed_runs',[])));state.pop('canary')
    state.pop('operation',None)
    state['owner_pin']={'model':current['model'],'digest':current['digest'],'context':current['context'],
                        'parameters_sha256':current.get('parameters_sha256'),'at':now().isoformat(),'reason':reason[:300]}
    history(state,'owner_pinned',model=current['model'],reason=reason[:300]);save(state)
    return state['owner_pin']


def pin(reason='owner_selected_worker'):
    with transaction():
        if GP.active():raise ValueError('Disable the GLM provider selection before pinning a local worker')
        return _pin(load(),reason)


def adopt(model,context=None,reason='owner_selected_at_setup'):
    """Select an installed local model as the pinned worker without editing tracked configuration."""
    with transaction():
        if GP.active():raise ValueError('Disable the GLM provider selection before adopting a local worker')
        RUNTIME.mkdir(parents=True,mode=0o700,exist_ok=True);os.chmod(RUNTIME,0o700)
        state=load()
        if any(state.get(key) for key in ('comparison','canary','operation')):
            raise ValueError('Model maintenance is in progress; adopt after it finishes')
        item=local_descriptor(model,int(context or config()['context']))
        state.setdefault('schema_version',1);state.setdefault('evaluated',{});state.setdefault('history',[])
        state['active']=item;history(state,'owner_adopted',model=item['model'],digest=item['digest'])
        return _pin(state,reason,item)


def unpin(reason='owner_released_pin'):
    with transaction():
        state=load()
        if not state.pop('owner_pin',None):return {'status':'not_pinned'}
        history(state,'owner_unpinned',model=state.get('active',{}).get('model'),reason=reason[:300]);save(state)
        return {'status':'unpinned'}


def snapshot():
    """File-only observation. Never discovers/downloads/benchmarks on a GET."""
    try:
        state=load();cfg=config();incumbent=state.get('active',cfg['baseline']);item=GP.active() or incumbent
        selected=GP.trial_status();owner_default=bool(selected and selected.get('mode')=='owner_default')
        return {'enabled':cfg['enabled'],'active':item,'incumbent':incumbent,'owner_selection':selected,
            'owner_trial':None if owner_default else selected,'selection_kind':('owner_default' if owner_default else 'owner_trial') if GP.matches(item) else 'owner_pinned' if state.get('owner_pin') else 'qualified_incumbent','previous':state.get('previous'),
            'owner_pin':state.get('owner_pin'),
            'discovery':state.get('discovery'),'operation':state.get('operation'),
            'comparison':({k:v for k,v in state['comparison'].items() if k not in ('results','source')} | {'completed':len(state['comparison']['results']),'total':len(CASES)*4}) if state.get('comparison') else None,
            'history':state.get('history',[])[-12:],'evaluated':state.get('evaluated',{}),
            'acquisition_failures':state.get('acquisition_failures',{}),'error':state.get('error'),'retry_after':state.get('retry_after'),
            'qualification_scope':(('Explicit owner default for real work; model quality unproven, stored incumbent preserved.' if owner_default else 'Owner-requested experimental real-work trial; model quality unqualified, stored incumbent preserved.') if GP.matches(item) else 'Best proven eligible configuration in the tracked catalogue; unassessed releases require Codex compatibility review.')}
    except (OSError,ValueError,KeyError,TypeError) as exc:return {'error':'Model observation unavailable: '+type(exc).__name__}

def _rollback(state,reason):
    if not state.get('previous'):return {'status':'no_previous_model'}
    old=state['active'];state['active']=state['previous'];state.pop('previous');state.pop('canary',None)
    history(state,'rolled_back',model=state['active']['model'],rejected=old['model'],reason=reason);save(state)
    return {'status':'rolled_back','model':state['active']['model']}

def rollback(reason='operator_requested'):
    with transaction():
        if GP.active():return GP.disable(reason)
        return _rollback(load(),reason)

def _observe(projects):
    """Only protocol failures in the promoted canary trigger automatic rollback."""
    state=load()
    if not state.get('canary'):return
    canary=state['canary']
    records=[]
    for project in projects:
        runs=Path(project['state_dir'])/'runs'
        for receipt in sorted(runs.glob('*/worker-model.json')):
            identity=project['id']+'/'+receipt.parent.name
            if identity in canary['observed_runs']:continue
            try:recorded=json.loads(receipt.read_text())
            except (OSError,ValueError):continue
            if not isinstance(recorded,dict):continue
            if recorded.get('digest')!=state['active']['digest'] or recorded.get('promotion_generation')!=canary['generation']:continue
            outcome=receipt.parent/'outcome.json'
            if not outcome.exists():continue
            try:
                value=json.loads(outcome.read_text())
                if not isinstance(value,dict):continue
                completion=json.loads((receipt.parent/'worker-completion.json').read_text())
                if not isinstance(completion,dict) or completion.get('response_observed') is not True:continue
                dt.datetime.fromisoformat(value['at'])
                if value['at']>=canary['started_at']:records.append((value['at'],identity,receipt.parent,completion))
            except (OSError,ValueError,KeyError,TypeError):continue
    for _,identity,folder,completion in sorted(records):
        diagnostic=folder/'worker-diagnostic.json'
        try:
            detail=json.loads(diagnostic.read_text()) if diagnostic.exists() else {}
            if not isinstance(detail,dict):continue
            failure=detail.get('unfinished_tool_call',False)
            if not failure and completion.get('finished') is not True:continue
        except (OSError,ValueError):continue
        canary['observed_runs'].append(identity)
        canary['failures']=canary['failures']+1 if failure else 0
        if canary['failures']>=3:
            _rollback(state,'three_consecutive_tool_protocol_failures');return
        if len(canary['observed_runs'])>=20:
            state.pop('canary');history(state,'canary_finished',model=state['active']['model'],observed_runs=20);break
    save(state)

def observe(projects):
    with transaction():_observe(projects)

def step():
    with transaction():
        try:return _step()
        except InterruptedError:raise
        except Exception as exc:
            state=load()
            state['error']={'at':now().isoformat(),'type':type(exc).__name__,'detail':str(exc)[:240]}
            state['retry_after']=(now()+dt.timedelta(hours=1)).isoformat();save(state)
            return {'status':'maintenance_unavailable','error':type(exc).__name__}

def main():
    def interrupted(_sig,_frame):raise InterruptedError('Model maintenance interrupted at schedule/control boundary')
    signal.signal(signal.SIGTERM,interrupted)
    try:
        import midnight as controller
        if len(sys.argv)!=3 or sys.argv[1]!='--managed-token':raise ValueError('Controller maintenance lease required')
        lease=controller.read(controller.STATE/'model-maintenance.json')
        if (lease.get('token')!=sys.argv[2] or lease.get('controller_pid')!=os.getppid()
                or lease.get('boot_id')!=D.boot_id() or controller.stamp(os.getppid())!=lease.get('controller_stamp')):
            raise ValueError('Controller maintenance lease mismatch')
        def control():
            timing=controller.window(now(),controller.registry(),controller.read(controller.STATE/'override.json'))
            if ((controller.STATE/'PAUSED').exists() or not timing['active'] or now()>=dt.datetime.fromisoformat(lease['cutoff'])
                    or controller.stamp(lease['controller_pid'])!=lease['controller_stamp']):
                raise InterruptedError('Model maintenance outside permitted window')
        global CONTROL_CHECK
        CONTROL_CHECK=control;control()
        observe(controller.registry()['projects'])
        print(json.dumps(step()));return 0
    except InterruptedError:return 75
    except Exception as exc:
        print(json.dumps({'status':'maintenance_unavailable','error':type(exc).__name__}));return 1

if __name__=='__main__':sys.exit(main())
