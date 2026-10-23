"""Private SQLite job ledger and append-only, hash-linked protocol audit."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading

CONTEXT = ContextVar("minion_audit", default={})
_stores = {}
_lock = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@contextmanager
def context(**values):
    token = CONTEXT.set({**CONTEXT.get(), **values})
    try:
        yield
    finally:
        CONTEXT.reset(token)


def store_for(config):
    path = config.get("_state_dir")
    if not path:
        return None
    with _lock:
        if path not in _stores:
            _stores[path] = Store(Path(path))
        return _stores[path]


def event(config, source, target, kind, summary, **details):
    store = store_for(config)
    if store:
        ctx = CONTEXT.get()
        return store.event(source, target, kind, summary, job=ctx.get("job"),
                           owner=ctx.get("owner", os.environ.get("CODEX_THREAD_ID", "manual")),
                           request_id=details.pop("request_id", None), details=details)


class Store:
    def __init__(self, root):
        self.root = Path(root)
        if self.root.is_symlink():
            raise ValueError("Controller state directory must not be a symlink")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.path = self.root / "controller.sqlite3"
        if self.path.is_symlink():
            raise ValueError("Controller database must not be a symlink")
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL, metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    remote TEXT NOT NULL DEFAULT '{}', error TEXT,
                    managed INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, kind TEXT NOT NULL,
                    event_key TEXT UNIQUE, payload TEXT NOT NULL, previous TEXT NOT NULL,
                    hash TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_job ON events(job,seq);
                CREATE TRIGGER IF NOT EXISTS immutable_events_update BEFORE UPDATE ON events
                    BEGIN SELECT RAISE(ABORT,'Audit events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_events_delete BEFORE DELETE ON events
                    BEGIN SELECT RAISE(ABORT,'Audit events are append-only'); END;
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def event(self, source, target, kind, summary, *, job=None, owner=None,
              request_id=None, details=None, event_key=None, observed_at=None, db=None):
        payload = {"at": now(), "job": job, "owner": owner, "source": source,
                   "target": target, "kind": kind, "summary": summary,
                   "request_id": request_id, "details": details or {}}
        if observed_at:
            payload["worker_at"] = observed_at
        def insert(conn):
            if event_key and conn.execute("SELECT 1 FROM events WHERE event_key=?", (event_key,)).fetchone():
                return
            last = conn.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            previous = last[0] if last else "0" * 64
            raw = canonical(payload)
            hashed = hashlib.sha256((previous + "\n" + raw).encode()).hexdigest()
            conn.execute("INSERT INTO events(job,kind,event_key,payload,previous,hash) VALUES(?,?,?,?,?,?)",
                         (job, kind, event_key, raw, previous, hashed))
        if db is not None:
            insert(db)
        else:
            with self.db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                insert(conn)

    def enqueue(self, metadata):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO jobs(id,state,metadata,created_at,updated_at) VALUES(?,?,?,?,?)",
                       (metadata["id"], "queued", canonical(metadata), metadata["created_at"], now()))
            self.event("sender", "controller", "job.requested", metadata["title"],
                       job=metadata["id"], owner=metadata["owner"],
                       details={"description": metadata["description"], "snapshot": metadata["snapshot"],
                                "script_sha256": metadata["script_sha256"]}, db=db)
            self.event("controller", "sender", "job.queued", "Job saved in the queue", job=metadata["id"], db=db)

    def job(self, job):
        with self.db() as db:
            row = db.execute("SELECT rowid AS queue_order,* FROM jobs WHERE id=?", (job,)).fetchone()
        return self.unpack(row) if row else None

    @staticmethod
    def unpack(row):
        return {**dict(row), "metadata": json.loads(row["metadata"]), "remote": json.loads(row["remote"])}

    def jobs(self):
        with self.db() as db:
            rows = db.execute("SELECT rowid AS queue_order,* FROM jobs ORDER BY rowid").fetchall()
        return [self.unpack(row) for row in rows]

    def transition(self, job, state, *, remote=None, error=None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
            if not row:
                return
            db.execute("UPDATE jobs SET state=?,updated_at=?,remote=?,error=? WHERE id=?",
                       (state, now(), canonical(remote) if remote is not None else row["remote"], error, job))
            if row["state"] != state:
                self.event("controller", "sender", "job." + state, "Job " + state,
                           job=job, details={"from": row["state"], "to": state}, db=db)

    def claim(self):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE state='queued' AND managed=1 ORDER BY rowid LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET state='dispatching',updated_at=? WHERE id=?", (now(), row["id"]))
                self.event("controller", "worker", "job.dispatching", "Dispatching the next queued job", job=row["id"], db=db)
                return self.unpack(row)

    def cancel(self, job):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE jobs SET state='cancelled',updated_at=? WHERE id=? AND state='queued'",
                                 (now(), job)).rowcount
            if not changed:
                raise ValueError("Only a queued, undispatched job can be cancelled")
            self.event("sender", "controller", "job.cancelled", "Sender cancelled the queued job", job=job, db=db)

    def import_job(self, metadata, remote, state):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("INSERT OR IGNORE INTO jobs(id,state,metadata,created_at,updated_at,remote,managed) VALUES(?,?,?,?,?,?,0)",
                (metadata["id"], state, canonical(metadata), metadata["created_at"], now(), canonical(remote))).rowcount
            if changed:
                self.event("worker", "controller", "job.imported", "Existing worker job discovered; earlier messages unavailable",
                           job=metadata["id"], details={"state": state}, db=db)

    def state(self, key, value=None):
        with self.db() as db:
            if value is not None:
                db.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", (key, canonical(value)))
                return value
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def events(self, job=None, limit=200, before=None):
        query, args = "SELECT * FROM events WHERE 1=1", []
        if job:
            query += " AND job=?"
            args.append(job)
        if before:
            query += " AND seq<?"
            args.append(before)
        query += " ORDER BY seq DESC LIMIT ?"
        args.append(min(max(limit, 1), 1000))
        with self.db() as db:
            rows = db.execute(query, args).fetchall()
        return [{**json.loads(r["payload"]), "seq": r["seq"], "hash": r["hash"], "previous": r["previous"]} for r in reversed(rows)]

    def verify(self, export=None):
        previous, count = "0" * 64, 0
        with self.db() as db:
            for row in db.execute("SELECT * FROM events ORDER BY seq"):
                count += 1
                expected = hashlib.sha256((previous + "\n" + row["payload"]).encode()).hexdigest()
                if row["seq"] != count or row["previous"] != previous or row["hash"] != expected:
                    raise ValueError("Audit chain mismatch at event " + str(row["seq"]))
                if export:
                    export.write(canonical({**json.loads(row["payload"]), "seq": row["seq"], "hash": row["hash"], "previous": previous}) + "\n")
                previous = row["hash"]
        return {"valid": True, "events": count, "head_sha256": previous,
                "scope": "Local integrity chain; not externally anchored or tamper-proof"}
