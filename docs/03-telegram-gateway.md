# Telegram Gateway

`pi_gateway/telegram_bot.py` contains the Telegram adapter and command router. It uses `python-telegram-bot`.

## Responsibilities

`TelegramGateway` is responsible for:

- Starting Telegram polling.
- Registering Telegram slash commands.
- Authorizing Telegram users.
- Mapping Telegram updates to gateway conversations.
- Routing gateway commands.
- Sending normal text to Pi.
- Sending lifecycle notifications on startup/shutdown.

## Startup

```text
TelegramGateway.start()
  ↓
Application.initialize()
  ↓
bot.set_my_commands(...)
  ↓
Application.start()
  ↓
updater.start_polling(...)
```

The gateway currently uses Telegram polling, not webhooks. This is simpler for VPS and personal use because it does not require a public HTTPS endpoint.

## Authorization

Authorization happens before commands or normal messages are processed.

```python
if self.telegram.allowed_user_ids and user.id not in self.telegram.allowed_user_ids:
    return False
```

Config:

```yaml
telegram:
  allowedUserIds:
    - 123456789
  allowGroups: false
```

Behavior:

- If `allowedUserIds` is set, only those users are accepted.
- Group chats are rejected by default.
- Unknown/unauthorized messages are ignored silently.

## Gateway Session Key

Telegram does not know about Pi session IDs. The gateway derives a stable key from Telegram identity:

```text
telegram:<chat_id>:<thread_id?>:<user_id?>
```

Examples:

```text
telegram:123456789:123456789              # private chat, includes user id
telegram:-1001234567890:42                # group/forum thread if enabled
```

The session key is used to find or create a row in SQLite. See [Session Mapping and SQLite](05-session-mapping-and-sqlite.md).

## Command Routing

The Telegram adapter handles these gateway commands directly:

```text
/start
/help
/status
/new
/name <name>
/compact [instructions]
/stop
/last
/export
/sessions
/switch <id>
/clone
/models
/model <provider/model-id>
/thinking <level>
/queue <text>
/steer <text>
/pi <text>
```

Normal non-command text is sent to Pi as a prompt.

### Gateway Commands vs Pi Slash Commands

Gateway commands are consumed before Pi sees them. To send a Pi slash command, use `/pi`:

```text
/pi /skill:some-skill do something
/pi /compact summarize architecture decisions
```

## Message Flow

```text
Telegram text update
  ↓
_authorized(update)
  ↓
conversation_for(update)
  ↓
log inbound message to SQLite
  ↓
if slash command: _command(...)
else: _send_to_pi(...)
  ↓
reply with assistant text
```

## Lifecycle Notifications

On startup and graceful shutdown, `run_gateway()` calls:

```python
await telegram.notify_lifecycle("🟢 Pi gateway connected.")
await telegram.notify_lifecycle("🔴 Pi gateway disconnected.")
```

`notify_lifecycle()` sends the message to every configured `allowedUserIds` entry. If no allowlist is configured, no lifecycle message is sent.

Startup notifications include an update notice when PyPI has a newer `pi-gateway` version available. `/status` also includes the installed version and cached update-check result. The check is implemented in `pi_gateway.version_check` so other gateway integrations can reuse it.

This is useful because the Telegram user can see when the agent becomes unavailable.

## Response Chunking

Telegram has message length limits. `chunks()` splits long messages before sending them.

Current behavior:

- The gateway sends a temporary `⏳ Pi is working...` message.
- Once Pi returns, it deletes the temporary message if possible.
- It sends the final assistant text split into chunks.

## Important Code Locations

| Function/Class | Purpose |
|----------------|---------|
| `TelegramGateway` | Main Telegram adapter |
| `_authorized()` | User/group allowlist checks |
| `_session_key_parts()` | Builds the Telegram session key |
| `_command()` | Slash command router |
| `_message()` | Normal text handler |
| `_send_to_pi()` | Sends text to Pi and replies with result |
| `notify_lifecycle()` | Startup/shutdown Telegram notifications |

## Related Documents

- [Session Mapping and SQLite](05-session-mapping-and-sqlite.md)
- [Pi RPC Integration](04-pi-rpc-integration.md)
- [Configuration and Deployment](06-configuration-and-deployment.md)
