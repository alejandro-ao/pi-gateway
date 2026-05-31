from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class Conversation:
    id: int
    platform: str
    chat_id: str
    thread_id: str | None
    user_id: str | None
    gateway_session_key: str
    pi_session_id: str | None
    pi_session_file: str | None
    pi_session_name: str | None
    cwd: str
    created_at: str
    updated_at: str
    last_message_at: str | None


class GatewayDB:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        async with self._lock:
            self._conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS conversations (
                  id INTEGER PRIMARY KEY,
                  platform TEXT NOT NULL,
                  chat_id TEXT NOT NULL,
                  thread_id TEXT,
                  user_id TEXT,
                  gateway_session_key TEXT UNIQUE NOT NULL,
                  pi_session_id TEXT,
                  pi_session_file TEXT,
                  pi_session_name TEXT,
                  cwd TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  last_message_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_conversations_user
                  ON conversations(platform, user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS messages (
                  id INTEGER PRIMARY KEY,
                  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                  platform_message_id TEXT,
                  direction TEXT NOT NULL,
                  text TEXT,
                  created_at TEXT NOT NULL
                );
                """
            )
            self._conn.commit()

    def _row_to_conversation(self, row: sqlite3.Row) -> Conversation:
        return Conversation(**dict(row))

    async def get_or_create_conversation(
        self,
        *,
        platform: str,
        chat_id: str,
        thread_id: str | None,
        user_id: str | None,
        gateway_session_key: str,
        cwd: str,
    ) -> Conversation:
        async with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE gateway_session_key = ?",
                (gateway_session_key,),
            ).fetchone()
            if row:
                return self._row_to_conversation(row)
            now = utc_now()
            cur = self._conn.execute(
                """
                INSERT INTO conversations(platform, chat_id, thread_id, user_id, gateway_session_key, cwd, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (platform, chat_id, thread_id, user_id, gateway_session_key, cwd, now, now),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM conversations WHERE id = ?", (cur.lastrowid,)).fetchone()
            return self._row_to_conversation(row)

    async def update_pi_session(
        self,
        conversation_id: int,
        *,
        pi_session_id: str | None,
        pi_session_file: str | None,
        pi_session_name: str | None = None,
    ) -> None:
        async with self._lock:
            self._conn.execute(
                """
                UPDATE conversations
                SET pi_session_id = ?, pi_session_file = ?, pi_session_name = COALESCE(?, pi_session_name), updated_at = ?
                WHERE id = ?
                """,
                (pi_session_id, pi_session_file, pi_session_name, utc_now(), conversation_id),
            )
            self._conn.commit()

    async def touch_message(self, conversation_id: int) -> None:
        async with self._lock:
            now = utc_now()
            self._conn.execute(
                "UPDATE conversations SET last_message_at = ?, updated_at = ? WHERE id = ?",
                (now, now, conversation_id),
            )
            self._conn.commit()

    async def log_message(self, conversation_id: int, *, direction: str, text: str | None, platform_message_id: str | None = None) -> None:
        async with self._lock:
            self._conn.execute(
                "INSERT INTO messages(conversation_id, platform_message_id, direction, text, created_at) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, platform_message_id, direction, text, utc_now()),
            )
            self._conn.commit()

    async def list_conversations_for_user(self, platform: str, user_id: str | None, limit: int = 10) -> list[Conversation]:
        async with self._lock:
            if user_id:
                rows = self._conn.execute(
                    "SELECT * FROM conversations WHERE platform = ? AND user_id = ? ORDER BY updated_at DESC LIMIT ?",
                    (platform, user_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM conversations WHERE platform = ? ORDER BY updated_at DESC LIMIT ?",
                    (platform, limit),
                ).fetchall()
            return [self._row_to_conversation(r) for r in rows]

    async def get_conversation(self, conversation_id: int) -> Conversation | None:
        async with self._lock:
            row = self._conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            return self._row_to_conversation(row) if row else None

    async def point_conversation_to(self, target_id: int, source: Conversation) -> Conversation | None:
        async with self._lock:
            self._conn.execute(
                """
                UPDATE conversations
                SET pi_session_id = ?, pi_session_file = ?, pi_session_name = ?, updated_at = ?
                WHERE id = ?
                """,
                (source.pi_session_id, source.pi_session_file, source.pi_session_name, utc_now(), target_id),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM conversations WHERE id = ?", (target_id,)).fetchone()
            return self._row_to_conversation(row) if row else None

    async def close(self) -> None:
        async with self._lock:
            self._conn.close()
