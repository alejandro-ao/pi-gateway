from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class TelegramConfig:
    bot_token: str
    allowed_user_ids: set[int] = field(default_factory=set)
    allow_groups: bool = False
    include_user_in_group_session_key: bool = False


@dataclass(slots=True)
class PiConfig:
    command: str = "pi"
    cwd: str = field(default_factory=os.getcwd)
    session_dir: str | None = None
    default_model: str | None = None
    default_provider: str | None = None
    default_thinking: str | None = None
    extra_args: list[str] = field(default_factory=list)
    idle_ttl_seconds: int = 30 * 60


@dataclass(slots=True)
class GatewayConfig:
    database_path: str = "./pi-gateway.sqlite3"
    log_level: str = "INFO"
    telegram: TelegramConfig | None = None
    pi: PiConfig = field(default_factory=PiConfig)


def _env_or_value(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("env:"):
        return os.environ.get(value[4:], "")
    return value


def _expand_path(path: str) -> str:
    return str(Path(os.path.expandvars(os.path.expanduser(path))).resolve())


def load_config(path: str | None) -> GatewayConfig:
    raw: dict[str, Any] = {}
    if path:
        config_path = Path(os.path.expandvars(os.path.expanduser(path)))
        if config_path.exists():
            with config_path.open("r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}

    telegram_raw = raw.get("telegram") or {}
    token = _env_or_value(telegram_raw.get("botToken") or telegram_raw.get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    telegram = None
    if token:
        telegram = TelegramConfig(
            bot_token=str(token),
            allowed_user_ids={int(x) for x in telegram_raw.get("allowedUserIds", telegram_raw.get("allowed_user_ids", []))},
            allow_groups=bool(telegram_raw.get("allowGroups", telegram_raw.get("allow_groups", False))),
            include_user_in_group_session_key=bool(
                telegram_raw.get("includeUserInGroupSessionKey", telegram_raw.get("include_user_in_group_session_key", False))
            ),
        )

    pi_raw = raw.get("pi") or {}
    pi = PiConfig(
        command=str(pi_raw.get("command", "pi")),
        cwd=_expand_path(str(pi_raw.get("cwd", os.getcwd()))),
        session_dir=_expand_path(str(pi_raw["sessionDir"])) if pi_raw.get("sessionDir") else None,
        default_model=pi_raw.get("defaultModel") or pi_raw.get("default_model"),
        default_provider=pi_raw.get("defaultProvider") or pi_raw.get("default_provider"),
        default_thinking=pi_raw.get("defaultThinking") or pi_raw.get("default_thinking"),
        extra_args=[str(x) for x in pi_raw.get("extraArgs", pi_raw.get("extra_args", []))],
        idle_ttl_seconds=int(pi_raw.get("idleTtlSeconds", pi_raw.get("idle_ttl_seconds", 30 * 60))),
    )

    db = raw.get("databasePath") or raw.get("database_path") or os.environ.get("PI_GATEWAY_DB", "./pi-gateway.sqlite3")
    return GatewayConfig(
        database_path=_expand_path(str(db)),
        log_level=str(raw.get("logLevel", raw.get("log_level", os.environ.get("PI_GATEWAY_LOG_LEVEL", "INFO")))).upper(),
        telegram=telegram,
        pi=pi,
    )
