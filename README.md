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

Create/update the default config at `~/.config/pi-gateway/config.yaml` interactively:

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

`start` writes logs to:

```text
~/.local/state/pi-gateway/pi-gateway.log
```

With an explicit config:

```bash
pi-gateway -c config.yaml start
pi-gateway -c config.yaml run
# or
pi-gateway run -c config.yaml
```

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
