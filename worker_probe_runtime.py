"""Fixed check broker for disposable worker qualification on macOS.

Candidate harness code stays in its outer sandbox. The native trusted probe
executes only its own fixed syntax command in the existing no-fork test sandbox.
Requests carry identifiers, never commands, source, paths or test definitions.
This module is not writable by automatic maintenance.
"""
import hashlib,json,os,re,sys,time,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import worker_probe_cases as C

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def confined_worker(source,directory,command):
 from verification_sandbox import confined
 source=Path(source).resolve();directory=Path(directory).resolve()
 args,env=confined(source,directory,command,directory/'ralphy.log',network_ports=[11434])
 profile=directory/'validation.sb';gpu=Path.home()/'.codex/artifacts/local-coding-agent-setup-20261029/gpu-evaluation.lock'
 lines=profile.read_text()+'\n'+f'(allow file-read* file-write* (subpath {json.dumps(str(directory))}) (literal {json.dumps(str(gpu))}))\n'
 lines+='\n'.join(f'(allow file-read-metadata (literal {json.dumps(str(p))}))' for p in gpu.parents)+'\n'
 # Native Git and host checks must never consume candidate-controlled config,
 # symlink ancestors, test definitions or broker results after confinement.
 protected=[directory/'work',directory/'work/.git',directory/'worker-tools',directory/'probe-broker',directory/'worker-manifest.json',profile]
 protected.extend(directory/'work'/n for n in ('test_solution.py','AGENTS.md','PRD.md','contracts.md','fixtures.json','reference.py'))
 lines+='\n'.join(f'(deny file-write* (literal {json.dumps(str(p))}))' for p in protected)+'\n'
 lines+=f'(deny file-write* (subpath {json.dumps(str(directory/"work/.git"))}))\n'
 pattern=re.escape(str(directory))+r'/worker-tools/test-[0-9]+(/|$)'
 responses=re.escape(str(directory/'probe-broker'))+r'/[a-f0-9]+\.response$'
 lines+=f'(deny file-write* (regex {json.dumps(pattern)}) (regex {json.dumps(responses)}))\n'
 lines+='(deny process-fork)\n'
 profile.write_text(lines)
 env['HOME']=str(Path.home())
 # sandbox-exec may reset TMPDIR; set the private temporary path inside it.
 return args[:3]+['/usr/bin/env','TMPDIR='+str(directory/'test-tmp')]+args[3:],env

def request(work,directory,manifest,deadline):
 from durability import atomic_write
 broker=Path(os.environ['MIDNIGHT_WORKER_PROBE_BROKER']).resolve();work=Path(work).resolve();directory=Path(directory).resolve()
 case=work.parent.name
 if (case not in C.CASE_NAMES or broker!=work.parent/'probe-broker' or directory.parent!=work.parent/'worker-tools'
     or not re.fullmatch('test-[0-9]+',directory.name) or manifest.get('task_id')!='typed-worker-qualification-'+case
     or manifest.get('allowed_files')!=['solution.py']):raise ValueError('Disposable probe broker cannot execute product checks')
 identity=uuid.uuid4().hex
 value={'case':case,'test':directory.name,'manifest_sha256':digest(work.parent/'worker-manifest.json')}
 atomic_write(broker/(identity+'.request'),json.dumps(value).encode())
 response=broker/(identity+'.response');end=min(deadline,time.monotonic()+120)
 while time.monotonic()<end:
  if response.is_file() and not response.is_symlink():
   result=json.loads(response.read_text())
   if result.get('command')!=manifest['test_command'] or result.get('reason')!='exited' or result.get('code')!=0:raise ValueError('Fixed probe syntax check failed')
   return result
  time.sleep(.1)
 raise TimeoutError('Fixed probe check broker did not respond')

class Broker:
 def __init__(self,directory,manifest,runner):
  self.directory=Path(directory).resolve();self.manifest=manifest;self.runner=runner;self.done=set()
  self.root=self.directory/'probe-broker';self.root.mkdir(mode=0o700)
  self.manifest_hash=digest(self.directory/'worker-manifest.json')
 def step(self,deadline):
  from durability import atomic_write
  paths=sorted(self.root.glob('*.request'))
  if len(paths)>32:raise ValueError('Disposable probe exceeded fixed check request bound')
  for path in paths:
   if path.stem in self.done:continue
   if path.is_symlink() or not re.fullmatch('[a-f0-9]{32}',path.stem) or path.stat().st_size>1000:raise ValueError('Unsafe fixed probe request')
   value=json.loads(path.read_text())
   if (set(value)!={'case','test','manifest_sha256'} or value['case']!=self.directory.name
       or value['manifest_sha256']!=self.manifest_hash or digest(self.directory/'worker-manifest.json')!=self.manifest_hash
       or not isinstance(value['test'],str) or not re.fullmatch('test-[0-9]+',value['test'])):raise ValueError('Fixed probe request does not match host manifest')
   target=self.directory/'worker-tools'/value['test']
   work=self.directory/'work'
   paths=[work,self.root,target,target.parent,work/'solution.py',work/'test_solution.py']
   if any(p.is_symlink() for p in paths) or any(p.is_symlink() for p in target.rglob('*')):raise ValueError('Unsafe fixed probe input/result path')
   result=self.runner(self.directory/'work',target,self.manifest,deadline)
   atomic_write(self.root/(path.stem+'.response'),json.dumps(result).encode());self.done.add(path.stem)

def guard(launch):
 """Native group leader remains alive until its supervising probe drains it.

 The sandboxed candidate cannot signal this parent. A short-lived Ralphy leader
 cannot orphan its group before the host takes a birth-bound member snapshot.
 """
 import signal,subprocess
 from durability import atomic_write
 import phase_runtime as P
 path=Path(launch);value=json.loads(path.read_text())
 if os.getpgrp()!=os.getpid():raise ValueError('Probe guard must own its process group')
 mine=dict(P.lease(),group=True);stopping=False
 def stop(*_):
  nonlocal stopping
  stopping=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 atomic_write(path.with_suffix('.guard.json'),json.dumps(mine).encode())
 parent=value['parent'];deadline=value['deadline']
 valid=lambda:not stopping and P.alive(parent) and time.time()<deadline
 if os.getppid()!=parent['pid'] or not valid():return
 proc=None;members=[]
 try:
  proc=subprocess.Popen(value['command'],cwd=value['cwd'],env=value['env'],stdin=subprocess.DEVNULL)
  atomic_write(path.with_suffix('.child.json'),json.dumps(P.lease(proc.pid)).encode())
  exited=False
  while valid():
   members=P.group_members(mine)+P.descendants(mine)
   atomic_write(path.with_suffix('.members.json'),json.dumps(members).encode())
   if proc.poll() is not None and not exited:
    atomic_write(path.with_suffix('.exit.json'),json.dumps({'code':proc.returncode}).encode());exited=True
   time.sleep(.1)
 finally:
  # Keep the birth-bound native group leader alive throughout draining. The
  # candidate sandbox cannot signal it or remove these protected receipts.
  end=time.monotonic()+4
  while True:
   current={p['pid']:p for p in [*members,*P.group_members(mine),*P.descendants(mine)] if p['pid']!=mine['pid'] and P.alive(p)}
   if not current:break
   for p in current.values():
    try:os.kill(p['pid'],signal.SIGTERM if time.monotonic()<end-2 else signal.SIGKILL)
    except ProcessLookupError:pass
   if time.monotonic()>=end:raise RuntimeError('Probe guard could not drain its verified children')
   time.sleep(.05)
  if proc:proc.wait(timeout=2)
  atomic_write(path.with_suffix('.drained.json'),json.dumps({'drained':True}).encode())

if __name__=='__main__':
 import argparse,sys
 sys.path.insert(0,str(Path(__file__).resolve().parent))
 parser=argparse.ArgumentParser();parser.add_argument('--guard',required=True);args=parser.parse_args();guard(args.guard)
