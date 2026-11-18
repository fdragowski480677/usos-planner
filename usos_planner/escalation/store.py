# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

"""SQLite store for registration attempts and escalation cases.

Case lifecycle:
  open -> coordinator_drafted -> coordinator_sent -> coordinator_approved | coordinator_refused | no_reply
       -> dean_drafted -> dean_sent -> dean_approved | dean_refused
       -> registered (verified in USOS) | closed
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

STATUSES = [
    "open",
    "coordinator_drafted",
    "coordinator_sent",
    "coordinator_approved",
    "coordinator_refused",
    "no_reply",
    "dean_drafted",
    "dean_sent",
    "dean_approved",
    "dean_refused",
    "registered",
    "closed",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _locked(fn):
    def wrapper(self, *a, **kw):
        with self.lock:
            return fn(self, *a, **kw)

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


class CaseStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # the web GUI calls us from worker threads; serialise access with a lock
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY, at TEXT, data TEXT
            );
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY,
                created TEXT, updated TEXT,
                course TEXT, usos_course_id TEXT, term_id TEXT,
                class_type TEXT, group_number INTEGER,
                reason TEXT, server_message TEXT,
                coordinators TEXT, status TEXT DEFAULT 'open', notes TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, case_id INTEGER, created TEXT,
                kind TEXT, recipient TEXT, subject TEXT, body TEXT, sent INTEGER DEFAULT 0, sent_at TEXT,
                FOREIGN KEY(case_id) REFERENCES cases(id)
            );
            """
        )

    # ----- attempts -------------------------------------------------------
    @_locked
    def record_attempt(self, data: dict) -> None:
        self.db.execute("INSERT INTO attempts(at, data) VALUES (?, ?)", (_now(), json.dumps(data, ensure_ascii=False)))
        self.db.commit()

    @_locked
    def attempts(self, limit: int = 100) -> list[dict]:
        rows = self.db.execute("SELECT at, data FROM attempts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"at": r["at"], **json.loads(r["data"])} for r in rows]

    # ----- cases ----------------------------------------------------------
    @_locked
    def open_case(self, *, course, usos_course_id, term_id, class_type, group_number, reason, server_message, coordinators) -> int:
        existing = self.db.execute(
            "SELECT id FROM cases WHERE usos_course_id=? AND term_id=? AND status NOT IN ('registered','closed')",
            (usos_course_id, term_id),
        ).fetchone()
        if existing:
            self.db.execute(
                "UPDATE cases SET updated=?, server_message=?, reason=? WHERE id=?",
                (_now(), server_message, reason, existing["id"]),
            )
            self.db.commit()
            return existing["id"]
        cur = self.db.execute(
            """INSERT INTO cases(created, updated, course, usos_course_id, term_id, class_type, group_number,
                                 reason, server_message, coordinators)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (_now(), _now(), course, usos_course_id, term_id, class_type, group_number, reason, server_message,
             json.dumps(coordinators, ensure_ascii=False)),
        )
        self.db.commit()
        return cur.lastrowid

    @_locked
    def cases(self, include_closed: bool = False) -> list[dict]:
        q = "SELECT * FROM cases" + ("" if include_closed else " WHERE status NOT IN ('registered','closed')") + " ORDER BY updated DESC"
        return [self._case(r) for r in self.db.execute(q).fetchall()]

    @_locked
    def case(self, case_id: int) -> dict | None:
        r = self.db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        return self._case(r) if r else None

    @_locked
    def set_status(self, case_id: int, status: str, notes: str | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        if notes is None:
            self.db.execute("UPDATE cases SET status=?, updated=? WHERE id=?", (status, _now(), case_id))
        else:
            self.db.execute("UPDATE cases SET status=?, updated=?, notes=? WHERE id=?", (status, _now(), notes, case_id))
        self.db.commit()

    # ----- messages -------------------------------------------------------
    @_locked
    def add_message(self, case_id: int, kind: str, recipient: str, subject: str, body: str) -> int:
        cur = self.db.execute(
            "INSERT INTO messages(case_id, created, kind, recipient, subject, body) VALUES (?,?,?,?,?,?)",
            (case_id, _now(), kind, recipient, subject, body),
        )
        self.db.commit()
        return cur.lastrowid

    @_locked
    def update_message(self, message_id: int, recipient: str, subject: str, body: str) -> None:
        self.db.execute("UPDATE messages SET recipient=?, subject=?, body=? WHERE id=?", (recipient, subject, body, message_id))
        self.db.commit()

    @_locked
    def mark_sent(self, message_id: int) -> None:
        self.db.execute("UPDATE messages SET sent=1, sent_at=? WHERE id=?", (_now(), message_id))
        self.db.commit()

    @_locked
    def messages(self, case_id: int) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM messages WHERE case_id=? ORDER BY id", (case_id,)).fetchall()]

    @_locked
    def message(self, message_id: int) -> dict | None:
        r = self.db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        return dict(r) if r else None

    @staticmethod
    def _case(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["coordinators"] = json.loads(d.get("coordinators") or "[]")
        return d
