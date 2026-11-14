"""Read-only loopback dashboard; no model, scheduler or account actions."""
from __future__ import annotations

import csv
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import hashlib
import json
import signal
import secrets
from pathlib import Path
import threading
import time
from telemetry import Telemetry

ROOT = Path(__file__).resolve().parent
LOADED_CODE_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def collect():
    import midnight as M
    from performance import collect as performance
    data = M.registry()
    view = M.snapshot(data)
    from model_observation import snapshot as model_snapshot
    view['models']=model_snapshot()
    instant = M.now()
    override = view.get("override")
    since = dt.datetime.fromisoformat(override["created_at"]) if override else instant - dt.timedelta(hours=24)
    until = min(instant, dt.datetime.fromisoformat(override["expires_at"])) if override and override.get('expires_at') else instant
    periods = performance(data["projects"], since, max(since, until))
    stats = {p['project']: {k: p[k] for k in ('accepted', 'failed', 'interrupted', 'in_progress', 'awaiting_review', 'acceptance_rate', 'worker_seconds_total', 'review_seconds_total')} | {'checkpoints':p.get('checkpoints',0)} for p in periods['projects']}
    projects = []
    for p in view['projects']:
        feedback = []
        for identity, message in list(p['recent_feedback'].items())[-6:]:
            try:
                value = json.loads(message)
                summary = value.get('summary', message) if isinstance(value, dict) else message
            except (ValueError, TypeError):
                summary = message
            feedback.append({'task': identity, 'summary': str(summary)[:600]})
        projects.append({k: p[k] for k in ('id', 'name', 'on_schedule', 'why', 'phase', 'observed_at', 'active_task', 'queue', 'attempts_today', 'failures_today', 'cloud_calls_today', 'interruptions_today', 'pending_review', 'errors')} | {
            'control':p.get('control',{}), 'improvement':p.get('improvement',{}), 'quality_monitor':p.get('quality_monitor',{}),'repair_status':p.get('repair_status',{}),
            'productivity':p.get('productivity',{}),
            'routine_provider':p.get('routine_provider'), 'local_supervision_calls_today':p.get('local_supervision_calls_today',0),
            'accepted_total':p.get('accepted_total',len(p['accepted'])), 'accepted': p['accepted'][-8:], 'feedback': feedback, 'performance': stats.get(p['id'], {})})
    health = M.doctor(data)
    from operation_status import describe
    import phase_runtime as PR
    return {'schema_version':2,'dashboard_code_sha256':LOADED_CODE_SHA256,'dashboard_process':PR.lease(),
            'pipeline': view['pipeline'], 'checked_at': view['checked_at'], 'phase': view['phase'],
            'operation':view.get('operation') or describe(view), 'effective_cutoff':view.get('effective_cutoff'),
            'service': view['service'], 'schedule': view['schedule'], 'timezone': view['timezone'],
            'override_active': view['override_active'], 'override_expires': override.get('expires_at') if override else None,
            'run_mode':view.get('run_mode','scheduled'),'run_mode_label':view.get('run_mode_label'),
            'commissioning':view.get('commissioning'),
            'readiness':view.get('readiness'),
            'next_start': view['next_start'], 'projects': projects,
            'performance_since': since.isoformat(), 'recovery': health['status'],
            'models': view.get('models',{}),
            'controller': view.get('controller',{}), 'power':health.get('power',{}),
            'workflow':view.get('workflow',{}),
            'agent_workspace':view.get('agent_workspace',{}),
            'recovery_checks':health.get('controller_checkpoints',[]),
            'cold_boot_requirement': health['cold_boot_requirement'],
            'recent_sessions': [{k: row.get(k, '') for k in ('date', 'project', 'why', 'achieved', 'accepted_count', 'attempt_count', 'outcome', 'summary')} for row in view['recent_sessions']]}


def sessions_csv():
    import midnight as M
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=M.CSV_FIELDS)
    writer.writeheader()
    # Spreadsheet-safe export; the durable original ledger is not rewritten.
    for row in M.ledger_rows():
        writer.writerow({key: "'" + str(value) if str(value).lstrip().startswith(('=', '+', '-', '@')) else value for key, value in row.items() if key in M.CSV_FIELDS})
    return output.getvalue().encode('utf-8')


class ObservationUnavailable(RuntimeError):pass


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, provider=collect, ledger=sessions_csv, activity=None, telemetry=None):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError('Dashboard port must be an integer from 0 to 65535')
        self.token=secrets.token_urlsafe(32)
        self.control_lock=threading.Lock()
        self.provider, self.ledger = provider, ledger
        self.activity=activity
        self.telemetry=telemetry
        self.stopping=threading.Event()
        self.cache, self.cache_at, self.cache_lock = None, 0, threading.Lock()
        self.observation_ready=threading.Event();self.refreshing=False;self.refresh_error=None
        self.last_refresh=None;self.started_at=time.monotonic();self.observation_thread=None
        self.activity_error=None
        super().__init__(('127.0.0.1', port), Handler)

    def refresh(self):
        """Exactly one reader owns expensive collection; HTTP never owns it."""
        with self.cache_lock:
            instant=time.monotonic()
            if self.stopping.is_set() or self.refreshing or self.last_refresh is not None and instant-self.last_refresh<2:return
            self.refreshing=True;self.last_refresh=instant;provider=self.provider
        def collect_one():
            try:
                value=provider()
                if not isinstance(value,dict):raise ValueError('Observation must be an object')
                payload=json.dumps(value,allow_nan=False).encode()
                with self.cache_lock:
                    if not self.stopping.is_set():self.cache=payload;self.cache_at=instant;self.refresh_error=None
            except Exception as exc:
                with self.cache_lock:self.refresh_error=type(exc).__name__
            finally:
                with self.cache_lock:self.refreshing=False
                self.observation_ready.set()
        threading.Thread(target=collect_one,name='midnight-status-collect',daemon=True).start()

    def start_observation(self):
        if self.observation_thread is not None:return
        def loop():
            while not self.stopping.is_set():self.refresh();self.stopping.wait(2)
        self.observation_thread=threading.Thread(target=loop,name='midnight-status-refresh',daemon=True);self.observation_thread.start()

    def observation_health(self):
        with self.cache_lock:
            age=max(0,time.monotonic()-self.cache_at) if self.cache is not None else None
            warming=self.cache is None and time.monotonic()-self.started_at<90 and self.refresh_error is None
            return {'available':age is not None and age<=90 and self.refresh_error is None,'warming':warming,
                    'age_seconds':round(age,3) if age is not None else None,'refreshing':self.refreshing,'error':self.refresh_error}

    def payload(self):
        with self.cache_lock:needs_refresh=self.cache is None or time.monotonic()-self.cache_at>2
        if needs_refresh:self.refresh()
        with self.cache_lock:missing=self.cache is None
        if missing:self.observation_ready.wait(.25)
        with self.cache_lock:
            cached=self.cache;age=max(0,time.monotonic()-self.cache_at);refreshing=self.refreshing;error=self.refresh_error
        if cached is None or age>90 or error:raise ObservationUnavailable('Current observation is not available')
        value=json.loads(cached);state='unavailable' if error else 'updating' if age>30 else 'fresh'
        value['observation']={'state':state,'age_seconds':round(age,3),'refreshing':refreshing,'error':error,
            'as_of':value.get('checked_at'),'available':error is None,'maximum_age_seconds':90}
        if error or age>30:
            value['last_observed_operation']=value.get('operation')
            value['operation']={'running':None,'work_active':None,'local_worker_active':None,'state':'updating','answer':'CHECK',
                'headline':'Midnight status is updating','happening':'Showing a verified snapshot from '+str(round(age))+' seconds ago while fresh observation completes.',
                'next_run_label':'Refresh pending','next_run_at':None,'next_run_blocked':True}
        value['activity']=self.activity.view() if self.activity else {'events':[],
            'error':self.activity_error or 'Activity journal is starting; saved events are not yet available.',
            'observation_state':'unavailable' if self.activity_error else 'starting'}
        return json.dumps(value,allow_nan=False).encode()

    def activity_observation(self):
        value=json.loads(self.payload())
        if value['observation']['state']!='fresh':raise ObservationUnavailable('Activity observation is not fresh')
        return value

    def server_close(self):
        self.stopping.set()
        if self.activity:self.activity.stop()
        if self.observation_thread:self.observation_thread.join(timeout=1)
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        port = self.server.server_port
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        origin = self.headers.get('Origin')
        user_navigation=(self.path=='/' and self.headers.get('Sec-Fetch-Mode')=='navigate'
            and self.headers.get('Sec-Fetch-Dest')=='document' and self.headers.get('Sec-Fetch-User')=='?1')
        if (self.headers.get('Host') not in hosts
                or (origin and origin not in {'http://' + host for host in hosts})
                or (self.headers.get('Sec-Fetch-Site') == 'cross-site' and not user_navigation)):
            return self.reply(403, b'Local browser access required', 'text/plain')
        if self.path in ('/api/status','/api/oversight'):
            try:
                return self.reply(200, self.server.payload(), 'application/json')
            except Exception:
                return self.reply(503, b'{"error":"Current Midnight observation unavailable"}', 'application/json')
        if self.path=='/api/telemetry':
            return self.reply(200,self.telemetry_payload(),'application/json')
        if self.path=='/favicon.ico':return self.reply(204,b'','image/x-icon')
        if self.path=='/api/health':
            import phase_runtime as PR
            return self.reply(200,json.dumps({'service':'midnight-dashboard','code_sha256':LOADED_CODE_SHA256,'process':PR.lease(),
                'status_observation':self.server.observation_health()}).encode(),'application/json')
        if self.path in ('/api/events','/api/telemetry/events'):
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
            try:
                while not self.server.stopping.is_set():
                    metric_stream=self.path=='/api/telemetry/events'
                    payload=self.telemetry_payload() if metric_stream else self.server.payload()
                    self.wfile.write((b'event: telemetry\ndata: ' if metric_stream else b'event: state\ndata: ')+payload+b'\n\n');self.wfile.flush()
                    self.server.stopping.wait(2)
            except (BrokenPipeError,ConnectionResetError,OSError,ObservationUnavailable):return
        if self.path.startswith(('/api/project?','/api/run?')):
            from dashboard_details import inspect_request
            try:return self.reply(200,json.dumps(inspect_request(self.path),allow_nan=False).encode(),'application/json')
            except (ValueError,KeyError):return self.reply(400,b'{"error":"Invalid or unavailable inspection request"}','application/json')
            except Exception:return self.reply(503,b'{"error":"Inspection unavailable"}','application/json')
        if self.path.startswith('/api/model-comparison?'):
            from model_observation import inspect_model
            try:return self.reply(200,json.dumps(inspect_model(self.path),allow_nan=False).encode(),'application/json')
            except (ValueError,KeyError):return self.reply(400,b'{"error":"Invalid or unavailable comparison"}','application/json')
            except Exception:return self.reply(503,b'{"error":"Model evidence unavailable"}','application/json')
        if self.path.startswith('/api/chat/'):
            from workspace_view import inspect as inspect_workspace
            try:return self.reply(200,json.dumps(inspect_workspace(self.path),allow_nan=False).encode(),'application/json')
            except (ValueError,KeyError):return self.reply(400,b'{"error":"Invalid workspace read request"}','application/json')
            except Exception:return self.reply(503,b'{"error":"Saved workspace unavailable"}','application/json')
        if self.path == '/api/sessions.csv':
            try:
                return self.reply(200, self.server.ledger(), 'text/csv', download=True)
            except Exception:
                return self.reply(503, b'Session history unavailable', 'text/plain')
        assets = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css'),
                  '/inspect.js':('inspect.js','text/javascript'),'/sw.js':('sw.js','text/javascript'),
                  '/telemetry.js':('telemetry.js','text/javascript'),
                  '/workspace.js':('workspace.js','text/javascript'),
                  '/recovery.js':('recovery.js','text/javascript'),
                  '/workspace.css':('workspace.css','text/css'),
}
        asset = assets.get(self.path)
        if not asset:
            return self.reply(404, b'Not found', 'text/plain')
        body=(ROOT/'dashboard'/asset[0]).read_bytes()
        if self.path=='/':
            body=body.replace(b'</head>',b'<meta name="midnight-token" content="'+self.server.token.encode()+b'"></head>')
        return self.reply(200,body,asset[1])

    def telemetry_payload(self):
        try:
            value=self.server.telemetry.view() if self.server.telemetry else {'available':False,'errors':['Telemetry is not enabled'],'history':[]}
            return json.dumps(value,allow_nan=False).encode()
        except Exception:
            return b'{"available":false,"errors":["Telemetry observation unavailable"],"history":[]}'

    def do_POST(self):
        expected='127.0.0.1:'+str(self.server.server_port)
        if (self.path!='/api/control' or self.headers.get('Host')!=expected
                or self.headers.get('Origin')!='http://'+expected
                or not secrets.compare_digest(self.headers.get('X-Midnight-Token',''),self.server.token)
                or self.headers.get('Sec-Fetch-Site')=='cross-site'):
            return self.reply(403,b'{"error":"Local owner control required"}','application/json')
        if self.headers.get_content_type()!='application/json' or self.headers.get('Transfer-Encoding'):
            return self.reply(400,b'{"error":"JSON body required"}','application/json')
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=8192:raise ValueError('Invalid body length')
            def unique(pairs):
                result={}
                for key,value in pairs:
                    if key in result:raise ValueError('Duplicate field')
                    result[key]=value
                return result
            value=json.loads(self.rfile.read(length),object_pairs_hook=unique)
            if not isinstance(value,dict) or set(value)!={'action','detail'}:raise ValueError('Invalid control request')
            if not isinstance(value['action'],str) or not isinstance(value['detail'],dict):raise ValueError('Invalid action')
            import midnight as M
            with self.server.control_lock:
                result=M.owner_control(value['action'],value['detail'],via='dashboard')
            self.server.refresh()
            return self.reply(200,json.dumps({'ok':True,'result':result}).encode(),'application/json')
        except (ValueError,TypeError,KeyError,StopIteration):
            return self.reply(400,b'{"error":"Invalid or unavailable owner action"}','application/json')
        except Exception:
            return self.reply(503,b'{"error":"Owner action failed; inspect the owner action log before retrying"}','application/json')

    def reject_write(self):
        self.reply(403,b'{"error":"Method is not allowed"}','application/json')

    do_PUT = do_DELETE = do_PATCH = reject_write

    def reply(self, code, body, kind, download=False):
        try:return self.send_reply(code,body,kind,download)
        except (BrokenPipeError,ConnectionResetError):return

    def send_reply(self, code, body, kind, download=False):
        self.send_response(code)
        self.send_header('Content-Type', kind + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; worker-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="midnight-sessions.csv"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def serve(port=8766):
    import midnight as M
    from dashboard_events import ActivityHub
    telemetry=Telemetry(M.registry,M.STATE)
    import oversight
    with DashboardServer(port,provider=lambda:oversight.collect(collect()),telemetry=telemetry) as server:
        server.start_observation()
        def start_activity():
            try:
                hub=ActivityHub(M.registry,M.STATE/'dashboard',observer=server.activity_observation)
                server.activity=hub
                if server.stopping.is_set():hub.stop();return
                hub.start()
            except Exception as exc:server.activity_error=type(exc).__name__
        threading.Thread(target=start_activity,name='midnight-activity-start',daemon=True).start()
        telemetry.start()
        previous={sig:signal.getsignal(sig) for sig in (signal.SIGTERM,signal.SIGINT)}
        def shutdown(_sig,_frame):
            if server.activity:server.activity.stop()
            telemetry.stop()
            threading.Thread(target=server.shutdown,daemon=True).start()
        for sig in previous:signal.signal(sig,shutdown)
        print(f'Midnight dashboard: http://127.0.0.1:{server.server_port} (local owner controls)', flush=True)
        try:
            server.serve_forever(poll_interval=.5)
        except KeyboardInterrupt:
            pass
        finally:
            if server.activity:server.activity.stop()
            telemetry.stop()
            for sig,handler in previous.items():signal.signal(sig,handler)
    return 0
