"""Register an execution group before launching a command; retain its slot.

The backend hands this guard a private immutable launch description and an
inherited flock descriptor. It survives backend loss long enough to drain the
execution group. Neither a printed heartbeat nor output volume extends a cap.
"""
import sys
from pathlib import Path
# Every host entry uses python -I. Add only the trusted coordinator/bundle,
# preserving the intended command environment without importing from it.
sys.path.insert(0,str(Path(__file__).resolve().parent))
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import durability as D
from action_store import Actions
import phase_runtime as P

STOP=False


def stopping(_signal,_frame):
    global STOP
    STOP=True


def execute(runtime,identity,launch,slot,memory_slot=None,gpu_slot=None):
    store=Actions(runtime)
    item=next((p for p in store.phases() if p['id']==identity),None)
    if not item or item['state']!='running' or not P.alive(item['lease']):return 75
    job=store.get(item['job'])
    if not job or job['state']!='running' or not P.alive(job['lease']):return 75
    parent=item['lease'];mine=dict(P.lease(),group=True)
    if os.getpgrp()!=os.getpid():raise ValueError('Execution guard must own its session')
    if os.fstat(slot).st_ino!=os.stat(Path(runtime)/'resources'/launch['slot_name']).st_ino:
        raise ValueError('Resource descriptor does not match its declared slot')
    if launch.get('memory_slot') is not None:
        if (memory_slot is None or launch['memory_slot'] != 'heavy-memory.lock'
                or os.fstat(memory_slot).st_ino != os.stat(Path(runtime)/'resources'/'heavy-memory.lock').st_ino):
            raise ValueError('Heavy memory descriptor does not match its declared slot')
    elif memory_slot is not None:
        raise ValueError('Unexpected heavy memory descriptor')
    if launch.get('gpu_lease'):
        import model_bench as B
        actual=os.fstat(gpu_slot) if gpu_slot is not None else None
        expected=os.stat(B.GPU_LOCK)
        if actual is None or (actual.st_dev,actual.st_ino)!=(expected.st_dev,expected.st_ino):
            raise ValueError('GPU descriptor does not match the shared lock')
    elif gpu_slot is not None:raise ValueError('Unexpected GPU descriptor')
    store.phase_update(identity,'running',lease=mine,expected_lease=parent,progress=True)
    store.execution(identity,mine)
    reason='exited'
    def receipt(code):
        D.atomic_write(Path(runtime)/'phases'/identity/'guard-result.json',
            (json.dumps({'phase':identity,'launch_sha256':P.fingerprint(launch),
                         'guard':mine,'code':code,'reason':reason})+'\n').encode())
        return code
    if STOP or not P.alive(parent) or time.time()>=item['deadline'] or (launch.get('fresh_controls') and P.control_stopped(runtime,job)):
        reason='stopped_before_start'
        return receipt(75)
    # Only this fixed guard owns the descriptor. Model/test code cannot unlock
    # a shared slot through an inherited FD; durable group fencing covers a
    # SIGKILL of the guard until its surviving execution group is reconciled.
    child_launch=Path(runtime)/'phases'/identity/'command.json'
    D.atomic_write(child_launch,(json.dumps(launch)+'\n').encode())
    child_env=os.environ.copy()
    child_env.pop('MIDNIGHT_GPU_LEASE_FD',None);child_env.pop('MIDNIGHT_GPU_GUARD_PHASE',None)
    if gpu_slot is not None:
        child_env.update(MIDNIGHT_GPU_LEASE_FD=str(gpu_slot),MIDNIGHT_GPU_GUARD_PHASE=identity,MIDNIGHT_SHARED_ROOT=str(Path(runtime).parent))
    proc=subprocess.Popen([sys.executable,'-I','-B',str(Path(__file__).resolve()),'--runtime',str(runtime),
        '--phase',identity,'--launch',str(child_launch),'--child'],cwd=launch['cwd'],env=child_env,
        pass_fds=(gpu_slot,) if gpu_slot is not None else ())
    known=set()
    try:
        while proc.poll() is None:
            members=P.descendants(mine)
            fresh=[m for m in members if (m['pid'],m['stamp']) not in known]
            if fresh:
                store.execution_members(identity,fresh);known.update((m['pid'],m['stamp']) for m in fresh)
            if STOP or not P.alive(parent) or time.time()>=item['deadline']:
                reason='paused_or_shutdown' if STOP else 'parent_lost' if not P.alive(parent) else 'task_timeout'
                break
            output=launch.get('output_file');cap=launch.get('output_cap')
            if output and type(cap) is int and Path(output).is_file() and Path(output).stat().st_size>cap:
                reason='output_limit'
                break
            time.sleep(.25)
        code=proc.poll()
        # The guard stays alive while descendants drain, including children of
        # a command which exited without waiting for its own children.
        saved=next((e['lease'] for e in store.executions() if e['phase']==identity),mine)
        others=[p for p in P.group_members(mine)+saved.get('members',[]) if p['pid']!=os.getpid()]
        for value in others:
            if P.alive(value):
                try:os.kill(value['pid'],signal.SIGTERM)
                except ProcessLookupError:pass
        end=time.monotonic()+5
        while time.monotonic()<end and any(P.alive(p) for p in others):
            proc.poll();time.sleep(.1)
        for value in P.group_members(mine)+saved.get('members',[]):
            if value['pid']!=os.getpid() and P.alive(value):
                try:os.kill(value['pid'],signal.SIGKILL)
                except ProcessLookupError:pass
        proc.wait(timeout=3)
        return receipt(code if code is not None else 75)
    finally:
        if proc.poll() is None:proc.kill();proc.wait(timeout=3)


def command_child(runtime,identity,launch):
    """No actual command executes before this child's durable registration."""
    store=Actions(runtime)
    execution=next((e for e in store.executions() if e['phase']==identity),None)
    if not execution or not P.alive(execution['lease']) or os.getppid()!=execution['lease']['pid']:return 75
    mine=P.lease();store.execution_members(identity,[mine])
    phase=next((p for p in store.phases() if p['id']==identity),None)
    job=store.get(execution['job'])
    if (STOP or not phase or phase['state']!='running' or not job or job['state']!='running'
            or not P.alive(job['lease']) or not P.alive(execution['lease'])
            or time.time()>=phase['deadline'] or (Path(runtime)/'PAUSED').exists()
            or (Path(runtime)/'projects'/job['project']/'PAUSED').exists()
            or (launch.get('fresh_controls') and P.control_stopped(runtime,job))):return 75
    if launch.get('gpu_lease'):
        os.set_inheritable(int(os.environ['MIDNIGHT_GPU_LEASE_FD']),True)
    os.chdir(launch['cwd'])
    os.execvpe(launch['command'][0],launch['command'],os.environ)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',required=True);parser.add_argument('--phase',required=True)
    parser.add_argument('--launch',required=True);parser.add_argument('--slot',type=int);parser.add_argument('--child',action='store_true')
    parser.add_argument('--memory-slot',type=int);parser.add_argument('--gpu-slot',type=int)
    args=parser.parse_args();signal.signal(signal.SIGTERM,stopping);signal.signal(signal.SIGINT,stopping)
    launch=json.loads(Path(args.launch).read_text())
    if args.child:return command_child(Path(args.runtime),args.phase,launch)
    if args.slot is None:parser.error('Parent guard requires its resource slot')
    return execute(Path(args.runtime),args.phase,launch,args.slot,args.memory_slot,args.gpu_slot)


if __name__=='__main__':sys.exit(main())
