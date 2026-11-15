"""Bounded Ollama coding-tool qualification; authoritative checks stay in host.

Reuses existing Ollama/local-code model settings and GPU serialization. The
qualifier has three small file/test tools, not the production Codex shell loop.
"""
import datetime as dt
import ast
import http.client
import verification_sandbox as VS
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import process_observer as PO
import threading
import time
import urllib.request
import uuid
import durability as D

LOCAL_CODE=str(Path.home()/'.local/bin/local-code')
GPU_LOCK=Path.home()/'.codex/artifacts/local-coding-agent-setup-20261029/gpu-evaluation.lock'
CATALOG=Path.home()/'.local/share/local-code-client/catalogs/coder-context32.json'


def api(path,body=None,timeout=20):
    req=urllib.request.Request('http://127.0.0.1:11434'+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read(4*1024*1024))


def sample():
    raw=subprocess.check_output(['/usr/sbin/sysctl','vm.swapusage','kern.memorystatus_vm_pressure_level'],text=True,timeout=5)
    return {'swap_mib':float(re.search(r'used = ([\d.]+)M',raw)[1]),'pressure':int(re.search(r'pressure_level: (\d+)',raw)[1]),
        'model_bytes':sum(m.get('size',0) for m in api('/api/ps',timeout=5).get('models',[]))}


def catalog(directory,model):
    value=json.loads(CATALOG.read_text());value['models'][0].update(slug=model,display_name=model)
    path=directory/'catalog.json';D.atomic_write(path,json.dumps(value).encode());return path


def worker_command(model,catalog_path,args):
    return [LOCAL_CODE,'-m',model,'-c','model_catalog_json='+json.dumps(str(catalog_path)),*args]


def stamp(pid):
    return PO.run(['/bin/ps','-p',str(pid),'-o','uid=,lstart=,comm='],capture_output=True,text=True).stdout.strip()


def terminate(proc):
    # Group cleanup still matters after a direct child exits.
    for sig in (signal.SIGTERM,signal.SIGKILL):
        try:os.killpg(proc.pid,sig)
        except ProcessLookupError:pass
        if sig==signal.SIGTERM:
            try:proc.wait(timeout=.5)
            except subprocess.TimeoutExpired:pass
    proc.wait(timeout=5)


def cleanup(repo,runs_root):
    """Only disposable evaluation repositories; retained for leased recovery."""
    repo=repo.resolve();runs_root=runs_root.resolve()
    if not repo.is_relative_to(runs_root) or repo==runs_root or repo.name!='work':raise ValueError('Not an evaluation repository')
    raw=subprocess.run(['/usr/sbin/lsof','-nP','-u',str(os.getuid()),'-a','-d','cwd','-Fpn'],capture_output=True,text=True,timeout=10).stdout
    found=[];pid=None
    for line in raw.splitlines():
        if line.startswith('p'):pid=int(line[1:])
        elif line.startswith('n') and pid not in (None,os.getpid(),os.getppid()) and Path(line[1:]).is_relative_to(repo):
            identity=stamp(pid)
            if identity:found.append((pid,identity))
    for sig in (signal.SIGTERM,signal.SIGKILL):
        for pid,identity in found:
            if stamp(pid)==identity:
                try:os.kill(pid,sig)
                except ProcessLookupError:pass
        if found:time.sleep(.1)
    return [p for p,_ in found]


def recover_probe(runtime):
    path=runtime/'model-probe.json';lease=D.read_json(path,{})
    if not lease:return
    repo=Path(lease['repo'])
    if not repo.resolve().is_relative_to((runtime/'runs').resolve()) or repo.name!='work':raise ValueError('Invalid model probe lease')
    if lease['boot_id']==D.boot_id():
        if lease.get('stamp') and stamp(lease['pid'])==lease['stamp']:
            for sig in (signal.SIGTERM,signal.SIGKILL):
                if stamp(lease['pid'])==lease['stamp']:
                    try:os.killpg(lease['pid'],sig)
                    except ProcessLookupError:pass
                time.sleep(.1)
        cleanup(repo,runtime/'runs')
    D.save_json(path,{})


def profile(directory,repo,runner):
    path=directory/(runner.stem+'.sb')
    path.write_text('(version 1)\n(allow default)\n(deny network*)\n(deny process-fork)\n(deny signal)\n(deny file-write*)\n'
        +'(deny file-read* (subpath '+json.dumps(str(Path.home()))+'))\n'
        +'(allow file-read* (subpath '+json.dumps(str(repo))+') (literal '+json.dumps(str(runner))+'))\n'+'\n'.join(VS.ISOLATION)+'\n')
    return path


LIMITS="import resource\nresource.setrlimit(resource.RLIMIT_CPU,(10,10))\n"

class Confined:
    """No fork/network/private reads; bounded pipes, RSS and lifetime throughout."""
    def __init__(self,command,repo,sandbox,lease,seconds=20,gpu_fd=None,guard=None):
        self.proc=subprocess.Popen(['/usr/bin/sandbox-exec','-f',str(sandbox),*command],cwd=repo,
            env={'PATH':'/opt/homebrew/bin:/usr/bin:/bin','PYTHONPATH':str(repo),'PYTHONDONTWRITEBYTECODE':'1'},
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,
            pass_fds=(gpu_fd,) if gpu_fd is not None else ())
        self.lease=lease;self.deadline=time.monotonic()+seconds;self.size=0;self.buffers={'stdout':b'','stderr':b''};self.reason='exited'
        self.selector=selectors.DefaultSelector()
        self.guard=guard
        for name in self.buffers:
            pipe=getattr(self.proc,name);os.set_blocking(pipe.fileno(),False);self.selector.register(pipe,selectors.EVENT_READ,name)
        if lease:D.save_json(lease,{'pid':self.proc.pid,'stamp':stamp(self.proc.pid),'boot_id':D.boot_id(),'repo':str(repo)})
    def poll(self):
        if self.guard:self.guard()
        if time.monotonic()>=self.deadline:raise TimeoutError('host_deadline')
        raw=PO.run(['/bin/ps','-p',str(self.proc.pid),'-o','rss='],capture_output=True,text=True,timeout=2).stdout.strip()
        if raw and int(raw)*1024>536870912:raise MemoryError('host_memory_bound')
        for key,_ in self.selector.select(.05):
            data=os.read(key.fileobj.fileno(),8192)
            if not data:self.selector.unregister(key.fileobj);continue
            self.size+=len(data)
            if self.size>65536:raise ValueError('host_output_bound')
            self.buffers[key.data]+=data
    def call(self,request):
        identity=uuid.uuid4().hex
        self.proc.stdin.write((json.dumps({'id':identity,**request})+'\n').encode());self.proc.stdin.flush()
        while b'\n' not in self.buffers['stdout']:
            self.poll()
            if self.proc.poll() is not None and not self.selector.get_map():raise ValueError('host_rpc_ended_early')
        line,self.buffers['stdout']=self.buffers['stdout'].split(b'\n',1)
        value=json.loads(line)
        if not isinstance(value,dict) or value.get('id')!=identity or set(value)-{'id','result','exception'}:raise ValueError('invalid_host_rpc')
        return value
    def finish(self):
        self.proc.stdin.close()
        while self.proc.poll() is None or self.selector.get_map():self.poll()
        return {'code':self.proc.returncode,'stdout':self.buffers['stdout'].decode(errors='replace'),
            'stderr':self.buffers['stderr'].decode(errors='replace'),'reason':self.reason}
    def close(self):
        terminate(self.proc);self.selector.close()
        for pipe in (self.proc.stdin,self.proc.stdout,self.proc.stderr):
            if not pipe.closed:pipe.close()
        if self.lease:D.save_json(self.lease,{})


def host_check(command,repo,sandbox,seconds=20,lease_path=None,gpu_fd=None,guard=None):
    worker=Confined(command,repo,sandbox,lease_path,seconds,gpu_fd,guard)
    try:return worker.finish()
    except (TimeoutError,MemoryError,ValueError) as exc:
        return {'code':-1,'stdout':'','stderr':str(exc),'reason':str(exc)}
    finally:worker.close()


def constrained_source(path):
    """These pure exercises need no I/O, introspection or general imports."""
    source=path.read_text();tree=ast.parse(source)
    forbidden={'open','eval','exec','compile','globals','locals','vars','getattr','setattr','delattr','dir','input','print','exit','quit','SystemExit','__import__','__builtins__'}
    attributes={'append','extend','add','update','discard','remove','clear','count','index','items','keys','values','get','pop','setdefault','copy','lower','casefold','strip','lstrip','rstrip','isspace','startswith','endswith','deepcopy','isfinite','isnan','isinf','clock'}
    # Ordinary public data fields are an implementation choice, not the grade.
    for node in ast.walk(tree):
        if isinstance(node,ast.Attribute) and isinstance(node.ctx,ast.Store) and isinstance(node.value,ast.Name) and node.value.id=='self' and not node.attr.startswith('_'):
            attributes.add(node.attr)
    forbidden_attributes={'gi_frame','cr_frame','ag_frame','f_globals','f_locals','f_back','tb_frame','tb_next'}
    for node in ast.walk(tree):
        if isinstance(node,(ast.Import,ast.ImportFrom)):
            names=[x.name for x in node.names] if isinstance(node,ast.Import) else [node.module]
            if any(x not in ('copy','math') for x in names):raise ValueError('qualification_import_subset')
        if isinstance(node,ast.Attribute) and (node.attr.startswith('_') or node.attr in forbidden_attributes or node.attr not in attributes):raise ValueError('qualification_attribute_subset')
        if isinstance(node,ast.Name) and (node.id in forbidden or node.id.startswith('__')):raise ValueError('qualification_introspection_forbidden')
        if isinstance(node,(ast.FunctionDef,ast.ClassDef)) and node.name.startswith('__') and node.name!='__init__':raise ValueError('qualification_special_method_forbidden')
    return source

RPC=LIMITS+'''import copy,json,sys,math,types
safe_builtins={name:getattr(__builtins__,name) for name in ('__build_class__','Exception','ValueError','TypeError','bool','int','float','str','dict','list','tuple','set','len','isinstance','type','any','all','enumerate','range','zip','sorted','min','max','abs')}
modules={'copy':types.SimpleNamespace(deepcopy=copy.deepcopy),'math':types.SimpleNamespace(isfinite=math.isfinite,isnan=math.isnan,isinf=math.isinf,inf=math.inf,nan=math.nan)}
safe_builtins['__import__']=lambda name,*args:modules[name]
namespace={'__builtins__':safe_builtins,'__name__':'qualified_solution'}
exec(SOURCE,namespace)
solution=types.SimpleNamespace(**{k:v for k,v in namespace.items() if not k.startswith('__')})
clock=[10]
cache=None
for line in sys.stdin:
 req=json.loads(line)
 try:
  op=req['op']
  if op=='validate':
   value=req['value'];result=solution.validate(value)
   if req.get('mutate'):result['tags'].append('changed')
   result={'returned':result,'original':value}
  elif op=='redact':
   value=req['value'];result=solution.redact(value)
   if req.get('mutate'):result['nested'].append('changed')
   result={'returned':result,'original':value}
  elif op=='cache_init':cache=solution.Cache(lambda:clock[0]);result=True
  elif op=='time':clock[0]=req['value'];result=True
  elif op=='put':result=cache.put(req['key'],req['value'],req['ttl'])
  elif op=='get':result=cache.get(req['key'],req.get('default'))
  else:raise ValueError('unknown operation')
  response={'id':req['id'],'result':result}
 except Exception as exc:response={'id':req['id'],'exception':type(exc).__name__}
 print(json.dumps(response,allow_nan=True),flush=True)
'''


def oracle(directory,repo,case,lease,seconds=20,gpu_fd=None,guard=None):
    """All expected values/assertions execute here, outside generated Python."""
    source=constrained_source(repo/'solution.py')
    runner=directory/'rpc_runner.py';runner.write_text('SOURCE='+repr(source)+'\n'+RPC);sandbox=profile(directory,repo,runner)
    remote=Confined(['/opt/homebrew/bin/python3','-B',str(runner)],repo,sandbox,lease,seconds,gpu_fd,guard)
    calls=[]
    def call(**request):
        value=remote.call(request);calls.append({'request':request,'response':value});return value
    def result(**request):
        value=call(**request)
        assert 'exception' not in value,value
        return value['result']
    def invalid(**request):assert call(**request).get('exception')=='ValueError'
    try:
        if case=='strict-record':
            x={'version':1,'name':'demo','tags':['a']}
            got=result(op='validate',value=x,mutate=True);assert got['original']==x and got['returned']['tags']==['a','changed']
            for x in [None,[],{}, {'version':True,'name':'x','tags':[]},{'version':1.0,'name':'x','tags':[]},{'version':1,'name':' x','tags':[]},{'version':1,'name':'x','tags':['a','a']},{'version':1,'name':'x','tags':['']},{'version':1,'name':'x','tags':[],'extra':0}]:invalid(op='validate',value=x)
            assert result(op='validate',value={'version':1,'name':'x','tags':[]})['returned']['tags']==[]
        elif case=='expiry-boundary':
            result(op='cache_init');result(op='put',key='x',value='old',ttl=5)
            for bad in [True,False,-1,float('inf'),float('nan'),'1',None]:
                invalid(op='put',key='x',value='bad',ttl=bad);assert result(op='get',key='x')=='old'
            result(op='time',value=15);assert result(op='get',key='x',default='miss')=='miss'
            result(op='time',value=10);assert result(op='get',key='x',default='miss')=='miss'
            result(op='time',value=15)
            result(op='put',key='x',value='zero',ttl=0);assert result(op='get',key='x',default='miss')=='miss'
            result(op='put',key='n',value=None,ttl=1);assert result(op='get',key='n',default='miss') is None
            result(op='time',value=16);assert result(op='get',key='n',default='miss')=='miss'
        elif case=='redact-nested':
            x={'TOKEN':'a','nested':[{'Password':'b','token_count':2},None,False],'safe':{'authorization':{'x':'secret'}}}
            got=result(op='redact',value=x);assert got['original']==x and got['returned']=={'TOKEN':'[REDACTED]','nested':[{'Password':'[REDACTED]','token_count':2},None,False],'safe':{'authorization':'[REDACTED]'}}
            assert len(result(op='redact',value=x,mutate=True)['original']['nested'])==3
            assert result(op='redact',value=None)['returned'] is None
            assert result(op='redact',value=0)['returned']==0
        else:raise ValueError('Unknown host oracle')
        return True
    finally:
        remote.close();D.atomic_write(directory/'oracle-transcript.json',json.dumps(calls,indent=2,allow_nan=True).encode())


def unit_tests(directory,repo,lease,seconds=20,gpu_fd=None,guard=None):
    runner=directory/'unit_runner.py'
    runner.write_text(LIMITS+"import unittest\nr=unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('.'))\nraise SystemExit(0 if r.wasSuccessful() and r.testsRun else 1)\n")
    result=host_check(['/opt/homebrew/bin/python3','-B',str(runner)],repo,profile(directory,repo,runner),seconds=seconds,lease_path=lease,gpu_fd=gpu_fd,guard=guard)
    (directory/'unit-check.log').write_text(result['stdout']+result['stderr']);return result


TOOLS=[{'type':'function','function':{'name':name,'description':description,'parameters':parameters}} for name,description,parameters in [
 ('read_file','Read solution.py or test_solution.py',{'type':'object','properties':{'path':{'type':'string'}},'required':['path']}),
 ('write_file','Replace solution.py or test_solution.py with supplied complete content',{'type':'object','properties':{'path':{'type':'string'},'content':{'type':'string'}},'required':['path','content']}),
 ('run_tests','Run the focused unit tests; hidden host assertions are separate',{'type':'object','properties':{}})]]


def chat(model,messages,deadline,guard):
    """Only a bounded HTTP request thread, no detached coding-agent processes."""
    connection=http.client.HTTPConnection('127.0.0.1',11434,timeout=max(1,deadline-time.monotonic()))
    result={};finished=threading.Event()
    def request():
        try:
            connection.request('POST','/api/chat',json.dumps({'model':model,'messages':messages,'tools':TOOLS,'stream':False}),{'Content-Type':'application/json'})
            result['socket']=connection.sock
            response=connection.getresponse();raw=response.read(2*1024*1024+1)
            if response.status!=200 or len(raw)>2*1024*1024:raise ValueError('local_chat_response_rejected')
            result['value']=json.loads(raw)
        except Exception as exc:result['error']=exc
        finally:finished.set()
    thread=threading.Thread(target=request,daemon=True);thread.start()
    try:
        while not finished.wait(.5):
            if time.monotonic()>=deadline:raise TimeoutError('deadline')
            guard()
        if 'error' in result:raise result['error']
        return result['value']
    finally:
        transport=result.get('socket') or connection.sock
        if transport:
            try:transport.shutdown(2)
            except OSError:pass
        connection.close();thread.join(timeout=3)
        if thread.is_alive():
            # The outer subprocess lifecycle closes all descriptors on exit.
            # Preserve cancellation instead of converting it into a model failure.
            import sys
            if sys.exc_info()[0] is None:raise RuntimeError('local_request_did_not_cancel')


def probe(directory,descriptor,case,seconds,lock_fd,ram_bytes,*,lease_path,cancel=None):
    directory.mkdir(parents=True,exist_ok=True);repo=directory/'work';repo.mkdir();(repo/'solution.py').write_text(case['source'])
    start=time.monotonic();deadline=start+seconds;before=sample();samples=[];reason=None;tool_calls=0;events=[];critical=None
    def guard():
        nonlocal critical
        if cancel:cancel()
        if time.monotonic()>=deadline:raise TimeoutError('deadline')
        m=sample();samples.append(m)
        with (directory/'memory.jsonl').open('a') as f:f.write(json.dumps(m)+'\n')
        critical=critical or time.monotonic() if m['pressure']>=4 else None
        if critical and time.monotonic()-critical>=20:raise MemoryError('critical_memory_pressure')
        if m['swap_mib']-before['swap_mib']>4096:raise MemoryError('swap_growth')
        if m['model_bytes']>ram_bytes*.80:raise MemoryError('runtime_memory_fit')
    messages=[{'role':'system','content':'You are a bounded local coding worker. Use the supplied file and unit-test tools to complete one task. You may edit only solution.py and test_solution.py. Solution code is pure Python: only copy/math imports, no I/O, printing, dynamic execution, introspection or private attributes. Tests can import unittest. Do not output simulated tool-call text. Finish with a short factual summary after tests. No network, packages, shell or other files.'},{'role':'user','content':case['task']}]
    try:
        while time.monotonic()<deadline:
            guard()
            if len(json.dumps(messages))>96000:raise ValueError('context_bound')
            response=chat(descriptor['model'],messages,deadline,guard);message=response['message'];messages.append(message)
            calls=message.get('tool_calls',[])
            if not calls:break
            for call in calls:
                guard()
                tool_calls+=1;function=call['function'];name=function['name'];args=function['arguments']
                if isinstance(args,str):args=json.loads(args)
                if not isinstance(args,dict):raise ValueError('invalid_tool_arguments')
                if name in ('read_file','write_file'):
                    filename=args.get('path')
                    if filename not in ('solution.py','test_solution.py'):raise ValueError('out_of_scope_tool_path')
                    path=repo/filename
                    if name=='read_file':output=path.read_text() if path.exists() else 'File does not exist.'
                    else:
                        content=args.get('content')
                        if not isinstance(content,str) or len(content.encode())>32768:raise ValueError('file_size_bound')
                        path.write_text(content);output='File saved.'
                elif name=='run_tests':output=json.dumps(unit_tests(directory,repo,lease_path,min(20,max(.1,deadline-time.monotonic())),lock_fd,guard))[-4000:]
                else:raise ValueError('unknown_tool')
                messages.append({'role':'tool','tool_name':name,'content':output})
                events.append({'tool':name,'at':dt.datetime.now(dt.timezone.utc).isoformat()})
            if time.monotonic()>=deadline:raise TimeoutError('deadline')
        else:raise TimeoutError('deadline')
    except InterruptedError:raise
    except (OSError,ValueError,KeyError,TypeError,TimeoutError,MemoryError,RuntimeError) as exc:reason=str(exc)[:200]
    passed=False;error=None
    try:
        if reason is None and (repo/'test_solution.py').is_file():
            guard()
            unit=unit_tests(directory,repo,lease_path,min(20,max(.1,deadline-time.monotonic())),lock_fd,guard)
            guard()
            passed=unit['code']==0 and unit['reason']=='exited' and oracle(directory,repo,case['id'],lease_path,min(20,max(.1,deadline-time.monotonic())),lock_fd,guard)
    except InterruptedError:raise
    except (OSError,ValueError,AssertionError,TimeoutError,MemoryError,SyntaxError,KeyError,TypeError) as exc:error=type(exc).__name__+': '+str(exc)[:200]
    result={'case':case['id'],'model':descriptor,'seconds':round(time.monotonic()-start,2),'passed':bool(passed),
        'stop_reason':reason,'host_error':error,'tool_calls':tool_calls,'peak_model_bytes':max([s['model_bytes'] for s in samples] or [0]),
        'peak_pressure':max([s['pressure'] for s in samples] or [0]),'swap_growth_mib':max([s['swap_mib']-before['swap_mib'] for s in samples] or [0]),
        'at':dt.datetime.now(dt.timezone.utc).isoformat(),'runner':'bounded Ollama file/test tools; production uses Ralphy/local-code'}
    D.atomic_write(directory/'tool-events.json',json.dumps(events,indent=2).encode())
    D.atomic_write(directory/'result.json',(json.dumps(result,indent=2)+'\n').encode());return result
