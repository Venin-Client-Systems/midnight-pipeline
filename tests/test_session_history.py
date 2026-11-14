import csv
import io
from pathlib import Path
import tempfile
import unittest
import durability as D
from session_history import Sessions,FIELDS,LEGACY_FIELDS,parse

class SessionHistoryTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.ledger=Sessions(self.root)
    def tearDown(self):self.temp.cleanup()
    def row(self,n):return dict.fromkeys(FIELDS,'')|{'session_id':'s'+str(n),'project':'alpha','attempt_count':'4','cloud_implementation_count':'1','schema_version':'2'}
    def test_legacy_migration_preserves_local_count_and_replay(self):
        out=io.StringIO();w=csv.DictWriter(out,fieldnames=LEGACY_FIELDS);w.writeheader();w.writerow({k:v for k,v in self.row(0).items() if k in LEGACY_FIELDS})
        D.save_bytes(self.root/'sessions.csv',out.getvalue().encode())
        self.ledger.append([self.row(1)]);self.ledger.append([self.row(1)])
        rows=self.ledger.rows();self.assertEqual(len(rows),2);self.assertEqual(rows[0]['cloud_implementation_count'],'0');self.assertEqual(rows[0]['attempt_count'],'4')
        self.assertEqual(len(parse((self.root/'sessions.csv').read_bytes())),2)
    def test_crash_after_empty_schema_never_hides_or_loses_legacy_rows(self):
        out=io.StringIO();w=csv.DictWriter(out,fieldnames=LEGACY_FIELDS);w.writeheader()
        w.writerow({k:v for k,v in self.row(0).items() if k in LEGACY_FIELDS})
        D.save_bytes(self.root/'sessions.csv',out.getvalue().encode())
        with self.ledger.connect(True):pass  # Crash before the import transaction.
        self.assertFalse(self.ledger.ready())
        before=self.ledger.csv.read_bytes()
        self.assertEqual([r['session_id'] for r in self.ledger.rows()],['s0'])
        self.assertEqual(self.ledger.csv.read_bytes(),before)
        self.assertEqual([r['session_id'] for r in parse(D.read_bytes(self.ledger.csv))],['s0'])
        self.ledger.append([self.row(1)])
        self.assertTrue(self.ledger.ready())
        self.assertEqual([r['session_id'] for r in self.ledger.rows()],['s0','s1'])
        self.assertEqual(len(parse(self.ledger.csv.read_bytes())),2)

    def test_unmarked_existing_database_merges_legacy_and_committed_receipts(self):
        import json
        legacy=self.row(0)
        D.save_bytes(self.ledger.csv,__import__('session_history').encode([legacy],True))
        with self.ledger.connect(True) as c:
            c.execute('INSERT INTO sessions(session,project,payload) VALUES(?,?,?)',('s1','alpha',json.dumps(self.row(1))))
        self.assertFalse(self.ledger.ready())
        self.assertEqual({r['session_id'] for r in self.ledger.rows()},{'s0','s1'})
        D.read_bytes(self.ledger.csv,repair=True)
        self.assertTrue(self.ledger.ready())
        self.assertEqual({r['session_id'] for r in self.ledger.rows()},{'s0','s1'})
        self.assertEqual(len(parse(self.ledger.csv.read_bytes())),2)

    def test_failed_legacy_import_rolls_back_rows_and_ready_marker(self):
        import json
        raw=__import__('session_history').encode([self.row(0),self.row(1)],True)
        D.save_bytes(self.ledger.csv,raw)
        conflicting=dict(self.row(1),attempt_count='99')
        with self.ledger.connect(True) as c:
            c.execute('INSERT INTO sessions(session,project,payload) VALUES(?,?,?)',('s1','alpha',json.dumps(conflicting)))
        with self.assertRaises(ValueError):self.ledger.append([self.row(2)])
        self.assertFalse(self.ledger.ready())
        with self.ledger.connect() as c:
            self.assertEqual(c.execute('SELECT session FROM sessions').fetchall(),[('s1',)])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM session_meta').fetchone()[0],0)
        self.assertEqual(self.ledger.csv.read_bytes(),raw)

    def test_uncertain_append_is_truncated_and_replayed(self):
        self.ledger.append([self.row(0)])
        with self.ledger.connect(True) as c:
            import json
            c.execute('INSERT INTO sessions(session,project,payload) VALUES(?,?,?)',('s1','alpha',json.dumps(self.row(1))))
        with self.ledger.csv.open('ab') as f:f.write(b'partial,torn,row')
        self.ledger.export();self.assertEqual(len(parse(self.ledger.csv.read_bytes())),2)
    def test_missing_export_recovers_authoritative_rows(self):
        self.ledger.append([self.row(0),self.row(1)]);self.ledger.csv.unlink()
        D.read_bytes(self.ledger.csv,repair=True)
        self.assertEqual(len(parse(self.ledger.csv.read_bytes())),2)
    def test_same_size_corruption_rebuilds_and_conflicts_reject(self):
        self.ledger.append([self.row(0)])
        raw=self.ledger.csv.read_bytes();self.ledger.csv.write_bytes(raw.replace(b'alpha',b'wrong'))
        self.assertEqual(self.ledger.health()['status'],'repair_needed')
        self.ledger.export();self.assertEqual(self.ledger.csv.read_bytes(),raw)
        with self.assertRaises(ValueError):self.ledger.append([dict(self.row(0),attempt_count='88')])

    def test_receipt_lookup_is_project_scoped_and_read_only(self):
        self.ledger.append([self.row(n) for n in range(1000)])
        before=self.ledger.db.stat().st_mtime_ns
        self.assertEqual(self.ledger.receipt('s731','alpha')['session_id'],'s731')
        self.assertIsNone(self.ledger.receipt('s731','other'))
        self.assertIsNone(self.ledger.receipt('missing','alpha'))
        self.assertEqual(self.ledger.db.stat().st_mtime_ns,before)

    def test_bounded_reads_do_not_rewrite(self):
        self.ledger.append([self.row(n) for n in range(1000)]);before=self.ledger.db.stat().st_mtime_ns
        self.assertEqual(len(self.ledger.rows(5)),5);self.assertEqual(self.ledger.db.stat().st_mtime_ns,before)

if __name__=='__main__':unittest.main()
