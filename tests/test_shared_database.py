import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from pi_gateway import cli
from pi_gateway.config import load_config
from pi_gateway.db import GatewayDB
from pi_gateway.storage import shared_database_path


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PI_GATEWAY_DB", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def configure(path, name):
    args = cli.build_parser().parse_args(
        [
            "configure",
            "telegram",
            "-c",
            str(path),
            "--name",
            name,
            "--bot-token",
            f"fake:{name}",
            "--allowed-user-id",
            "123",
        ]
    )
    cli.configure_telegram(args)
    return load_config(str(path))


async def conversation(db, key="telegram:123::123"):
    return await db.get_or_create_conversation(
        platform="telegram",
        chat_id="123",
        thread_id=None,
        user_id="123",
        gateway_session_key=key,
        cwd="/work",
    )


def test_shared_defaults_and_full_query_isolation(workspace):
    first = configure(workspace / "first.yaml", "research")
    second = configure(workspace / "second.yaml", "coding")
    assert first.database_path == second.database_path == shared_database_path()
    assert first.instance_id != second.instance_id

    async def run():
        a = GatewayDB(
            first.database_path,
            instance_id=first.instance_id,
            config_path=str(workspace / "first.yaml"),
            instance_name="research",
        )
        b = GatewayDB(
            second.database_path,
            instance_id=second.instance_id,
            config_path=str(workspace / "second.yaml"),
            instance_name="coding",
        )
        try:
            await a.init()
            await b.init()
            own = await conversation(a)
            other = await conversation(b)
            assert own.id != other.id
            assert (await conversation(a)).id == own.id
            await b.update_pi_session(
                other.id, pi_session_id="secret", pi_session_file="/coding.jsonl"
            )
            original = await b.get_conversation(other.id)
            assert original is not None
            assert await a.get_conversation(other.id) is None
            assert [
                c.id for c in await a.list_conversations_for_user("telegram", "123")
            ] == [own.id]
            assert [
                c.id for c in await a.list_conversations_for_user("telegram", None)
            ] == [own.id]
            await a.update_pi_session(
                other.id, pi_session_id="bad", pi_session_file="/bad"
            )
            await a.touch_message(other.id)
            await a.log_message(other.id, direction="outbound", text="bad")
            await a.point_conversation_to(own.id, original)
            await a.point_conversation_to(other.id, own)
            assert await b.get_conversation(other.id) == original
            current = await a.get_conversation(own.id)
            assert current is not None and current.pi_session_file is None
            await a.update_pi_session(
                own.id, pi_session_id="good", pi_session_file="/research.jsonl"
            )
            target = await conversation(a, "other-chat")
            switched = await a.point_conversation_to(target.id, own)
            assert (
                switched is not None and switched.pi_session_file == "/research.jsonl"
            )
            await a.log_message(own.id, direction="inbound", text="good")
        finally:
            await a.close()
            await b.close()

    asyncio.run(run())
    with sqlite3.connect(first.database_path) as conn:
        assert conn.execute("SELECT text FROM messages").fetchall() == [("good",)]
        assert conn.execute("PRAGMA user_version").fetchone() == (1,)
    assert Path(first.database_path).stat().st_mode & 0o077 == 0


def legacy(path, name):
    database = path.with_suffix(".sqlite3")
    cli.write_raw_config(
        path,
        {
            "instanceName": name,
            "databasePath": str(database),
            "telegram": {"botToken": f"fake:{name}", "allowedUserIds": [123]},
        },
    )
    registry = cli.InstanceRegistry(cli.expand_path(cli.REGISTRY_PATH))
    with registry.locked():
        registry.save(registry.upsert(registry.load(), path, name))

    async def seed():
        db = GatewayDB(str(database))
        try:
            await db.init()
            conv = await conversation(db)
            await db.update_pi_session(
                conv.id, pi_session_id=name, pi_session_file=f"/{name}.jsonl"
            )
            await db.log_message(conv.id, direction="inbound", text=name)
        finally:
            await db.close()

    asyncio.run(seed())
    return database


def migrate(*args):
    cli.migrate_databases(cli.build_parser().parse_args(["migrate-db", *args]))


def test_migration_backups_remapped_ids_and_idempotency(workspace):
    first, second = workspace / "a.yaml", workspace / "b.yaml"
    databases = [legacy(first, "a"), legacy(second, "b")]
    configs_before = [path.read_bytes() for path in (first, second)]
    migrate("--dry-run")
    assert [path.read_bytes() for path in (first, second)] == configs_before
    assert not Path(shared_database_path()).exists()
    migrate()
    after = [load_config(str(path)) for path in (first, second)]
    assert after[0].instance_id != after[1].instance_id
    with sqlite3.connect(shared_database_path()) as conn:
        rows = conn.execute(
            "SELECT id, pi_session_file, instance_id FROM conversations ORDER BY id"
        ).fetchall()
        assert [(r[1], r[2]) for r in rows] == [
            ("/a.jsonl", after[0].instance_id),
            ("/b.jsonl", after[1].instance_id),
        ]
        assert conn.execute(
            "SELECT conversation_id, text FROM messages ORDER BY id"
        ).fetchall() == [(rows[0][0], "a"), (rows[1][0], "b")]
        assert conn.execute("SELECT COUNT(*) FROM database_imports").fetchone()[0] == 2
    for path in databases:
        backups = list(path.parent.glob(path.name + ".backup-*"))
        assert len(backups) == 1
        for copy in (path, backups[0]):
            with sqlite3.connect(copy) as conn:
                assert (
                    conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
                    == 1
                )
                assert "instance_id" not in [
                    r[1] for r in conn.execute("PRAGMA table_info(conversations)")
                ]
    migrate()
    assert [load_config(str(path)).instance_id for path in (first, second)] == [
        c.instance_id for c in after
    ]
    with sqlite3.connect(shared_database_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2


def test_migration_resumes_after_config_write_failure(workspace):
    path = workspace / "bot.yaml"
    database = legacy(path, "bot")
    with patch.object(cli, "write_raw_config", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            migrate("-c", str(path))
    assert load_config(str(path)).instance_id is None
    migrate("-c", str(path))
    assert len(list(workspace.glob(database.name + ".backup-*"))) == 1
    with sqlite3.connect(shared_database_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1


def test_migration_refuses_running_background_and_foreground(workspace):
    path = workspace / "bot.yaml"
    legacy(path, "bot")
    before = path.read_bytes()
    with cli.instance_runtime_lock(path):
        with pytest.raises(SystemExit, match="running"):
            migrate("-c", str(path))
    pid, _ = cli.instance_state(path)
    pid.write_text(str(os.getpid()))
    with pytest.raises(SystemExit, match="Stop gateway"):
        migrate("-c", str(path))
    assert path.read_bytes() == before
    assert not Path(shared_database_path()).exists()


def test_rename_move_duplicate_and_archive_preserve_identity(workspace):
    path = workspace / "bot.yaml"
    initial = configure(path, "bot")
    renamed = configure(path, "new-bot")
    assert initial.instance_id == renamed.instance_id
    duplicate = workspace / "copy.yaml"
    duplicate.write_bytes(path.read_bytes())
    before = duplicate.read_bytes()
    with pytest.raises(ValueError, match="Duplicate instanceId"):
        configure(duplicate, "copy")
    assert duplicate.read_bytes() == before
    duplicate.unlink()
    moved = workspace / "moved.yaml"
    path.rename(moved)
    assert configure(moved, "new-bot").instance_id == initial.instance_id
    cli.remove_gateway(cli.build_parser().parse_args(["remove", "new-bot", "--yes"]))
    with sqlite3.connect(shared_database_path()) as conn:
        assert conn.execute(
            "SELECT archived_at FROM instances WHERE id=?", (initial.instance_id,)
        ).fetchone()[0]
    new = configure(moved, "new-bot")
    assert new.instance_id != initial.instance_id


def test_legacy_reconfigure_does_not_switch_database(workspace):
    path = workspace / "bot.yaml"
    database = legacy(path, "bot")
    config = configure(path, "bot")
    assert config.database_path == str(database)
    assert config.instance_id is None
    assert not Path(shared_database_path()).exists()


def test_legacy_cannot_open_shared_and_new_cannot_open_legacy(workspace):
    path = workspace / "bot.yaml"
    settings = configure(path, "bot")
    legacy_path = legacy(workspace / "old.yaml", "old")

    async def run():
        for db in (
            GatewayDB(settings.database_path),
            GatewayDB(
                str(legacy_path), instance_id=str(uuid.uuid4()), config_path=str(path)
            ),
        ):
            try:
                with pytest.raises(ValueError, match="migrate-db"):
                    await db.init()
            finally:
                await db.close()

    asyncio.run(run())


def test_concurrent_processes_initialize_and_upsert(workspace):
    # Real subprocesses exercise SQLite locks, not just one asyncio.Lock.
    database = shared_database_path()
    config = workspace / "bot.yaml"
    identity = str(uuid.uuid4())
    script = """
import asyncio, sys
from pi_gateway.db import GatewayDB
async def main():
    db = GatewayDB(sys.argv[1], instance_id=sys.argv[2], config_path=sys.argv[3])
    try:
        await db.init()
        for _ in range(30):
            conv = await db.get_or_create_conversation(platform='telegram', chat_id='123',
                thread_id=None, user_id='123', gateway_session_key='same', cwd='/work')
            await db.log_message(conv.id, direction='inbound', text='hello')
    finally:
        await db.close()
asyncio.run(main())
"""
    root = Path(cli.__file__).resolve().parent.parent
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, database, identity, str(config)],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(4)
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, (stdout, stderr)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 120


def test_bad_source_preflight_changes_no_configs(workspace):
    first, second = workspace / "a.yaml", workspace / "b.yaml"
    legacy(first, "a")
    database = legacy(second, "b")
    database.write_bytes(b"not sqlite")
    before = first.read_bytes()
    with pytest.raises(sqlite3.DatabaseError):
        migrate()
    assert first.read_bytes() == before
    assert not Path(shared_database_path()).exists()


def test_empty_legacy_instance_and_explicit_destination(workspace):
    path = workspace / "bot.yaml"
    path.write_text("telegram:\n  botToken: fake:token\n  allowedUserIds: [123]\n")
    target = workspace / "custom.sqlite3"
    migrate("-c", str(path), "--database", str(target))
    settings = load_config(str(path))
    assert settings.database_path == str(target)
    assert settings.instance_id
    assert not Path(shared_database_path()).exists()
    assert json.loads(cli.expand_path(cli.REGISTRY_PATH).read_text())["instances"][0][
        "config"
    ] == str(path)
