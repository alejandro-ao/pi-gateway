from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .storage import connect, init_shared, register_instance


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
    instance_id: str | None = None


class GatewayDB:
    def __init__(
        self,
        path: str,
        *,
        instance_id: str | None = None,
        config_path: str | None = None,
        instance_name: str | None = None,
    ):
        self.path = path
        self.instance_id = instance_id
        self.config_path = config_path
        self.instance_name = instance_name
        self._conn = connect(path)
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        async with self._lock:
            if self.instance_id:
                if not self.config_path:
                    raise ValueError("Scoped databases require a config path")
                init_shared(self._conn)
                with self._conn:
                    register_instance(
                        self._conn,
                        self.instance_id,
                        str(Path(self.config_path).resolve()),
                        self.instance_name,
                        utc_now(),
                    )
                return
            columns = {
                row[1] for row in self._conn.execute("PRAGMA table_info(conversations)")
            }
            if "instance_id" in columns:
                raise ValueError(
                    "Shared databases require instanceId; run `pi-gateway migrate-db`."
                )
            # Compatibility only: pre-upgrade configurations keep their legacy schema.
            self._conn.executescript("""
                PRAGMA journal_mode=WAL;
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
            """)
            self._conn.commit()

    def _scope(self) -> tuple[str, tuple[str, ...]]:
        return (
            (" AND instance_id = ?", (self.instance_id,))
            if self.instance_id
            else ("", ())
        )

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
            scope, params = self._scope()
            # An atomic upsert avoids a SELECT/INSERT race across gateway connections.
            with self._conn:
                now = utc_now()
                fields = "platform, chat_id, thread_id, user_id, gateway_session_key, cwd, created_at, updated_at"
                values: tuple[object, ...] = (
                    platform,
                    chat_id,
                    thread_id,
                    user_id,
                    gateway_session_key,
                    cwd,
                    now,
                    now,
                )
                conflict = "gateway_session_key"
                if self.instance_id:
                    fields += ", instance_id"
                    values += (self.instance_id,)
                    conflict = "instance_id, gateway_session_key"
                placeholders = ", ".join("?" for _ in values)
                self._conn.execute(
                    f"INSERT INTO conversations({fields}) VALUES ({placeholders}) "
                    f"ON CONFLICT({conflict}) DO NOTHING",
                    values,
                )
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE gateway_session_key = ?" + scope,
                (gateway_session_key, *params),
            ).fetchone()
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
            scope, params = self._scope()
            with self._conn:
                self._conn.execute(
                    "UPDATE conversations SET pi_session_id = ?, pi_session_file = ?, "
                    "pi_session_name = COALESCE(?, pi_session_name), updated_at = ? WHERE id = ?"
                    + scope,
                    (
                        pi_session_id,
                        pi_session_file,
                        pi_session_name,
                        utc_now(),
                        conversation_id,
                        *params,
                    ),
                )

    async def touch_message(self, conversation_id: int) -> None:
        async with self._lock:
            scope, params = self._scope()
            with self._conn:
                now = utc_now()
                self._conn.execute(
                    "UPDATE conversations SET last_message_at = ?, updated_at = ? WHERE id = ?"
                    + scope,
                    (now, now, conversation_id, *params),
                )

    async def log_message(
        self,
        conversation_id: int,
        *,
        direction: str,
        text: str | None,
        platform_message_id: str | None = None,
    ) -> None:
        async with self._lock:
            scope, params = self._scope()
            with self._conn:
                self._conn.execute(
                    "INSERT INTO messages(conversation_id, platform_message_id, direction, text, created_at) "
                    "SELECT id, ?, ?, ?, ? FROM conversations WHERE id = ?" + scope,
                    (
                        platform_message_id,
                        direction,
                        text,
                        utc_now(),
                        conversation_id,
                        *params,
                    ),
                )

    async def list_conversations_for_user(
        self, platform: str, user_id: str | None, limit: int = 10
    ) -> list[Conversation]:
        async with self._lock:
            scope, params = self._scope()
            query = "SELECT * FROM conversations WHERE platform = ?" + scope
            values: tuple[object, ...] = (platform, *params)
            if user_id:
                query += " AND user_id = ?"
                values += (user_id,)
            rows = self._conn.execute(
                query + " ORDER BY updated_at DESC LIMIT ?", (*values, limit)
            ).fetchall()
            return [self._row_to_conversation(row) for row in rows]

    async def get_conversation(self, conversation_id: int) -> Conversation | None:
        async with self._lock:
            scope, params = self._scope()
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE id = ?" + scope,
                (conversation_id, *params),
            ).fetchone()
            return self._row_to_conversation(row) if row else None

    async def point_conversation_to(
        self, target_id: int, source: Conversation
    ) -> Conversation | None:
        async with self._lock:
            scope, params = self._scope()
            # Re-read the source under this scope; never trust a supplied object's ownership.
            with self._conn:
                self._conn.execute(
                    "UPDATE conversations SET (pi_session_id, pi_session_file, pi_session_name) = "
                    "(SELECT pi_session_id, pi_session_file, pi_session_name FROM conversations WHERE id = ?"
                    + scope
                    + ")"
                    ", updated_at = ? WHERE id = ?"
                    + scope
                    + " AND EXISTS (SELECT 1 FROM conversations WHERE id = ?"
                    + scope
                    + ")",
                    (
                        source.id,
                        *params,
                        utc_now(),
                        target_id,
                        *params,
                        source.id,
                        *params,
                    ),
                )
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE id = ?" + scope, (target_id, *params)
            ).fetchone()
            return self._row_to_conversation(row) if row else None

    async def close(self) -> None:
        async with self._lock:
            self._conn.close()
