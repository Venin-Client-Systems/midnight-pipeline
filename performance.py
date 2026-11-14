"""Performance summaries from retained worker, verification and review receipts."""
import collections
import datetime as dt
import json
from pathlib import Path
import statistics


def read(path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {} if default is None else default


def events(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue


def legacy_collect(projects, since, instant):
    import worker_quality as Q
    reports=[]
    for project in projects:
        root=Path(project['state_dir'])
        runtime=read(root/'state.json')
        accepted_runs={a['run']:a for a in runtime.get('accepted',[]) if a.get('run')}
        finished={e['run']:e for e in events(root/'events.jsonl') if e.get('event')=='attempt_finished' and e.get('run')}
        rows=[]
        for directory in sorted((root/'runs').glob('*')):
            try:
                started=dt.datetime.strptime(directory.name.split('-')[0],'%Y%m%dT%H%M%SZ').replace(tzinfo=dt.timezone.utc)
            except ValueError:
                continue
            if not since <= started <= instant:
                continue
            worker=read(directory/'worker-process.json')
            accepted=read(directory/'accepted.json') or accepted_runs.get(directory.name,{})
            review=read(directory/'review/result.json')
            review_process=read(directory/'review/process.json')
            checks=read(directory/'checks.json',[])
            final=finished.get(directory.name) or read(directory/'outcome.json')
            retry=read(directory/'retry-progress.json')
            diagnostic=read(directory/'worker-diagnostic.json')
            pending=(runtime.get('pending') or {}).get('run')==directory.name
            state='accepted' if accepted else 'awaiting_review' if pending else 'checkpoint' if final and final.get('outcome')=='checkpoint' else 'interrupted' if final and final.get('outcome')=='interrupted' else 'failed' if final else 'awaiting_review' if worker and checks and all(c.get('code')==0 for c in checks) else 'in_progress'
            usage=[e['usage'] for e in events(directory/'worker-events.jsonl') if isinstance(e.get('usage'),dict)]
            rows.append({'run':directory.name,'task':read(directory/'task.json').get('title'),
                         'started_at':started.isoformat(),'state':state,'worker_seconds':worker.get('seconds'),
                         'worker_exit':worker.get('code'),'worker_reason':worker.get('reason'),'worker_failure':Q.worker_failure(directory),
                         'review_seconds':review_process.get('seconds'),
                         'review_decision':review.get('decision'),'commit':accepted.get('commit'),
                         'unchanged_retry':retry.get('blocked',retry.get('unchanged_candidate',False)),
                         'unfinished_tool_call':diagnostic.get('unfinished_tool_call',False),
                         'detail':(final or {}).get('summary','')[:500],'usage':usage[-1] if usage else None})
        counts=collections.Counter(r['state'] for r in rows)
        durations=[r['worker_seconds'] for r in rows if isinstance(r['worker_seconds'],(int,float))]
        reviews=[r['review_seconds'] for r in rows if isinstance(r['review_seconds'],(int,float))]
        completed=counts['accepted']+counts['failed']
        reports.append({'project':project['id'],'runs':len(rows),'accepted':counts['accepted'],'failed':counts['failed'],
                        'interrupted':counts['interrupted'],'checkpoints':counts['checkpoint'],
                        'unchanged_reviews_avoided':sum(r['unchanged_retry'] for r in rows),
                        'tool_call_fragments':sum(r['unfinished_tool_call'] for r in rows),
                        'in_progress':counts['in_progress'],'awaiting_review':counts['awaiting_review'],
                        'acceptance_rate':round(counts['accepted']/completed,3) if completed else None,
                        'worker_seconds_total':round(sum(durations),2),
                        'worker_seconds_mean':round(statistics.mean(durations),2) if durations else None,
                        'review_seconds_total':round(sum(reviews),2),'details':rows})
    return {'since':since.isoformat(),'checked_at':instant.isoformat(),'projects':reports}


def render_performance(value):
    print('Midnight performance since '+value['since'])
    for project in value['projects']:
        print(f"\n{project['project']}: {project['accepted']} accepted, {project['failed']} failed, "
              f"{project['interrupted']} interrupted, {project['in_progress']} in progress, {project['awaiting_review']} awaiting review")
        rate=project['acceptance_rate']
        print('Acceptance rate: '+(f'{rate:.0%}' if rate is not None else 'no completed attempts yet'))
        print(f"Local worker: {project['worker_seconds_total']} seconds recorded; mean {project['worker_seconds_mean']}")
        print(f"Independent review: {project['review_seconds_total']} seconds recorded")
        for row in project['details'][-5:]:
            print(f"  {row['state']}: {row['task']} ({row['run']})")


def collect(projects,since,instant):
    from history_store import HistoryStore
    reports=[]
    for project in projects:
        store=HistoryStore(Path(project['state_dir']))
        if not store.path.exists():
            reports.extend(legacy_collect([project],since,instant)['projects']);continue
        with store.reader() as con:
            table=con.execute("SELECT 1 FROM sqlite_master WHERE name='qc_runs'").fetchone() if con else None
            if not table:
                reports.extend(legacy_collect([project],since,instant)['projects']);continue
            rows=con.execute("SELECT outcome,worker_kind,COUNT(*) AS count,SUM(duration_seconds) AS seconds,SUM(COALESCE(json_extract(payload,'$.review_seconds'),0)) AS review_seconds,SUM(COALESCE(json_extract(payload,'$.unchanged_retry'),0)) AS unchanged,SUM(COALESCE(json_extract(payload,'$.unfinished_tool_call'),0)) AS fragments FROM qc_runs WHERE project_id=? AND created_at>=? AND created_at<=? GROUP BY outcome,worker_kind",(project['id'],since.timestamp(),instant.timestamp())).fetchall()
            details=[json.loads(r[0]) for r in con.execute("SELECT payload FROM qc_runs WHERE project_id=? AND created_at>=? AND created_at<=? ORDER BY created_at DESC LIMIT 50",(project['id'],since.timestamp(),instant.timestamp()))]
        counts=collections.Counter();durations=reviews=unchanged=fragments=local_count=cloud_count=0
        for row in rows:
            counts[row['outcome']]+=row['count'];reviews+=row['review_seconds'];unchanged+=row['unchanged'];fragments+=row['fragments']
            if row['worker_kind']=='local':durations+=row['seconds'];local_count+=row['count']
            else:cloud_count+=row['count']
        total=sum(counts.values());completed=counts['accepted']+counts['failed']
        reports.append({'project':project['id'],'runs':total,'accepted':counts['accepted'],'failed':counts['failed'],'interrupted':counts['interrupted'],'checkpoints':counts['checkpoint'],
            'in_progress':counts['in_progress'],'awaiting_review':counts['awaiting_review'],'unchanged_reviews_avoided':unchanged,'tool_call_fragments':fragments,
            'acceptance_rate':round(counts['accepted']/completed,3) if completed else None,'worker_seconds_total':round(durations,2),
            'worker_seconds_mean':round(durations/local_count,2) if local_count else None,'review_seconds_total':round(reviews,2),
            'cloud_implementation_attempts':cloud_count,'details':list(reversed(details)),'details_truncated':total>len(details),'history_storage':'indexed_v1','history_coverage':read(Path(project['state_dir'])/'history-migration.json') or {'complete':False}})
    return {'schema_version':2,'since':since.isoformat(),'checked_at':instant.isoformat(),'projects':reports}
