from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import importlib.metadata
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .config import custom_state_dir, default_database_path, load_config
from .db import GatewayDB
from .session_manager import PiSessionManager
from .telegram_bot import TelegramGateway
from .version_check import check_version, format_update_notice

DEFAULT_CONFIG_PATH = "~/.config/pi-gateway/config.yaml"
LOCAL_CONFIG = ".pi-gateway/config.yaml"
REGISTRY_PATH = "~/.config/pi-gateway/instances.json"
DEFAULT_STATE_DIR = "~/.local/state/pi-gateway"
DEFAULT_PID_PATH = f"{DEFAULT_STATE_DIR}/pi-gateway.pid"
DEFAULT_LOG_PATH = f"{DEFAULT_STATE_DIR}/pi-gateway.log"
_HELD_LOCK_FILES: list[Any] = []


def acquire_bot_token_lock(bot_token: str) -> None:
    token_hash = hashlib.sha256(bot_token.encode("utf-8")).hexdigest()[:16]
    path = Path(tempfile.gettempdir()) / f"pi-gateway-telegram-{token_hash}.lock"
    lock_file = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.seek(0)
        existing = lock_file.read().strip() or "unknown"
        lock_file.close()
        raise SystemExit(
            "Another pi-gateway process is already running with this Telegram bot token "
            f"(lock: {path}, pid: {existing}). Stop it before starting another instance."
        )
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    _HELD_LOCK_FILES.append(lock_file)


def local_config(directory: Path | None = None) -> Path:
    return (directory or Path.cwd()).resolve() / LOCAL_CONFIG


def resolve_config(args: argparse.Namespace, *, creating: bool = False) -> Path:
    if args.config:
        return expand_path(args.config)
    if getattr(args, "instance", None):
        path = local_config(expand_path(args.instance))
        if not path.is_file():
            raise SystemExit(f"No initialized instance at {path}")
        return path
    local = local_config()
    if creating or local.is_file():
        return local
    return expand_path(DEFAULT_CONFIG_PATH)


def instance_state(config: Path) -> tuple[Path, Path]:
    config = config.expanduser().resolve()
    if config == expand_path(DEFAULT_CONFIG_PATH):
        return pid_path(), log_path()
    if config.name == "config.yaml" and config.parent.name == ".pi-gateway":
        state_dir = config.parent
    else:
        state_dir = custom_state_dir(config)
    return state_dir / "pi-gateway.pid", state_dir / "pi-gateway.log"


def register_instance(config: Path) -> None:
    registry = expand_path(REGISTRY_PATH)
    registry.parent.mkdir(parents=True, exist_ok=True)
    lock_path = registry.with_suffix(".lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        paths = []
        if registry.exists():
            paths = json.loads(registry.read_text(encoding="utf-8"))
        entry = str(config.resolve())
        if entry not in paths:
            paths.append(entry)
            with tempfile.NamedTemporaryFile(mode="w", dir=registry.parent, delete=False, encoding="utf-8") as tmp:
                json.dump(paths, tmp, indent=2)
                tmp.write("\n")
                tmp_path = Path(tmp.name)
            try:
                tmp_path.replace(registry)
            finally:
                tmp_path.unlink(missing_ok=True)


def list_instances(args: argparse.Namespace) -> None:
    registry = expand_path(REGISTRY_PATH)
    paths = json.loads(registry.read_text(encoding="utf-8")) if registry.exists() else []
    if not paths:
        print("No registered local instances. Run `pi-gateway init` in a bot directory.")
        return
    for entry in paths:
        config = Path(entry)
        if not config.is_file():
            state = "missing config"
        else:
            pid, _ = instance_state(config)
            running = read_pid(pid)
            state = f"running (PID {running})" if running and is_process_running(running) else "stopped"
        print(f"{config.parent.parent}: {state}  (config: {config})")


def init_instance(args: argparse.Namespace) -> None:
    path = local_config()
    if path.exists():
        raise SystemExit(f"Instance already exists: {path}")
    args.config = str(path)
    configure_telegram(args)


async def run_gateway(config_path: str | None) -> None:
    config = load_config(config_path)
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not config.telegram:
        raise SystemExit("Telegram is not configured. Run `pi-gateway configure telegram` or set TELEGRAM_BOT_TOKEN.")
    if not config.telegram.allowed_user_ids:
        raise SystemExit("Set telegram.allowedUserIds before starting the gateway.")
    acquire_bot_token_lock(config.telegram.bot_token)

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
    lifecycle_message = "🟢 Pi gateway connected."
    update_notice = format_update_notice(await check_version())
    if update_notice:
        lifecycle_message += f"\n\n{update_notice}"
    await telegram.notify_lifecycle(lifecycle_message)
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
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        os.fchmod(f.fileno(), 0o600)
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
    path = resolve_config(args, creating=True)
    data = load_raw_config(path)
    interactive = sys.stdin.isatty()

    default_db = (
        "~/.local/share/pi-gateway/pi-gateway.sqlite3"
        if path == expand_path(DEFAULT_CONFIG_PATH)
        else default_database_path(str(path))
    )
    data.setdefault("databasePath", default_db)
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
    if args.pi_agent_dir:
        pi["agentDir"] = str(expand_path(args.pi_agent_dir))
    pi.setdefault("idleTtlSeconds", 1800)
    pi.setdefault("extraArgs", [])

    write_raw_config(path, data)
    if path == local_config() or (path.name == "config.yaml" and path.parent.name == ".pi-gateway"):
        register_instance(path)
        print("Keep .pi-gateway/ out of version control: it may contain secrets and session metadata.")
    print(f"\nWrote config: {path}")
    if str(telegram.get("botToken", "")).startswith("env:"):
        print(f"Set {str(telegram['botToken'])[4:]} in the daemon environment for this bot.")
    if allowed_user_id is None:
        print("WARNING: no allowed Telegram user id was configured. Set one before exposing the bot.")
    else:
        print(f"Only Telegram user id {allowed_user_id} is allowed.")


def show_config_path(args: argparse.Namespace) -> None:
    print(resolve_config(args))


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


def read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def start_background(args: argparse.Namespace) -> None:
    config = resolve_config(args)
    if not config.is_file():
        raise SystemExit(f"Config not found: {config}. Run `pi-gateway init` first.")
    settings = load_config(str(config))
    if not settings.telegram or not settings.telegram.allowed_user_ids:
        raise SystemExit("Configure a Telegram token and allowed user ID before starting.")
    pid_file, log_file = instance_state(config)
    existing = read_pid(pid_file)
    if existing and is_process_running(existing):
        print(f"pi-gateway is already running with PID {existing}")
        print(f"Log: {log_file}")
        return

    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = [sys.argv[0], "run", "--config", str(config)]

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
    pid_file, _ = instance_state(resolve_config(args))
    pid = read_pid(pid_file)
    if not pid:
        print("pi-gateway is not running (no PID file found)")
        return
    if not is_process_running(pid):
        pid_file.unlink(missing_ok=True)
        print(f"pi-gateway is not running (stale PID {pid} removed)")
        return

    os.kill(pid, signal.SIGTERM)
    timeout = float(args.timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_process_running(pid):
            pid_file.unlink(missing_ok=True)
            print(f"Stopped pi-gateway PID {pid}")
            return
        time.sleep(0.2)

    print(f"pi-gateway PID {pid} did not stop within {timeout:g}s")
    print("Use kill manually if needed.")


def status_background(args: argparse.Namespace) -> None:
    pid_file, log_file = instance_state(resolve_config(args))
    pid = read_pid(pid_file)
    if pid and is_process_running(pid):
        print(f"pi-gateway is running with PID {pid}")
    elif pid:
        print(f"pi-gateway is not running (stale PID {pid})")
    else:
        print("pi-gateway is not running")
    print(f"Log: {log_file}")


def show_logs(args: argparse.Namespace) -> None:
    _, path = instance_state(resolve_config(args))
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


def package_version() -> str:
    try:
        return importlib.metadata.version("pi-gateway")
    except importlib.metadata.PackageNotFoundError:
        return __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pi-gateway")
    parser.add_argument("--version", action="version", version=f"pi-gateway {package_version()}")
    parser.add_argument("-c", "--config", help="Path to config YAML (overrides local instance)")
    parser.add_argument("-i", "--instance", help="Manage an initialized bot directory from anywhere")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Run the Telegram gateway daemon in the foreground")
    run.add_argument("-c", "--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    start = sub.add_parser("start", help="Start pi-gateway in the background")
    start.add_argument("-c", "--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

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
    telegram.add_argument("--pi-agent-dir", help="Optional isolated Pi agent directory for global skills and credentials")
    telegram.add_argument("--allow-groups", action="store_true", help="Allow the bot in group chats")
    telegram.add_argument(
        "--include-user-in-group-session-key",
        action="store_true",
        help="Separate group sessions by sender user id as well as chat/thread",
    )

    sub.add_parser("config-path", help="Print the effective config path")
    sub.add_parser("instances", help="List initialized local instances")
    init = sub.add_parser("init", help="Initialize a bot in the current directory")
    init.add_argument("--bot-token")
    init.add_argument("--allowed-user-id", type=int)
    init.add_argument("--pi-cwd")
    init.add_argument("--pi-agent-dir")
    init.add_argument("--allow-groups", action="store_true")
    init.add_argument("--include-user-in-group-session-key", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "run":
        config = resolve_config(args)
        if not config.is_file():
            raise SystemExit(f"Config not found: {config}. Run `pi-gateway init` first.")
        asyncio.run(run_gateway(str(config)))
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
    elif args.command == "init":
        init_instance(args)
    elif args.command == "instances":
        list_instances(args)
    elif args.command == "config-path":
        show_config_path(args)
    elif hasattr(args, "_help_parser"):
        args._help_parser.print_help()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
