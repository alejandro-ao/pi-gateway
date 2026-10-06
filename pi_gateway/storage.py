"""Shared SQLite schema and connections (no credentials or Pi history)."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

SCHEMA_VERSION = 1
SHARED_DATABASE = "~/.local/state/pi-gateway/gateway.sqlite3"


def shared_database_path() -> str:
    return str(Path(SHARED_DATABASE).expanduser().resolve())


def connect(path: str) -> sqlite3.Connection:
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Database contains audit text. New files must not be world-readable.
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def enable_wal(conn: sqlite3.Connection) -> None:
    # Journal-mode changes can return SQLITE_BUSY immediately (without invoking
    # the busy handler) when another process initializes the same empty file.
    deadline = time.monotonic() + 5
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            remaining = deadline - time.monotonic()
            if (
                code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                or remaining <= 0
            ):
                raise
            time.sleep(min(0.05, remaining))


def init_shared(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise ValueError(
            "Database schema is newer than this gateway; upgrade pi-gateway."
        )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
    if columns and "instance_id" not in columns:
        raise ValueError(
            "Legacy database: run `pi-gateway migrate-db` before using instanceId."
        )
    enable_wal(conn)
    # BEGIN in the script makes schema creation/versioning atomic across processes.
    conn.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS instances (
            id TEXT PRIMARY KEY,
            name TEXT,
            config_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_instances_active_path
            ON instances(config_path) WHERE archived_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_instances_active_name
            ON instances(name) WHERE archived_at IS NULL AND name IS NOT NULL;
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY,
            instance_id TEXT NOT NULL REFERENCES instances(id),
            platform TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            thread_id TEXT,
            user_id TEXT,
            gateway_session_key TEXT NOT NULL,
            pi_session_id TEXT,
            pi_session_file TEXT,
            pi_session_name TEXT,
            cwd TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_message_at TEXT,
            UNIQUE(instance_id, gateway_session_key)
        );
        CREATE INDEX IF NOT EXISTS idx_conversations_instance_user
            ON conversations(instance_id, platform, user_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_conversations_instance_recent
            ON conversations(instance_id, platform, updated_at DESC);
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            platform_message_id TEXT,
            direction TEXT NOT NULL,
            text TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON messages(conversation_id, id);
        CREATE TABLE IF NOT EXISTS database_imports (
            instance_id TEXT NOT NULL REFERENCES instances(id),
            source_path TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            PRIMARY KEY(instance_id, source_path)
        );
        PRAGMA user_version=1;
        COMMIT;
    """)


def register_instance(
    conn: sqlite3.Connection,
    instance_id: str,
    config_path: str,
    name: str | None,
    now: str,
) -> None:
    existing = conn.execute(
        "SELECT config_path FROM instances WHERE id=?", (instance_id,)
    ).fetchone()
    if existing and existing[0] != config_path and Path(existing[0]).exists():
        raise ValueError(f"Duplicate instanceId: already used by {existing[0]}")
    conn.execute(
        """
        INSERT INTO instances(id, name, config_path, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name,
            config_path=excluded.config_path, updated_at=excluded.updated_at, archived_at=NULL
    """,
        (instance_id, name, config_path, now, now),
    )
