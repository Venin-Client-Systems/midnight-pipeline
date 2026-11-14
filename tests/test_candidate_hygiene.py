import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import candidate_hygiene as H

class HygieneTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.work=self.root/'work';self.work.mkdir()
        (self.work/'src').mkdir();(self.work/'src/a.py').write_bytes(b'changed')
        self.refs={'src/a.py':{'base':hashlib.sha256(b'original').hexdigest(),'candidate':hashlib.sha256(b'changed').hexdigest()}}
    def apply(self,names):return H.quarantine_artifacts(self.work,self.root,names,self.refs)
    def test_preserves_only_verified_redundant_copies_for_unrelated_projects(self):
        for suffix,data in [('.bak',b'original'),('.backup',b'changed'),('.fixed',b'original')]:
            (self.work/('src/a.py'+suffix)).write_bytes(data)
        names=['src/a.py'+s for s in ['.bak','.backup','.fixed']]
        self.assertCountEqual(self.apply(names),names)
        self.assertEqual((self.work/'src/a.py').read_bytes(),b'changed')
        for name in names:self.assertTrue((self.root/'quarantine'/name).is_file());self.assertFalse((self.work/name).exists())
        record=json.loads((self.root/'candidate-hygiene.json').read_text())
        self.assertEqual({x['state'] for x in record['entries']},{'quarantined'})
        self.assertEqual(self.apply([]),[])
    def test_unique_unknown_and_unassociated_files_remain_scope_violations(self):
        for name in ['src/a.py.bak','src/other.py.bak','temp_file.txt']:(self.work/name).write_bytes(b'unique')
        self.assertEqual(self.apply(['src/a.py.bak','src/other.py.bak','temp_file.txt']),[])
        self.assertTrue((self.work/'src/a.py.bak').exists())
    def test_symlinks_hardlinks_and_oversized_artifacts_are_not_quarantined(self):
        outside=self.root/'outside';outside.write_bytes(b'original')
        (self.work/'src/a.py.bak').symlink_to(outside)
        self.assertEqual(self.apply(['src/a.py.bak']),[])
        (self.work/'src/a.py.bak').unlink();os.link(outside,self.work/'src/a.py.bak')
        self.assertEqual(self.apply(['src/a.py.bak']),[])
        (self.work/'src/a.py.bak').unlink();(self.work/'src/a.py.bak').write_bytes(b'x'*128001)
        self.assertEqual(self.apply(['src/a.py.bak']),[])
    def test_crash_between_archive_and_journal_recovers_without_overwrite(self):
        name='src/a.py.bak';(self.work/name).write_bytes(b'original')
        archive=self.root/'quarantine'/name;archive.parent.mkdir(parents=True);archive.write_bytes(b'original')
        self.assertEqual(self.apply([name]),[name]);self.assertEqual(archive.read_bytes(),b'original')
    def test_crash_after_preservation_reconciles_or_refuses_changed_source(self):
        name='src/a.py.bak';(self.work/name).write_bytes(b'original')
        real=H.D.atomic_write
        def crash(path,*args,**kwargs):
            result=real(path,*args,**kwargs)
            if Path(path).name=='candidate-hygiene.json':raise InterruptedError('synthetic power loss')
            return result
        with mock.patch.object(H.D,'atomic_write',side_effect=crash):
            with self.assertRaises(InterruptedError):self.apply([name])
        self.assertTrue((self.work/name).exists())
        self.assertEqual(self.apply([name]),[name])
        self.assertFalse((self.work/name).exists())
    def test_unsafe_paths_and_quarantine_links_never_escape_run(self):
        with self.assertRaises(ValueError):H.read_bytes(self.work,'../outside')
        (self.root/'quarantine').symlink_to(self.root/'elsewhere')
        (self.work/'src/a.py.bak').write_bytes(b'original')
        with self.assertRaises(ValueError):self.apply(['src/a.py.bak'])

if __name__=='__main__':unittest.main()
