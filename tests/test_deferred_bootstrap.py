from contextlib import nullcontext
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import deferred_bootstrap as B


class FrozenBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.bundle = self.root / 'bundle'; self.bundle.mkdir()
        (self.bundle / 'deferred_release.py').write_text('trusted fixture')
        self.value = {'bundle': str(self.bundle), 'bundle_files': {'deferred_release.py': hashlib.sha256(b'trusted fixture').hexdigest()}}
        self.path = self.root / 'manifest.json'; self.path.write_text(json.dumps(self.value)); self.hash = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_exact_frozen_manifest_and_source_are_required(self):
        self.assertEqual(B.verify(self.path, self.hash), self.value)
        (self.bundle / 'deferred_release.py').write_text('changed')
        with self.assertRaises(ValueError):B.verify(self.path, self.hash)

    def test_unexpected_import_file_or_changed_manifest_are_rejected(self):
        (self.bundle / 'unexpected.py').write_text('untrusted')
        with self.assertRaises(ValueError):B.verify(self.path, self.hash)
        (self.bundle / 'unexpected.py').unlink(); self.path.write_text('{}')
        with self.assertRaises(ValueError):B.verify(self.path, self.hash)

    def test_links_instead_of_frozen_files_are_rejected(self):
        source = self.bundle / 'deferred_release.py'; source.unlink()
        target = self.root / 'elsewhere.py'; target.write_text('trusted fixture'); source.symlink_to(target)
        with self.assertRaises(ValueError):B.verify(self.path, self.hash)

    def test_corrupt_staged_source_can_clear_only_its_own_hold_through_pinned_host_helpers(self):
        runtime = self.root / 'runtime'; runtime.mkdir()
        for name in B.CLEANUP_NAMES:(self.root / name).write_text('fixture')
        pins = ','.join([hashlib.sha256(b'fixture').hexdigest()] * 3)
        marker = Mock(); store = Mock(); store.meta.side_effect = [{'id': 'release'}, None]
        helpers = (SimpleNamespace(marker=marker), Mock(return_value=store))
        with patch.object(B, 'CANONICAL', self.root.resolve()), patch.object(B, 'host_helpers', return_value=helpers):
            B.cleanup(self.root, 'release', pins)
        marker.assert_called_once_with(runtime.resolve() / 'INSTALLING', False)
        store.meta.assert_any_call('deferred_install_owner', {}, write=True)
        marker.reset_mock(); store.meta.side_effect = [{'id': 'other'}]
        with patch.object(B, 'CANONICAL', self.root.resolve()), patch.object(B, 'host_helpers', return_value=helpers):
            B.cleanup(self.root, 'release', pins)
        marker.assert_not_called()

    def test_changed_minimal_cleanup_code_is_never_imported(self):
        for name in B.CLEANUP_NAMES:(self.root / name).write_text('changed')
        with patch.object(B, 'CANONICAL', self.root.resolve()):
            with self.assertRaises(ValueError):B.cleanup(self.root, 'release', ','.join(['0'*64]*3))

    def test_cleanup_loads_hashed_bytes_without_stdlib_shadow_or_cached_bytecode(self):
        source = Path(B.__file__).parent
        for name in B.CLEANUP_NAMES:(self.root / name).write_bytes((source / name).read_bytes())
        pins = ','.join(hashlib.sha256((self.root / name).read_bytes()).hexdigest() for name in B.CLEANUP_NAMES)
        canary = self.root / 'canary'
        malicious = 'from pathlib import Path;Path(' + repr(str(canary)) + ').write_text("executed")\n'
        (self.root / 'uuid.py').write_text(malicious)
        original = self.root / 'durability.py'; raw = original.read_bytes(); stamp = original.stat()
        # Forge the ordinary timestamp/size cache header; the loader must ignore
        # it and compile only the already hashed, retained source bytes.
        original.write_text(malicious + '\n' + ' ' * max(0, len(raw)-len(malicious)-1)); os.utime(original, ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        py_compile.compile(str(original), doraise=True)
        original.write_bytes(raw); os.utime(original, ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        code = "import runpy;from pathlib import Path;g=runpy.run_path(__import__('sys').argv[1]);g['host_helpers'](Path(__import__('sys').argv[2]).resolve(),__import__('sys').argv[3])"
        result = subprocess.run([sys.executable,'-I','-B','-c',code,str(B.__file__),str(self.root),pins],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(canary.exists())

    def dispatch_fixture(self, proof=True, old=None, drain=True):
        import action_store, core_repair, deferred_release, phase_runtime, project_action
        root = self.root.resolve(); runtime = root / 'runtime'; runtime.mkdir(exist_ok=True)
        value = {'root':str(root),'id':'release','bundle':str(Path(B.__file__).parent),
                 'expires_at':(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=1)).isoformat(),'commit':'b'*40,
                 'validation_path':str(root/'result.json')}
        args = SimpleNamespace(root=str(root),release_id='release',manifest='fixture',sha256='0'*64)
        store = Mock(); store.jobs.return_value = [old] if old else []; store.meta.return_value = {}
        job = {'id':'new','payload':{'kind':'prepared_release','release':'release'}};store.submit.return_value=job;store.claim.return_value=True
        stack = __import__('contextlib').ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch.object(B,'CANONICAL',root));stack.enter_context(patch.object(B.sys,'path',list(B.sys.path)))
        stack.enter_context(patch.object(action_store,'Actions',return_value=store))
        stack.enter_context(patch.object(phase_runtime,'source_lock',side_effect=lambda *_a,**_k:nullcontext()))
        stack.enter_context(patch.object(phase_runtime,'lease',return_value={'pid':123,'birth_us':1}))
        stack.enter_context(patch.object(phase_runtime,'alive',return_value=False))
        stack.enter_context(patch.object(deferred_release,'file_lock',side_effect=lambda *_a,**_k:nullcontext()))
        stack.enter_context(patch.object(deferred_release,'validation_ready',return_value=proof))
        stack.enter_context(patch.object(deferred_release,'release_marker'))
        stack.enter_context(patch.object(core_repair,'permitted',return_value=True))
        command = stack.enter_context(patch.object(core_repair,'command',return_value={'code':0}))
        drained = stack.enter_context(patch.object(project_action,'drain',return_value=drain))
        return value,args,store,command,drained

    def test_waiting_gate_creates_no_job_or_host_phase(self):
        value,args,store,command,_ = self.dispatch_fixture(proof=False)
        self.assertEqual(B.dispatch(value,args),0)
        store.submit.assert_not_called();command.assert_not_called()

    def test_ready_handoff_uses_the_existing_durable_phase_guard_and_shared_host_slot(self):
        value,args,store,command,drain = self.dispatch_fixture()
        self.assertEqual(B.dispatch(value,args),0)
        self.assertEqual(command.call_args.args[1:3],('new','host'))
        self.assertEqual(command.call_args.kwargs['env']['MIDNIGHT_ACTION_ID'],'new')
        self.assertIn('deferred_release.py',command.call_args.args[3][3])
        store.claim.assert_called_once();drain.assert_called_once()

    def test_parent_loss_drains_recorded_execution_before_new_claim(self):
        old={'id':'old','project':'midnight','payload':{'kind':'prepared_release','release':'release'},'lease':{'pid':99}}
        value,args,store,command,drain = self.dispatch_fixture(old=old)
        self.assertEqual(B.dispatch(value,args),0)
        self.assertEqual(drain.call_args_list[0].args[1],'old')
        store.finish.assert_any_call('old','interrupted',{'reason':'prepared_release_parent_lost'},lease={'pid':99})
        command.assert_called_once()

    def test_undrained_prior_execution_or_foreign_maintenance_blocks_new_launch(self):
        old={'id':'old','project':'midnight','payload':{'kind':'prepared_release','release':'release'},'lease':{'pid':99}}
        value,args,store,command,drain = self.dispatch_fixture(old=old,drain=False)
        self.assertEqual(B.dispatch(value,args),0);store.submit.assert_not_called();command.assert_not_called()


if __name__ == '__main__':unittest.main()
