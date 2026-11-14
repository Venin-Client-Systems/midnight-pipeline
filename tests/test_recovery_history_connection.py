"""Reproduce metadata-lock contention without weakening real transactions."""
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import history_store as H
import action_store as A


class HistoryConnectionTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.store=H.HistoryStore(self.root/'workflow.sqlite3',write_ahead=True)
        connection=self.store.connect(write=True);connection.close()
        actual=sqlite3.connect
        def bounded(*args,**kwargs):
            con=actual(*args,**kwargs);con.execute('PRAGMA busy_timeout=50');return con
        patch=mock.patch.object(H.sqlite3,'connect',side_effect=bounded)
        patch.start();self.addCleanup(patch.stop)

    def test_initialized_writer_setup_does_not_contend_for_another_writers_lock(self):
        owner=self.store.connect(write=True);self.addCleanup(owner.close)
        owner.execute('BEGIN IMMEDIATE')
        owner.execute("UPDATE qc_meta SET value=value WHERE key='schema_version'")
        # On the old source this fails at PRAGMA application_id=... before
        # the caller is given a connection. No test merely raises a busy mock.
        try:connection=self.store.connect(write=True)
        except sqlite3.OperationalError as exc:self.fail('Initialized connection setup contended for a metadata write: '+str(exc))
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute('PRAGMA application_id').fetchone()[0],H.APPLICATION_ID)
        with self.assertRaisesRegex(sqlite3.OperationalError,'locked'):
            connection.execute('BEGIN IMMEDIATE')
        owner.rollback()
        connection.execute('BEGIN IMMEDIATE')
        connection.rollback()

    def test_ordinary_connection_preserves_extra_fsync_and_performs_no_schema_or_header_write(self):
        statements=[];actual=H.sqlite3.connect
        def traced(*args,**kwargs):
            connection=actual(*args,**kwargs);connection.set_trace_callback(statements.append);return connection
        with mock.patch.object(H.sqlite3,'connect',side_effect=traced),mock.patch.object(H,'sync_directory') as directory_sync:
            connection=self.store.connect(write=True)
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute('PRAGMA synchronous').fetchone()[0],3)
        self.assertEqual(connection.execute('PRAGMA fullfsync').fetchone()[0],1)
        self.assertEqual(connection.execute('PRAGMA checkpoint_fullfsync').fetchone()[0],1)
        self.assertEqual(connection.execute('PRAGMA journal_mode').fetchone()[0],'wal')
        self.assertFalse(any(s.startswith(('BEGIN','CREATE','INSERT','PRAGMA application_id=')) for s in statements))
        directory_sync.assert_not_called()

    def test_actions_reader_remains_fresh_and_failed_real_transaction_grants_no_charge(self):
        actions=A.Actions(self.root)
        job=actions.submit('fixture',{},10**12)
        owner=self.store.connect(write=True);self.addCleanup(owner.close);owner.execute('BEGIN IMMEDIATE')
        self.assertEqual([j['id'] for j in actions.jobs()],[job['id']])
        with self.assertRaisesRegex(sqlite3.OperationalError,'locked'):
            actions.incident('fixture','controller_error','fixed','evidence',{})
        self.assertEqual(actions.incidents(),[])
        owner.rollback()
        self.assertIsNotNone(actions.incident('fixture','controller_error','fixed','evidence',{}))

    def test_incomplete_initialized_schema_is_preserved_and_rejected(self):
        connection=self.store.connect(write=True)
        connection.execute('DROP TABLE qc_runs');connection.close()
        with self.assertRaisesRegex(ValueError,'incomplete'):
            self.store.connect(write=True)
        inspect=sqlite3.connect(self.store.path)
        self.addCleanup(inspect.close)
        self.assertIsNone(inspect.execute("SELECT name FROM sqlite_master WHERE name='qc_runs'").fetchone())

    def test_initialized_database_with_missing_metadata_is_not_bootstrapped_again(self):
        connection=self.store.connect(write=True)
        connection.execute('DROP TABLE qc_meta');connection.close()
        with self.assertRaisesRegex(ValueError,'incomplete'):
            self.store.connect(write=True)
        inspect=sqlite3.connect(self.store.path);self.addCleanup(inspect.close)
        self.assertEqual(inspect.execute('PRAGMA application_id').fetchone()[0],H.APPLICATION_ID)
        self.assertIsNone(inspect.execute("SELECT name FROM sqlite_master WHERE name='qc_meta'").fetchone())


if __name__=='__main__':unittest.main()
