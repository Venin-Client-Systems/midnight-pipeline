from contextlib import nullcontext
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import deferred_release as R
import durability as D
import install_guardian


class DeferredReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.runtime = self.root / 'runtime'; self.directory = self.runtime / 'deferred-releases/test'
        self.directory.mkdir(parents=True)
        project = self.runtime / 'projects/fixture'; project.mkdir(parents=True)
        (self.runtime / 'controller.lock').touch(); (project / 'supervisor.lock').touch()
        (self.root / 'projects.json').write_text(json.dumps({'projects': [{'id': 'fixture'}]}))
        (self.root / 'midnight.py').write_text('fixture controller'); (self.root / 'dashboard.py').write_text('fixture dashboard')
        self.before = 'a' * 40; self.commit = 'b' * 40; self.tree = 'c' * 40; self.head = self.before
        self.now = dt.datetime.now(dt.timezone.utc)
        self.value = {'id': 'test', 'before': self.before, 'commit': self.commit, 'tree': self.tree,
                      'expires_at': (self.now + dt.timedelta(days=1)).isoformat()}
        for kind in ('observer', 'monitor'):
            p = self.root / (kind + '.json'); p.write_text('{}')
            self.value[kind + '_path'] = str(p); self.value[kind + '_sha256'] = R.digest(p)
        self.proof = {'state': 'passed', 'continuous_healthy_seconds': 14400, 'required_seconds': 14400,
                      'maximum_resource_ownership': {'local': 1, 'host': 1, 'cloud': 2},
                      'latest': {'actual_commit': self.before, 'expected_commit': self.before, 'installed_commit': self.before,
                                 'installation': 'verified', 'issues': []}}
        self.proof_path = self.root / 'result.json'; self.proof_path.write_text(json.dumps(self.proof)); self.value['validation_path'] = str(self.proof_path)
        self.metadata = {'install_health': {'installed': self.before, 'state': 'verified'}}
        self.store = Mock(); self.store.jobs.return_value = []; self.store.executions.return_value = []
        def meta(key, value=None, *, write=False):
            if write:self.metadata[key] = value
            return self.metadata.get(key)
        self.store.meta.side_effect = meta
        self.calls = []
        def git(_root, *args):
            self.calls.append(args)
            if args == ('rev-parse', 'HEAD'):return self.head
            if args == ('status', '--porcelain'):return ''
            if args == ('rev-parse', self.commit + '^{tree}'):return self.tree
            if args == ('merge', '--ff-only', self.commit):self.head = self.commit
            return ''
        for obj, attr, kwargs in [
            (R, 'Actions', {'return_value': self.store}), (R, 'git', {'side_effect': git}),
            (R.P, 'source_lock', {'side_effect': lambda *_a, **_k: nullcontext()}),
            (R.GU, 'window', {'return_value': True}), (R, 'check_authority', {'return_value': {}}),
            (R.subprocess, 'run', {'return_value': subprocess.CompletedProcess([], 0)}),
            (install_guardian, 'install', {'return_value': {}}),
            (R.GU, 'restart_controller', {'return_value': True}), (R.GU, 'restart_dashboard', {'return_value': True})]:
            patcher = patch.object(obj, attr, **kwargs); patcher.start(); self.addCleanup(patcher.stop)

    def tick(self):return R.tick(self.root, self.value, self.directory, instant=self.now)

    def test_validation_result_is_required_before_any_stop_or_source_mutation(self):
        self.proof_path.unlink()
        self.assertEqual(self.tick()['state'], 'waiting_validation')
        self.assertFalse((self.runtime / 'INSTALLING').exists())
        R.subprocess.run.assert_not_called(); R.GU.restart_controller.assert_not_called()

    def test_four_hour_proof_must_match_source_and_real_resource_caps(self):
        self.assertTrue(R.validation_ready(self.value, self.proof))
        for key, value in [('continuous_healthy_seconds', 14399), ('required_seconds', 10)]:
            proof = {**self.proof, key: value}
            with self.assertRaises(ValueError):R.validation_ready(self.value, proof)
        with self.assertRaises(ValueError):
            R.validation_ready(self.value, {**self.proof, 'latest': {**self.proof['latest'], 'actual_commit': self.commit}})
        with self.assertRaises(ValueError):
            R.validation_ready(self.value, {**self.proof, 'maximum_resource_ownership': {'local': 2, 'host': 1, 'cloud': 2}})

    def test_busy_owned_work_drains_without_termination(self):
        self.store.jobs.return_value = [{'id':'foreign','project': 'fixture'}]
        result = self.tick()
        self.assertEqual(result['state'], 'draining')
        self.assertTrue((self.runtime / 'INSTALLING').exists())
        R.subprocess.run.assert_not_called()
        self.assertEqual(self.head, self.before)

    def test_retained_execution_blocks_adoption_even_without_a_job(self):
        self.store.executions.return_value = [{'phase':'retained','job':'foreign'}]
        self.assertEqual(self.tick()['state'], 'draining')
        R.subprocess.run.assert_not_called()

    def test_only_matching_live_prepared_action_is_excluded_from_idle_ownership(self):
        owned = {'id':'mine','state':'running','payload':{'kind':'prepared_release','release':'test'},'lease':{'pid':123}}
        self.store.get.return_value = owned
        self.store.jobs.return_value = [owned]
        self.store.executions.return_value = [{'phase':'own','job':'mine'}]
        with patch.dict(R.os.environ,{'MIDNIGHT_ACTION_ID':'mine'}),patch.object(R.P,'alive',return_value=True):
            self.assertEqual(self.tick()['state'],'verifying_health')
        self.assertEqual(self.head,self.commit)
        D.save_json(self.directory/'state.json',{});self.head=self.before
        owned['payload']['release']='other'
        with patch.dict(R.os.environ,{'MIDNIGHT_ACTION_ID':'mine'}),patch.object(R.P,'alive',return_value=True):
            with self.assertRaises(ValueError):self.tick()

    def test_pause_preserves_owner_control_and_releases_only_own_install_marker(self):
        D.marker(self.runtime / 'PAUSED', True); D.marker(self.runtime / 'INSTALLING', True)
        self.metadata['deferred_install_owner'] = {'id': 'test', 'since': 1}
        self.assertEqual(self.tick()['state'], 'waiting_window')
        self.assertTrue((self.runtime / 'PAUSED').exists())
        self.assertFalse((self.runtime / 'INSTALLING').exists())
        R.subprocess.run.assert_not_called()

    def test_other_installer_marker_is_not_cleared_or_adopted(self):
        D.marker(self.runtime / 'INSTALLING', True)
        self.assertEqual(self.tick()['state'], 'waiting_installer')
        self.assertTrue((self.runtime / 'INSTALLING').exists())
        R.subprocess.run.assert_not_called()

    def test_exact_reviewed_source_adopts_once_and_waits_for_live_health(self):
        self.assertEqual(self.tick()['state'], 'verifying_health')
        self.assertEqual(self.head, self.commit)
        self.assertFalse((self.runtime / 'INSTALLING').exists())
        self.assertEqual(self.tick()['state'], 'verifying_health')
        self.assertEqual(self.calls.count(('merge', '--ff-only', self.commit)), 1)
        self.metadata['install_health']['state'] = 'verified'
        with patch.object(R, 'sensor_health', return_value={'passed': True, 'fans': [{'rpm': 0}]}):
            self.assertEqual(self.tick()['state'], 'complete')
        self.assertEqual(self.tick()['state'], 'complete')
        self.assertEqual(self.calls.count(('merge', '--ff-only', self.commit)), 1)

    def test_crash_after_checkout_replays_restarts_without_reapplying_source(self):
        self.assertEqual(self.tick()['state'], 'verifying_health')
        saved = D.read_json(self.directory / 'state.json')
        saved['state'] = 'adopting'
        saved.pop('guardian_installed', None)
        D.save_json(self.directory / 'state.json', saved)
        D.delete(self.directory / 'restart-receipt.json')
        R.GU.restart_controller.reset_mock(); R.GU.restart_dashboard.reset_mock()
        self.head = self.commit
        D.marker(self.runtime / 'INSTALLING', True)
        self.metadata['deferred_install_owner'] = {'id': 'test', 'since': 1}
        self.assertEqual(self.tick()['state'], 'verifying_health')
        self.assertEqual(self.calls.count(('merge', '--ff-only', self.commit)), 1)
        R.GU.restart_controller.assert_called_once(); R.GU.restart_dashboard.assert_called_once()

    def test_failed_restart_replays_without_overwriting_source(self):
        R.GU.restart_dashboard.return_value = False
        with self.assertRaises(RuntimeError):self.tick()
        self.assertEqual(self.head, self.commit)
        self.assertTrue((self.runtime / 'INSTALLING').exists())
        R.GU.restart_dashboard.return_value = True
        self.assertEqual(self.tick()['state'], 'verifying_health')
        self.assertFalse((self.runtime / 'INSTALLING').exists())
        self.assertEqual(self.calls.count(('merge', '--ff-only', self.commit)), 1)
        self.assertEqual(R.GU.restart_controller.call_count, 1)

    def test_restart_timeout_preserves_successful_peer_and_retries_only_missing_request(self):
        R.GU.restart_dashboard.side_effect = [subprocess.TimeoutExpired('restart', 10), True]
        with self.assertRaises(subprocess.TimeoutExpired):self.tick()
        receipt = D.read_json(self.directory / 'restart-receipt.json')
        self.assertEqual(receipt['requests']['controller']['state'], 'confirmed')
        with patch.object(R, 'service_started', return_value=False):
            self.assertEqual(self.tick()['state'], 'verifying_health')
        self.assertEqual(R.GU.restart_controller.call_count, 1)
        self.assertEqual(R.GU.restart_dashboard.call_count, 2)

    def test_unknown_restart_intent_reconciles_live_service_without_duplicate_request(self):
        R.GU.restart_dashboard.side_effect = subprocess.TimeoutExpired('restart', 10)
        with self.assertRaises(subprocess.TimeoutExpired):self.tick()
        with patch.object(R, 'service_started', return_value=True):
            self.assertEqual(self.tick()['state'], 'verifying_health')
        self.assertEqual(R.GU.restart_controller.call_count, 1)
        self.assertEqual(R.GU.restart_dashboard.call_count, 1)

    def test_fresh_heartbeat_from_old_controller_does_not_confirm_restart_intent(self):
        after = self.now.timestamp()
        status = {'updated_at': (self.now + dt.timedelta(seconds=1)).isoformat(),
                  'code_sha256': R.digest(self.root / 'midnight.py'),
                  'process': {'pid': 123, 'birth_us': int((after-3600)*1e6)}}
        D.save_json(self.runtime / 'status.json', status)
        with patch.object(R.P, 'alive', return_value=True):
            self.assertFalse(R.service_started(self.root, 'controller', after))
            status['process']['birth_us'] = int((after+.1)*1e6)
            D.save_json(self.runtime / 'status.json', status)
            self.assertTrue(R.service_started(self.root, 'controller', after))

    def test_new_head_without_pre_mutation_proof_intent_cannot_skip_gate(self):
        self.head = self.commit; self.proof_path.unlink()
        self.assertEqual(self.tick()['state'], 'held')
        R.GU.restart_controller.assert_not_called(); R.GU.restart_dashboard.assert_not_called()

    def test_expiry_during_idle_lock_acquisition_prevents_merge_and_cleans_hold(self):
        def stop(*_args, **_kwargs):
            self.value['expires_at'] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)).isoformat()
            return subprocess.CompletedProcess([], 0)
        R.subprocess.run.side_effect = stop
        with self.assertRaises(R.P.PhaseInterrupted):self.tick()
        self.assertEqual(self.head, self.before)
        self.assertFalse((self.runtime / 'INSTALLING').exists())

    def test_terminal_and_revoked_proof_paths_release_their_own_prior_hold(self):
        for condition in ('expiry', 'source', 'observer', 'deleted_proof', 'wrong_proof'):
            with self.subTest(condition=condition):
                self.head = self.before
                self.value['expires_at'] = (self.now + dt.timedelta(days=1)).isoformat()
                Path(self.value['monitor_path']).write_text('{}')
                self.proof_path.write_text(json.dumps(self.proof))
                D.save_json(self.directory / 'state.json', {})
                self.store.jobs.return_value = [{'id':'foreign','project': 'fixture'}]
                self.assertEqual(self.tick()['state'], 'draining')
                if condition == 'expiry':self.value['expires_at'] = (self.now - dt.timedelta(seconds=1)).isoformat()
                elif condition == 'source':self.head = 'd' * 40
                elif condition == 'observer':Path(self.value['monitor_path']).write_text('changed')
                elif condition == 'deleted_proof':self.proof_path.unlink()
                else:self.proof_path.write_text(json.dumps({**self.proof, 'continuous_healthy_seconds': 5}))
                self.tick()
                self.assertFalse((self.runtime / 'INSTALLING').exists())
                self.assertEqual(self.metadata['deferred_install_owner'], {})

    def test_native_health_failure_has_bounded_guardian_rollback_without_reapplying(self):
        self.assertEqual(self.tick()['state'], 'verifying_health')
        self.metadata['install_health'].update(state='verified', at=self.now.timestamp()-361)
        with patch.object(R, 'sensor_health', side_effect=OSError('unavailable')):
            self.assertEqual(self.tick()['state'], 'rollback_requested')
        self.assertEqual(self.metadata['install_health']['state'], 'rolling_back')
        self.assertEqual(self.tick()['state'], 'rollback_requested')
        self.metadata['install_health']['state'] = 'rolled_back'; self.head = self.before
        self.assertEqual(self.tick()['state'], 'rolled_back')
        self.assertEqual(self.calls.count(('merge', '--ff-only', self.commit)), 1)

    def test_source_advance_and_rollback_are_held_without_overwrite(self):
        self.head = 'd' * 40
        self.assertEqual(self.tick()['state'], 'held')
        R.subprocess.run.assert_not_called()
        D.save_json(self.directory / 'state.json', {'state': 'verifying_health'})
        self.head = self.before
        self.assertEqual(self.tick()['state'], 'rolled_back')
        self.assertEqual(self.tick()['state'], 'rolled_back')
        self.assertNotIn(('merge', '--ff-only', self.commit), self.calls)

    def test_expired_release_and_changed_observer_do_not_install(self):
        self.value['expires_at'] = (self.now - dt.timedelta(seconds=1)).isoformat()
        self.assertEqual(self.tick()['state'], 'held')
        R.subprocess.run.assert_not_called()
        D.save_json(self.directory / 'state.json', {})
        self.value['expires_at'] = (self.now + dt.timedelta(days=1)).isoformat()
        Path(self.value['monitor_path']).write_text('changed')
        self.assertEqual(self.tick()['state'], 'held')

    def test_completion_record_retires_personal_outbox_and_preserves_uncertain_evidence(self):
        # The retired personal-send path stays retired; this supersedes the old
        # requirement. Preserve its original operation/version as history.
        self.value['completion_record'] = {'taskId': 'task.midnight-temperature-fans-20261101', 'expectedVersion': 4,
                                            'title': 'Show Mac temperature and fan speed in Midnight', 'domainId': 'purchases_technology',
                                            'source': {'kind': 'codex_operator', 'ref': 'record.fixture'}}
        request = {**self.value['completion_record'], 'operationId': 'original-uncertain'}
        D.save_json(self.directory / 'operating-picture-request.json', request)
        D.save_json(self.directory / 'operating-picture-result.json', {'status': 'uncertain'})
        before = {p.name: p.read_bytes() for p in self.directory.glob('operating-picture-*.json')}
        with patch.object(R.subprocess, 'run', side_effect=AssertionError('VENIN never sends personal records')) as writer:
            first = R.completion_record(self.value, self.directory)
            self.assertEqual(R.completion_record(self.value, self.directory), first)
            self.assertEqual(first['status'], 'retired_personal_reporting')
            writer.assert_not_called()
        self.assertEqual(D.read_json(self.directory / 'operating-picture-request.json')['expectedVersion'], 4)
        self.assertEqual({p.name: p.read_bytes() for p in self.directory.glob('operating-picture-*.json')}, before)


if __name__ == '__main__':unittest.main()
