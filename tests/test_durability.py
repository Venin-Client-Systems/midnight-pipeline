from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import durability as D


class DurabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="midnight-recovery-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "state.json"

    def test_read_only_missing_state_creates_nothing(self):
        self.assertEqual(D.read_json(self.path), {})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_corrupt_mirror_is_observable_without_mutation_then_restored(self):
        D.save_json(self.path, {"accepted": ["verified"]})
        self.path.write_bytes(b'{truncated')
        self.assertEqual(D.read_json(self.path), {"accepted": ["verified"]})
        self.assertEqual(self.path.read_bytes(), b'{truncated')
        self.assertEqual(D.health(self.path)["status"], "repair_needed")
        D.read_json(self.path, repair=True)
        self.assertEqual(json.loads(self.path.read_bytes()), {"accepted": ["verified"]})
        self.assertEqual(next(self.root.glob('state.json.recovered-*')).read_bytes(), b'{truncated')

    def test_committed_snapshot_survives_missing_mirror_and_permissions_are_private(self):
        D.save_json(self.path, {"n": 1})
        D.commit_snapshot(self.path, b'{"n":2}')
        self.path.unlink()
        self.assertEqual(D.read_json(self.path, repair=True), {"n": 2})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(D.database(self.path).stat().st_mode & 0o777, 0o600)

    def child_until_killed(self, body):
        script = "import sys,time; sys.path.insert(0," + repr(str(Path(D.__file__).parent)) + "); import durability as D; from pathlib import Path; p=Path(" + repr(str(self.path)) + "); " + body + "; print('READY',flush=True); time.sleep(60)"
        proc = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready, _, _ = select.select([proc.stdout], [], [], 5)
            self.assertTrue(ready, "Synthetic child did not reach fault point")
            line = proc.stdout.readline()
            self.assertEqual(line.strip(), 'READY', proc.stderr.read() if proc.poll() is not None else '')
        finally:
            proc.kill()
            proc.wait(timeout=5)
            proc.stdout.close(); proc.stderr.close()

    def test_killed_process_rolls_back_uncommitted_sqlite_transaction(self):
        D.save_json(self.path, {"n": 1})
        body = "c=D.connect(p,writable=True); c.execute('PRAGMA cache_size=1'); c.execute('BEGIN IMMEDIATE'); c.execute(\"INSERT INTO snapshots VALUES('state.json',2,?, 'uncommitted',0,'fixture')\",(b'x'*300000,))"
        self.child_until_killed(body)
        self.assertEqual(D.read_json(self.path, repair=True), {"n": 1})
        with closing(sqlite3.connect(D.database(self.path))) as con, con:
            self.assertEqual(con.execute('PRAGMA quick_check').fetchone()[0], 'ok')

    def test_killed_process_after_commit_recovers_before_mirror_replace(self):
        D.save_json(self.path, {"n": 1})
        self.child_until_killed("D.commit_snapshot(p,b'{\"n\":2}')")
        self.assertEqual(json.loads(self.path.read_text()), {"n": 1})
        self.assertEqual(D.read_json(self.path, repair=True), {"n": 2})

    def test_cancelled_override_cannot_resurrect_after_power_loss(self):
        D.save_json(self.path, {"expires_at": "future"})
        D.commit_snapshot(self.path, b'', deleted=True)
        self.assertEqual(D.read_json(self.path), {})
        D.read_json(self.path, repair=True)
        self.assertFalse(self.path.exists())
        self.assertEqual(D.health(self.path)['status'], 'healthy')

    def test_damaged_latest_digest_uses_previous_verified_generation(self):
        D.save_json(self.path, {"n": 1}); D.save_json(self.path, {"n": 2})
        with closing(sqlite3.connect(D.database(self.path))) as con, con:
            con.execute("UPDATE snapshots SET digest='invalid' WHERE version=2")
        self.assertEqual(D.read_json(self.path, repair=True), {"n": 1})
        self.assertEqual(D.health(self.path)['status'], 'healthy')

    def test_unrecoverable_database_is_preserved_and_reported(self):
        D.save_json(self.path, {"n": 1})
        D.database(self.path).write_bytes(b'broken database')
        with self.assertRaises(D.CheckpointError): D.read_json(self.path, repair=True)
        self.assertEqual(D.database(self.path).read_bytes(), b'broken database')
        self.assertEqual(json.loads(self.path.read_text()), {"n": 1})
        self.assertEqual(D.health(self.path)['status'], 'unavailable')

    def test_symlinked_mirror_is_rejected_without_touching_target(self):
        target = self.root/'other'; target.write_bytes(b'private')
        self.path.symlink_to(target)
        with self.assertRaises(D.CheckpointError): D.read_json(self.path, repair=True)
        self.assertEqual(target.read_bytes(), b'private')

    def test_foreign_database_is_not_adopted_or_modified(self):
        with closing(sqlite3.connect(D.database(self.path))) as con, con:
            con.execute('CREATE TABLE unrelated(value TEXT)')
        before=D.database(self.path).read_bytes()
        with self.assertRaises(D.CheckpointError):D.read_json(self.path,repair=True)
        self.assertEqual(D.database(self.path).read_bytes(),before)
