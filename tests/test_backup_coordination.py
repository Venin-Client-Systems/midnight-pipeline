"""Temporary ownership receipts; no backup process or shared memory lease."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import backup_coordination as B
import durability as D


class BackupCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.coordinator = B.Coordination(self.root)
        self.context = MagicMock()

    def test_activation_gate_blocks_before_coordination_is_enabled(self):
        gate=self.coordinator.runtime/'WEEKDAY_ACTIVATION_PENDING';gate.parent.mkdir()
        for linked in (False,True):
            with self.subTest(linked=linked):
                if linked:gate.symlink_to(self.root/'missing')
                else:gate.write_text('owned activation pending')
                with patch.object(B.M,'heavy') as heavy:
                    self.assertEqual(self.coordinator.acquire(),(False,'midnight_activation_pending'))
                heavy.assert_not_called();gate.unlink()

    def test_disabled_coordination_without_gate_keeps_backup_allowed(self):
        self.assertEqual(self.coordinator.acquire(),(True,'coordination_disabled'))

    def test_failed_initial_receipt_releases_context_without_starting_child(self):
        with patch.object(B.M, 'enabled', return_value=True), \
             patch.object(self.coordinator, 'blocked', return_value=(False, 'outside_window')), \
             patch.object(B.M, 'heavy', return_value=self.context), \
             patch.object(B.P, 'lease', return_value={'pid': 123, 'birth_us': 1}), \
             patch.object(self.coordinator, 'save', side_effect=OSError('receipt unavailable')):
            with self.assertRaisesRegex(OSError, 'receipt unavailable'):
                self.coordinator.acquire()
        self.context.__exit__.assert_called_once()
        self.assertIsNone(self.coordinator.context)
        self.assertEqual(self.coordinator.record['state'], 'cleanup_unconfirmed')
        self.assertEqual(self.coordinator.record['members'], [])

    def test_failed_final_receipt_keeps_durable_owner_uncertain_and_releases_context(self):
        self.coordinator.context = self.context
        original = {'state': 'active', 'owner': {'boot_id': 'same-boot'}, 'members': []}
        self.coordinator.record = dict(original); self.coordinator.save()
        with patch.object(self.coordinator, 'save', side_effect=OSError('receipt unavailable')):
            with self.assertRaises(OSError):self.coordinator.close(True)
        self.context.__exit__.assert_called_once()
        self.assertIsNone(self.coordinator.context)
        self.assertEqual(D.read_json(self.coordinator.runtime / 'memory-backup-owner.json'), original)
        with patch.object(B.M.P, 'Actions') as actions, patch.object(D, 'boot_id', return_value='same-boot'):
            actions.return_value.executions.return_value = []
            self.assertTrue(B.M.surviving_execution(self.coordinator.runtime))

    def test_failed_member_observation_still_releases_context(self):
        self.coordinator.context = self.context
        self.coordinator.record = {'state': 'active', 'members': [{'pid': 123}]}
        self.coordinator.save()
        with patch.object(B.P, 'live_tree', side_effect=OSError('unavailable')):
            with self.assertRaises(OSError):self.coordinator.close(True)
        self.context.__exit__.assert_called_once()
        self.assertEqual(D.read_json(self.coordinator.runtime / 'memory-backup-owner.json')['state'], 'active')

    def test_proven_empty_owner_is_drained_and_close_does_not_release_twice(self):
        self.coordinator.context = self.context
        self.coordinator.record = {'state': 'active', 'members': []}
        self.coordinator.close(True); self.coordinator.close(True)
        self.context.__exit__.assert_called_once()
        self.assertEqual(D.read_json(self.coordinator.runtime / 'memory-backup-owner.json')['state'], 'drained')


if __name__ == '__main__':unittest.main()
