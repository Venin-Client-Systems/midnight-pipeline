"""Unprivileged process metadata for confined macOS verification.

Uses the public libproc ABI from the installed Apple SDK. No command arguments
or process memory is read. Normal operations retain the system ps behaviour.
"""
import ctypes as C
import os
from pathlib import Path
import subprocess
import time


class BSD(C.Structure):
    _fields_=[(n,C.c_uint32) for n in ('flags','status','xstatus','pid','ppid','uid','gid','ruid','rgid','svuid','svgid','reserved')]+[
        ('comm',C.c_char*16),('name',C.c_char*32)]+[(n,C.c_uint32) for n in ('nfiles','pgid','jobc','tty','tpgid')]+[
        ('nice',C.c_int32),('started',C.c_uint64),('usec',C.c_uint64)]


def info(pid):
    lib=C.CDLL('/usr/lib/libproc.dylib');value=BSD()
    if lib.proc_pidinfo(int(pid),3,0,C.byref(value),C.sizeof(value))!=C.sizeof(value):return None
    path=C.create_string_buffer(4096);lib.proc_pidpath(int(pid),path,len(path))
    task=(C.c_uint64*16)();size=lib.proc_pidinfo(int(pid),4,0,C.byref(task),C.sizeof(task))
    return {'pid':int(pid),'parent':value.ppid,'uid':value.uid,'pgid':value.pgid,'state':'Z' if value.status==5 else 'S',
        'stamp':time.strftime('%a %b %e %H:%M:%S %Y',time.localtime(value.started)),
        'birth_us':value.started*1000000+value.usec,
        'name':path.value.decode(errors='replace') or bytes(value.comm).decode(errors='replace'),
        'rss':int(task[1]//1024) if size else 0}


def processes():
    lib=C.CDLL('/usr/lib/libproc.dylib');pids=(C.c_int*65536)()
    length=lib.proc_listpids(1,0,C.byref(pids),C.sizeof(pids))
    return [v for pid in list(pids)[:max(0,length)//C.sizeof(C.c_int)] if pid and (v:=info(pid)) and v['uid']==os.getuid()]


def run(command,**kwargs):
    if os.environ.get('MIDNIGHT_CONFINED_TESTS')!='1':return subprocess.run(command,**kwargs)
    args=list(command)[1:]
    rows=[info(args[args.index('-p')+1])] if '-p' in args else processes()
    option='-o' if '-o' in args else '-axo' if '-axo' in args else None
    fields=args[args.index(option)+1].replace('=','').split(',') if option else ['pid']
    mapping={'ppid':'parent','lstart':'stamp','stat':'state','comm':'name'};lines=[]
    for row in rows:
        if row:lines.append(' '.join(str(row[mapping.get(f,f)]) for f in fields))
    raw='\n'.join(lines)+('\n' if lines else '')
    return subprocess.CompletedProcess(command,0,raw if kwargs.get('text') else raw.encode(),'' if kwargs.get('text') else b'')


def check_output(command,**kwargs):
    if os.environ.get('MIDNIGHT_CONFINED_TESTS')!='1':return subprocess.check_output(command,**kwargs)
    return run(command,**kwargs).stdout
