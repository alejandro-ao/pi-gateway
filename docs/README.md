# Pi Gateway Documentation

This directory documents the Pi Gateway codebase for future maintainers and AI agents.

Pi Gateway is a small Python daemon that connects Telegram to persistent Pi coding-agent sessions. Telegram chat/session identity is stored in SQLite, while Pi itself remains the source of truth for conversation history through its JSONL session files.

## Reading Order

| # | Document | Description |
|---|----------|-------------|
| 01 | [Architecture Overview](01-architecture-overview.md) | Big-picture structure and design decisions |
| 02 | [Startup and CLI Flow](02-startup-and-cli-flow.md) | How `pi-gateway` commands start, stop, configure, and run the daemon |
| 03 | [Telegram Gateway](03-telegram-gateway.md) | Telegram adapter, commands, authorization, lifecycle messages |
| 04 | [Pi RPC Integration](04-pi-rpc-integration.md) | How the gateway talks to `pi --mode rpc` subprocesses |
| 05 | [Session Mapping and SQLite](05-session-mapping-and-sqlite.md) | How Telegram conversations map to Pi session files |
| 06 | [Configuration and Deployment](06-configuration-and-deployment.md) | Config files, uv tool usage, background process, systemd |
| 07 | [Troubleshooting](07-troubleshooting.md) | Common problems and debugging commands |

## Key Files

| File | Purpose |
|------|---------|
| `pi_gateway/cli.py` | CLI entry point, configuration wizard, foreground/background process commands |
| `pi_gateway/config.py` | YAML/env config loading and typed config objects |
| `pi_gateway/db.py` | SQLite schema and conversation/message persistence |
| `pi_gateway/telegram_bot.py` | Telegram bot adapter and Telegram command handling |
| `pi_gateway/session_manager.py` | Caches and serializes per-conversation Pi RPC clients |
| `pi_gateway/pi_rpc.py` | JSONL RPC client for spawned `pi --mode rpc` processes |
| `examples/config.yaml` | Example config file |
| `systemd/pi-gateway.service` | systemd service template |

## Current Design Summary

```text
Telegram user
  ↓
python-telegram-bot
  ↓
TelegramGateway
  ↓
GatewayDB maps telegram:<chat>:<thread?>:<user?> to Pi session file
  ↓
PiSessionManager chooses/caches PiRpcClient
  ↓
pi --mode rpc --session <session-file>
  ↓
Pi JSONL session history under ~/.pi/agent/sessions
```

## Maintenance Notes

- Keep Pi conversation history in Pi JSONL files. Do not duplicate it into SQLite.
- SQLite is for gateway metadata: Telegram identity, Pi session file/id/name, audit messages.
- `pi-gateway run` is foreground mode.
- `pi-gateway start/stop/status/logs` is a lightweight background-process wrapper for personal VPS use.
- For production, systemd is still preferred.
