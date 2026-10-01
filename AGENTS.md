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

Initialize a local instance in the Pi working directory:

```bash
pi-gateway init --name research
pi-gateway instances
pi-gateway status -i research
pi-gateway remove research --dry-run  # preview config-only deletion
```

`configure telegram` also creates/updates a local config by default. `init` and `configure telegram` accept optional `--name` for a unique gateway identifier; the registry indexes all configured bots (including custom `-c` files), and `-i` accepts names or bot directories. Both commands also accept optional `--model provider/model-id` and `--thinking <level>` for Pi startup defaults. Interactive setup uses `pi --list-models` with the configured Pi executable/agent directory for a fuzzy-searchable model picker; on listing failure it falls back to manual entry. Empty keeps the current model, `default` clears it; noninteractive flags skip catalog lookup. Existing values are retained when flags are omitted. `-c` and `-i` work before or after runtime subcommands; `start` must print bot-specific stop/log commands using the resolved absolute config path. Explicit `-c` overrides local discovery; legacy global config is the runtime fallback. Local state (config, DB, PID, log) belongs in `.pi-gateway/`; add that directory to bot projects' `.gitignore`.

## Repository Structure

```text
pi_gateway/
├── cli.py              # CLI, config wizard, foreground/background process commands
├── config.py           # YAML/env config loader and dataclasses
├── db.py               # SQLite schema and gateway persistence
├── instance_registry.py # names to config paths; upgrades legacy path-only index
├── pi_rpc.py           # JSONL RPC subprocess client for `pi --mode rpc`
├── session_manager.py  # per-conversation Pi client cache/locks
└── telegram_bot.py     # Telegram adapter, auth, commands, lifecycle notifications
```

## Design Rules

- `remove <name>` deletes only the named gateway's config and registry entry; never delete its database, logs, directories, or Pi session files. Refuse live background PIDs unless `--stop` succeeds.
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
- Prompt completion requires `agent_settled`; `agent_end` only ends one low-level run and Pi may still compact/retry. Private-chat Telegram previews may stream `message_update.assistantMessageEvent.text_delta` and temporary sanitized tool names from `tool_execution_start`/`tool_execution_end` through rate-limited ephemeral drafts; never expose tool args/results, thinking content, or treat drafts as the final reply. Keep the group/unsupported-API working-message fallback. `/status` must distinguish Pi's instantaneous generating flag from Telegram draft availability.

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
