"""Slow notification-journal work cannot hold the read-only status feed."""
import json,os,tempfile,threading,unittest
from pathlib import Path
from dashboard import DashboardServer
from dashboard_events import ActivityHub

class ActivityReaderTests(unittest.TestCase):
 def test_status_feed_returns_while_notification_journal_lock_is_owned(self):
  with tempfile.TemporaryDirectory() as name:
   root=Path(name);hub=ActivityHub(lambda:{'projects':[]},root,notifier=lambda _e:None)
   before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
   with DashboardServer(int(os.environ.get('MIDNIGHT_VALIDATION_PORT','0')),provider=lambda:{'operation':{'running':True,'happening':'Local coding'}},activity=hub) as server:
    done=threading.Event();result={}
    def get():
     try:result['payload']=json.loads(server.payload())
     finally:done.set()
    with hub.lock:
     thread=threading.Thread(target=get,daemon=True);thread.start();responded=done.wait(.5)
    thread.join(2);self.assertTrue(responded,'Notification journal held the entire status feed')
    self.assertEqual(result['payload']['operation']['happening'],'Local coding')
    self.assertEqual(result['payload']['activity']['observation_state'],'busy')
   self.assertEqual(before,{str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()})
 def test_busy_history_is_explicit_and_recovers_without_losing_saved_events(self):
  with tempfile.TemporaryDirectory() as name:
   hub=ActivityHub(lambda:{'projects':[]},Path(name),notifier=lambda _e:None);event={'id':'a'*64,'summary':'Actual saved event'};hub.saved['events']=[event]
   done=threading.Event();result={}
   def read():result['busy']=hub.view();done.set()
   with hub.lock:
    thread=threading.Thread(target=read,daemon=True);thread.start();responded=done.wait(.5)
   thread.join(2);self.assertTrue(responded,'Activity reader waited on the journal lock');busy=result['busy']
   self.assertEqual(busy['observation_state'],'busy');self.assertTrue(busy['error']);self.assertEqual(busy['events'],[])
   ready=hub.view();self.assertEqual(ready['events'],[event]);ready['events'][0]['summary']='Caller edit'
   self.assertEqual(hub.saved['events'][0]['summary'],'Actual saved event')

if __name__=='__main__':unittest.main()
