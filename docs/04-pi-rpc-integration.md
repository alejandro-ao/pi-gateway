# Pi RPC Integration

Pi Gateway talks to Pi by spawning `pi --mode rpc` subprocesses and exchanging JSON Lines over stdin/stdout.

The implementation lives in `pi_gateway/pi_rpc.py`.

## Why RPC?

RPC mode gives the gateway a clean process boundary:

```text
Pi Gateway process
  ↓ JSONL stdin/stdout
Pi child process: pi --mode rpc --session <file>
```

Benefits:

- The gateway does not depend on Pi SDK internals.
- A Pi process can be restarted by session file.
- Crashes are isolated to child processes.
- It works well for a daemon supervising multiple conversations.

## Starting Pi

`PiRpcClient.start()` builds a command like:

```bash
pi --mode rpc --session /path/to/session.jsonl
```

It may also include:

```bash
--session-dir <dir>
--name <name>
--provider <provider>
--model <model>
--thinking <level>
```

The child process runs with cwd from config:

```yaml
pi:
  cwd: /home/agent/pi-workspace
```

## JSONL Framing

Pi RPC expects one JSON object per line.

Example request:

```json
{"id":"uuid","type":"prompt","message":"Hello"}
```

Example response:

```json
{"id":"uuid","type":"response","command":"prompt","success":true}
```

Events then stream asynchronously:

```json
{"type":"message_end","message":{...}}
{"type":"agent_end","messages":[...]}
```

## Request/Response Handling

`PiRpcClient.request()`:

1. Adds a UUID request id.
2. Writes JSON + `\n` to stdin.
3. Stores a future in `_pending` keyed by id.
4. `_read_stdout()` resolves the future when a matching response arrives.
5. Non-response lines are placed into `_events`.

```text
request()
  ↓
stdin write
  ↓
_read_stdout() receives response id
  ↓
future resolves
```

## Prompt Handling

`PiRpcClient.prompt()` sends:

```json
{"type":"prompt","message":"..."}
```

Then it consumes events until `agent_end`.

The final assistant text is extracted from either:

- the last assistant `message_end`, or
- the `agent_end.messages` array.

This avoids requiring token-by-token Telegram streaming for v1.

## Supported Pi Operations

The client wraps several RPC commands:

| Method | Pi RPC Command |
|--------|----------------|
| `prompt()` | `prompt` |
| `get_state()` | `get_state` |
| `get_session_stats()` | `get_session_stats` |
| `new_session()` | `new_session` |
| `compact()` | `compact` |
| `abort()` | `abort` |
| `set_session_name()` | `set_session_name` |
| `clone()` | `clone` |
| `export_html()` | `export_html` |
| `get_last_assistant_text()` | `get_last_assistant_text` |
| `set_model()` | `set_model` |
| `set_thinking_level()` | `set_thinking_level` |
| `get_available_models()` | `get_available_models` |

## Session Manager Layer

`pi_gateway/session_manager.py` wraps `PiRpcClient`.

Responsibilities:

- Cache one `PiRpcClient` per gateway conversation id.
- Serialize access with an `asyncio.Lock` per conversation.
- Close idle clients after `idleTtlSeconds`.
- Persist Pi session state back to SQLite after operations.

```text
TelegramGateway
  ↓
PiSessionManager.prompt(conversation, text)
  ↓
lock_for(conversation.id)
  ↓
client_for(conversation)
  ↓
PiRpcClient.prompt(text)
  ↓
persist get_state() into SQLite
```

## Important Edge Cases

### Process exits and reader failures

If the child Pi process exits, `_read_stdout()` fails pending requests with `PiRpcError`.

If stdout reading itself fails (for example `LimitOverrunError`/`ValueError` from an oversized line), the client is marked unhealthy, pending requests and event consumers receive `PiRpcError`, and the child process is terminated. The cached client keeps the last known `sessionFile`; on the next gateway operation `PiRpcClient.start()` restarts `pi --mode rpc --session <session-file>` automatically instead of leaving the gateway permanently stuck.

### Concurrent Telegram messages

Per-conversation locks prevent simultaneous writes to the same Pi RPC process.

### Switching sessions

When `/switch <id>` points a Telegram conversation at another Pi session, `PiSessionManager.forget(conversation.id)` closes the old cached client. The next message starts a fresh client with the newly mapped session file.

## Related Documents

- [Telegram Gateway](03-telegram-gateway.md)
- [Session Mapping and SQLite](05-session-mapping-and-sqlite.md)
- [Troubleshooting](07-troubleshooting.md)
