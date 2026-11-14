"""Initialized checkpoint reads must coexist with another SQLite reader."""
import contextlib
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import durability as D


class CheckpointConnectionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.path=self.root/'state.json';self.value={'phase':'retained','earlier_charges':'spent'}
  D.save_json(self.path,self.value);self.db=D.database(self.path);self.sqlite_connect=sqlite3.connect

 @contextlib.contextmanager
 def reader(self):
  con=self.sqlite_connect(self.db.resolve().as_uri()+'?mode=ro',uri=True,isolation_level=None)
  try:
   con.execute('BEGIN');con.execute('SELECT payload FROM snapshots').fetchall();yield con
  finally:con.rollback();con.close()

 def bounded_connect(self,*args,**kwargs):
  kwargs['timeout']=.025
  return self.sqlite_connect(*args,**kwargs)

 def test_initialized_writable_connection_does_not_need_exclusive_metadata_access(self):
  before=hashlib.sha256(self.db.read_bytes()).hexdigest()
  with self.reader(),mock.patch.object(D.sqlite3,'connect',side_effect=self.bounded_connect):
   try:con=D.connect(self.path,writable=True)
   except (sqlite3.Error,D.CheckpointError) as exc:self.fail('An ordinary connection tried to write metadata under an existing reader: '+str(exc))
   try:
    self.assertEqual(con.execute('PRAGMA synchronous').fetchone()[0],3)
    self.assertEqual(con.execute('PRAGMA fullfsync').fetchone()[0],1)
    self.assertEqual(con.execute('PRAGMA journal_mode').fetchone()[0].lower(),'delete')
    self.assertFalse(con.in_transaction)
   finally:con.close()
  self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(),before)

 def test_repair_read_preserves_saved_state_while_another_reader_is_active(self):
  with self.reader(),mock.patch.object(D.sqlite3,'connect',side_effect=self.bounded_connect):
   try:value=D.read_json(self.path,repair=True)
   except D.CheckpointError as exc:self.fail('An initialized repair read should not compete for a database write: '+str(exc))
  self.assertEqual(value,self.value);self.assertEqual(json.loads(self.path.read_text()),self.value)

 def test_actual_write_still_serializes_and_preserves_the_prior_snapshot(self):
  replacement={'phase':'new','earlier_charges':'spent'}
  with self.reader(),mock.patch.object(D.sqlite3,'connect',side_effect=self.bounded_connect):
   with self.assertRaises(D.CheckpointError):D.save_json(self.path,replacement)
   self.assertEqual(D.read_json(self.path),self.value)
  D.save_json(self.path,replacement);self.assertEqual(D.read_json(self.path),replacement)
  con=self.sqlite_connect(self.db)
  try:self.assertEqual(con.execute('SELECT COUNT(*) FROM snapshots WHERE name=?',(self.path.name,)).fetchone()[0],2)
  finally:con.close()

 def test_ordinary_connection_preserves_private_permissions_and_durability_settings(self):
  con=D.connect(self.path,writable=True)
  try:
   self.assertEqual(self.db.stat().st_mode & 0o777,0o600)
   self.assertEqual(con.execute('PRAGMA application_id').fetchone()[0],D.APPLICATION_ID)
   self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],1)
   self.assertEqual(con.execute('PRAGMA synchronous').fetchone()[0],3)
   self.assertEqual(con.execute('PRAGMA fullfsync').fetchone()[0],1)
  finally:con.close()


if __name__=='__main__':unittest.main()
