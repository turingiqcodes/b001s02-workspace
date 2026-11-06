"""SQLite persistence: incidents, occurrences, retries, approvals, pending Linear
operations, the detection watermark, and the append-only audit log.

Design notes
------------
* One file, standard-library ``sqlite3``; WAL mode so the CLI can read while the
  agent writes.
* Every write that guards an external action (retry, approval) is committed
  *before* that action runs. If the write fails, the action does not run.
* UNIQUE constraints are the idempotency backbone: the same failed try of a
  task can only get one retry row, and an approval can only be claimed once.
* The audit table is protected by triggers that reject UPDATE and DELETE.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Optional

from .models import iso, utcnow


class StoreError(Exception):
    """SQLite is unavailable or a write failed. Callers must fail safe."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    kind TEXT NOT NULL,
    dag_id TEXT NOT NULL,
    task_id TEXT,
    run_id TEXT,
    map_index INTEGER DEFAULT -1,
    try_number INTEGER DEFAULT 0,
    error TEXT,
    normalized_error TEXT,
    classification TEXT,
    confidence REAL,
    root_cause TEXT,
    suggested_fix TEXT,
    proposed_action TEXT,
    analysis_source TEXT,
    analysis_json TEXT,
    decision TEXT,
    decision_reasons TEXT,
    status TEXT NOT NULL,
    linear_issue_id TEXT,
    linear_issue_identifier TEXT,
    assigned_developer TEXT,
    assignment_json TEXT,
    context_gaps TEXT,
    actions_json TEXT DEFAULT '[]',
    previous_incident_id TEXT,
    issue_draft TEXT,
    occurrence_count INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_fp ON incidents(fingerprint, status);

CREATE TABLE IF NOT EXISTS occurrences (
    occurrence_key TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    incident_id TEXT NOT NULL,
    is_repeat INTEGER NOT NULL DEFAULT 0,
    details_json TEXT,
    detected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS retries (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    dag_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    map_index INTEGER NOT NULL DEFAULT -1,
    try_number_before INTEGER NOT NULL,
    decision_reasons TEXT,
    status TEXT NOT NULL,          -- executing | requested | succeeded | failed | timed_out | aborted | interrupted | error
    result_json TEXT,
    verification_status TEXT,      -- pending | verified_success | verified_failure | timed_out | n/a
    executed_at TEXT NOT NULL,
    verified_at TEXT,
    UNIQUE(dag_id, run_id, task_id, map_index, try_number_before)
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    linear_issue_id TEXT,
    action TEXT NOT NULL,
    reasons TEXT,
    status TEXT NOT NULL,          -- pending | executing | executed | failed | interrupted | stale
    approval_source TEXT,          -- label | comment | mcp
    approved_by TEXT,
    requested_at TEXT NOT NULL,
    approved_at TEXT,
    executed_at TEXT,
    result_json TEXT,
    UNIQUE(incident_id, action)
);

CREATE TABLE IF NOT EXISTS approval_signals (
    signal_id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    reason TEXT,
    seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_linear_ops (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    op_type TEXT NOT NULL,         -- create_issue | comment | resolve
    dedupe_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(incident_id, op_type, dedupe_key)
);

CREATE TABLE IF NOT EXISTS watermark (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    value TEXT,
    last_successful_cycle TEXT,
    state_json TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,
    ts TEXT NOT NULL,
    incident_id TEXT,
    event_type TEXT NOT NULL,
    action TEXT NOT NULL,
    input_summary TEXT,
    output_summary TEXT,
    result TEXT NOT NULL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_incident ON audit(incident_id);

CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""

_INCIDENT_JSON_FIELDS = {"decision_reasons", "context_gaps", "actions_json", "assignment_json", "analysis_json",
                         "issue_draft"}


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Store:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        try:
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=10)
            self._conn.row_factory = sqlite3.Row
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
        except (sqlite3.Error, OSError) as exc:
            raise StoreError(f"cannot open SQLite database {path!r}: {exc}") from exc

    # ---- plumbing ---------------------------------------------------------------------
    def close(self) -> None:
        self._conn.close()

    def ping(self) -> bool:
        try:
            self._conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise StoreError(f"cannot start transaction: {exc}") from exc
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except sqlite3.IntegrityError:
                self._conn.execute("ROLLBACK")
                raise
            except sqlite3.Error as exc:
                self._conn.execute("ROLLBACK")
                raise StoreError(str(exc)) from exc
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            try:
                return self._conn.execute(sql, params).fetchall()
            except sqlite3.Error as exc:
                raise StoreError(str(exc)) from exc

    @staticmethod
    def _row_to_incident(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for key in _INCIDENT_JSON_FIELDS:
            if data.get(key):
                data[key] = json.loads(data[key])
        return data

    # ---- incidents --------------------------------------------------------------------
    def create_incident(self, incident: dict[str, Any], occurrence_key: str, occurrence_details: dict) -> str:
        """Insert an incident and its first occurrence atomically. Returns the id."""
        now = iso(utcnow())
        incident_id = incident.get("id") or new_id("inc")
        row = {
            "id": incident_id,
            "status": incident.get("status", "investigating"),
            "created_at": now,
            "updated_at": now,
            **{k: v for k, v in incident.items() if k != "id"},
        }
        for key in _INCIDENT_JSON_FIELDS:
            if key in row and not isinstance(row[key], (str, type(None))):
                row[key] = json.dumps(row[key])
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self.transaction() as conn:
            conn.execute(f"INSERT INTO incidents ({cols}) VALUES ({marks})", tuple(row.values()))
            conn.execute(
                "INSERT INTO occurrences VALUES (?, ?, ?, 0, ?, ?)",
                (occurrence_key, row["fingerprint"], incident_id, json.dumps(occurrence_details), now),
            )
        return incident_id

    def update_incident(self, incident_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = iso(utcnow())
        for key in _INCIDENT_JSON_FIELDS:
            if key in fields and not isinstance(fields[key], (str, type(None))):
                fields[key] = json.dumps(fields[key])
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.transaction() as conn:
            conn.execute(f"UPDATE incidents SET {sets} WHERE id = ?", (*fields.values(), incident_id))

    def append_incident_action(self, incident_id: str, action: str) -> None:
        inc = self.get_incident(incident_id)
        if inc is None:
            return
        actions = list(inc.get("actions_json") or [])
        actions.append(f"{iso(utcnow())} {action}")
        self.update_incident(incident_id, actions_json=actions)

    def get_incident(self, incident_id: str) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT * FROM incidents WHERE id = ?", (incident_id,))
        return self._row_to_incident(rows[0]) if rows else None

    def find_open_incident(self, fingerprint: str) -> Optional[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM incidents WHERE fingerprint = ? AND status != 'resolved' ORDER BY created_at DESC LIMIT 1",
            (fingerprint,),
        )
        return self._row_to_incident(rows[0]) if rows else None

    def find_latest_incident(self, fingerprint: str) -> Optional[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM incidents WHERE fingerprint = ? ORDER BY created_at DESC LIMIT 1", (fingerprint,)
        )
        return self._row_to_incident(rows[0]) if rows else None

    def find_incident_by_issue(self, linear_issue_id: str) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT * FROM incidents WHERE linear_issue_id = ? LIMIT 1", (linear_issue_id,))
        return self._row_to_incident(rows[0]) if rows else None

    def list_incidents(self, status: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
        if status:
            rows = self._query(
                "SELECT * FROM incidents WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit)
            )
        else:
            rows = self._query("SELECT * FROM incidents ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._row_to_incident(r) for r in rows]

    def open_incidents(self) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM incidents WHERE status != 'resolved' ORDER BY created_at")
        return [self._row_to_incident(r) for r in rows]

    # ---- occurrences ------------------------------------------------------------------
    def occurrence_seen(self, key: str) -> bool:
        return bool(self._query("SELECT 1 FROM occurrences WHERE occurrence_key = ?", (key,)))

    def add_repeat_occurrence(self, key: str, fingerprint: str, incident_id: str, details: dict) -> bool:
        """Record a repeat. Returns False if this occurrence was already recorded."""
        try:
            with self.transaction() as conn:
                conn.execute(
                    "INSERT INTO occurrences VALUES (?, ?, ?, 1, ?, ?)",
                    (key, fingerprint, incident_id, json.dumps(details), iso(utcnow())),
                )
                conn.execute(
                    "UPDATE incidents SET occurrence_count = occurrence_count + 1, updated_at = ? WHERE id = ?",
                    (iso(utcnow()), incident_id),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def mark_occurrence_seen(self, key: str, fingerprint: str, incident_id: str, details: dict) -> None:
        """Record an occurrence the agent already handles elsewhere (e.g. a retry's own failure)."""
        try:
            with self.transaction() as conn:
                conn.execute(
                    "INSERT INTO occurrences VALUES (?, ?, ?, 1, ?, ?)",
                    (key, fingerprint, incident_id, json.dumps(details), iso(utcnow())),
                )
        except sqlite3.IntegrityError:
            pass

    def occurrences_for_incident(self, incident_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query(
            "SELECT * FROM occurrences WHERE incident_id = ? ORDER BY detected_at", (incident_id,))]

    # ---- retries ----------------------------------------------------------------------
    def create_retry(
        self,
        incident_id: str,
        dag_id: str,
        run_id: str,
        task_id: str,
        map_index: int,
        try_number_before: int,
        reasons: list[str],
    ) -> Optional[str]:
        """Insert a retry row in state 'executing'. Returns None if this exact try was already retried."""
        retry_id = new_id("retry")
        try:
            with self.transaction() as conn:
                attempt = conn.execute(
                    "SELECT COUNT(*) FROM retries WHERE dag_id=? AND run_id=? AND task_id=? AND map_index=? "
                    "AND status NOT IN ('aborted')",
                    (dag_id, run_id, task_id, map_index),
                ).fetchone()[0] + 1
                conn.execute(
                    "INSERT INTO retries (id, incident_id, attempt, dag_id, run_id, task_id, map_index, "
                    "try_number_before, decision_reasons, status, verification_status, executed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'executing', 'pending', ?)",
                    (retry_id, incident_id, attempt, dag_id, run_id, task_id, map_index, try_number_before,
                     json.dumps(reasons), iso(utcnow())),
                )
            return retry_id
        except sqlite3.IntegrityError:
            return None

    def update_retry(self, retry_id: str, **fields: Any) -> None:
        if "result_json" in fields and not isinstance(fields["result_json"], (str, type(None))):
            fields["result_json"] = json.dumps(fields["result_json"], default=str)
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.transaction() as conn:
            conn.execute(f"UPDATE retries SET {sets} WHERE id = ?", (*fields.values(), retry_id))

    def get_retry(self, retry_id: str) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT * FROM retries WHERE id = ?", (retry_id,))
        return dict(rows[0]) if rows else None

    def count_task_retries(self, dag_id: str, run_id: str, task_id: str, map_index: int) -> int:
        """Automatic retries already spent on this task instance (aborted ones don't count)."""
        return self._query(
            "SELECT COUNT(*) FROM retries WHERE dag_id=? AND run_id=? AND task_id=? AND map_index=? "
            "AND status != 'aborted'",
            (dag_id, run_id, task_id, map_index),
        )[0][0]

    def count_retries_since(self, since: datetime) -> int:
        return self._query(
            "SELECT COUNT(*) FROM retries WHERE executed_at >= ? AND status != 'aborted'", (iso(since),)
        )[0][0]

    def count_retries_last_hour(self) -> int:
        return self.count_retries_since(utcnow() - timedelta(hours=1))

    def active_retries(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query(
            "SELECT * FROM retries WHERE status IN ('executing', 'requested', 'error') ORDER BY executed_at")]

    def has_active_retry(self, dag_id: str, run_id: str, task_id: str, map_index: int) -> bool:
        return bool(self._query(
            "SELECT 1 FROM retries WHERE dag_id=? AND run_id=? AND task_id=? AND map_index=? "
            "AND status IN ('executing', 'requested', 'error')",
            (dag_id, run_id, task_id, map_index),
        ))

    def retries_for_incident(self, incident_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query(
            "SELECT * FROM retries WHERE incident_id = ? ORDER BY executed_at", (incident_id,))]

    # ---- approvals --------------------------------------------------------------------
    def create_approval(self, incident_id: str, linear_issue_id: Optional[str], action: str,
                        reasons: list[str]) -> str:
        """Create a pending approval; returns the existing id if one already exists."""
        existing = self._query("SELECT id FROM approvals WHERE incident_id=? AND action=?", (incident_id, action))
        if existing:
            return existing[0]["id"]
        approval_id = new_id("appr")
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO approvals (id, incident_id, linear_issue_id, action, reasons, status, requested_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                (approval_id, incident_id, linear_issue_id, action, json.dumps(reasons), iso(utcnow())),
            )
        return approval_id

    def set_approval_issue(self, approval_id: str, linear_issue_id: str) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE approvals SET linear_issue_id=? WHERE id=?", (linear_issue_id, approval_id))

    def get_approval(self, approval_id: str) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        return dict(rows[0]) if rows else None

    def approvals_with_status(self, status: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query("SELECT * FROM approvals WHERE status=? ORDER BY requested_at", (status,))]

    def approvals_for_incident(self, incident_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query("SELECT * FROM approvals WHERE incident_id=?", (incident_id,))]

    def all_approvals(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query("SELECT * FROM approvals ORDER BY requested_at DESC")]

    def claim_approval(self, approval_id: str, source: str, approved_by: Optional[str]) -> bool:
        """Atomically move pending -> executing. Only one caller can ever win."""
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE approvals SET status='executing', approval_source=?, approved_by=?, approved_at=? "
                "WHERE id=? AND status='pending'",
                (source, approved_by, iso(utcnow()), approval_id),
            )
            return cur.rowcount == 1

    def finish_approval(self, approval_id: str, status: str, result: Any) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE approvals SET status=?, executed_at=?, result_json=? WHERE id=?",
                (status, iso(utcnow()), json.dumps(result, default=str), approval_id),
            )

    def record_signal(self, signal_id: str, approval_id: str, accepted: bool, reason: str) -> bool:
        """Remember that an approval signal (comment/label event) was evaluated. False if seen before."""
        try:
            with self.transaction() as conn:
                conn.execute(
                    "INSERT INTO approval_signals VALUES (?, ?, ?, ?, ?)",
                    (signal_id, approval_id, int(accepted), reason, iso(utcnow())),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def signals_for_approval(self, approval_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query("SELECT * FROM approval_signals WHERE approval_id=?", (approval_id,))]

    # ---- pending Linear operations ----------------------------------------------------
    def enqueue_linear_op(self, incident_id: str, op_type: str, payload: dict, dedupe_key: str = "") -> bool:
        try:
            with self.transaction() as conn:
                conn.execute(
                    "INSERT INTO pending_linear_ops (id, incident_id, op_type, dedupe_key, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (new_id("op"), incident_id, op_type, dedupe_key, json.dumps(payload, default=str), iso(utcnow())),
                )
            return True
        except sqlite3.IntegrityError:
            return False  # already queued

    def pending_linear_ops(self) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM pending_linear_ops ORDER BY created_at")
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d.pop("payload_json"))
            out.append(d)
        return out

    def has_pending_op(self, incident_id: str, op_type: str) -> bool:
        return bool(self._query(
            "SELECT 1 FROM pending_linear_ops WHERE incident_id=? AND op_type=?", (incident_id, op_type)))

    def complete_linear_op(self, op_id: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM pending_linear_ops WHERE id=?", (op_id,))

    def fail_linear_op(self, op_id: str, error: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE pending_linear_ops SET attempts = attempts + 1, last_error=? WHERE id=?", (error[:500], op_id))

    # ---- watermark --------------------------------------------------------------------
    def get_watermark(self) -> dict[str, Any]:
        rows = self._query("SELECT * FROM watermark WHERE id = 1")
        if not rows:
            return {"value": None, "last_successful_cycle": None, "state": {}}
        row = dict(rows[0])
        return {
            "value": row["value"],
            "last_successful_cycle": row["last_successful_cycle"],
            "state": json.loads(row["state_json"] or "{}"),
        }

    def set_watermark(self, value: str, state: Optional[dict] = None) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO watermark (id, value, last_successful_cycle, state_json) VALUES (1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET value=excluded.value, "
                "last_successful_cycle=excluded.last_successful_cycle, state_json=excluded.state_json",
                (value, iso(utcnow()), json.dumps(state or {})),
            )

    # ---- audit ------------------------------------------------------------------------
    def append_audit(self, record: dict[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO audit (id, ts, incident_id, event_type, action, input_summary, output_summary, "
                "result, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record["id"], record["ts"], record.get("incident_id"), record["event_type"], record["action"],
                 record.get("input_summary"), record.get("output_summary"), record["result"], record.get("error")),
            )

    def audit_for_incident(self, incident_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query("SELECT * FROM audit WHERE incident_id=? ORDER BY seq", (incident_id,))]

    def list_audit(self, limit: int = 100, event_type: Optional[str] = None) -> list[dict[str, Any]]:
        if event_type:
            rows = self._query("SELECT * FROM audit WHERE event_type=? ORDER BY seq DESC LIMIT ?", (event_type, limit))
        else:
            rows = self._query("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
