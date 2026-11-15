"""Owned background project action; only registered backend commands execute."""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import durability as D
from action_store import Actions, fingerprint
import phase_runtime as P
import commissioning as COM

ROOT=Path(__file__).resolve().parent
STOP=False


def stopping(_sig,_frame):
    global STOP
    STOP=True


def source_head():
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip():
        raise ValueError('Coordinator has owner changes; no new action launched')
    return subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()


def launch(runtime,project,worker_model,brief,session,cutoff,*,spawn=None,commissioning=False):
    runtime=Path(runtime);store=Actions(runtime)
    with P.source_lock(runtime):
        if any((runtime/name).exists() or (runtime/name).is_symlink() for name in ('INSTALLING','PAUSED','WEEKDAY_ACTIVATION_PENDING')):return None
        if not commissioning and COM.snapshot(runtime,dt.datetime.now(dt.timezone.utc))['blocks_dispatch']:return None
        payload={'project':project['id'],'worker_model':worker_model,'brief':brief,'session':session,
                 'source_head':source_head()}
        state=D.read_json(Path(project['state_dir'])/'state.json',{})
        payload['resume']=bool(state.get('pending') or state.get('active_attempt'))
        job=(COM.reserve(runtime,project,payload,dt.datetime.now(dt.timezone.utc)) if commissioning
             else store.submit(project['id'],payload,cutoff.timestamp()))
        if job is None:return None
        if job['state']=='queued':spawn_pending(runtime,job,spawn=spawn)
        return job['id']


def spawn_pending(runtime,job,*,spawn=None):
    runtime=Path(runtime)
    spawn=spawn or subprocess.Popen
    if job['state']=='queued':
        log=runtime/'actions'/job['id']/'supervisor.log';log.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with log.open('a') as output:
            entry='agent_discussion.py' if job['payload'].get('kind')=='agent_discussion' else 'project_action.py'
            flags=['-I','-B'] if entry=='agent_discussion.py' else ['-B']
            spawn([sys.executable,*flags,str(ROOT/entry),'--runtime',str(runtime),'--action',job['id']],
                  stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)


def stop(proc):
    if proc.poll() is None:
        proc.terminate()
        try:proc.wait(timeout=15)
        except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)


def register_backend(runtime,identity,project):
    """The child records ownership before reading or recovering product state."""
    job=Actions(runtime).get(identity)
    if (not job or job['state']!='running' or job['project']!=project
            or not P.alive(job['lease']) or os.getppid()!=job['lease']['pid']
            or time.time()>=job['deadline']):raise ValueError('Managed backend has no live owning action')
    D.atomic_write(Path(runtime)/'actions'/identity/'backend-lease.json',(json.dumps(P.lease())+'\n').encode())


def drain(runtime,identity,*,grace=7):
    store=Actions(runtime);remaining=False
    for execution in store.executions(job=identity):
        if P.stop_tree(execution['lease'],grace=grace):
            if not store.clear_execution(execution['phase'],execution['lease']):remaining=True
        else:remaining=True
    for phase in store.phases():
        if phase['job']!=identity:continue
        if P.stop_tree(phase.get('lease'),grace=grace):
            try:store.phase_update(phase['id'],'interrupted',expected_lease=phase.get('lease'),result={'reason':'owned_action_drained'})
            except ValueError:pass # The owning backend may have committed first.
        else:remaining=True
    backend=D.read_json(Path(runtime)/'actions'/identity/'backend-lease.json',{})
    if not P.stop_tree(backend,grace=grace):remaining=True
    return not remaining


def control(runtime,identity,registry,now,window):
    job=Actions(runtime).get(identity)
    if not job:return False,None,{'active':False,'cutoff':now}
    project=next((p for p in registry['projects'] if p['id']==job['project']),None)
    timing=window(now,registry,D.read_json(Path(runtime)/'override.json',{}))
    if job.get('payload',{}).get('commissioning'):
        timing=dict(timing,active=COM.permits(runtime,job,now),
                    cutoff=dt.datetime.fromtimestamp(job['deadline'],dt.timezone.utc),mode='commissioning')
    elif COM.snapshot(runtime,now)['blocks_dispatch']:
        timing=dict(timing,active=False)
    permitted=bool(project and project.get('on_schedule') and not STOP and not (Path(runtime)/'PAUSED').exists()
        and not (Path(runtime)/'INSTALLING').exists() and not ((Path(runtime)/'WEEKDAY_ACTIVATION_PENDING').exists() or (Path(runtime)/'WEEKDAY_ACTIVATION_PENDING').is_symlink())
        and not (Path(project['state_dir'])/'PAUSED').exists() and timing['active']
        and now.timestamp()<min(job['deadline'],timing['cutoff'].timestamp()))
    return permitted,project,timing


def execute(runtime,identity,*,spawn=None,clock=time.time):
    runtime=Path(runtime)
    spawn=spawn or subprocess.Popen
    if runtime.resolve()!=(ROOT/'runtime').resolve():
        raise ValueError('Unregistered action runtime')
    store=Actions(runtime);job=store.get(identity)
    if not job:return 1
    import midnight as M
    registry=M.registry();project=next((p for p in registry['projects'] if p['id']==job['project']),None)
    if project is None:raise ValueError('Action project is no longer registered')
    lease=P.lease()
    if not store.claim(identity,lease):return 0
    process=None;outcome='failed';result={'reason':'not_launched'}
    try:
        permitted,project,timing=control(runtime,identity,M.registry(),M.now(),M.window)
        if not permitted or (runtime/'INSTALLING').exists():
            outcome='interrupted';result={'reason':'control_before_start'};return 0
        expected=source_head()
        if expected!=job['payload']['source_head']:raise ValueError('Coordinator source changed before action launch')
        M.validate_backend(project)
        folder=runtime/'actions'/identity;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        brief=folder/'brief.json';D.atomic_write(brief,(json.dumps(job['payload']['brief'],indent=2)+'\n').encode())
        deadline=min(dt.datetime.fromtimestamp(job['deadline'],dt.timezone.utc),timing['cutoff'])
        mode='managed-resume' if job['payload'].get('resume') else 'managed-once'
        command=[M.PYTHON,'-P',str(Path(project['path'])/project['controller']),mode,'--until',deadline.isoformat(),'--brief',str(brief)]
        if mode=='managed-resume':command+=['--action',identity]
        if M.effective_limits(M.now()):command+=['--limits',str(runtime/'override.json')]
        env=os.environ.copy();env.update(MIDNIGHT_SHARED_ROOT=str(ROOT),MIDNIGHT_LOCAL_CODE_BIN=str(ROOT/'local_worker.py'),
            MIDNIGHT_WORKER_DESCRIPTOR=json.dumps(job['payload']['worker_model']),MIDNIGHT_ACTION_ID=identity)
        log=runtime/'sessions'/job['payload']['session']/(project['id']+'.log');log.parent.mkdir(parents=True,exist_ok=True)
        with log.open('a') as output:
            permitted,_,_=control(runtime,identity,M.registry(),M.now(),M.window)
            if not permitted or (runtime/'INSTALLING').exists():
                outcome='interrupted';result={'reason':'control_before_spawn'};return 0
            process=spawn(command,cwd=project['path'],env=env,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT)
            while process.poll() is None:
                permitted,_,timing=control(runtime,identity,M.registry(),M.now(),M.window)
                if not permitted or clock()>=min(job['deadline'],timing['cutoff'].timestamp()):
                    stop(process);outcome='interrupted';result={'reason':'control_or_deadline'};break
                time.sleep(.5)
            else:
                code=process.wait(timeout=5);outcome='succeeded' if code==0 else 'failed';result={'returncode':code}
        return 0 if outcome in {'succeeded','interrupted'} else 1
    except BaseException as exc:
        result={'reason':type(exc).__name__,'detail':str(exc)[:300]}
        raise
    finally:
        if process:stop(process)
        if drain(runtime,identity):store.finish(identity,outcome,result,lease=lease)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',required=True);parser.add_argument('--action',required=True)
    args=parser.parse_args();signal.signal(signal.SIGTERM,stopping);signal.signal(signal.SIGINT,stopping)
    return execute(Path(args.runtime),args.action)


if __name__=='__main__':sys.exit(main())
