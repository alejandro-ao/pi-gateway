from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from .config import load_config
from .db import GatewayDB
from .session_manager import PiSessionManager
from .telegram_bot import TelegramGateway

DEFAULT_CONFIG_PATH = "~/.config/pi-gateway/config.yaml"
DEFAULT_STATE_DIR = "~/.local/state/pi-gateway"
DEFAULT_PID_PATH = f"{DEFAULT_STATE_DIR}/pi-gateway.pid"
DEFAULT_LOG_PATH = f"{DEFAULT_STATE_DIR}/pi-gateway.log"


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
    await telegram.notify_lifecycle("🟢 Pi gateway connected.")
    try:
        await stop_event.wait()
    finally:
        try:
            await telegram.notify_lifecycle("🔴 Pi gateway disconnected.")
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


def pid_path() -> Path:
    return expand_path(DEFAULT_PID_PATH)


def log_path() -> Path:
    return expand_path(DEFAULT_LOG_PATH)


def is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_pid() -> int | None:
    path = pid_path()
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def start_background(args: argparse.Namespace) -> None:
    existing = read_pid()
    if existing and is_process_running(existing):
        print(f"pi-gateway is already running with PID {existing}")
        print(f"Log: {log_path()}")
        return

    pid_file = pid_path()
    log_file = log_path()
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = [sys.argv[0], "run"]
    if args.config:
        cmd += ["--config", args.config]

    with log_file.open("ab", buffering=0) as out:
        out.write(f"\n--- starting pi-gateway at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode())
        process = subprocess.Popen(
            cmd,
            stdout=out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )

    pid_file.write_text(str(process.pid), encoding="utf-8")
    print(f"Started pi-gateway in the background with PID {process.pid}")
    print(f"Log: {log_file}")
    print("Stop with: pi-gateway stop")
    print("Follow logs with: pi-gateway logs")


def stop_background(args: argparse.Namespace) -> None:
    pid = read_pid()
    if not pid:
        print("pi-gateway is not running (no PID file found)")
        return
    if not is_process_running(pid):
        pid_path().unlink(missing_ok=True)
        print(f"pi-gateway is not running (stale PID {pid} removed)")
        return

    os.kill(pid, signal.SIGTERM)
    timeout = float(args.timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_process_running(pid):
            pid_path().unlink(missing_ok=True)
            print(f"Stopped pi-gateway PID {pid}")
            return
        time.sleep(0.2)

    print(f"pi-gateway PID {pid} did not stop within {timeout:g}s")
    print("Use kill manually if needed.")


def status_background(args: argparse.Namespace) -> None:
    pid = read_pid()
    if pid and is_process_running(pid):
        print(f"pi-gateway is running with PID {pid}")
    elif pid:
        print(f"pi-gateway is not running (stale PID {pid})")
    else:
        print("pi-gateway is not running")
    print(f"Log: {log_path()}")


def show_logs(args: argparse.Namespace) -> None:
    path = log_path()
    if not path.exists():
        print(f"Log file does not exist yet: {path}")
        return
    cmd = ["tail", "-n", str(args.lines)]
    if args.follow:
        cmd.append("-f")
    cmd.append(str(path))
    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pi-gateway")
    parser.add_argument("-c", "--config", help=f"Path to config YAML (default: {DEFAULT_CONFIG_PATH})")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Run the Telegram gateway daemon in the foreground")
    run.add_argument("-c", "--config", help=argparse.SUPPRESS)

    start = sub.add_parser("start", help="Start pi-gateway in the background")
    start.add_argument("-c", "--config", help=argparse.SUPPRESS)

    stop = sub.add_parser("stop", help="Stop a background pi-gateway process")
    stop.add_argument("--timeout", type=float, default=10, help="Seconds to wait for graceful shutdown")

    sub.add_parser("status", help="Show background process status")

    logs = sub.add_parser("logs", help="Show pi-gateway log file")
    logs.add_argument("-n", "--lines", type=int, default=80, help="Number of lines to show")
    logs.add_argument("-f", "--follow", action="store_true", help="Follow log output")

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
    elif args.command == "start":
        start_background(args)
    elif args.command == "stop":
        stop_background(args)
    elif args.command == "status":
        status_background(args)
    elif args.command == "logs":
        show_logs(args)
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
