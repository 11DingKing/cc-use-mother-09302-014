"""SQLite 持久化层。

只用标准库。时间窗统一拆成 ``local_date/start_minutes/end_minutes`` 三列保存，
从而原生保留跨午夜班次（end_minutes 可 > 1440），不依赖任何时区换算。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .models import (
    Assignment,
    Availability,
    Event,
    Person,
    Position,
    Qualification,
    Recusal,
    TimeWindow,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    name TEXT NOT NULL,
    profile_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS qualifications (
    person_id TEXT PRIMARY KEY REFERENCES people(id),
    role TEXT NOT NULL,
    status TEXT NOT NULL,
    training_valid_until TEXT,
    version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS recusals (
    person_id TEXT NOT NULL REFERENCES people(id),
    vendor_id TEXT NOT NULL,
    reason TEXT,
    PRIMARY KEY (person_id, vendor_id)
);

CREATE TABLE IF NOT EXISTS availabilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES people(id),
    local_date TEXT NOT NULL,
    start_minutes INTEGER NOT NULL,
    end_minutes INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    local_timezone TEXT NOT NULL,
    status TEXT NOT NULL,
    qualification_snapshot_version INTEGER
);

CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id),
    title TEXT NOT NULL,
    required_role TEXT NOT NULL,
    required_headcount INTEGER NOT NULL,
    local_date TEXT NOT NULL,
    start_minutes INTEGER NOT NULL,
    end_minutes INTEGER NOT NULL,
    vendor_id TEXT,
    min_rest_minutes INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS assignments (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id),
    position_id TEXT NOT NULL REFERENCES positions(id),
    person_id TEXT NOT NULL REFERENCES people(id),
    status TEXT NOT NULL,
    vendor_id TEXT,
    local_date TEXT NOT NULL,
    start_minutes INTEGER NOT NULL,
    end_minutes INTEGER NOT NULL,
    snapshot_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_assign_event ON assignments(event_id);
CREATE INDEX IF NOT EXISTS idx_assign_person ON assignments(person_id);
CREATE INDEX IF NOT EXISTS idx_assign_position ON assignments(position_id);
CREATE INDEX IF NOT EXISTS idx_avail_person ON availabilities(person_id);

-- 每个活动岗位上同一个志愿者只能有一条"占用中"的排班（planned/confirmed）。
CREATE UNIQUE INDEX IF NOT EXISTS idx_assign_active_unique
    ON assignments(event_id, position_id, person_id)
    WHERE status IN ('planned', 'confirmed');

-- 确认活动冻结资格时记录的快照。
CREATE TABLE IF NOT EXISTS qualification_snapshots (
    event_id TEXT PRIMARY KEY REFERENCES events(id),
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""


def connect(database: str | Path = ":memory:", check_same_thread: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(database, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


class Repository:
    """面向服务层的数据访问对象；所有写操作默认在调用方事务内。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---- people / 资格 / 回避 / 可用时段 ----------------------------------

    def upsert_person(self, p: Person) -> None:
        self.conn.execute(
            "INSERT INTO people(id, role, name, profile_json) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET role=excluded.role, name=excluded.name, "
            "profile_json=excluded.profile_json",
            (p.id, p.role, p.name, json.dumps(p.profile, ensure_ascii=False)),
        )

    def get_person(self, person_id: str) -> Person | None:
        row = self.conn.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
        return self._person(row) if row else None

    def list_people(self, role: str | None = None) -> list[Person]:
        if role:
            rows = self.conn.execute("SELECT * FROM people WHERE role=?", (role,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM people ORDER BY id").fetchall()
        return [self._person(r) for r in rows]

    @staticmethod
    def _person(row: sqlite3.Row) -> Person:
        return Person(
            id=row["id"], role=row["role"], name=row["name"],
            profile=json.loads(row["profile_json"] or "{}"),
        )

    def upsert_qualification(self, q: Qualification) -> None:
        self.conn.execute(
            "INSERT INTO qualifications(person_id, role, status, training_valid_until, version) "
            "VALUES(?,?,?,?,?) ON CONFLICT(person_id) DO UPDATE SET role=excluded.role, "
            "status=excluded.status, training_valid_until=excluded.training_valid_until, "
            "version=qualifications.version + 1",
            (q.person_id, q.role, q.status, q.training_valid_until, q.version),
        )

    def get_qualification(self, person_id: str) -> Qualification | None:
        row = self.conn.execute(
            "SELECT * FROM qualifications WHERE person_id=?", (person_id,)
        ).fetchone()
        return self._qualification(row) if row else None

    @staticmethod
    def _qualification(row: sqlite3.Row) -> Qualification:
        return Qualification(
            person_id=row["person_id"], role=row["role"], status=row["status"],
            training_valid_until=row["training_valid_until"], version=row["version"],
        )

    def add_recusal(self, r: Recusal) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO recusals(person_id, vendor_id, reason) VALUES(?,?,?)",
            (r.person_id, r.vendor_id, r.reason),
        )

    def remove_recusal(self, person_id: str, vendor_id: str) -> None:
        self.conn.execute(
            "DELETE FROM recusals WHERE person_id=? AND vendor_id=?",
            (person_id, vendor_id),
        )

    def recusals_for(self, person_id: str) -> list[Recusal]:
        rows = self.conn.execute(
            "SELECT * FROM recusals WHERE person_id=?", (person_id,)
        ).fetchall()
        return [Recusal(r["person_id"], r["vendor_id"], r["reason"]) for r in rows]

    def vendor_ids_for(self, person_id: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT vendor_id FROM recusals WHERE person_id=?", (person_id,)
        ).fetchall()
        return {r["vendor_id"] for r in rows}

    def add_availability(self, a: Availability) -> None:
        self.conn.execute(
            "INSERT INTO availabilities(person_id, local_date, start_minutes, end_minutes) "
            "VALUES(?,?,?,?)",
            (a.person_id, a.local_date, a.start_minutes, a.end_minutes),
        )

    def availabilities_for(self, person_id: str) -> list[Availability]:
        rows = self.conn.execute(
            "SELECT * FROM availabilities WHERE person_id=?", (person_id,)
        ).fetchall()
        return [
            Availability(r["person_id"], r["local_date"], r["start_minutes"], r["end_minutes"])
            for r in rows
        ]

    # ---- events / positions -----------------------------------------------

    def upsert_event(self, e: Event) -> None:
        self.conn.execute(
            "INSERT INTO events(id, name, local_timezone, status, qualification_snapshot_version) "
            "VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
            "local_timezone=excluded.local_timezone, status=excluded.status, "
            "qualification_snapshot_version=excluded.qualification_snapshot_version",
            (e.id, e.name, e.local_timezone, e.status, e.qualification_snapshot_version),
        )

    def get_event(self, event_id: str) -> Event | None:
        row = self.conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return self._event(row) if row else None

    @staticmethod
    def _event(row: sqlite3.Row) -> Event:
        return Event(
            id=row["id"], name=row["name"], local_timezone=row["local_timezone"],
            status=row["status"],
            qualification_snapshot_version=row["qualification_snapshot_version"],
        )

    def upsert_position(self, pos: Position) -> None:
        self.conn.execute(
            "INSERT INTO positions(id, event_id, title, required_role, required_headcount, "
            "local_date, start_minutes, end_minutes, vendor_id, min_rest_minutes) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title, "
            "required_role=excluded.required_role, required_headcount=excluded.required_headcount, "
            "local_date=excluded.local_date, start_minutes=excluded.start_minutes, "
            "end_minutes=excluded.end_minutes, vendor_id=excluded.vendor_id, "
            "min_rest_minutes=excluded.min_rest_minutes",
            (pos.id, pos.event_id, pos.title, pos.required_role, pos.required_headcount,
             pos.window.local_date, pos.window.start_minutes, pos.window.end_minutes,
             pos.vendor_id, pos.min_rest_minutes),
        )

    def get_position(self, position_id: str) -> Position | None:
        row = self.conn.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
        return self._position(row) if row else None

    def positions_for_event(self, event_id: str) -> list[Position]:
        rows = self.conn.execute(
            "SELECT * FROM positions WHERE event_id=? ORDER BY id", (event_id,)
        ).fetchall()
        return [self._position(r) for r in rows]

    @staticmethod
    def _position(row: sqlite3.Row) -> Position:
        return Position(
            id=row["id"], event_id=row["event_id"], title=row["title"],
            required_role=row["required_role"], required_headcount=row["required_headcount"],
            window=TimeWindow(row["local_date"], row["start_minutes"], row["end_minutes"]),
            vendor_id=row["vendor_id"], min_rest_minutes=row["min_rest_minutes"],
        )

    # ---- assignments -------------------------------------------------------

    def insert_assignment(self, a: Assignment) -> None:
        self.conn.execute(
            "INSERT INTO assignments(id, event_id, position_id, person_id, status, vendor_id, "
            "local_date, start_minutes, end_minutes, snapshot_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (a.id, a.event_id, a.position_id, a.person_id, a.status, a.vendor_id,
             a.window.local_date, a.window.start_minutes, a.window.end_minutes,
             json.dumps(a.snapshot, ensure_ascii=False) if a.snapshot else None),
        )

    def update_assignment_status(self, assignment_id: str, status: str,
                                 snapshot: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            "UPDATE assignments SET status=?, snapshot_json=COALESCE(?, snapshot_json) WHERE id=?",
            (status, json.dumps(snapshot, ensure_ascii=False) if snapshot else None, assignment_id),
        )

    def get_assignment(self, assignment_id: str) -> Assignment | None:
        row = self.conn.execute(
            "SELECT * FROM assignments WHERE id=?", (assignment_id,)
        ).fetchone()
        return self._assignment(row) if row else None

    def active_assignments_for_event(self, event_id: str) -> list[Assignment]:
        """占用中的排班：planned 与 confirmed。"""
        rows = self.conn.execute(
            "SELECT * FROM assignments WHERE event_id=? AND status IN ('planned','confirmed') "
            "ORDER BY id",
            (event_id,),
        ).fetchall()
        return [self._assignment(r) for r in rows]

    def active_assignments_for_person(self, person_id: str) -> list[Assignment]:
        rows = self.conn.execute(
            "SELECT * FROM assignments WHERE person_id=? AND status IN ('planned','confirmed') "
            "ORDER BY event_id, id",
            (person_id,),
        ).fetchall()
        return [self._assignment(r) for r in rows]

    def active_assignments_for_position(self, position_id: str) -> list[Assignment]:
        rows = self.conn.execute(
            "SELECT * FROM assignments WHERE position_id=? AND status IN ('planned','confirmed')",
            (position_id,),
        ).fetchall()
        return [self._assignment(r) for r in rows]

    def released_person_ids_for_position(self, position_id: str) -> set[str]:
        """已从该岗位离开（请假释放/换出/取消）的人，紧急补位不再回填。"""
        rows = self.conn.execute(
            "SELECT DISTINCT person_id FROM assignments WHERE position_id=? "
            "AND status IN ('released','cancelled')",
            (position_id,),
        ).fetchall()
        return {r["person_id"] for r in rows}

    @staticmethod
    def _assignment(row: sqlite3.Row) -> Assignment:
        snap = row["snapshot_json"]
        return Assignment(
            id=row["id"], event_id=row["event_id"], position_id=row["position_id"],
            person_id=row["person_id"], status=row["status"], vendor_id=row["vendor_id"],
            window=TimeWindow(row["local_date"], row["start_minutes"], row["end_minutes"]),
            snapshot=json.loads(snap) if snap else None,
        )

    # ---- snapshots ---------------------------------------------------------

    def save_snapshot(self, event_id: str, version: int, created_at: str,
                      payload: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO qualification_snapshots(event_id, version, created_at, "
            "payload_json) VALUES(?,?,?,?)",
            (event_id, version, created_at, json.dumps(payload, ensure_ascii=False)),
        )

    def get_snapshot(self, event_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM qualification_snapshots WHERE event_id=?", (event_id,)
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"])
        return {
            "event_id": event_id, "version": row["version"], "created_at": row["created_at"],
            "qualifications": payload["qualifications"],
            "recusals": payload.get("recusals", []),
            "availabilities": payload.get("availabilities", []),
        }
