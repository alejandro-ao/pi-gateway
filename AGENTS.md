# AGENTS.md

Guidance for AI agents working on this repository.

## Project Overview

Pi Gateway is a Python CLI/daemon that connects Telegram to persistent Pi coding-agent sessions.

Core idea:

```text
Telegram conversation identity -> SQLite mapping -> Pi JSONL session file -> pi --mode rpc
```

Pi owns conversation history. The gateway owns Telegram routing, authorization, process management, and session metadata.

## Read These First

Before making changes, read:

1. [`README.md`](README.md) - user-facing install/run instructions
2. [`docs/README.md`](docs/README.md) - documentation index
3. [`docs/01-architecture-overview.md`](docs/01-architecture-overview.md) - architecture and design constraints
4. The specific doc for the area you are modifying:
   - CLI/startup: [`docs/02-startup-and-cli-flow.md`](docs/02-startup-and-cli-flow.md)
   - Telegram: [`docs/03-telegram-gateway.md`](docs/03-telegram-gateway.md)
   - Pi RPC: [`docs/04-pi-rpc-integration.md`](docs/04-pi-rpc-integration.md)
   - SQLite/session mapping: [`docs/05-session-mapping-and-sqlite.md`](docs/05-session-mapping-and-sqlite.md)
   - deployment/config: [`docs/06-configuration-and-deployment.md`](docs/06-configuration-and-deployment.md)
   - debugging: [`docs/07-troubleshooting.md`](docs/07-troubleshooting.md)

## Important Commands

Syntax check:

```bash
python3 -m compileall pi_gateway main.py
```

CLI smoke tests:

```bash
python3 main.py --help
python3 main.py configure --help
python3 main.py configure telegram --help
python3 main.py status
```

uv development:

```bash
uv sync
uv run pi-gateway --help
```

Install/update as uv tool from checkout:

```bash
uv tool install --force .
```

## Runtime Commands

Foreground daemon:

```bash
pi-gateway run
```

Background daemon:

```bash
pi-gateway start
pi-gateway status
pi-gateway logs -f
pi-gateway stop
```

Interactive Telegram configuration:

```bash
pi-gateway configure telegram
```

## Repository Structure

```text
pi_gateway/
├── cli.py              # CLI, config wizard, foreground/background process commands
├── config.py           # YAML/env config loader and dataclasses
├── db.py               # SQLite schema and gateway persistence
├── pi_rpc.py           # JSONL RPC subprocess client for `pi --mode rpc`
├── session_manager.py  # per-conversation Pi client cache/locks
└── telegram_bot.py     # Telegram adapter, auth, commands, lifecycle notifications
```

## Design Rules

- Do not duplicate Pi conversation history in SQLite.
- Store gateway metadata in SQLite: Telegram identity, Pi session file/id/name, audit messages.
- Prefer `pi_session_file` over only `pi_session_id` when resuming sessions.
- Keep Telegram user allowlisting secure by default.
- Group chats should remain disabled by default.
- Keep `pi-gateway run` as foreground mode; `start/stop/status/logs` are convenience wrappers.
- For production VPS deployment, continue to recommend systemd.
- If changing behavior, update `README.md` and relevant files in `docs/`.

## Security Notes

This gateway can expose a coding agent with filesystem and shell tools. Be careful.

- Maintain `telegram.allowedUserIds` checks.
- Do not add broad unauthenticated webhooks or APIs.
- Do not log secrets such as Telegram bot tokens.
- If adding new platforms, implement explicit allowlists.
- If adding group support, consider session-key and authorization implications.

## Pi Integration Notes

The gateway uses Pi RPC, not the Pi SDK.

Important Pi RPC assumptions:

- Start command is `pi --mode rpc`.
- Existing sessions can resume with `--session <session-file>`.
- JSONL records are newline-delimited.
- Prompt completion is detected by consuming events until `agent_end`.

If Pi RPC protocol changes, update:

- `pi_gateway/pi_rpc.py`
- [`docs/04-pi-rpc-integration.md`](docs/04-pi-rpc-integration.md)

## Git Hygiene

- Run syntax checks before committing.
- Keep commits focused and descriptive.
- Do not commit local `config.yaml`, SQLite DBs, logs, PID files, virtualenvs, or `__pycache__`.
- `.gitignore` already excludes common runtime state.

## Documentation Requirement

When changing code, update relevant docs:

- User-facing behavior: `README.md`
- Architecture or internals: `docs/*.md`
- Agent handoff instructions: this `AGENTS.md`
