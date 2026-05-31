from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

import yaml

from .config import load_config
from .db import GatewayDB
from .session_manager import PiSessionManager
from .telegram_bot import TelegramGateway

DEFAULT_CONFIG_PATH = "~/.config/pi-gateway/config.yaml"


async def run_gateway(config_path: str | None) -> None:
    config = load_config(config_path or DEFAULT_CONFIG_PATH)
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not config.telegram:
        raise SystemExit("Telegram is not configured. Run `pi-gateway configure telegram` or set TELEGRAM_BOT_TOKEN.")

    db = GatewayDB(config.database_path)
    await db.init()
    sessions = PiSessionManager(config.pi, db)
    await sessions.start()
    telegram = TelegramGateway(config, db, sessions)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    await telegram.start()
    try:
        await stop_event.wait()
    finally:
        await telegram.stop()
        await sessions.stop()
        await db.close()


def expand_path(path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path))).resolve()


def load_raw_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def write_raw_config(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def _prompt(message: str, *, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{message}{suffix}: ").strip()
    return value or (default or "")


def _prompt_int(message: str, *, default: int | None = None, required: bool = False) -> int | None:
    while True:
        value = _prompt(message, default=str(default) if default is not None else None)
        if not value and not required:
            return None
        try:
            return int(value)
        except ValueError:
            print("Please enter a numeric Telegram user id.")


def configure_telegram(args: argparse.Namespace) -> None:
    path = expand_path(args.config or DEFAULT_CONFIG_PATH)
    data = load_raw_config(path)
    interactive = sys.stdin.isatty()

    data.setdefault("databasePath", "~/.local/share/pi-gateway/pi-gateway.sqlite3")
    data.setdefault("logLevel", "INFO")

    telegram = data.setdefault("telegram", {})
    existing_token = telegram.get("botToken")
    if args.bot_token:
        telegram["botToken"] = args.bot_token
    elif interactive:
        print("Telegram setup")
        print("- Create a bot with @BotFather and paste its token here.")
        print("- Leave blank to read the token from TELEGRAM_BOT_TOKEN at runtime.")
        token_default = existing_token if existing_token and str(existing_token).startswith("env:") else None
        token = _prompt("Telegram bot token", default=token_default)
        telegram["botToken"] = token or existing_token or "env:TELEGRAM_BOT_TOKEN"
    else:
        telegram.setdefault("botToken", "env:TELEGRAM_BOT_TOKEN")

    existing_ids = telegram.get("allowedUserIds") or []
    existing_id = int(existing_ids[0]) if existing_ids else None
    if args.allowed_user_id is not None:
        allowed_user_id = int(args.allowed_user_id)
    elif interactive:
        print("\nSecurity setup")
        print("Only this Telegram user id will be allowed to use the bot.")
        print("Tip: message @userinfobot or @RawDataBot on Telegram to find your numeric user id.")
        allowed_user_id = _prompt_int("Allowed Telegram user id", default=existing_id, required=existing_id is None)
    else:
        allowed_user_id = existing_id

    if allowed_user_id is not None:
        telegram["allowedUserIds"] = [allowed_user_id]
    else:
        telegram.setdefault("allowedUserIds", [])

    telegram["allowGroups"] = bool(args.allow_groups)
    telegram["includeUserInGroupSessionKey"] = bool(args.include_user_in_group_session_key)

    pi = data.setdefault("pi", {})
    pi.setdefault("command", "pi")
    existing_cwd = str(pi.get("cwd") or Path.cwd())
    if args.pi_cwd:
        pi["cwd"] = str(expand_path(args.pi_cwd))
    elif interactive:
        print("\nPi setup")
        current_cwd = str(Path.cwd())
        if existing_cwd != current_cwd:
            print(f"Existing configured Pi directory: {existing_cwd}")
        pi["cwd"] = str(expand_path(_prompt("Directory where Pi should run sessions", default=current_cwd)))
    else:
        pi["cwd"] = existing_cwd
    pi.setdefault("idleTtlSeconds", 1800)
    pi.setdefault("extraArgs", [])

    write_raw_config(path, data)
    print(f"\nWrote config: {path}")
    if str(telegram.get("botToken", "")).startswith("env:"):
        print("Telegram token is configured as env:TELEGRAM_BOT_TOKEN; set that environment variable when running the daemon.")
    if allowed_user_id is None:
        print("WARNING: no allowed Telegram user id was configured. Set one before exposing the bot.")
    else:
        print(f"Only Telegram user id {allowed_user_id} is allowed.")


def show_config_path(args: argparse.Namespace) -> None:
    print(expand_path(args.config or DEFAULT_CONFIG_PATH))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pi-gateway")
    parser.add_argument("-c", "--config", help=f"Path to config YAML (default: {DEFAULT_CONFIG_PATH})")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Run the Telegram gateway daemon")
    run.add_argument("-c", "--config", help=argparse.SUPPRESS)

    configure = sub.add_parser("configure", help="Configure gateway integrations")
    configure.set_defaults(_help_parser=configure)
    configure_sub = configure.add_subparsers(dest="configure_command")
    telegram = configure_sub.add_parser("telegram", help="Create/update Telegram gateway config")
    telegram.set_defaults(_help_parser=telegram)
    telegram.add_argument("--bot-token", help="Telegram bot token. Omit to use env:TELEGRAM_BOT_TOKEN")
    telegram.add_argument("--allowed-user-id", type=int, help="Only accept messages from this Telegram user id")
    telegram.add_argument("--pi-cwd", help="Working directory where Pi should run sessions")
    telegram.add_argument("--allow-groups", action="store_true", help="Allow the bot in group chats")
    telegram.add_argument(
        "--include-user-in-group-session-key",
        action="store_true",
        help="Separate group sessions by sender user id as well as chat/thread",
    )

    sub.add_parser("config-path", help="Print the effective default config path")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "run":
        asyncio.run(run_gateway(args.config))
    elif args.command == "configure" and args.configure_command == "telegram":
        configure_telegram(args)
    elif args.command == "config-path":
        show_config_path(args)
    elif hasattr(args, "_help_parser"):
        args._help_parser.print_help()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
