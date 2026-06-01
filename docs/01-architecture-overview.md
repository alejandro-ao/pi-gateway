# Architecture Overview

Pi Gateway is a personal gateway daemon that lets a Telegram user communicate with Pi from a VPS or always-on machine.

The central design decision is:

> The gateway owns platform routing and metadata. Pi owns the agent conversation history.

Pi sessions are still normal Pi JSONL sessions. The gateway stores only enough SQLite metadata to reconnect a Telegram conversation to the right Pi session file.

## Main Components

```text
pi-gateway CLI
  ├── configure telegram
  ├── run / start / stop / status / logs
  │
  ▼
Gateway runtime
  ├── Config loader
  ├── SQLite GatewayDB
  ├── TelegramGateway adapter
  └── PiSessionManager
         └── PiRpcClient subprocesses
                └── pi --mode rpc
```

## Runtime Flow

```text
Telegram message arrives
  ↓
TelegramGateway authorizes sender
  ↓
Build gateway session key
  ↓
GatewayDB finds/creates conversation row
  ↓
Command router handles gateway commands, or forwards normal text
  ↓
PiSessionManager gets per-conversation PiRpcClient
  ↓
PiRpcClient sends JSONL command to `pi --mode rpc`
  ↓
Assistant response is extracted from Pi RPC events
  ↓
TelegramGateway sends response back to Telegram
```

## Directory Structure

```text
pi-gateway/
├── pi_gateway/
│   ├── cli.py              # command-line interface and daemon startup
│   ├── config.py           # config dataclasses and YAML/env loading
│   ├── db.py               # SQLite schema and database access
│   ├── pi_rpc.py           # Pi RPC subprocess client
│   ├── session_manager.py  # per-conversation Pi client cache and locks
│   └── telegram_bot.py     # Telegram adapter and command router
├── examples/config.yaml
├── systemd/pi-gateway.service
├── docs/
└── pyproject.toml
```

## Why RPC Instead of SDK?

The gateway uses Pi RPC mode instead of importing the Pi SDK directly.

Reasons:

1. **Process isolation**: Pi runs as a child process. Gateway and Pi failures are more isolated.
2. **Stable boundary**: The gateway talks to the documented JSONL RPC protocol.
3. **VPS-friendly recovery**: On restart, the gateway can spawn `pi --mode rpc --session <file>`.
4. **Hermes-like architecture**: Messaging gateway and agent runtime are cleanly separated.

The tradeoff is that we must manage subprocesses, JSONL framing, and stdout/stderr readers.

## State Ownership

| State | Owner | Storage |
|-------|-------|---------|
| Pi conversation history | Pi | JSONL session files |
| Active branch/session tree | Pi | JSONL session files |
| Telegram to Pi mapping | Gateway | SQLite |
| Inbound/outbound audit log | Gateway | SQLite |
| Running child processes | Gateway | In memory + PID file for background daemon |

## Design Constraints

- One Telegram conversation should map to one Pi session file.
- Normal text goes to Pi as a prompt.
- Gateway slash commands are handled before Pi sees the message.
- Pi slash commands can still be sent with `/pi <text>`.
- Messages from non-allowlisted Telegram users are ignored.
- Pi runs from a configured working directory so session storage and filesystem tools are predictable.

## Related Documents

- [Startup and CLI Flow](02-startup-and-cli-flow.md)
- [Telegram Gateway](03-telegram-gateway.md)
- [Pi RPC Integration](04-pi-rpc-integration.md)
- [Session Mapping and SQLite](05-session-mapping-and-sqlite.md)
