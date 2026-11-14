"""Private, transactional runtime checkpoints using Python's SQLite library.

SQLite commits precede replacement of the readable file. The committed snapshot
is authoritative if a crash leaves that mirror missing, stale or truncated.
Readers do not repair files; the single owning scheduler performs recovery.
"""
import datetime as dt
from contextlib import closing
import functools
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import uuid

CRITICAL_JSON = {"state.json", "session.json", "override.json", "promotion-intent.json", "model-state.json", "model-probe.json"}
APPLICATION_ID = 0x4D49444E


class CheckpointError(ValueError):
    pass


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, payload, *, durable=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            if durable:
                os.fsync(handle.fileno())
        os.replace(temporary, path)
        if durable:
            sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def database(path):
    return Path(path).parent / "checkpoints.sqlite3"


def connect(path, *, writable=False):
    db = database(path)
    if db.is_symlink():
        raise CheckpointError("Checkpoint database is a symlink")
    if writable:
        db.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(db, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        con = sqlite3.connect(db, timeout=5)
        try:
            identity = con.execute("PRAGMA application_id").fetchone()[0]
            tables = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if identity not in (0, APPLICATION_ID) or (identity == 0 and tables) or version not in (0, 1):
                raise CheckpointError("Checkpoint database has an unsupported identity or schema")
            initialized = identity == APPLICATION_ID and version == 1 and any(row[0] == "snapshots" for row in tables)
            journal = con.execute("PRAGMA journal_mode").fetchone()[0].lower()
            con.execute("PRAGMA synchronous=EXTRA")
            con.execute("PRAGMA fullfsync=ON")
            # Repair reads need a writable handle for hot-journal recovery, but
            # initialized databases must not acquire a metadata write lock.
            if not initialized:
                if identity != APPLICATION_ID:
                    con.execute("PRAGMA application_id=" + str(APPLICATION_ID))
                if version != 1:
                    con.execute("PRAGMA user_version=1")
                con.execute("CREATE TABLE IF NOT EXISTS snapshots(name TEXT, version INTEGER, payload BLOB, digest TEXT, deleted INTEGER, created TEXT, PRIMARY KEY(name,version))")
            if journal != "delete":
                con.execute("PRAGMA journal_mode=DELETE")
            if not initialized or journal != "delete":
                con.commit()
            os.chmod(db, 0o600)
            sync_directory(db.parent)
            return con
        except BaseException:
            con.close()
            raise
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        if con.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID or con.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise CheckpointError("Checkpoint database has an unsupported identity or schema")
        return con
    except BaseException:
        con.close()
        raise


def snapshot(path, *, writable=False):
    path = Path(path)
    if not writable and not database(path).exists():
        return None
    try:
        with closing(connect(path, writable=writable)) as con:
            rows = con.execute("SELECT version,payload,digest,deleted FROM snapshots WHERE name=? ORDER BY version DESC LIMIT 2", (path.name,)).fetchall()
        for version, payload, digest, deleted in rows:
            if isinstance(payload, bytes) and hashlib.sha256(payload).hexdigest() == digest and deleted in (0, 1):
                return {"version": version, "payload": payload, "deleted": bool(deleted), "latest": version == rows[0][0]}
        if rows:
            raise CheckpointError("No verified checkpoint generation for " + path.name)
        return None
    except sqlite3.Error as exc:
        raise CheckpointError("Checkpoint database unavailable: " + type(exc).__name__) from exc


def commit_snapshot(path, payload, *, deleted=False):
    path = Path(path)
    try:
        with closing(connect(path, writable=True)) as con:
            con.execute("BEGIN IMMEDIATE")
            version = con.execute("SELECT COALESCE(MAX(version),0)+1 FROM snapshots WHERE name=?", (path.name,)).fetchone()[0]
            con.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?)", (path.name, version, payload, hashlib.sha256(payload).hexdigest(), int(deleted), dt.datetime.now(dt.timezone.utc).isoformat()))
            con.execute("DELETE FROM snapshots WHERE name=? AND version<?", (path.name, version - 1))
            con.commit()
        sync_directory(path.parent)
    except sqlite3.Error as exc:
        raise CheckpointError("Cannot commit runtime checkpoint: " + type(exc).__name__) from exc


def save_bytes(path, payload):
    if Path(path).is_symlink():
        raise CheckpointError("Runtime mirror is a symlink")
    commit_snapshot(path, payload)
    atomic_write(path, payload)


def read_bytes(path, default=None, *, repair=False):
    path = Path(path)
    if path.name == "sessions.csv" and (path.parent/"session-history.sqlite3").exists():
        from session_history import Sessions, encode
        ledger=Sessions(path.parent)
        if repair:ledger.export()
        return encode(ledger.rows(),True)
    if path.is_symlink():
        raise CheckpointError("Runtime mirror is a symlink")
    saved = snapshot(path, writable=repair)
    if saved is None:
        try:
            value = path.read_bytes()
        except FileNotFoundError:
            return default
        if repair:
            save_bytes(path, value)
        return value
    expected = None if saved["deleted"] else saved["payload"]
    try:
        actual = path.read_bytes()
    except FileNotFoundError:
        actual = None
    if repair and actual != expected:
        if actual is not None:
            archive = path.with_name(path.name + ".recovered-" + uuid.uuid4().hex)
            os.replace(path, archive)
            os.chmod(archive, 0o600)
            sync_directory(path.parent)
        if expected is not None:
            atomic_write(path, expected)
    if repair and not saved["latest"]:
        commit_snapshot(path, saved["payload"], deleted=saved["deleted"])
    return default if expected is None else expected


def read_json(path, default=None, *, repair=False):
    if repair:
        snapshot(path, writable=True)  # Roll back a hot SQLite journal first.
    value = read_bytes(path, None)
    if value is None:
        if repair:
            read_bytes(path, None, repair=True)
        return {} if default is None else default
    result = json.loads(value)
    if repair:
        read_bytes(path, None, repair=True)
    return result


def save_json(path, value):
    payload = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
    save_bytes(path, payload)


def delete(path):
    path = Path(path)
    commit_snapshot(path, b"", deleted=True)
    path.unlink(missing_ok=True)
    sync_directory(path.parent)


def health(path):
    path = Path(path)
    if path.name == "sessions.csv" and (path.parent/"session-history.sqlite3").exists():
        from session_history import Sessions
        return Sessions(path.parent).health()
    try:
        if path.is_symlink():
            raise CheckpointError("Runtime mirror is a symlink")
        saved = snapshot(path)
        if saved is None:
            if path.exists() and path.name in CRITICAL_JSON:
                json.loads(path.read_bytes())
            return {"file": path.name, "status": "legacy" if path.exists() else "missing"}
        expected = None if saved["deleted"] else saved["payload"]
        actual = path.read_bytes() if path.exists() else None
        return {"file": path.name, "status": "healthy" if actual == expected and saved["latest"] else "repair_needed", "generation": saved["version"], "deleted": saved["deleted"]}
    except (OSError, ValueError) as exc:
        return {"file": path.name, "status": "unavailable", "detail": str(exc)}


def marker(path, enabled):
    path = Path(path)
    if enabled:
        atomic_write(path, b"{}\n")
    else:
        path.unlink(missing_ok=True)
        sync_directory(path.parent)


@functools.lru_cache(maxsize=1)
def boot_id():
    try:
        value = subprocess.check_output(["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"], text=True, stderr=subprocess.DEVNULL).strip()
        return str(uuid.UUID(value))
    except (OSError, ValueError, subprocess.CalledProcessError):
        try:
            return str(uuid.UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()))
        except (OSError, ValueError) as exc:
            raise CheckpointError("Cannot establish this boot's process identity") from exc
