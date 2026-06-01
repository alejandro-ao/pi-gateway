# Session Mapping and SQLite

Pi Gateway uses SQLite to remember which Telegram conversation maps to which Pi session file.

Pi conversation history remains in Pi JSONL session files. SQLite stores gateway metadata only.

## Why SQLite?

Telegram gives the gateway chat/user identifiers. Pi gives the gateway session identifiers and session files. SQLite bridges those worlds.

```text
Telegram identity
  ↓
gateway_session_key
  ↓
SQLite conversations row
  ↓
Pi session file
  ↓
pi --mode rpc --session <file>
```

## Gateway Session Key

The Telegram session key is built in `TelegramGateway._session_key_parts()`:

```text
telegram:<chat_id>:<thread_id?>:<user_id?>
```

Private chats include user id. Group behavior depends on config.

The key must be stable because it is the primary lookup for continuing a conversation.

## Database Schema

Created in `GatewayDB.init()` in `pi_gateway/db.py`.

### conversations

```sql
CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY,
  platform TEXT NOT NULL,
  chat_id TEXT NOT NULL,
  thread_id TEXT,
  user_id TEXT,
  gateway_session_key TEXT UNIQUE NOT NULL,
  pi_session_id TEXT,
  pi_session_file TEXT,
  pi_session_name TEXT,
  cwd TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_message_at TEXT
);
```

Important fields:

| Field | Meaning |
|-------|---------|
| `gateway_session_key` | Stable Telegram-derived key |
| `pi_session_id` | Pi session UUID from `get_state` |
| `pi_session_file` | Absolute JSONL session path; most important for resume |
| `pi_session_name` | Human-readable name set by `/name` |
| `cwd` | Pi working directory used for this conversation |

### messages

```sql
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  platform_message_id TEXT,
  direction TEXT NOT NULL,
  text TEXT,
  created_at TEXT NOT NULL
);
```

This is an audit log of inbound/outbound gateway messages. It is not used to reconstruct Pi context.

## First Message Flow

```text
Telegram message
  ↓
build gateway_session_key
  ↓
GatewayDB.get_or_create_conversation(...)
  ↓
new conversation row has no pi_session_file yet
  ↓
PiSessionManager.client_for(conversation)
  ↓
PiRpcClient starts `pi --mode rpc`
  ↓
client.get_state()
  ↓
SQLite row updated with pi_session_id and pi_session_file
```

## Continuing a Conversation

```text
Telegram message
  ↓
lookup conversation by gateway_session_key
  ↓
read pi_session_file
  ↓
start/reuse PiRpcClient with --session <pi_session_file>
  ↓
send prompt
```

The `pi_session_file` is preferred over only storing the session UUID because it is unambiguous.

## Switching Sessions

`/sessions` lists recent conversations for the same Telegram user.

`/switch <id>` copies the source conversation's Pi session fields onto the current conversation:

```text
current Telegram chat row
  pi_session_id   ← source.pi_session_id
  pi_session_file ← source.pi_session_file
  pi_session_name ← source.pi_session_name
```

Then it closes any cached Pi RPC client for the current conversation so the next request starts with the new session file.

## What Not To Store in SQLite

Do not duplicate Pi's full message tree in SQLite.

Pi already stores:

- user messages
- assistant messages
- tool calls/results
- compactions
- branch summaries
- model/thinking changes

SQLite should store gateway concerns only.

## Related Documents

- [Telegram Gateway](03-telegram-gateway.md)
- [Pi RPC Integration](04-pi-rpc-integration.md)
- [Configuration and Deployment](06-configuration-and-deployment.md)
