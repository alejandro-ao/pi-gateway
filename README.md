# pi-gateway

Telegram gateway for persistent [Pi](https://pi.dev) coding-agent sessions.

The gateway is a long-running process. Telegram conversations are mapped to Pi JSONL session files in SQLite, while Pi remains the source of truth for agent history.

## Documentation

See [`docs/`](docs/README.md) for architecture, startup flow, Telegram gateway internals, Pi RPC integration, session mapping, deployment, and troubleshooting notes.

## Install with uv

Directly from GitHub (no clone needed):

```bash
uv tool install git+https://github.com/YOUR_USERNAME/pi-gateway.git
```

Install a specific tag or branch:

```bash
uv tool install git+https://github.com/YOUR_USERNAME/pi-gateway.git@v0.1.0
```

Upgrade later:

```bash
uv tool install --force git+https://github.com/YOUR_USERNAME/pi-gateway.git
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
```

Pi must already be installed and authenticated on the machine as the same user that runs the gateway.

## Configure Telegram

Create an instance in the directory where Pi should work:

```bash
mkdir -p ~/bots/my-bot && cd ~/bots/my-bot
pi-gateway init
```

`init` prompts for Telegram setup and writes `.pi-gateway/config.yaml`. The bot's SQLite database, PID and log also live under `.pi-gateway/`; **add `.pi-gateway/` to your project's `.gitignore`** (config may contain a bot token). `pi-gateway configure telegram` creates/updates the local config as well. Commands in this directory automatically select it; `-c <config-path>` always overrides discovery. Existing `~/.config/pi-gateway/config.yaml` installations remain usable when no local instance exists.

Set a distinct bot token and allowed user ID for each instance. Each bot needs its own token. For separate *global* Pi skills/auth, use `pi-gateway init --pi-agent-dir /path/to/agent-dir` and authenticate Pi in that agent directory; otherwise Pi uses the OS user's shared agent directory. Project-local skills follow the configured Pi working directory. For an interactive update:

```bash
pi-gateway configure telegram
```

It will ask for your BotFather token, your allowed Telegram user id, and the Pi working directory.

You can also configure non-interactively:

```bash
pi-gateway configure telegram \
  --allowed-user-id YOUR_TELEGRAM_USER_ID \
  --pi-cwd /home/agent/pi-workspace
```

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

Repeat `init` and `start` in other directories to run multiple bots concurrently. From anywhere, use `pi-gateway instances` to list initialized bots and `pi-gateway -i ~/bots/my-bot status|start|stop|logs` to manage one. The `-i` option expects an initialized bot directory; `-c` takes precedence if both are given. For production, use one systemd service per bot with its working directory set to the bot directory.

`start` writes logs to:

```text
.pi-gateway/pi-gateway.log (local instances) or ~/.local/state/pi-gateway/pi-gateway.log (legacy config)
```

With an explicit config (including two bots sharing one Pi working directory):

```bash
pi-gateway -c a.yaml start
pi-gateway -c b.yaml start
pi-gateway -c a.yaml stop
pi-gateway -c config.yaml start
pi-gateway -c config.yaml run
# or
pi-gateway run -c config.yaml
```

Nonstandard `-c` configs use separate PID, log, and default SQLite paths derived from their absolute config paths, even when they live in the same directory. Set distinct Telegram tokens. Explicit `databasePath` values in YAML are respected; choose different ones per bot. Moving a config changes its derived paths, so move its database or set `databasePath` explicitly if you need its history.

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

Normal Telegram messages are sent to Pi as prompts.

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
