"""Explicit, idempotent consolidation of stopped instances' gateway metadata."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from .db import utc_now
from .storage import connect, init_shared, register_instance

CONVERSATION_FIELDS = (
    "platform",
    "chat_id",
    "thread_id",
    "user_id",
    "gateway_session_key",
    "pi_session_id",
    "pi_session_file",
    "pi_session_name",
    "cwd",
    "created_at",
    "updated_at",
    "last_message_at",
)


def open_source(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def inspect_source(path: Path, instance_id: str | None) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    with closing(open_source(path)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
        if not set(CONVERSATION_FIELDS).issubset(columns):
            raise ValueError(f"Unsupported source schema: {path}")
        scope = " WHERE instance_id=?" if "instance_id" in columns else ""
        if scope and not instance_id:
            raise ValueError(f"Shared source requires instanceId: {path}")
        params = (instance_id,) if scope else ()
        conversations = conn.execute(
            "SELECT COUNT(*) FROM conversations" + scope, params
        ).fetchone()[0]
        messages = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id IN (SELECT id FROM conversations"
            + scope
            + ")",
            params,
        ).fetchone()[0]
        return conversations, messages


def import_instance(
    target: str,
    *,
    config_path: Path,
    source: Path,
    instance_id: str | None,
    name: str | None,
) -> str:
    conn = connect(target)
    try:
        init_shared(conn)
        previous = conn.execute(
            "SELECT id FROM instances WHERE config_path=? AND archived_at IS NULL",
            (str(config_path),),
        ).fetchone()
        # Reuse an ID after a crash between the DB commit and config replacement.
        identity = instance_id or (previous[0] if previous else str(uuid.uuid4()))
        if source == Path(target):
            with conn:
                register_instance(conn, identity, str(config_path), name, utc_now())
            return identity
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            register_instance(conn, identity, str(config_path), name, utc_now())
            if conn.execute(
                "SELECT 1 FROM database_imports WHERE instance_id=? AND source_path=?",
                (identity, str(source)),
            ).fetchone():
                return identity
            if source.exists():
                original = open_source(source)
                backup = source.with_name(source.name + f".backup-{uuid.uuid4().hex}")
                backup_conn = connect(str(backup))
                try:
                    original.backup(backup_conn)
                    backup_conn.execute("PRAGMA journal_mode=DELETE")
                finally:
                    backup_conn.close()
                    original.close()
                print(f"Backup: {backup}")
                snapshot = open_source(backup)
                try:
                    columns = {
                        row[1]
                        for row in snapshot.execute("PRAGMA table_info(conversations)")
                    }
                    scope = " WHERE instance_id=?" if "instance_id" in columns else ""
                    params = (instance_id,) if scope else ()
                    fields = ", ".join(CONVERSATION_FIELDS)
                    placeholders = ", ".join("?" for _ in CONVERSATION_FIELDS)
                    for row in snapshot.execute(
                        "SELECT * FROM conversations" + scope, params
                    ):
                        # Never merge onto a live/new destination conversation.
                        cur = conn.execute(
                            f"INSERT INTO conversations(instance_id, {fields}) VALUES (?, {placeholders})",
                            (identity, *(row[field] for field in CONVERSATION_FIELDS)),
                        )
                        for message in snapshot.execute(
                            "SELECT * FROM messages WHERE conversation_id=?",
                            (row["id"],),
                        ):
                            conn.execute(
                                "INSERT INTO messages(conversation_id, platform_message_id, direction, text, created_at) "
                                "VALUES (?, ?, ?, ?, ?)",
                                (
                                    cur.lastrowid,
                                    message["platform_message_id"],
                                    message["direction"],
                                    message["text"],
                                    message["created_at"],
                                ),
                            )
                finally:
                    snapshot.close()
            conn.execute(
                "INSERT INTO database_imports VALUES (?, ?, ?)",
                (identity, str(source), utc_now()),
            )
        return identity
    finally:
        conn.close()
