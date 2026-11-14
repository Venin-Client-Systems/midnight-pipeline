"""Shared resource leases for independently supervised project phases."""
from contextlib import contextmanager, ExitStack
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import process_observer as PO
import signal
import sys
import time
import durability as D
from action_store import Actions, fingerprint

CAPACITY={'local':1,'host':1,'cloud':2}
SLOT_FD=None
MEMORY_FD=None
GPU_FD=None
SLOT_NAMES={}


class PhaseInterrupted(RuntimeError):pass


def stamp(pid):
    if sys.platform=='darwin':
        value=PO.info(pid)
        return value['stamp'] if value and value['state']!='Z' else ''
    value=PO.run(['/bin/ps','-p',str(pid),'-o','lstart=,stat='],capture_output=True,text=True).stdout.strip()
    parts=value.rsplit(None,1)
    return parts[0].strip() if len(parts)==2 and not parts[1].startswith('Z') else ''


def lease(pid=None):
    pid=pid or os.getpid()
    if sys.platform=='darwin':
        native=PO.info(pid)
        return {'pid':pid,'stamp':native['stamp'] if native and native['state']!='Z' else '',
                'boot_id':D.boot_id(),'birth_us':native['birth_us'] if native else 0}
    return {'pid':pid,'stamp':stamp(pid),'boot_id':D.boot_id()}


def alive(value):
    if (value and type(value.get('pid')) is int and value.get('birth_us') and sys.platform=='darwin'
            and value.get('boot_id')==D.boot_id()):
        native=PO.info(value['pid'])
        return bool(native and native['state']!='Z' and native['birth_us']==value['birth_us'])
    matched=bool(value and type(value.get('pid')) is int and value.get('boot_id')==D.boot_id()
                and value.get('stamp') and stamp(value['pid'])==value['stamp'])
    return matched


def group_members(value):
    """A boot-bound session identity; a recycled leader is never our group."""
    if not value or value.get('boot_id')!=D.boot_id() or not value.get('group') or type(value.get('pid')) is not int:return []
    current=stamp(value['pid'])
    if current and not alive(value):return []
    if sys.platform=='darwin':
        rows=[p for p in PO.processes() if p['pgid']==value['pid'] and p['state']!='Z']
        if not current and not any(alive(m) and any(p['pid']==m['pid'] and p['birth_us']==m.get('birth_us',p['birth_us']) for p in rows) for m in value.get('members',[])):return []
        return [dict(pid=p['pid'],stamp=p['stamp'],boot_id=value['boot_id'],birth_us=p['birth_us']) for p in rows]
    output=PO.check_output(['/bin/ps','-axo','pid=,pgid=,stat=,lstart='],text=True,start_new_session=True)
    result=[]
    for line in output.splitlines():
        parts=line.split(None,3)
        if len(parts)==4 and parts[1]==str(value['pid']) and not parts[2].startswith('Z'):
            result.append({'pid':int(parts[0]),'stamp':parts[3].strip(),'boot_id':value['boot_id']})
    return result


def descendants(value):
    if not alive(value):return []
    rows=[]
    if sys.platform=='darwin':
        rows=[dict(pid=p['pid'],parent=p['parent'],stamp=p['stamp'],boot_id=value['boot_id'],birth_us=p['birth_us']) for p in PO.processes() if p['state']!='Z']
    else:
        output=PO.check_output(['/bin/ps','-axo','pid=,ppid=,pgid=,stat=,lstart='],text=True,start_new_session=True)
        for line in output.splitlines():
            p=line.split(None,4)
            if len(p)==5 and not p[3].startswith('Z'):
                rows.append({'pid':int(p[0]),'parent':int(p[1]),'stamp':p[4].strip(),'boot_id':value['boot_id']})
    selected={value['pid']};result=[]
    for _ in range(32):
        found=[r for r in rows if r['parent'] in selected and r['pid'] not in selected]
        if not found:break
        selected.update(r['pid'] for r in found);result+=found
    return [m for m in result if alive(m)]


def live_tree(value):return bool(value and (alive(value) or group_members(value) or any(alive(m) for m in value.get('members',[]))))


def stop_tree(value,*,grace=15):
    """Recheck PID identities at every signal; retain evidence on failed drain."""
    if not value:return True
    members=(group_members(value) if value.get('group') else ([value] if alive(value) else []))+value.get('members',[])+descendants(value)
    for item in members:
        if alive(item):
            try:os.kill(item['pid'],signal.SIGTERM)
            except ProcessLookupError:pass
    deadline=time.monotonic()+grace
    while time.monotonic()<deadline and live_tree(value):time.sleep(.1)
    for item in (group_members(value) if value.get('group') else ([value] if alive(value) else []))+members+descendants(value):
        if alive(item):
            try:os.kill(item['pid'],signal.SIGKILL)
            except ProcessLookupError:pass
    deadline=time.monotonic()+2
    while time.monotonic()<deadline and live_tree(value):time.sleep(.05)
    return not live_tree(value)


def enabled():return bool(os.environ.get('MIDNIGHT_ACTION_ID'))


def control_stopped(runtime, job=None, instant=None):
    """Read fresh owner controls at admission and execution, failing closed."""
    runtime=Path(runtime);instant=instant or dt.datetime.now(dt.timezone.utc)
    try:
        if any((runtime/name).exists() or (runtime/name).is_symlink() for name in
               ('PAUSED','INSTALLING','WEEKDAY_ACTIVATION_PENDING')):return True
        identity=job['id'] if job else os.environ.get('MIDNIGHT_ACTION_ID','')
        job=Actions(runtime).get(identity)
        if not job or job['state']!='running' or not alive(job.get('lease')) or instant.timestamp()>=job['deadline']:return True
        import schedule_control as S
        if S.performance_review(job):return False
        import project_action as A
        registry=D.read_json(runtime.parent/'projects.json',{})
        if job['project']=='midnight':
            return auxiliary_stopped(runtime,job,registry,instant)
        permitted,_,_=A.control(runtime,job['id'],registry,instant,S.window)
        return not permitted
    except (OSError,ValueError,KeyError,TypeError):return True


def auxiliary_stopped(runtime,job,registry,instant):
    """Exact ledger-backed discussion/maintenance identities, never a name bypass."""
    import schedule_control as S
    import commissioning as C
    import project_action as A
    if (not S.window(instant,registry,D.read_json(runtime/'override.json',{}))['active']
            or C.snapshot(runtime,instant)['blocks_dispatch']):return True
    payload=job['payload']
    if payload.get('source_head')!=A.source_head():return True
    if payload.get('kind')=='agent_discussion':
        import agent_workspace as W
        import agent_discussion as AD
        workspace=W.Workspace(runtime/'agent-workspace')
        with workspace.store.reader() as con:
            if not workspace.ready(con):return True
            row=con.execute('SELECT * FROM aw_work WHERE id=?',(payload.get('work'),)).fetchone()
        if not row:return True
        work=dict(row)
        return not (work['state']=='claimed' and json.loads(work['lease'] or '{}')==job['lease']
            and work['actor']==payload.get('actor') and work['kind']==payload.get('discussion_kind')
            and AD.eligible_work(work,registry))
    if payload.get('maintenance'):
        import core_repair as R
        if payload.get('continuation'):
            import maintenance_recovery as MR
            saved=MR.session_for(runtime.parent,payload['continuation'])
            valid=bool(saved and saved['id']==payload['maintenance'] and saved['state']=='running'
                and saved['lease']==job['lease'] and saved['payload']['capsule']['base']==payload['source_head']
                and saved['payload']['capsule']['fault']==payload.get('incident_binding'))
        else:
            saved=Actions(runtime).repair(payload['maintenance'])
            valid=bool(saved and saved['state']=='running' and saved['lease']==job['lease']
                and saved['payload'].get('incident_binding')==payload.get('incident_binding'))
        return not (valid and R.maintenance_current(runtime.parent,job))
    return True


@contextmanager
def gpu_lock(path, deadline=None):
    """Use an inherited managed lease, or acquire the personal shared lock.

    Only trusted worker entry code sees the descriptor. Close its copy before
    model/tool execution; the execution guard retains ownership until drainage.
    """
    token=os.environ.pop('MIDNIGHT_GPU_LEASE_FD',None)
    if token is not None:
        fd=int(token)
        try:
            expected=os.stat(path);actual=os.fstat(fd)
            runtime=Path(os.environ['MIDNIGHT_SHARED_ROOT'])/'runtime'
            identity=os.environ['MIDNIGHT_GPU_GUARD_PHASE']
            store=Actions(runtime)
            execution=next((e for e in store.executions() if e['phase']==identity),None)
            launch=D.read_json(runtime/'phases'/identity/'launch.json',{})
            if (actual.st_dev!=expected.st_dev or actual.st_ino!=expected.st_ino
                    or not execution or execution['job']!=os.environ['MIDNIGHT_ACTION_ID']
                    or not alive(execution['lease']) or os.getpgrp()!=execution['lease']['pid']
                    or launch.get('gpu_lease') is not True or control_stopped(runtime)):
                raise PhaseInterrupted('Inherited GPU lease is not owned by this action')
        finally:os.close(fd)
        yield
        return
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as handle:
        while True:
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if deadline is not None and time.monotonic()>=deadline:raise TimeoutError('Shared local GPU remained busy')
                time.sleep(.25)
        yield


@contextmanager
def admission_lock(runtime,*,deadline=None):
    folder=Path(runtime)/'resources';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    if folder.is_symlink():raise ValueError('Resource lease directory is a symlink')
    path=folder/'admission.lock';fd=os.open(path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'a') as handle:
        end=deadline if deadline is not None else time.time()+5
        while True:
            if time.time()>=end:raise PhaseInterrupted('Resource admission comparison expired')
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:time.sleep(.01)
        yield


@contextmanager
def source_lock(runtime,*,deadline=None,stopped=lambda:False):
    path=Path(runtime)/'source-adoption.lock';path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'a') as handle:
        end=deadline if deadline is not None else time.time()+5
        while True:
            if stopped() or time.time()>=end:raise PhaseInterrupted('Source adoption lock is held or control expired')
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:time.sleep(.1)
        yield


def memory_critical():
    try:
        result=subprocess.check_output(['/usr/sbin/sysctl','-n','kern.memorystatus_vm_pressure_level'],text=True,timeout=2)
        return result.strip()=='4'
    except (OSError,subprocess.SubprocessError):return False


@contextmanager
def resource(runtime,kind,deadline,*,stopped=lambda:False,waiting=lambda reason:None,pressure=memory_critical,poll=.25,admitted=lambda:None,turn=None):
    if kind not in CAPACITY:raise ValueError('Unknown resource class')
    folder=Path(runtime)/'resources';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    if folder.is_symlink():raise ValueError('Resource lease directory is a symlink')
    handles=[];selected=None;last_reason=None;last_pressure=0;critical=False
    try:
        for index in range(CAPACITY[kind]):
            path=folder/(kind+'-'+str(index)+'.lock')
            fd=os.open(path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
            SLOT_NAMES[fd]=path.name
            handles.append(os.fdopen(fd,'a'))
        while selected is None:
            if stopped() or time.time()>=deadline:raise PhaseInterrupted('Control or deadline prevents phase launch')
            if kind in {'local','host'} and time.monotonic()-last_pressure>5:
                critical=pressure();last_pressure=time.monotonic()
            reason='critical_memory_pressure' if critical else 'resource_'+kind
            if not critical:
                with admission_lock(runtime,deadline=deadline):
                    store=Actions(runtime)
                    owners={p['id']:p for p in store.phases() if p['kind']==kind and p['state']=='running'}
                    changed=False
                    for execution in store.executions():
                        if execution['kind']==kind:owners[execution['phase']]=execution
                        if not live_tree(execution['lease']) and not store.clear_execution(execution['phase'],execution['lease'],admission_owned=True):changed=True
                    live=sum(live_tree(p.get('lease')) for p in owners.values())
                    earlier=[] if turn is None else [p for p in store.phases() if p['kind']==kind and p['state']=='waiting'
                        and p['deadline']>time.time() and (p['created'],p['id'])<turn]
                    if not changed and not earlier and live<CAPACITY[kind]:
                        for handle in handles:
                            try:
                                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB);selected=handle;admitted();break
                            except BlockingIOError:pass
            if selected is None:
                if reason!=last_reason:waiting(reason);last_reason=reason
                time.sleep(poll)
        yield selected.fileno()
    finally:
        for handle in handles:
            SLOT_NAMES.pop(handle.fileno(),None);handle.close()


@contextmanager
def phase(runtime,project,kind,command,cwd,log,deadline,*,stdin=None,stopped=lambda:False,waiting=lambda reason:None,role=None):
    """Journal before process creation and pass the slot into the owned guard."""
    if not enabled():
        yield None;return
    store=Actions(runtime);job=os.environ['MIDNIGHT_ACTION_ID']
    key=fingerprint({'command':command,'cwd':str(cwd),'log':str(log),
                     'input':hashlib.sha256((stdin or '').encode()).hexdigest()})
    item=store.phase(job,project,kind,key,deadline,{'log':str(log),'command_sha256':fingerprint(command),'role':role})
    try:
        with resource(runtime,kind,deadline,stopped=stopped,waiting=waiting,
                      turn=(item['created'],item['id']),
                      admitted=lambda:store.phase_update(item['id'],'running',lease=lease(),progress=True)) as descriptor:
            global SLOT_FD, MEMORY_FD
            previous_slot,previous_memory=SLOT_FD,MEMORY_FD
            SLOT_FD=descriptor
            try:
                with ExitStack() as resources:
                    if kind in {'local','host'}:
                        import memory_coordination as MC
                        MEMORY_FD=resources.enter_context(MC.heavy(runtime,deadline,stopped=stopped,waiting=waiting,own_phase=item['id']))
                    yield item['id']
            finally:SLOT_FD,MEMORY_FD=previous_slot,previous_memory
    except PhaseInterrupted:
        if any(p['id']==item['id'] for p in store.phases()):
            store.phase_update(item['id'],'interrupted',result={'reason':'stopped_before_start'})
        raise
    except BaseException as exc:
        if any(p['id']==item['id'] for p in store.phases()):
            store.phase_update(item['id'],'failed',result={'reason':type(exc).__name__})
        raise
