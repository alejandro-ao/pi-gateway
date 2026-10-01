# pi-gateway

Telegram gateway for persistent [Pi](https://pi.dev) coding-agent sessions.

The gateway is a long-running process. Telegram conversations are mapped to Pi JSONL session files in SQLite, while Pi remains the source of truth for agent history.

## Documentation

See [`docs/`](docs/README.md) for architecture, startup flow, Telegram gateway internals, Pi RPC integration, session mapping, deployment, and troubleshooting notes.

## Install with uv

Directly from GitHub (no clone needed):

```bash
uv tool install git+https://github.com/alejandro-ao/pi-gateway.git
```

Install a specific tag or branch:

```bash
uv tool install git+https://github.com/alejandro-ao/pi-gateway.git@v0.2.2
```

Upgrade later:

```bash
uv tool install --force git+https://github.com/alejandro-ao/pi-gateway.git
# or
uv tool upgrade pi-gateway
```

From a local checkout:

```bash
uv tool install .
```

Or for development:

```bash
uv sync
uv run pi-gateway --help
uv run ruff check .
uv run mypy pi_gateway tests
uv run pytest -q
```

Pi must already be installed and authenticated on the machine as the same user that runs the gateway.

## Configure Telegram

Create an instance in the directory where Pi should work:

```bash
mkdir -p ~/bots/my-bot && cd ~/bots/my-bot
pi-gateway init --name research
```

`init` prompts for a gateway name and Telegram setup, and writes `.pi-gateway/config.yaml`. The name is a gateway identifier, not a Pi session name or Telegram username. `--name research` also works non-interactively; names must be unique per OS user and use letters, digits, or hyphens (max 64 characters). Existing unnamed bots keep working and appear by path until named. The bot's SQLite database, PID and log also live under `.pi-gateway/`; **add `.pi-gateway/` to your project's `.gitignore`** (config may contain a bot token). `pi-gateway configure telegram` creates/updates the local config as well. Commands in this directory automatically select it; `-c <config-path>` always overrides discovery. Existing `~/.config/pi-gateway/config.yaml` installations remain usable when no local instance exists.

Set a distinct bot token and allowed user ID for each instance. Each bot needs its own token. For separate *global* Pi skills/auth, use `pi-gateway init --pi-agent-dir /path/to/agent-dir` and authenticate Pi in that agent directory; otherwise Pi uses the OS user's shared agent directory. Project-local skills follow the configured Pi working directory. For an interactive update:

```bash
pi-gateway configure telegram
```

It will ask for your BotFather token, your allowed Telegram user id, the Pi working directory, and optionally a Pi model and thinking level. In a terminal, setup shows a searchable list from `pi --list-models`: type to filter, use Tab/arrow keys to select a suggestion, then Enter. Empty selection keeps the current model (Pi default on a new config); type `default` to clear a previously selected model. If Pi cannot list models, setup falls back to manual `provider/model-id` entry. Listed models may still require authentication.

You can also configure non-interactively:

```bash
pi-gateway configure telegram \
  --allowed-user-id YOUR_TELEGRAM_USER_ID \
  --pi-cwd /home/agent/pi-workspace \
  --model anthropic/claude-sonnet-4-5 \
  --thinking high
```

`pi-gateway init` accepts the same `--model` and `--thinking` flags. Model IDs may contain additional `/` characters (for example, `huggingface/org/model-id`); the first part is the provider. Thinking levels: `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. Omitted flags leave existing settings unchanged. The defaults are stored per gateway in `.pi-gateway/config.yaml` (or your explicit `-c` file) as `pi.defaultProvider`, `pi.defaultModel`, and `pi.defaultThinking`; they apply to new Pi RPC processes. Existing Pi sessions can have their own model/thinking settings; use Telegram `/model` and `/thinking` for an active session. Restart a running gateway to pick up config edits.

By default the bot token can be read from `TELEGRAM_BOT_TOKEN`. You can also write it into the config:

```bash
pi-gateway configure telegram \
  --bot-token '123:abc' \
  --allowed-user-id YOUR_TELEGRAM_USER_ID \
  --pi-cwd /home/agent/pi-workspace
```

Security note: `--allowed-user-id` writes a single allowlisted Telegram user id. Messages from other users are ignored. Group chats are disabled unless you pass `--allow-groups`.

Print the installed version:

```bash
pi-gateway --version
```

Print the default config path:

```bash
pi-gateway config-path
```

You can still maintain config manually; see `examples/config.yaml`.

## Run

Foreground mode, useful for debugging or systemd:

```bash
export TELEGRAM_BOT_TOKEN=123:abc
pi-gateway run
```

Background mode, useful for a simple VPS setup without systemd:

```bash
pi-gateway start
pi-gateway status
pi-gateway logs -f
pi-gateway stop
```

Repeat `init` and `start` in other directories to run multiple bots concurrently. From anywhere, use `pi-gateway instances` to list all configured bots and `pi-gateway status -i research` (or `start`, `stop`, `logs`) to manage one by name. Instance selection flags can appear before or after these commands: `pi-gateway -i research start` and `pi-gateway start -i research` are equivalent. `-i ~/bots/my-bot` still selects a bot directory, and `-c <config-file>` always takes precedence. Bare `pi-gateway stop` still selects the local config; it never guesses among bots. For production, use one systemd service per bot with its working directory set to the bot directory.

`start` prints stop and log commands containing the selected config's absolute path, so they target the same bot even when run from another directory. It writes logs to:

```text
.pi-gateway/pi-gateway.log (local instances) or ~/.local/state/pi-gateway/pi-gateway.log (legacy config)
```

With an explicit config (including two bots sharing one Pi working directory):

```bash
pi-gateway -c a.yaml configure telegram --name research
pi-gateway -c b.yaml configure telegram --name coding
pi-gateway -c a.yaml start
pi-gateway -c b.yaml start
pi-gateway -i coding stop
pi-gateway -c config.yaml start
pi-gateway -c config.yaml run
# or
pi-gateway run -c config.yaml
```

`configure telegram --name <name>` can also name or rename an existing gateway (omit `--name` to retain it). To remove a stopped bot from the registry **without** deleting its config, run `pi-gateway instances forget <name>`.

To delete a bot's config and unregister it:

```bash
pi-gateway remove research --dry-run  # Show the exact config path; change nothing
pi-gateway remove research            # Confirm interactively (stopped bots only)
pi-gateway remove research --stop --yes  # Stop a background bot, then remove; for automation
# Unnamed bots can be selected explicitly by their config path:
pi-gateway remove -c /absolute/path/to/bot.yaml --yes
```

`remove` never deletes SQLite databases, logs, directories, or Pi session files. `--stop` handles bots started with `pi-gateway start`; stop foreground or systemd-managed bots through their supervisor before removing them. If a bot fails to stop within 10 seconds, its config is kept. After removal, commands run from its former directory may fall back to the legacy global config, so target other bots by name or `-c`. Nonstandard `-c` configs use separate PID, log, and default SQLite paths derived from their absolute config paths, even when they live in the same directory. Set distinct Telegram tokens. Explicit `databasePath` values in YAML are respected; choose different ones per bot. Moving a config changes its derived paths, so move its database or set `databasePath` explicitly if you need its history. If you previously ran an explicit `-c` config without `databasePath`, set `databasePath` to the old `pi-gateway.sqlite3` file before upgrading to retain its conversation mappings.

Development checkout:

```bash
uv run pi-gateway run
```

## Telegram commands

- `/status` current Pi session/model/stats
- `/new` fresh Pi session for this Telegram chat
- `/name <name>` name current Pi session
- `/compact [instructions]` compact current Pi context
- `/stop` abort current Pi operation
- `/last` resend last assistant response
- `/export` export current session to HTML
- `/sessions` list known sessions
- `/switch <id>` point this chat at another known Pi session
- `/clone` clone current branch into a new session
- `/models` list available models
- `/model <provider/model-id>` switch model
- `/thinking <level>` set thinking level
- `/queue <text>` queue follow-up
- `/steer <text>` steer current/next turn
- `/pi <text>` send raw text to Pi, including Pi slash commands

Normal Telegram messages are sent to Pi as prompts. In private chats, the gateway streams Pi's text deltas and temporary tool activity (e.g. `🔧 Running bash…`) to ephemeral Telegram message drafts (requires `python-telegram-bot` 22.7+ and Telegram support). Only sanitized tool names are shown, never arguments, commands, results, or thinking content. Drafts are throttled and capped at 4,096 characters; long final replies still arrive in separate message chunks. Group chats and unavailable draft APIs keep the working-message behavior. Pi handles automatic context compaction (when enabled in Pi settings); the gateway waits for Pi's `agent_settled` event before sending the persistent final reply, including any overflow recovery, retries, or queued work after an `agent_end`. This requires a Pi version that emits `agent_settled`. `/status` distinguishes `Pi generating now` (a momentary activity flag) from `Telegram draft preview` availability; available means the chat/SDK supports trying drafts, not that Telegram will accept every draft. Use `/compact [instructions]` to request manual compaction.

## Session mapping

Gateway key:

```text
telegram:<chat_id>:<thread_id?>:<user_id?>
```

SQLite stores that key plus Pi's `sessionId` and `sessionFile`. On restart the gateway resumes with:

```bash
pi --mode rpc --session <stored-session-file>
```

## systemd

See `systemd/pi-gateway.service` and adjust paths/user/env.

Example with uv tool install:

```ini
[Service]
User=agent
Environment=TELEGRAM_BOT_TOKEN=123:abc
ExecStart=/home/agent/.local/bin/pi-gateway run
Restart=always
```
