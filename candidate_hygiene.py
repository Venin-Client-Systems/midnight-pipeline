"""Preserve verified redundant editing artifacts outside disposable candidates.

This normalizes no product bytes and never grants scope to unique content.
The backend supplies host-established input/current hashes and untracked paths.
"""
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import durability as D
import json

SUFFIXES=('.bak','.backup','.orig','.fixed')
MAX_BYTES=128000

def safe_file(root,name):
    if not isinstance(name,str):raise ValueError('Unsafe artifact path')
    path=PurePosixPath(name)
    if not isinstance(name,str) or path.is_absolute() or '..' in path.parts or str(path)!=name:
        raise ValueError('Unsafe artifact path')
    item=root
    for part in path.parts:
        item=item/part
        if item.is_symlink():raise ValueError('Symlink artifact path')
    info=item.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>MAX_BYTES:
        raise ValueError('Unsupported artifact file')
    return item,info

def read_bytes(root,name):
    item,info=safe_file(root,name)
    fd=os.open(item,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        actual=os.fstat(fd)
        if (actual.st_dev,actual.st_ino)!=(info.st_dev,info.st_ino):raise ValueError('Artifact changed during inspection')
        data=b''
        while len(data)<=MAX_BYTES:
            chunk=os.read(fd,MAX_BYTES+1-len(data))
            if not chunk:break
            data+=chunk
        if len(data)>MAX_BYTES:raise ValueError('Artifact exceeds bound')
        after=os.fstat(fd)
        if len(data)!=info.st_size or (after.st_size,after.st_mtime_ns,after.st_ctime_ns)!=(info.st_size,info.st_mtime_ns,info.st_ctime_ns):
            raise ValueError('Artifact changed during read')
        return data,info
    finally:os.close(fd)

def quarantine_artifacts(work,directory,untracked,references):
    """Only exact known copies with a recognized suffix may leave the clone."""
    work=Path(work);directory=Path(directory)
    if work.is_symlink() or directory.is_symlink() or work.resolve().parent!=directory.resolve():
        raise ValueError('Artifact quarantine requires its own disposable run')
    journal=directory/'candidate-hygiene.json'
    try:record=json.loads(journal.read_text())
    except FileNotFoundError:record={'version':1,'entries':[]}
    if not isinstance(record,dict) or record.get('version')!=1 or not isinstance(record.get('entries'),list):
        raise ValueError('Invalid artifact journal')
    def save():D.atomic_write(journal,(json.dumps(record,indent=2)+'\n').encode())
    # Reconcile a crash after preserving bytes but before removing the duplicate.
    removed=[]
    def finish(entry):
        name=entry['path'];target='quarantine/'+name
        if entry.get('artifact')!=target:raise ValueError('Invalid quarantine destination')
        preserved,_=read_bytes(directory,target)
        if hashlib.sha256(preserved).hexdigest()!=entry['sha256']:raise ValueError('Preserved artifact changed')
        source=work/name
        if source.exists() or source.is_symlink():
            raw,info=read_bytes(work,name)
            if hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('Duplicate changed after preservation')
            if source.lstat().st_ino!=info.st_ino:raise ValueError('Duplicate identity changed')
            source.unlink();D.sync_directory(source.parent)
        entry['state']='quarantined';removed.append(name);save()
    for entry in record['entries']:
        if entry.get('state')=='preserved':finish(entry)
    known={entry['path'] for entry in record['entries']}
    for name in sorted(set(untracked)):
        if name in known:continue
        suffix=next((x for x in SUFFIXES if name.endswith(x)),None)
        if not suffix:continue
        original=name[:-len(suffix)]
        if original not in references:continue
        try:raw,_=read_bytes(work,name)
        except (OSError,ValueError):continue
        digest=hashlib.sha256(raw).hexdigest()
        match=next((label for label,value in references[original].items() if value==digest),None)
        if match is None:continue
        target='quarantine/'+name
        destination=directory/target
        for parent in destination.parents:
            if parent==directory:break
            if parent.is_symlink():raise ValueError('Unsafe quarantine directory')
        if destination.exists() or destination.is_symlink():
            saved,_=read_bytes(directory,target)
            if saved!=raw:raise ValueError('Quarantine destination already contains different bytes')
        else:D.atomic_write(destination,raw)
        entry={'path':name,'source':original,'artifact':target,'sha256':digest,'matched_reference':match,'state':'preserved'}
        record['entries'].append(entry);save();finish(entry)
    return removed
