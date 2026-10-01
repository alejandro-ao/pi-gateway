# Configuration and Deployment

This document explains configuration, uv tool installation, background process usage, and systemd deployment.

## Install as a uv Tool

From the project checkout:

```bash
uv tool install .
```

After changes:

```bash
uv tool install --force .
```

Development mode:

```bash
uv sync
uv run pi-gateway --help
```

## Pi Prerequisite

Pi must be installed and authenticated for the same OS user that runs the gateway.

```bash
pi
/login
```

The gateway invokes Pi via:

```bash
pi --mode rpc
```

If `pi` is not on PATH for your service user, set an absolute path in config:

```yaml
pi:
  command: /home/agent/.local/bin/pi
```

## Instance Config Paths

`pi-gateway init` or `pi-gateway configure telegram` creates `.pi-gateway/config.yaml` in the current directory. Local configs take priority over the legacy `~/.config/pi-gateway/config.yaml` for subsequent commands. `pi-gateway config-path` shows the selected path. Use `-c /path/to/config.yaml` for an explicit file or `-i /path/to/bot-directory` to manage an initialized instance elsewhere. Add `.pi-gateway/` to `.gitignore` in each bot project; configuration can contain secrets. `pi-gateway instances` lists registered bot directories and their process status.

## Interactive Telegram Setup

```bash
pi-gateway configure telegram
```

It asks for:

1. Telegram bot token.
2. Allowed Telegram user id.
3. Pi working directory.

The allowed user id is important. Without it, anyone who finds your bot could talk to it.

## Example Config

```yaml
databasePath: ~/.local/share/pi-gateway/pi-gateway.sqlite3
logLevel: INFO

telegram:
  botToken: env:TELEGRAM_BOT_TOKEN
  allowedUserIds:
    - 123456789
  allowGroups: false
  includeUserInGroupSessionKey: false

pi:
  command: pi
  cwd: /home/agent/pi-workspace
  idleTtlSeconds: 1800
  rpcStreamLimit: 16777216
  extraArgs: []
```

`rpcStreamLimit` controls the maximum bytes asyncio will buffer for one Pi RPC stdout/stderr frame. It defaults to 16 MiB and can also be set with `PI_GATEWAY_RPC_STREAM_LIMIT`.

## Pi Working Directory

Pi sessions are scoped by working directory. The gateway needs a stable Pi cwd so that:

- Pi session files are created in a predictable namespace.
- Pi tools (`read`, `write`, `edit`, `bash`) operate in an expected workspace.
- Resuming sessions is consistent.

For a neutral personal gateway workspace:

```bash
mkdir -p ~/pi-gateway-workspace
pi-gateway configure telegram --pi-cwd ~/pi-gateway-workspace
```

## Multiple Bots

Initialize each bot in its own directory, with a unique Telegram bot token and a distinct allowed user ID configuration. Each local instance gets its own config, SQLite database, PID and log; multiple `start` processes can run simultaneously. Two bots can also share a Pi working directory using different `-c` config files: nonstandard config paths get distinct state directories and default SQLite databases based on the config's canonical absolute path. Moving such a config changes its derived paths; explicitly set `databasePath` to keep using an existing database. Explicit database paths must differ between bots. Pi runs from the configured `pi.cwd` (defaults to the directory used at initialization). To isolate global Pi skills, settings, credentials, and sessions too, supply `--pi-agent-dir /absolute/path` to `init` or `configure telegram`; authenticate Pi for that agent directory. Without it, global Pi resources are shared by the OS user. Use one systemd service per directory in production.

## Foreground Run

```bash
export TELEGRAM_BOT_TOKEN=123:abc
pi-gateway run
```

Use foreground mode for:

- Debugging.
- systemd services.
- Seeing logs directly in the terminal.

## Background Run

```bash
pi-gateway start
pi-gateway status
pi-gateway logs -f
pi-gateway stop
```

Files:

```text
Local PID: .pi-gateway/pi-gateway.pid
Local log: .pi-gateway/pi-gateway.log
Legacy PID/log: ~/.local/state/pi-gateway/pi-gateway.{pid,log}
Other -c configs: ~/.local/state/pi-gateway/instances/<stem>-<path-hash>/ (PID, log, default DB)
```

This is a convenience wrapper, not a full supervisor. If the process crashes, it will not automatically restart unless you use systemd or another supervisor.

## systemd Deployment

For a VPS, systemd is the recommended robust deployment.

Template: `systemd/pi-gateway.service`

Example:

```ini
[Unit]
Description=Pi Telegram Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=agent
WorkingDirectory=/home/agent/pi-gateway
Environment=TELEGRAM_BOT_TOKEN=123:abc
ExecStart=/home/agent/.local/bin/pi-gateway run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Install:

```bash
sudo cp systemd/pi-gateway.service /etc/systemd/system/pi-gateway.service
sudo systemctl daemon-reload
sudo systemctl enable pi-gateway
sudo systemctl start pi-gateway
journalctl -u pi-gateway -f
```

## Lifecycle Notifications

When the daemon starts, the allowlisted Telegram user receives:

```text
🟢 Pi gateway connected.
```

On graceful shutdown:

```text
🔴 Pi gateway disconnected.
```

This depends on Telegram being reachable and at least one `allowedUserIds` value being configured.

## Related Documents

- [Startup and CLI Flow](02-startup-and-cli-flow.md)
- [Telegram Gateway](03-telegram-gateway.md)
- [Troubleshooting](07-troubleshooting.md)
