"""Bounded read-only projection; no model, discovery or mutation from a GET."""
import datetime as dt
import json
from pathlib import Path
import sqlite3
import subprocess
import durability as D
import quality_cycle as QC
import cloud_policy as CP
import profile_manager as PM
from history_store import HistoryStore


def read(path):
    try:return json.loads(path.read_text())
    except FileNotFoundError:return {}


def snapshot(project,root,view='quality'):
    runtime=Path(project['state_dir']);result={'project':project['id'],'schema_version':1,'errors':[]}
    def observe(name,fn,default=None):
        try:result[name]=fn()
        except (OSError,ValueError,KeyError,AttributeError,sqlite3.Error,subprocess.SubprocessError) as exc:
            result[name]=default or {'status':'unavailable','reason':type(exc).__name__}
            result['errors'].append(name+': '+type(exc).__name__)
    def cycle():
        profile=read(Path(root)/project['quality_profile']) if project.get('quality_profile') else {}
        return QC.QualityCycle(runtime,project['id'],PM.effective_profile(runtime,project['id'],profile))
    def quality():
        head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=project['path'],text=True,timeout=5).strip()
        return cycle().status(head)
    observe('quality',quality,{'feature_allowed':False,'reasons':['quality_observation_unavailable']})
    observe('findings',lambda:cycle().findings(limit=100))
    observe('history',lambda:HistoryStore(runtime).query(project['id'],limit=30))
    observe('history_coverage',lambda:read(runtime/'history-migration.json') or {'complete':False,'status':'migration_pending'})
    observe('cloud_manager',lambda:CP.CloudPolicy(Path(root)/'runtime').snapshot())
    observe('github',lambda:read(runtime/'github-status.json') or {'status':'unobserved',**project.get('github',{})})
    observe('storage',lambda:read(runtime/'storage-status.json'))
    observe('profile_updates',lambda:PM.status(runtime,project['id']))
    observe('decision',lambda:D.read_json(runtime/'state.json').get('quality_decision',{}))
    def rescue():
        store=HistoryStore(runtime)
        with store.reader() as con:
            if con is None:return {'status':'unobserved','rolling_hours':168,'limit':3}
            tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'cr_reservations' not in tables:return {'status':'unobserved','rolling_hours':168,'limit':3}
            cutoff=dt.datetime.now(dt.timezone.utc).timestamp()-168*3600
            used=con.execute('SELECT COUNT(*) FROM cr_reservations WHERE project=? AND created>=?',(project['id'],cutoff)).fetchone()[0]
            rows=[dict(r) for r in con.execute('SELECT token,episode,task_id,created,state FROM cr_reservations WHERE project=? ORDER BY created DESC LIMIT 20',(project['id'],))]
            return {'status':'observed','rolling_hours':168,'limit':3,'used':used,'remaining':max(0,3-used),'reservations':rows}
    observe('rescue',rescue)
    return result
