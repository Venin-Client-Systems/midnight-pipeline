"""Verify tested bytes against a private index built from trusted base + patch.

Candidate Git metadata is writable by tests and is not proof authority. This
host-owned bare object store/index lives outside the candidate's sandbox scope.
"""
import hashlib,json,os,stat,subprocess,uuid
from pathlib import Path
import durability as D


def command(args,*,env):
    return subprocess.check_output(['/usr/bin/git',*map(str,args)],env=env,stderr=subprocess.PIPE,timeout=30)


def manifest(canonical,base,patch,directory):
    canonical,directory=Path(canonical),Path(directory)
    digest=hashlib.sha256(patch.encode()).hexdigest();target=directory/'candidate-proof.json'
    saved=D.read_json(target)
    if saved:
        if saved.get('base')!=base or saved.get('patch_sha256')!=digest:raise ValueError('Candidate proof contract changed')
        return saved
    env={'PATH':'/usr/bin:/bin','LANG':'en_AU.UTF-8','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_SYSTEM':'/dev/null'}
    objects=command(['-C',canonical,'rev-parse','--git-path','objects'],env=env).decode().strip()
    objects=Path(objects) if Path(objects).is_absolute() else canonical/objects
    # Interrupted init/apply may leave partial metadata or an index.lock.
    # Retain it as evidence, and never reuse it as proof authority.
    proof=directory/('candidate-proof-'+uuid.uuid4().hex+'.git')
    command(['init','--quiet','--bare',proof],env=env)
    (proof/'objects/info/alternates').write_text(str(objects.resolve())+'\n')
    prefix=['--git-dir='+str(proof)]
    command([*prefix,'read-tree',base],env=env)
    patch_path=directory/'proof-input.patch';D.atomic_write(patch_path,patch.encode())
    command([*prefix,'apply','--cached','--binary',patch_path],env=env)
    rows=command([*prefix,'ls-files','--stage','-z'],env=env).split(b'\0')
    files={}
    for raw in rows:
        if not raw:continue
        fields,name=raw.split(b'\t',1);mode,blob,stage=fields.decode().split()
        if stage!='0' or mode not in {'100644','100755','120000'}:raise ValueError('Unsupported candidate tree entry')
        files[os.fsdecode(name)]={'mode':mode,'blob':blob}
    original=command(['-C',canonical,'ls-tree','-rz','--name-only',base],env=env).split(b'\0')
    result={'base':base,'patch_sha256':digest,'object_format':command(['-C',canonical,'rev-parse','--show-object-format'],env=env).decode().strip(),
        'files':files,'deleted':[os.fsdecode(n) for n in original if n and os.fsdecode(n) not in files]}
    D.save_json(target,result);return result


def preserve_worker_artifacts(canonical,work,base,directory):
    """Retain reports, restore their tracked source baseline before checks."""
    env={'PATH':'/usr/bin:/bin','LANG':'en_AU.UTF-8','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_SYSTEM':'/dev/null'}
    canonical,work,directory=Path(canonical),Path(work),Path(directory)
    names=['progress.txt','PRD.md','.midnight-ready.json']
    rows=command(['-C',canonical,'ls-tree','-z',base,'--',*names],env=env).split(b'\0')
    for raw in rows:
        if not raw:continue
        fields,name=raw.split(b'\t',1);mode,kind,blob=fields.decode().split();name=os.fsdecode(name)
        if mode not in {'100644','100755'} or kind!='blob':raise ValueError('Unsupported tracked worker artifact')
        path=work/name;baseline=command(['-C',canonical,'show',base+':'+name],env=env)
        if path.is_symlink() or (path.exists() and not path.is_file()):raise ValueError('Unsafe worker report path')
        current=path.read_bytes() if path.exists() else None
        if current is not None and current!=baseline:
            evidence=directory/'worker-artifacts'/(hashlib.sha256(current).hexdigest()+'-'+name)
            D.atomic_write(evidence,current)
        D.atomic_write(path,baseline);os.chmod(path,0o755 if mode=='100755' else 0o644)
        command(['-C',work,'update-index','--add','--cacheinfo',mode+','+blob+','+name],env=env)


def verify(canonical,work,base,patch,directory,source_dirs):
    proof=manifest(canonical,base,patch,directory);work=Path(work)
    for name,entry in proof['files'].items():
        path=work/name
        if any(p.is_symlink() for p in path.parents if p!=work and p.is_relative_to(work)):
            raise ValueError('Candidate parent became a symlink: '+name)
        mode=path.lstat().st_mode
        if entry['mode']=='120000':
            if not stat.S_ISLNK(mode):raise ValueError('Candidate link type changed: '+name)
            raw=os.fsencode(os.readlink(path))
        else:
            if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):raise ValueError('Candidate file type changed: '+name)
            if bool(mode&0o111)!=(entry['mode']=='100755'):raise ValueError('Candidate file mode changed: '+name)
            raw=path.read_bytes()
        actual=hashlib.new(proof['object_format'],b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if actual!=entry['blob']:raise ValueError('Candidate bytes differ from checked patch: '+name)
    for name in proof['deleted']:
        if os.path.lexists(work/name):raise ValueError('Deleted candidate path is present: '+name)
    suffixes={'.py','.pyc','.pyo','.ts','.tsx','.js','.jsx','.mjs','.cjs','.so','.dylib','.node'}
    for source in source_dirs:
        start=work/source
        if not start.is_dir():continue
        for folder,dirs,names in os.walk(start,followlinks=False):
            dirs[:]=[d for d in dirs if d not in {'node_modules','.git','__pycache__'} and not d.startswith('.ralphy')]
            for name in names:
                path=Path(folder)/name;relative=str(path.relative_to(work))
                if path.suffix in suffixes and relative not in proof['files']:
                    raise ValueError('Unexpected executable source outside candidate tree: '+relative)
    return proof
