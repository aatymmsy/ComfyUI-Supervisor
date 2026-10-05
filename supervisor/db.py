from __future__ import annotations

import sqlite3
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .models import dump_json


def uid() -> str:
    return uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS discussion_sessions(id TEXT PRIMARY KEY REFERENCES tasks(id), title TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discussion_turns(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES discussion_sessions(id),
 request_id TEXT NOT NULL UNIQUE, message TEXT NOT NULL, reply TEXT, state TEXT NOT NULL, error_code TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS review_localizations(evaluation_id TEXT NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
 language TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(evaluation_id,language));
CREATE TABLE IF NOT EXISTS result_batch_requests(request_id TEXT PRIMARY KEY, source_task TEXT NOT NULL,
 source_asset TEXT NOT NULL, fingerprint TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runtime_state(name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS generation_metadata(generation_id TEXT PRIMARY KEY, workflow_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS caption_jobs(task_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, node_id TEXT NOT NULL,
 graph TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'PREPARED', prompt_id TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS task_configs(task_id TEXT PRIMARY KEY, workflow_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_runtime(task_id TEXT PRIMARY KEY, elapsed REAL NOT NULL DEFAULT 0, running_since REAL);
CREATE TABLE IF NOT EXISTS user_deletions(asset_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, files TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS asset_sources(asset_id TEXT PRIMARY KEY, source_path TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, state TEXT NOT NULL, phase TEXT NOT NULL,
 settings TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 lease_owner TEXT, lease_until REAL, state_version INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS groups(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 ordinal INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'OPEN', round_index INTEGER NOT NULL DEFAULT 0,
 best_score REAL NOT NULL DEFAULT 0, stale_rounds INTEGER NOT NULL DEFAULT 0,
 UNIQUE(task_id, ordinal));
CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 group_id TEXT REFERENCES groups(id), generation_id TEXT, source_kind TEXT NOT NULL,
 path TEXT NOT NULL, thumb_path TEXT, sha256 TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
 metadata TEXT NOT NULL, content_label TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'AVAILABLE',
 protected INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS assets_hash ON assets(task_id, sha256);
CREATE TABLE IF NOT EXISTS style_cards(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 version INTEGER NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prompt_variants(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 group_id TEXT NOT NULL REFERENCES groups(id), parent_id TEXT, round_index INTEGER NOT NULL,
 body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS generations(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 group_id TEXT NOT NULL REFERENCES groups(id), variant_id TEXT NOT NULL REFERENCES prompt_variants(id),
 submission_token TEXT NOT NULL UNIQUE, prompt_id TEXT, client_id TEXT NOT NULL,
 state TEXT NOT NULL, graph TEXT NOT NULL, graph_hash TEXT NOT NULL, outputs TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS generation_outputs(generation_id TEXT NOT NULL REFERENCES generations(id),
 asset_id TEXT NOT NULL REFERENCES assets(id), node_id TEXT NOT NULL, output_index INTEGER NOT NULL,
 submitted_seed INTEGER NOT NULL, actual_seed INTEGER, identity TEXT NOT NULL,
 PRIMARY KEY(generation_id, identity));
CREATE TABLE IF NOT EXISTS evaluations(id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
 stage TEXT NOT NULL, body TEXT NOT NULL, effective_score REAL, call_id TEXT,
 rubric_version TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
 evaluation_id TEXT REFERENCES evaluations(id), action TEXT NOT NULL, reason TEXT NOT NULL,
 approved_by TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS decision_asset ON decisions(asset_id, created_at);
CREATE TABLE IF NOT EXISTS file_operations(id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
 kind TEXT NOT NULL, source TEXT NOT NULL, target TEXT, expected_hash TEXT NOT NULL,
 state TEXT NOT NULL, restore_until TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS model_catalog(provider_id TEXT NOT NULL, credential_id TEXT NOT NULL,
 model_id TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL, refreshed_at TEXT NOT NULL,
 PRIMARY KEY(provider_id, credential_id, model_id));
CREATE TABLE IF NOT EXISTS api_calls(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 purpose TEXT NOT NULL, provider_id TEXT NOT NULL, model_id TEXT NOT NULL, credential_id TEXT NOT NULL,
 status TEXT NOT NULL, usage TEXT, actual_cost INTEGER, pricing TEXT NOT NULL,
 error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS budget_reservations(call_id TEXT PRIMARY KEY REFERENCES api_calls(id),
 task_id TEXT NOT NULL REFERENCES tasks(id), amount INTEGER NOT NULL,
 state TEXT NOT NULL, settled INTEGER);
CREATE TABLE IF NOT EXISTS summaries(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 group_id TEXT NOT NULL REFERENCES groups(id), round_index INTEGER NOT NULL, body TEXT NOT NULL,
 source_ids TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS human_feedback(id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
 kind TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT REFERENCES tasks(id),
 kind TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 status TEXT NOT NULL, manifest_path TEXT NOT NULL, sheet_path TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS delivery_items(delivery_id TEXT NOT NULL REFERENCES deliveries(id),
 group_id TEXT NOT NULL REFERENCES groups(id), asset_id TEXT NOT NULL REFERENCES assets(id),
 ordinal INTEGER NOT NULL, PRIMARY KEY(delivery_id, asset_id));
"""


class BudgetExceeded(Exception):
    pass


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT OR IGNORE INTO schema_migrations VALUES(1,?)", (now(),))

    @contextmanager
    def transaction(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    def execute(self, sql: str, args=()):
        with self.lock:
            return self.conn.execute(sql, args)

    def rows(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def event(self, task_id, kind, body):
        self.execute("INSERT INTO events(task_id,kind,body,created_at) VALUES(?,?,?,?)", (task_id, kind, dump_json(body), now()))

    def transition(self, task_id, state, phase, reason=None):
        with self.transaction() as c:
            existing = c.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()
            if existing and existing[0] == "STOPPING" and state in ("RUNNING", "WAITING_APPROVAL"):
                return
            c.execute("UPDATE tasks SET state=?,phase=?,reason=?,updated_at=?,state_version=state_version+1 WHERE id=?", (state, phase, reason, now(), task_id))
            c.execute("INSERT INTO events(task_id,kind,body,created_at) VALUES(?,?,?,?)", (task_id, "STATE", dump_json({"state": state, "phase": phase, "reason": reason}), now()))

    def claim(self, task_id, owner, seconds=120) -> bool:
        import time
        with self.transaction() as c:
            result = c.execute("UPDATE tasks SET lease_owner=?,lease_until=? WHERE id=? AND (lease_owner IS NULL OR lease_until<? OR lease_owner=?)", (owner, time.time() + seconds, task_id, time.time(), owner))
            return result.rowcount == 1

    def release(self, task_id, owner):
        self.execute("UPDATE tasks SET lease_owner=NULL,lease_until=NULL WHERE id=? AND lease_owner=?", (task_id, owner))

    def accepted(self, task_id, group_id=None):
        extra = " AND a.group_id=?" if group_id else ""
        return self.rows("""SELECT a.*,d.evaluation_id FROM assets a JOIN decisions d ON d.asset_id=a.id
          WHERE a.task_id=? AND d.action='ACCEPTED' AND a.state='AVAILABLE'
          AND d.rowid=(SELECT MAX(rowid) FROM decisions WHERE asset_id=a.id)""" + extra + " ORDER BY a.created_at,a.id", (task_id, group_id) if group_id else (task_id,))

    def token_usage(self, task_id):
        return sum((usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
            for usage in (json.loads(row["usage"] or "{}") for row in self.rows("SELECT usage FROM api_calls WHERE task_id=?", (task_id,))))

    def budget(self, task_id):
        row = self.one("""SELECT COALESCE(SUM(CASE WHEN state='SETTLED' THEN settled ELSE 0 END),0) spent,
          COALESCE(SUM(CASE WHEN state='RESERVED' THEN amount ELSE 0 END),0) reserved,
          COALESCE(SUM(CASE WHEN state='UNCERTAIN' THEN amount ELSE 0 END),0) uncertain
          FROM budget_reservations WHERE task_id=?""", (task_id,))
        return row

    def reserve(self, task_id, purpose, provider, model, credential, pricing, amount, limit):
        call_id = uid()
        with self.transaction() as c:
            current = c.execute("SELECT COALESCE(SUM(CASE WHEN state='SETTLED' THEN settled WHEN state IN ('RESERVED','UNCERTAIN') THEN amount ELSE 0 END),0) FROM budget_reservations WHERE task_id=?", (task_id,)).fetchone()[0]
            if current + amount > limit:
                raise BudgetExceeded("Insufficient unreserved budget")
            c.execute("INSERT INTO api_calls(id,task_id,purpose,provider_id,model_id,credential_id,status,pricing,created_at,updated_at) VALUES(?,?,?,?,?,?,'RESERVED',?,?,?)", (call_id, task_id, purpose, provider, model, credential, dump_json(pricing), now(), now()))
            c.execute("INSERT INTO budget_reservations VALUES(?,?,?,'RESERVED',NULL)", (call_id, task_id, amount))
        return call_id

    def settle(self, call_id, status, cost=None, usage=None, error=None):
        reservation_state = "SETTLED" if cost is not None else "UNCERTAIN"
        with self.transaction() as c:
            c.execute("UPDATE api_calls SET status=?,actual_cost=?,usage=?,error_code=?,updated_at=? WHERE id=?", (status, cost, dump_json(usage or {}), error, now(), call_id))
            c.execute("UPDATE budget_reservations SET state=?,settled=? WHERE call_id=?", (reservation_state, cost, call_id))

    def recover_calls(self):
        import time
        with self.transaction() as c:
            recoverable = "SELECT id FROM tasks WHERE lease_until IS NULL OR lease_until<?"
            c.execute("UPDATE budget_reservations SET state='UNCERTAIN' WHERE state='RESERVED' AND task_id IN (" + recoverable + ")", (time.time(),))
            c.execute("UPDATE api_calls SET status='UNCERTAIN' WHERE status='RESERVED' AND task_id IN (" + recoverable + ")", (time.time(),))

    def backup(self, target: Path):
        with self.lock, sqlite3.connect(target) as dest:
            self.conn.backup(dest)

    def close(self):
        with self.lock:
            self.conn.close()
