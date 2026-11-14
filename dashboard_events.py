"""Local activity stream and best-effort macOS notifications, no model calls."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time
import durability as D


def valid_checkpoint(value):
    if not isinstance(value,dict) or set(value)-{'offsets','events','observed'} or not {'offsets','events'}<=set(value):
        raise ValueError('Invalid notification checkpoint shape')
    if not isinstance(value['offsets'],dict) or not isinstance(value['events'],list) or len(value['events'])>100:
        raise ValueError('Invalid notification checkpoint collections')
    for key,offset in value['offsets'].items():
        if not isinstance(key,str) or not key or len(key)>80 or type(offset) is not int or offset<0:
            raise ValueError('Invalid event offset')
    for event in value['events']:
        required={'id','at','project','kind','run','summary'}
        if not isinstance(event,dict) or not required<=set(event) or set(event)-required-{'notification'}:
            raise ValueError('Invalid retained event')
        if not isinstance(event['id'],str) or len(event['id'])!=64 or any(c not in '0123456789abcdef' for c in event['id']):
            raise ValueError('Invalid event identity')
        for field,limit in [('project',80),('kind',100),('summary',1200)]:
            if not isinstance(event[field],str) or len(event[field])>limit:raise ValueError('Invalid event text')
        if event['run'] is not None and (not isinstance(event['run'],str) or len(event['run'])>128):raise ValueError('Invalid event run')
        if event['at'] is not None:
            if not isinstance(event['at'],str) or len(event['at'])>80 or dt.datetime.fromisoformat(event['at']).tzinfo is None:
                raise ValueError('Invalid event date')
        delivery=event.get('notification')
        if delivery is not None:
            if not isinstance(delivery,dict) or set(delivery)-{'state','helper_exit','error','visible_delivery'}:raise ValueError('Invalid delivery receipt')
            if 'state' in delivery and (not isinstance(delivery['state'],str) or delivery['state'] not in {'queued','delivering','requested','failed'}):raise ValueError('Invalid delivery state')
            if delivery.get('helper_exit') is not None and type(delivery['helper_exit']) is not int:raise ValueError('Invalid helper status')
            if 'error' in delivery and (not isinstance(delivery['error'],str) or len(delivery['error'])>100):raise ValueError('Invalid delivery error')
            if 'visible_delivery' in delivery and delivery['visible_delivery']!='not_independently_verified':raise ValueError('Invalid delivery claim')
    if 'observed' in value:
        observed=value['observed']
        required={'phase','override','recovery','projects','controller_attention'}
        if not isinstance(observed,dict) or not required<=set(observed) or set(observed)-required-{'model'}:raise ValueError('Invalid observer state')
        if 'model' in observed and (not isinstance(observed['model'],str) or len(observed['model'])>120):raise ValueError('Invalid model observation')
        if not isinstance(observed['phase'],str) or len(observed['phase'])>100 or type(observed['controller_attention']) is not bool:raise ValueError('Invalid controller state')
        override=observed['override']
        if not isinstance(override,list) or len(override)!=2 or type(override[0]) is not bool or (override[1] is not None and (not isinstance(override[1],str) or len(override[1])>80)):raise ValueError('Invalid override observation')
        if observed['recovery'] is not None and (not isinstance(observed['recovery'],str) or len(observed['recovery'])>100):raise ValueError('Invalid health observation')
        if not isinstance(observed['projects'],list):raise ValueError('Invalid project observations')
        for item in observed['projects']:
            if not isinstance(item,list) or len(item)!=2 or not isinstance(item[0],str) or len(item[0])>80 or type(item[1]) is not bool:raise ValueError('Invalid project observation')
    return value


def notify(event):
    script=['/usr/bin/osascript','-e','on run argv','-e','display notification (item 1 of argv) with title "Midnight" subtitle (item 2 of argv)','-e','end run','--']
    body=' '.join(str(event.get('summary','')).split())[:240]
    if event['kind'] in {'attempt_finished','supervisor_error'}:
        body='Work needs attention. Open the Midnight dashboard for the retained details.'
    title=event['project']+' · '+event['kind'].replace('_',' ')
    try:
        result=subprocess.run([*script,body,title],capture_output=True,text=True,timeout=5)
        return {'helper_exit':result.returncode,'visible_delivery':'not_independently_verified'}
    except (OSError,subprocess.TimeoutExpired) as exc:
        return {'helper_exit':None,'error':type(exc).__name__,'visible_delivery':'not_independently_verified'}


class ActivityHub:
    def __init__(self,registry,state_dir,notifier=notify,observer=None):
        self.registry=registry;self.path=Path(state_dir)/'notification-state.json';self.notifier=notifier
        self.lock=threading.Lock();self.stop_event=threading.Event();self.error=None
        self.disabled=False
        try:self.saved=valid_checkpoint(D.read_json(self.path,{'offsets':{},'events':[]},repair=True))
        except (ValueError,OSError,TypeError,KeyError) as exc:
            self.saved={'offsets':{},'events':[]};self.disabled=True;self.error='Notification checkpoint unavailable: '+type(exc).__name__
        self.thread=None
        self.delivery_signal=threading.Event();self.delivery_thread=None
        for event in self.saved['events']:
            if (event.get('notification') or {}).get('state')=='delivering':event['notification']={'state':'queued'}
        self.observer=observer;self.last_observed=0

    def observe(self):
        if not self.observer or self.disabled:return
        value=self.observer();controller=value.get('controller',{})
        current={'phase':value['phase'],'override':[value.get('override_active'),value.get('override_expires')],
                 'recovery':value.get('recovery'),'projects':[[p['id'],p['on_schedule']] for p in value['projects']]}
        stamp=controller.get('updated_at')
        if stamp:
            import datetime as dt
            stale=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(stamp)).total_seconds()>90
        else:stale=False
        current['controller_attention']=value['phase']=='working' and (not value['service'].get('running') or stale)
        current['model']=str((value.get('models',{}).get('active') or {}).get('model','unobserved'))[:120]
        with self.lock:
            previous=self.saved.get('observed')
            if previous:
                for key in current:
                    if current[key]==previous.get(key):continue
                    summary={'phase':'Pipeline is now '+str(current[key]),'override':'Override eligibility changed; expiry '+str(value.get('override_expires')),
                        'recovery':'Recovery health is '+str(current[key]),'projects':'Registered project schedule changed',
                        'controller_attention':'Controller needs attention' if current[key] else 'Controller is responding again',
                        'model':'Qualified worker model is now '+current['model']}[key]
                    event={'id':hashlib.sha256((key+value['checked_at']+str(current[key])).encode()).hexdigest(),
                           'at':value['checked_at'],'project':'Midnight','kind':key+'_changed','run':None,'summary':summary}
                    if key in {'override','recovery','controller_attention','model'}:event['notification']={'state':'queued'}
                    self.saved['events'].append(event)
            if previous!=current:
                self.saved['observed']=current;self.saved['events']=self.saved['events'][-100:];D.save_json(self.path,self.saved)
        self.delivery_signal.set()
        self.last_observed=time.monotonic()

    def scan(self):
        if self.disabled:return
        changed=False
        with self.lock:
            for project in self.registry()['projects']:
                identity=project['id'];path=Path(project['state_dir'])/'events.jsonl'
                if not path.exists():
                    if identity not in self.saved['offsets']:self.saved['offsets'][identity]=0;changed=True
                    continue
                if path.is_symlink() or not path.is_file():raise ValueError('Unsupported activity log')
                size=path.stat().st_size;offset=self.saved['offsets'].get(identity)
                if offset is None:
                    self.saved['offsets'][identity]=size;changed=True;continue
                if size==offset:continue
                start=offset if 0<=offset<=size else 0
                if size-start>262144:start=size-262144
                with path.open('rb') as handle:
                    handle.seek(start)
                    if start!=offset and start:handle.readline();start=handle.tell()
                    data=handle.read(262144)
                # Leave an incomplete tail for the next scan.
                complete=data.rfind(b'\n')+1
                self.saved['offsets'][identity]=start+complete
                for line in data[:complete].splitlines():
                    try:value=json.loads(line)
                    except ValueError:continue
                    if not isinstance(value,dict):continue
                    digest=hashlib.sha256(identity.encode()+b'\0'+line).hexdigest()
                    if any(e['id']==digest for e in self.saved['events']):continue
                    kind=str(value.get('event','activity'))[:100];run=value.get('run')
                    if run is not None and (not isinstance(run,str) or len(run)>128):run=None
                    summary=value.get('summary') or value.get('detail') or value.get('title') or value.get('task') or kind
                    stamp=value.get('at')
                    try:
                        if not isinstance(stamp,str) or dt.datetime.fromisoformat(stamp).tzinfo is None:stamp=None
                    except ValueError:stamp=None
                    event={'id':digest,'at':stamp,'project':identity,'kind':kind,'run':run,'summary':str(summary)[:1200]}
                    if kind=='accepted' or (kind=='attempt_finished' and value.get('outcome')=='failed') or kind=='supervisor_error':
                        event['notification']={'state':'queued'}
                    self.saved['events'].append(event)
                self.saved['events']=self.saved['events'][-100:];changed=True
            from action_store import Actions
            workflow=Actions(self.path.parent)
            cursor=self.saved['offsets'].get('workflow')
            if cursor is None:
                latest=workflow.events(limit=1)
                self.saved['offsets']['workflow']=latest[0]['sequence'] if latest else 0;changed=True
            else:
                for value in workflow.events(after=cursor,limit=100):
                    self.saved['offsets']['workflow']=value['sequence'];changed=True
                    if not value['kind'].startswith('incident_'):continue
                    kind=value['kind'];payload=value.get('payload') or {}
                    summary=(kind.replace('_',' ').capitalize()+': '+str(payload.get('code') or payload.get('detail') or 'Open the recovery panel for details.'))[:1200]
                    self.saved['events'].append({'id':hashlib.sha256(('workflow:'+str(value['sequence'])).encode()).hexdigest(),
                        'at':dt.datetime.fromtimestamp(value['at'],dt.timezone.utc).isoformat(),'project':value['project'],
                        'kind':kind,'run':None,'summary':summary,'notification':{'state':'queued'}})
                self.saved['events']=self.saved['events'][-100:]
            if changed:D.save_json(self.path,self.saved)
        self.delivery_signal.set()

    def view(self):
        # Journal/fsync work may be slow under memory or disk pressure. It must
        # never make the whole status feed wait for notification history.
        notifications='unavailable' if self.disabled else 'enabled; macOS permission settings apply'
        if not self.lock.acquire(blocking=False):
            return {'events':[],'error':'Activity journal update in progress; saved events unavailable in this snapshot.',
                    'desktop_notifications':notifications,'observation_state':'busy'}
        try:return {'events':copy.deepcopy(self.saved['events'][-30:]),'error':self.error,'desktop_notifications':notifications}
        finally:self.lock.release()

    def deliver_one(self):
        if self.disabled:return False
        with self.lock:
            event=next((e for e in self.saved['events'] if (e.get('notification') or {}).get('state')=='queued'),None)
            if event is None:return False
            event['notification']={'state':'delivering'};D.save_json(self.path,self.saved);outgoing=copy.deepcopy(event)
        # OS delivery never owns the reader lock. A stalled helper cannot hold
        # the status API or SSE stream. An interrupted attempt may replay once.
        try:
            result=self.notifier(outgoing)
            if not isinstance(result,dict):result={'helper_exit':None,'error':'Invalid notifier result'}
        except Exception as exc:result={'helper_exit':None,'error':type(exc).__name__}
        code=result.get('helper_exit');code=code if type(code) is int else None
        result={'state':'requested' if code==0 else 'failed','helper_exit':code,'visible_delivery':'not_independently_verified',
                **({'error':str(result['error'])[:100]} if result.get('error') else {})}
        with self.lock:
            saved=next((e for e in self.saved['events'] if e['id']==outgoing['id']),None)
            if saved is not None:saved['notification']=result;D.save_json(self.path,self.saved)
        return True

    def deliver_loop(self):
        while not self.stop_event.is_set():
            try:
                if self.deliver_one():continue
            except Exception as exc:self.error=type(exc).__name__
            self.delivery_signal.wait(1);self.delivery_signal.clear()

    def loop(self):
        while not self.stop_event.is_set():
            try:
                self.scan()
                if time.monotonic()-self.last_observed>=10:self.observe()
                if not self.disabled:self.error=None
            except Exception as exc:self.error=type(exc).__name__
            self.stop_event.wait(2)

    def start(self):
        try:self.scan();self.observe()
        except Exception as exc:self.error=type(exc).__name__
        self.delivery_thread=threading.Thread(target=self.deliver_loop,daemon=True);self.delivery_thread.start()
        self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.delivery_signal.set()
