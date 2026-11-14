"""Narrow adapter for the existing owned MacDriveBackup file runner."""
import datetime as dt
from pathlib import Path
import time
import durability as D
import memory_coordination as M
import phase_runtime as P


class BackupYield(RuntimeError):
    pass


class Coordination:
    def __init__(self, root):
        self.root = Path(root)
        self.runtime = self.root / 'runtime'
        self.context = None
        self.record = None

    def blocked(self):
        return M.backup_blocked(self.root, dt.datetime.now(dt.timezone.utc))

    def acquire(self):
        blocked, reason = self.blocked()
        if blocked:
            return False, reason
        if not M.enabled(self.runtime):
            return True, 'coordination_disabled'
        try:
            context = M.heavy(self.runtime, time.time() + 1,
                              stopped=lambda: self.blocked()[0])
            context.__enter__()
            self.context = context
        except P.PhaseInterrupted:
            self.context = None
            return False, 'midnight_heavy_memory_busy'
        try:
            self.record = {'version': 1, 'state': 'active', 'owner': P.lease(),
                           'members': [], 'started_at': time.time()}
            self.save()
        except BaseException:
            # No child can have started before acquire returns. If publication
            # partly succeeded, record that proven empty owner where possible.
            try:
                self.close(True)
            except Exception:
                pass  # Preserve the original failure; close always releases.
            raise
        return True, 'memory_lease_acquired'

    def save(self):
        D.save_json(self.runtime / 'memory-backup-owner.json', self.record)

    def child(self, pid):
        if self.record:
            self.record['members'].append(dict(P.lease(pid), group=True))
            self.save()

    def check(self):
        if self.record:
            blocked, reason = self.blocked()
            if blocked:
                raise BackupYield(reason + '; backup remains incomplete')

    def close(self, cleanup_proven):
        try:
            if self.record:
                live = any(P.live_tree(item) for item in self.record['members'])
                self.record.update(state='drained' if cleanup_proven and not live else 'cleanup_unconfirmed',
                                   finished_at=time.time())
                try:
                    self.save()
                except BaseException:
                    # A failed final receipt cannot make the durable active
                    # owner safe to ignore, even after the flock is released.
                    self.record['state'] = 'cleanup_unconfirmed'
                    raise
        finally:
            context, self.context = self.context, None
            if context:
                context.__exit__(None, None, None)
