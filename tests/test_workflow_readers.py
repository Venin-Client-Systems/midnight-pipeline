"""Workflow observations stay committed and responsive during a real writer."""
from pathlib import Path
import sqlite3,tempfile,threading,time,unittest
from action_store import Actions
from history_store import HistoryStore,APPLICATION_ID

class WorkflowReaderTests(unittest.TestCase):
 def test_reader_sees_committed_state_during_exclusive_writer_then_fresh_commit(self):
  with tempfile.TemporaryDirectory() as name:
   actions=Actions(Path(name));actions.meta('reader_probe',0,write=True)
   configured=actions.store.connect(write=True)
   try:
    self.assertEqual(configured.execute('PRAGMA journal_mode').fetchone()[0],'wal')
    self.assertEqual(configured.execute('PRAGMA synchronous').fetchone()[0],3)
    self.assertEqual(configured.execute('PRAGMA fullfsync').fetchone()[0],1)
    self.assertEqual(configured.execute('PRAGMA checkpoint_fullfsync').fetchone()[0],1)
   finally:configured.close()
   started=threading.Event();release=threading.Event();errors=[]
   def write():
    try:
     con=actions.store.connect(write=True)
     try:
      con.execute('BEGIN EXCLUSIVE');con.execute("UPDATE wf_meta SET value='1' WHERE key='reader_probe'")
      started.set();release.wait(2);con.commit()
     finally:con.close()
    except Exception as exc:errors.append(type(exc).__name__);started.set()
   thread=threading.Thread(target=write);thread.start()
   try:
    self.assertTrue(started.wait(2));before=time.monotonic()
    self.assertEqual(actions.meta('reader_probe'),0)
    self.assertLess(time.monotonic()-before,.7,'A workflow reader must not await an uncommitted writer')
   finally:release.set();thread.join(3)
   self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
   self.assertEqual(actions.meta('reader_probe'),1)
 def test_read_only_does_not_migrate_and_foreign_writer_is_rejected_before_migration(self):
  with tempfile.TemporaryDirectory() as name:
   root=Path(name);store=HistoryStore(root/'workflow.sqlite3')
   with store.transaction() as con:con.execute('CREATE TABLE wf_jobs(id TEXT,state TEXT,created REAL)')
   actions=Actions(root);self.assertEqual(actions.jobs(),[])
   with sqlite3.connect(store.path) as con:
    self.assertEqual(con.execute('PRAGMA journal_mode').fetchone()[0],'delete')
    con.execute('PRAGMA application_id=12345')
   with self.assertRaisesRegex(ValueError,'Unrecognised history database identity'):actions.meta('blocked',1,write=True)
   with sqlite3.connect(store.path) as con:
    self.assertEqual(con.execute('PRAGMA application_id').fetchone()[0],12345)
    self.assertEqual(con.execute('PRAGMA journal_mode').fetchone()[0],'delete')
    con.execute('PRAGMA application_id='+str(APPLICATION_ID))
    con.execute("UPDATE qc_meta SET value='unsupported' WHERE key='schema_version'")
   with self.assertRaisesRegex(ValueError,'Unsupported history schema'):actions.meta('blocked',1,write=True)
   with sqlite3.connect(store.path) as con:self.assertEqual(con.execute('PRAGMA journal_mode').fetchone()[0],'delete')

 def test_backup_includes_committed_wal_rows_and_excludes_uncommitted_changes(self):
  with tempfile.TemporaryDirectory() as name:
   root=Path(name);actions=Actions(root);actions.meta('backup_probe',0,write=True)
   writer=actions.store.connect(write=True)
   try:
    writer.execute('PRAGMA wal_autocheckpoint=0')
    writer.execute('BEGIN IMMEDIATE');writer.execute("UPDATE wf_meta SET value='7' WHERE key='backup_probe'");writer.commit()
    self.assertGreater(Path(str(actions.store.path)+'-wal').stat().st_size,0)
    writer.execute('BEGIN IMMEDIATE');writer.execute("UPDATE wf_meta SET value='9' WHERE key='backup_probe'")
    reader=actions.store.connect();target=sqlite3.connect(root/'backup.sqlite3')
    try:reader.backup(target)
    finally:reader.close();target.close()
    with sqlite3.connect(root/'backup.sqlite3') as backup:
     self.assertEqual(backup.execute("SELECT value FROM wf_meta WHERE key='backup_probe'").fetchone()[0],'7')
     self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0],'ok')
   finally:writer.rollback();writer.close()

if __name__=='__main__':unittest.main()
