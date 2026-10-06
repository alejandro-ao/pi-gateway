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

The key must be stable because it is the primary lookup for continuing a conversation. In shared storage its namespace is the config's stable `instanceId`: the same Telegram identity can reach different bots without sharing Pi context.

## Database Schema

Shared schema created by `init_shared()` in `pi_gateway/storage.py`, versioned through `PRAGMA user_version` (currently 1). New instances default to `~/.local/state/pi-gateway/gateway.sqlite3`. Every connection enables foreign keys and a five-second busy timeout; WAL permits concurrent readers. Atomic upserts avoid competing connections creating duplicate conversations. Future schema versions are refused by older clients.

### instances

```sql
CREATE TABLE instances (
  id TEXT PRIMARY KEY,
  name TEXT,
  config_path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT
);
```

Partial unique indexes enforce active names/config paths while allowing archived records to retain history. UUIDs are persisted in config, independent of name/path; a moved config can update its metadata when the original path no longer exists. Duplicate IDs at live config paths are refused. `InstanceRegistry` remains the JSON discovery index, including legacy/unmigrated configs and configs whose database paths are overridden. Tokens are never stored in SQLite.

### conversations

```sql
CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY,
  instance_id TEXT NOT NULL REFERENCES instances(id),
  platform TEXT NOT NULL,
  chat_id TEXT NOT NULL,
  thread_id TEXT,
  user_id TEXT,
  gateway_session_key TEXT NOT NULL,
  pi_session_id TEXT,
  pi_session_file TEXT,
  pi_session_name TEXT,
  cwd TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_message_at TEXT,
  UNIQUE(instance_id, gateway_session_key)
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

This is an audit log of inbound/outbound gateway messages. It is not used to reconstruct Pi context. Messages inherit instance ownership through the conversation foreign key. Indexes cover `(instance_id, platform, user_id, updated_at DESC)`, `(instance_id, platform, updated_at DESC)`, and `(conversation_id, id)` for messages.

### database_imports

```sql
CREATE TABLE database_imports (
  instance_id TEXT NOT NULL REFERENCES instances(id),
  source_path TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  PRIMARY KEY(instance_id, source_path)
);
```

An import marker commits in the same transaction as the imported rows. Repeating an interrupted migration reuses the destination instance ID and skips already imported sources. Config replacement follows the database commit and is atomic; batch imports commit separately per instance. Never restart affected legacy bots between a failed migration and its retry.

### Legacy compatibility and migration

Configs without `instanceId` keep the original schema and previous database path defaults. A legacy client cannot open a scoped shared database, and an identified client refuses an unscoped legacy schema. Startup never rewrites populated legacy databases.

`migrate-db` preflights all selected sources, refuses running background PIDs, and takes runtime locks that also cover foreground/systemd gateways. It also checks existing Telegram token locks to detect older gateways when their token is available in the migration environment; always stop pre-upgrade foreground/systemd processes explicitly. The registry lock serializes configure/start/remove/migration. Each source is backed up using SQLite's backup API; the snapshot is imported read-only, preserving original databases and Pi session-file paths. Conversation/message IDs are remapped; collisions in a populated destination fail instead of overwriting data. Instance removal/forget archives ownership metadata without cascading deletion.

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

`/sessions` lists recent conversations for the same Telegram user **within the current instance**. All lookups/updates, including by numeric conversation ID, apply `instance_id`. `/switch` re-reads both source and target under this scope; a foreign-instance ID cannot expose or change a session.

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
