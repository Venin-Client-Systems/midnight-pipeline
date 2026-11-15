"""Task-boundary history migration and evidence-preserving clone retention."""
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import durability as D
from history_store import HistoryStore


def read(path,default=None):
    try:return json.loads(Path(path).read_text())
    except FileNotFoundError:return {} if default is None else default


def save(path,value):D.atomic_write(path,(json.dumps(value,indent=2)+'\n').encode())


def observe_run(root,run_id,state=None):
    import worker_quality as Q
    root=Path(root);folder=root/'runs'/run_id;state=state if state is not None else D.read_json(root/'state.json')
    task=read(folder/'task.json');worker=read(folder/'worker-process.json');model=read(folder/'worker-model.json')
    accepted=read(folder/'accepted.json');final=read(folder/'outcome.json') or read(folder/'history-outcome.json').get('event',{});review=read(folder/'review/result.json');reviewproc=read(folder/'review/process.json')
    pending=(state.get('pending') or {}).get('run')==run_id
    status='accepted' if accepted else 'pending_delivery' if pending and state['pending'].get('phase')=='delivery' else 'awaiting_review' if pending else 'checkpoint' if final.get('outcome')=='checkpoint' else 'interrupted' if final.get('outcome')=='interrupted' else 'needs_evidence' if final.get('accepted') or final.get('outcome')=='accepted' else 'failed' if final else 'in_progress'
    started=dt.datetime.strptime(run_id.split('-')[0],'%Y%m%dT%H%M%SZ').replace(tzinfo=dt.timezone.utc).isoformat()
    retry=read(folder/'retry-progress.json');diag=read(folder/'worker-diagnostic.json')
    backend=read(folder/'implementation-backend.json')
    return {'run':run_id,'task':task.get('title'),'task_id':task.get('id'),'started_at':started,'created_at':started,
        'state':status,'outcome':status,'worker_kind':backend.get('kind',model.get('backend',model.get('kind','local'))),'worker_model':model.get('model'),
        'worker_seconds':worker.get('seconds'),'duration_seconds':worker.get('seconds') or 0,
        'worker_exit':worker.get('code'),'worker_reason':worker.get('reason'),'worker_failure':Q.worker_failure(folder),
        'review_seconds':reviewproc.get('seconds'),'review_decision':review.get('decision'),'commit':accepted.get('commit'),
        'delivery':accepted.get('delivery','historical_local' if accepted else None),
        'unchanged_retry':retry.get('blocked',False),'unfinished_tool_call':diag.get('unfinished_tool_call',False),
        'detail':final.get('summary','')[:500],'usage':None}


def index_run(project,run_id):
    root=Path(project['state_dir']);row=observe_run(root,run_id)
    HistoryStore(root).record_run(project['id'],run_id,row,created_at=row['started_at']);return row


def reconcile_host_events(project, *, max_events=500, max_bytes=2*1024*1024):
    """Replay actual host closures at task boundaries, never from status reads.

    A byte cursor bounds each pass. Persist the original closure beside the run
    before indexing it, so later imports cannot turn a closed legacy run active.
    Replaying after a crash is safe; incomplete appended lines wait for next pass.
    """
    root=Path(project['state_dir']);path=root/'events.jsonl'
    if not path.exists():return
    cursor_path=root/'history-event-cursor.json';cursor=read(cursor_path);info=path.stat()
    identity=[info.st_dev,info.st_ino]
    offset=cursor.get('offset',0) if cursor.get('identity')==identity else 0
    if offset>info.st_size:offset=0
    start=offset;count=0;store=HistoryStore(root);state=D.read_json(root/'state.json')
    with path.open('rb') as handle:
        handle.seek(offset)
        while count<max_events and handle.tell()-start<max_bytes:
            line_start=handle.tell();raw=handle.readline(max_bytes+1)
            if not raw or not raw.endswith(b'\n') or len(raw)>max_bytes:break
            # A single event may be large; do not exceed the pass budget after
            # processing earlier lines. Its offset remains for the next pass.
            if count and handle.tell()-start>max_bytes:break
            try:event=json.loads(raw)
            except ValueError:
                save(root/'history-event-error.json',{'offset':line_start,'reason':'invalid_json'})
                break
            if isinstance(event,dict) and event.get('event')=='attempt_finished':
                run=event.get('run','')
                if isinstance(run,str) and re.fullmatch(r'\d{8}T\d{6}Z-[A-Za-z0-9_-]+',run) and (root/'runs'/run/'task.json').is_file():
                    save(root/'runs'/run/'history-outcome.json',{'event':event,'source_offset':line_start,'sha256':hashlib.sha256(raw).hexdigest()})
                    row=observe_run(root,run,state)
                    store.record_run(project['id'],run,row,row['started_at'])
            offset=handle.tell();count+=1
    save(cursor_path,{'identity':identity,'offset':offset,'observed_size':info.st_size,'caught_up':offset>=info.st_size})


def archive_clone(project,run_id,*,instant=None):
    """Only terminal unpinned work older than30d; reconstruct before deleting clone."""
    root=Path(project['state_dir']);folder=root/'runs'/run_id;work=folder/'work';state=D.read_json(root/'state.json')
    pinned={(state.get(k) or {}).get('run') for k in ('pending','active_attempt')}
    pinned|={v.get('run') for v in state.get('retry_candidates',{}).values()}
    pinned.add(D.read_json(root/'promotion-intent.json').get('run'))
    if run_id in pinned or not work.is_dir() or work.is_symlink():return {'status':'pinned_or_absent'}
    final=read(folder/'outcome.json');accepted=read(folder/'accepted.json')
    if not final and not accepted:return {'status':'not_terminal'}
    started=dt.datetime.strptime(run_id.split('-')[0],'%Y%m%dT%H%M%SZ').replace(tzinfo=dt.timezone.utc)
    if ((instant or dt.datetime.now(dt.timezone.utc))-started).total_seconds()<30*86400:return {'status':'retained'}
    checkpoint=read(folder/'candidate-checkpoint.json');patch=folder/'candidate.patch'
    if not patch.exists() or not checkpoint.get('base'):return {'status':'missing_reconstruction_proof'}
    base=checkpoint['base'];archive=root/'archives'/run_id;archive.mkdir(parents=True,exist_ok=True,mode=0o700)
    baseline=archive/'base.tar.gz'
    if not baseline.exists():
        data=subprocess.check_output(['git','archive','--format=tar.gz',base],cwd=work)
        D.atomic_write(baseline,data)
    with tempfile.TemporaryDirectory(prefix='verify-',dir=archive) as temporary:
        target=Path(temporary)
        with tarfile.open(baseline) as tar:tar.extractall(target,filter='data')
        def run(*args):return subprocess.check_output(['git',*args],cwd=target,stderr=subprocess.PIPE).strip()
        run('init','--quiet');run('add','-A');run('-c','user.name=Midnight','-c','user.email=midnight@localhost','commit','--quiet','-m','Retained reconstruction base')
        run('apply','--index',str(patch));tree=run('write-tree').decode()
        expected=subprocess.check_output(['git','write-tree'],cwd=work,text=True).strip()
        # Verify against the exact recorded patch independent of any dirty work clone.
        with tempfile.NamedTemporaryFile(dir=archive,delete=False) as handle:index=Path(handle.name)
        index.unlink()
        try:
            env=dict(os.environ,GIT_INDEX_FILE=str(index))
            subprocess.run(['git','read-tree',base],cwd=work,env=env,check=True,capture_output=True)
            subprocess.run(['git','apply','--cached',str(patch)],cwd=work,env=env,check=True,capture_output=True)
            expected=subprocess.check_output(['git','write-tree'],cwd=work,env=env,text=True).strip()
        finally:index.unlink(missing_ok=True)
        if tree!=expected:raise ValueError('Archived candidate reconstruction mismatch')
    manifest={'run':run_id,'base':base,'candidate_tree':tree,'files':{}}
    for path in folder.rglob('*'):
        if not path.is_file() or path.is_symlink() or path.is_relative_to(work) or 'delivery-work' in path.parts:continue
        rel=path.relative_to(folder);raw=path.read_bytes();manifest['files'][str(rel)]=hashlib.sha256(raw).hexdigest()
        dest=archive/'evidence'/rel;dest.parent.mkdir(parents=True,exist_ok=True)
        D.atomic_write(dest,raw)
    manifest['base_sha256']=hashlib.sha256(baseline.read_bytes()).hexdigest()
    # Preserve the complete actual working tree, including unique untracked edits.
    # It may differ from the last candidate checkpoint. Never silently discard it.
    working=archive/'working-tree.tar.gz'
    temp=archive/('working-tree.'+os.urandom(8).hex()+'.tmp')
    with tarfile.open(temp,'w:gz',dereference=False) as tar:
        for child in sorted(work.iterdir()):
            if child.name!='.git':tar.add(child,arcname=child.name,recursive=True)
    with temp.open('rb') as f:os.fsync(f.fileno())
    os.chmod(temp,0o600);os.replace(temp,working);D.sync_directory(archive)
    manifest['working_tree_sha256']=hashlib.sha256(working.read_bytes()).hexdigest()
    with tarfile.open(working) as tar:
        for member in tar.getmembers():
            if member.isfile():
                source=work/member.name
                if not source.is_file() or hashlib.sha256(tar.extractfile(member).read()).digest()!=hashlib.sha256(source.read_bytes()).digest():
                    raise ValueError('Working tree archive verification failed')
    save(archive/'manifest.json',manifest)
    # Read every saved evidence object back before reclaiming a working clone.
    for name,digest in manifest['files'].items():
        if hashlib.sha256((archive/'evidence'/name).read_bytes()).hexdigest()!=digest:raise ValueError('Archive validation failed')
    save(folder/'archive.json',dict(manifest,status='verified'))
    shutil.rmtree(work);D.sync_directory(folder)
    for path in folder.glob('*.log'):
        if path.is_symlink():continue
        D.atomic_write(path.with_suffix(path.suffix+'.gz'),gzip.compress(path.read_bytes()))
        path.unlink()
    return {'status':'archived','run':run_id,'candidate_tree':tree}


def maintain(project):
    root=Path(project['state_dir']);cursor_path=root/'history-migration.json';cursor=read(cursor_path)
    state=D.read_json(root/'state.json');store=HistoryStore(root)
    if not cursor.get('complete'):
        # A persisted snapshot of pre-migration run IDs is consumed in bounded pages.
        todo=root/'history-import-queue.json'
        if not todo.exists():save(todo,sorted(p.name for p in (root/'runs').iterdir() if p.is_dir()) if (root/'runs').exists() else [])
        names=read(todo,[]);offset=cursor.get('offset',0)
        with store.transaction() as con:
            for name in names[offset:offset+500]:
                try:row=observe_run(root,name,state)
                except (ValueError,OSError):continue
                store.record_run(project['id'],name,row,row['started_at'],connection=con)
        offset+=500;cursor.update(offset=offset,complete=offset>=len(names));save(cursor_path,cursor)
    reconcile_host_events(project)
    for key in ('active_attempt','pending','last_outcome'):
        run=(state.get(key) or {}).get('run')
        if run and (root/'runs'/run).exists():index_run(project,run)
    free=shutil.disk_usage(root).free
    save(root/'storage-status.json',{'free_bytes':free,'minimum_free_bytes':5*1024**3,'generation_allowed':free>=5*1024**3,'retention_days':30,'history_migration':cursor})
    # Bounded archival pages advance independently; unresolved candidates stay pinned.
    offset=cursor.get('archive_offset',0)
    for row in store.page_runs(project['id'],limit=20,offset=offset):
        try:archive_clone(project,row['run_id'])
        except (ValueError,OSError,subprocess.SubprocessError,tarfile.TarError) as exc:save(root/'archive-error.json',{'run':row['run_id'],'reason':type(exc).__name__})
    cursor['archive_offset']=0 if offset+20>=store.counters(project['id'])['total'] else offset+20
    save(cursor_path,cursor)
