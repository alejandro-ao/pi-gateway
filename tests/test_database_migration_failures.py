import asyncio
import fcntl
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from pi_gateway import cli
from pi_gateway.config import load_config
from pi_gateway.db import GatewayDB
from pi_gateway.migration import import_instance
from pi_gateway.storage import shared_database_path


def seed(path, *, identity=None, config=None):
    async def run():
        db = GatewayDB(
            str(path), instance_id=identity, config_path=str(config) if config else None
        )
        try:
            await db.init()
            conv = await db.get_or_create_conversation(
                platform="telegram",
                chat_id="1",
                thread_id=None,
                user_id="1",
                gateway_session_key="same",
                cwd="/work",
            )
            await db.log_message(conv.id, direction="inbound", text="original")
        finally:
            await db.close()

    asyncio.run(run())


def test_destination_collision_rolls_back_import(tmp_path):
    identity = str(uuid.uuid4())
    config = tmp_path / "bot.yaml"
    config.write_text(f"instanceId: {identity}\n")
    source, target = tmp_path / "old.sqlite3", tmp_path / "new.sqlite3"
    seed(source)
    seed(target, identity=identity, config=config)
    with pytest.raises(sqlite3.IntegrityError):
        import_instance(
            str(target),
            config_path=config,
            source=source,
            instance_id=identity,
            name=None,
        )
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM database_imports").fetchone()[0] == 0


def test_shared_source_imports_only_selected_owner(tmp_path):
    source, target = tmp_path / "source.sqlite3", tmp_path / "target.sqlite3"
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    seed(source, identity=first, config=a)
    seed(source, identity=second, config=b)
    import_instance(
        str(target), config_path=a, source=source, instance_id=first, name=None
    )
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT instance_id FROM conversations").fetchall() == [
            (first,)
        ]
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1


def test_dry_run_does_not_upgrade_legacy_discovery_index(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PI_GATEWAY_DB", raising=False)
    path = tmp_path / "bot.yaml"
    path.write_text("logLevel: INFO\n")
    registry = cli.expand_path(cli.REGISTRY_PATH)
    registry.parent.mkdir(parents=True)
    registry.write_text(f'["{path}"]')
    before = registry.read_bytes()
    args = cli.build_parser().parse_args(["migrate-db", "--dry-run"])
    cli.migrate_databases(args)
    assert registry.read_bytes() == before
    assert not Path(shared_database_path()).exists()


def test_path_overrides_and_duplicate_identity_across_databases(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    chosen = tmp_path / "chosen.sqlite3"
    monkeypatch.setenv("PI_GATEWAY_DB", str(chosen))
    parser = cli.build_parser()
    cli.init_instance(parser.parse_args(["init", "--allowed-user-id", "123"]))
    path = cli.local_config()
    initial = load_config(str(path))
    assert initial.database_path == str(chosen)
    duplicate = tmp_path / "copy.yaml"
    data = cli.load_raw_config(path)
    data["databasePath"] = str(tmp_path / "another.sqlite3")
    cli.write_raw_config(duplicate, data)
    before = duplicate.read_bytes()
    with pytest.raises(ValueError, match="Duplicate instanceId"):
        cli.configure_telegram(
            parser.parse_args(
                ["configure", "telegram", "-c", str(duplicate), "--name", "copy"]
            )
        )
    assert duplicate.read_bytes() == before
    assert not Path(shared_database_path()).exists()


def test_unsupported_future_schema_is_refused(tmp_path):
    database = tmp_path / "future.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.execute("PRAGMA user_version=99")

    async def run():
        db = GatewayDB(
            str(database),
            instance_id=str(uuid.uuid4()),
            config_path=str(tmp_path / "config.yaml"),
        )
        try:
            with pytest.raises(ValueError, match="newer"):
                await db.init()
        finally:
            await db.close()

    asyncio.run(run())


def test_removal_archives_and_preserves_shared_history(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PI_GATEWAY_DB", raising=False)
    monkeypatch.chdir(tmp_path)
    parser = cli.build_parser()
    cli.init_instance(
        parser.parse_args(["init", "--name", "bot", "--allowed-user-id", "123"])
    )
    config = cli.local_config()
    settings = load_config(str(config))
    seed(Path(settings.database_path), identity=settings.instance_id, config=config)
    cli.remove_gateway(parser.parse_args(["remove", "bot", "--yes"]))
    assert not config.exists()
    with sqlite3.connect(settings.database_path) as conn:
        assert conn.execute("SELECT archived_at FROM instances").fetchone()[0]
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1


def test_configuration_write_failure_rolls_back_registration(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PI_GATEWAY_DB", raising=False)
    monkeypatch.chdir(tmp_path)
    parser = cli.build_parser()
    with patch.object(cli, "write_raw_config", side_effect=OSError("disk error")):
        with pytest.raises(OSError):
            cli.init_instance(
                parser.parse_args(["init", "--name", "bot", "--allowed-user-id", "123"])
            )
    assert not cli.local_config().exists()
    with sqlite3.connect(shared_database_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM instances").fetchone()[0] == 0
    cli.init_instance(
        parser.parse_args(["init", "--name", "bot", "--allowed-user-id", "123"])
    )
    assert load_config(str(cli.local_config())).instance_id


def test_migration_detects_preupgrade_token_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PI_GATEWAY_DB", raising=False)
    path = tmp_path / "bot.yaml"
    path.write_text("telegram:\n  botToken: fake:token\n  allowedUserIds: [123]\n")
    lock_path = tmp_path / "token.lock"
    monkeypatch.setattr(cli, "bot_token_lock_path", lambda token: lock_path)
    with lock_path.open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with pytest.raises(SystemExit, match="bot token is running"):
            cli.migrate_databases(
                cli.build_parser().parse_args(["migrate-db", "-c", str(path)])
            )
    assert not Path(shared_database_path()).exists()
    assert load_config(str(path)).instance_id is None


def test_failed_atomic_config_write_preserves_original(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("logLevel: INFO\n")
    with patch.object(cli.os, "replace", side_effect=OSError("disk error")):
        with pytest.raises(OSError):
            cli.write_raw_config(path, {"logLevel": "DEBUG"})
    assert path.read_text() == "logLevel: INFO\n"
    assert not list(tmp_path.glob(".config-*"))
