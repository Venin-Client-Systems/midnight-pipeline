"""A slow authoritative reader cannot block HTTP or pretend stale data is current."""
import http.client,io,json,os,tempfile,threading,time,unittest
from pathlib import Path
from unittest import mock
import dashboard as V
import guardian as G

class StatusCacheTests(unittest.TestCase):
 def server(self,provider):return V.DashboardServer(int(os.environ.get('MIDNIGHT_VALIDATION_PORT','0')),provider=provider)
 def test_serve_binds_before_slow_activity_start_and_shares_the_only_collector(self):
  import midnight as M
  import dashboard_events as E
  release=threading.Event();activity_release=threading.Event();bound=threading.Event();serving=threading.Event();collecting=threading.Event();observing=threading.Event();servers=[];calls=[]
  def provider():
   calls.append(True);collecting.set();release.wait(3)
   return {'phase':'paused','override_active':False,'override_expires':None,'projects':[],
           'checked_at':'2026-11-02T00:00:00+00:00','controller':{},'service':{'running':False}}
  class Server(V.DashboardServer):
   def __init__(self,*args,**kwargs):
    super().__init__(*args,**kwargs);servers.append(self);bound.set()
   def serve_forever(self,*args,**kwargs):serving.set();return super().serve_forever(*args,**kwargs)
  class Hub(E.ActivityHub):
   def __init__(self,*args,**kwargs):
    activity_release.wait(3);super().__init__(*args,notifier=lambda event:{'helper_exit':0},**kwargs)
   def observe(self):observing.set();return super().observe()
  def request(path):
   conn=http.client.HTTPConnection('127.0.0.1',servers[0].server_port,timeout=1)
   try:
    conn.request('GET',path);response=conn.getresponse();return response.status,json.loads(response.read())
   finally:conn.close()
  telemetry=mock.Mock()
  with tempfile.TemporaryDirectory() as directory,mock.patch.object(M,'STATE',Path(directory)),mock.patch.object(M,'registry',return_value={'projects':[]}),mock.patch.object(V,'collect',side_effect=provider),mock.patch.object(V,'DashboardServer',Server),mock.patch.object(V,'Telemetry',return_value=telemetry),mock.patch.object(E,'ActivityHub',Hub),mock.patch.object(V.signal,'signal'):
   thread=threading.Thread(target=V.serve,args=(int(os.environ.get('MIDNIGHT_VALIDATION_PORT','0')),),daemon=True);thread.start()
   try:
    self.assertTrue(bound.wait(1),'HTTP must bind before status or activity disk work');self.assertTrue(collecting.wait(1))
    before=time.monotonic();code,value=request('/api/health');self.assertEqual(code,200);self.assertTrue(value['status_observation']['warming']);self.assertLess(time.monotonic()-before,.7)
    self.assertEqual(request('/api/status')[0],503)
    activity_release.set();self.assertTrue(observing.wait(1));self.assertEqual(request('/api/status')[0],503);self.assertEqual(len(calls),1)
   finally:
    release.set();activity_release.set()
    if serving.wait(3):servers[0].shutdown()
    thread.join(3)
   self.assertFalse(thread.is_alive());telemetry.stop.assert_called()
 def test_slow_refresh_returns_preserved_snapshot_and_has_one_collector(self):
  release=threading.Event();started=threading.Event();calls=[]
  def provider():
   calls.append(True)
   if len(calls)>1:started.set();release.wait(3)
   return {'checked_at':'2026-11-02T00:00:00+00:00','operation':{'answer':'YES'},'serial':len(calls)}
  with self.server(provider) as server:
   try:
    self.assertEqual(json.loads(server.payload())['serial'],1);server.cache_at=time.monotonic()-3;server.last_refresh=None
    before=time.monotonic();value=json.loads(server.payload());self.assertLess(time.monotonic()-before,.25);self.assertEqual(value['serial'],1)
    self.assertTrue(started.wait(.5));self.assertEqual(json.loads(server.payload())['serial'],1);self.assertEqual(len(calls),2)
   finally:release.set()
 def test_cold_and_overage_observations_fail_explicitly_without_waiting_for_io(self):
  release=threading.Event()
  def provider():release.wait(3);return {'operation':{'answer':'YES'}}
  with self.server(provider) as server:
   try:
    before=time.monotonic()
    with self.assertRaises(V.ObservationUnavailable):server.payload()
    self.assertLess(time.monotonic()-before,.7);self.assertTrue(server.observation_health()['warming'])
    server.started_at=time.monotonic()-91;self.assertFalse(server.observation_health()['warming'])
    with server.cache_lock:server.cache=b'{"operation":{"answer":"YES"}}';server.cache_at=time.monotonic()-91
    with self.assertRaises(V.ObservationUnavailable):server.payload()
    self.assertFalse(server.observation_health()['available'])
   finally:release.set()
 def test_aged_operation_is_labelled_unknown_and_real_timestamp_is_preserved(self):
  release=threading.Event()
  def provider():release.wait(3);return {'operation':{'answer':'YES'}}
  with self.server(provider) as server:
   try:
    with server.cache_lock:
     server.cache=b'{"checked_at":"original-time","operation":{"answer":"YES","running":true}}';server.cache_at=time.monotonic()-35
    value=json.loads(server.payload());self.assertEqual(value['checked_at'],'original-time');self.assertEqual(value['operation']['answer'],'CHECK');self.assertIsNone(value['operation']['running']);self.assertEqual(value['last_observed_operation']['answer'],'YES');self.assertGreaterEqual(value['observation']['age_seconds'],35)
   finally:release.set()
 def test_guardian_recognizes_snapshot_unavailability_without_slow_collection(self):
  for observation,expected in [(None,True),({'available':True,'warming':False},True),({'available':False,'warming':True},True),({'available':False,'warming':False},False)]:
   value={'service':'midnight-dashboard','process':{'pid':123}}
   if observation is not None:value['status_observation']=observation
   with self.subTest(observation=observation),mock.patch.object(G.urllib.request,'urlopen',return_value=io.StringIO(json.dumps(value))),mock.patch.object(G.P,'alive',return_value=True):self.assertEqual(G.dashboard_probe()['available'],expected)

if __name__=='__main__':unittest.main()
