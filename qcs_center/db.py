"""SQLite database layer for QCS Center (aiosqlite).

Tables
------
jobs    — recording jobs submitted by clients
agents  — agent heartbeat / status registry

All timestamps are UTC ISO-8601 strings.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

import config

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    instructions    TEXT NOT NULL,
    auto_name       INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'queued',
    agent_name      TEXT,
    created_at      TEXT NOT NULL,
    claimed_at      TEXT,
    finished_at     TEXT,
    exit_code       INTEGER,
    stdout          TEXT,
    stderr          TEXT,
    recording_jsonl TEXT
);

CREATE TABLE IF NOT EXISTS agents (
    name            TEXT PRIMARY KEY,
    last_heartbeat  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'idle',
    current_job_id  TEXT,
    hostname        TEXT,
    tags            TEXT
);
"""

_DB: aiosqlite.Connection | None = None
_dequeue_lock = asyncio.Lock()


# ── Lifecycle ─────────────────────────────────────────────────────────────────

async def init_db(path: str | Path | None = None) -> None:
    global _DB
    db_path = str(path or config.QCS_CENTER_DB_PATH)
    _DB = await aiosqlite.connect(db_path)
    _DB.row_factory = aiosqlite.Row
    await _DB.execute("PRAGMA journal_mode=WAL")
    await _DB.execute("PRAGMA foreign_keys=ON")
    await _DB.executescript(_SCHEMA)
    await _DB.commit()


async def close_db() -> None:
    global _DB
    if _DB:
        await _DB.close()
        _DB = None


async def get_db() -> aiosqlite.Connection:
    if _DB is None:
        raise RuntimeError("DB not initialised — call init_db() first")
    return _DB


# ── Job CRUD ──────────────────────────────────────────────────────────────────

async def job_create(run_id: str, instructions: str, auto_name: bool = True) -> dict:
    job_id = str(uuid.uuid4())
    now = _now()
    db = await get_db()
    await db.execute(
        """INSERT INTO jobs (id, run_id, instructions, auto_name, status, created_at)
           VALUES (?, ?, ?, ?, 'queued', ?)""",
        (job_id, run_id, instructions, int(auto_name), now),
    )
    await db.commit()
    return await _job_get_raw(job_id)


async def job_get(job_id: str) -> dict | None:
    return await _job_get_raw(job_id)


async def job_list(status: str | None = None, limit: int = 100) -> list[dict]:
    db = await get_db()
    if status:
        async with db.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with db.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def job_dequeue() -> dict | None:
    """Atomically claim the oldest queued job. Serialised by _dequeue_lock."""
    async with _dequeue_lock:
        db = await get_db()
        async with db.execute(
            "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        job_id = row["id"]
        now = _now()
        await db.execute(
            "UPDATE jobs SET status='claimed', claimed_at=? WHERE id=?",
            (now, job_id),
        )
        await db.commit()
    return await _job_get_raw(job_id)


async def job_start(job_id: str, agent_name: str) -> None:
    """Mark job as running (called after agent acknowledges it)."""
    now = _now()
    db = await get_db()
    await db.execute(
        "UPDATE jobs SET status='running', agent_name=?, claimed_at=? WHERE id=?",
        (agent_name, now, job_id),
    )
    await db.commit()


async def job_finish(
    job_id: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    recording_jsonl: str,
) -> None:
    now = _now()
    status = "done" if exit_code == 0 else "failed"
    db = await get_db()
    await db.execute(
        """UPDATE jobs
           SET status=?, finished_at=?, exit_code=?, stdout=?, stderr=?, recording_jsonl=?
           WHERE id=?""",
        (status, now, exit_code, stdout, stderr, recording_jsonl, job_id),
    )
    await db.commit()


# ── Agent CRUD ────────────────────────────────────────────────────────────────

async def agent_heartbeat(name: str, hostname: str = "", tags: str = "") -> None:
    now = _now()
    db = await get_db()
    await db.execute(
        """INSERT INTO agents (name, last_heartbeat, status, hostname, tags)
           VALUES (?, ?, 'idle', ?, ?)
           ON CONFLICT(name) DO UPDATE
             SET last_heartbeat = excluded.last_heartbeat,
                 hostname       = excluded.hostname,
                 tags           = excluded.tags""",
        (name, now, hostname, tags),
    )
    await db.commit()


async def agent_set_busy(name: str, job_id: str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE agents SET status='busy', current_job_id=? WHERE name=?",
        (job_id, name),
    )
    await db.commit()


async def agent_set_idle(name: str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE agents SET status='idle', current_job_id=NULL WHERE name=?",
        (name,),
    )
    await db.commit()


async def agent_list() -> list[dict]:
    async with (await get_db()).execute(
        "SELECT * FROM agents ORDER BY name"
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _job_get_raw(job_id: str) -> dict | None:
    db = await get_db()
    async with db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
