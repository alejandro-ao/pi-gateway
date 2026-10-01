# Startup and CLI Flow

The CLI entry point is `pi_gateway/cli.py`. The package exposes the `pi-gateway` console script through `pyproject.toml`:

```toml
[project.scripts]
pi-gateway = "pi_gateway.cli:main"
```

## Commands

```text
pi-gateway run                 # foreground daemon
pi-gateway start               # background daemon
pi-gateway stop                # stop background daemon
pi-gateway status              # show background status
pi-gateway logs [-f]           # read/follow log file
pi-gateway configure telegram  # interactive config wizard
pi-gateway init                # create local instance and configure Telegram
pi-gateway instances           # list initialized local instances
pi-gateway -i <directory> status  # manage a bot from elsewhere
pi-gateway config-path         # print selected config path
```

## Foreground Startup: `run`

`pi-gateway run` calls `run_gateway()`.

```text
main()
  ↓
parse args
  ↓
run_gateway(config_path)
  ↓
load_config()
  ↓
GatewayDB(...).init()
  ↓
PiSessionManager.start()
  ↓
TelegramGateway.start()
  ↓
notify Telegram: gateway connected
  ↓
wait for SIGINT/SIGTERM
  ↓
notify Telegram: gateway disconnected
  ↓
shutdown Telegram, Pi sessions, DB
```

Important behavior:

- `run` is blocking and logs to the current terminal.
- This is the right mode for debugging and for systemd.
- SIGINT/SIGTERM triggers graceful shutdown.

## Background Startup: `start`

`pi-gateway start` is a convenience wrapper for personal VPS use.

It does not implement a full supervisor. It:

1. Selects the local `.pi-gateway/config.yaml` if present, otherwise the legacy global config (or explicit `-c`/`-i`). Checks the selected instance's PID file.
2. If a live PID exists, it refuses to start another daemon.
3. Opens the selected instance's log file.
4. Spawns `pi-gateway run` with stdout/stderr redirected to the log.
5. Writes the child PID to the PID file.

```text
pi-gateway start
  ↓
subprocess.Popen([sys.argv[0], "run", ...])
  ↓
PID file: .pi-gateway/pi-gateway.pid (legacy: ~/.local/state/pi-gateway/pi-gateway.pid)
Log file: .pi-gateway/pi-gateway.log (legacy: ~/.local/state/pi-gateway/pi-gateway.log)
Other -c configs: ~/.local/state/pi-gateway/instances/<stem>-<path-hash>/{pi-gateway.pid,pi-gateway.log}
```

## Stop Flow: `stop`

`pi-gateway stop` reads the PID file and sends SIGTERM.

```text
read PID file
  ↓
os.kill(pid, SIGTERM)
  ↓
wait up to --timeout seconds
  ↓
remove stale PID file if process exits
```

The daemon catches SIGTERM in `run_gateway()`, which lets it send the Telegram disconnected notification before shutting down.

## Logs Flow: `logs`

`pi-gateway logs` shells out to `tail`:

```bash
pi-gateway logs      # tail -n 80 log
pi-gateway logs -n 200
pi-gateway logs -f   # tail -f
```

Local instances use `.pi-gateway/pi-gateway.log`; legacy global configurations keep `~/.local/state/pi-gateway/pi-gateway.log`. Other explicit configs use per-config state directories keyed by canonical absolute path, so even two configs in one directory can run independently.

## Configure Flow

`pi-gateway configure telegram` is interactive when stdin is a TTY.

It asks for:

1. Telegram bot token, or blank for `env:TELEGRAM_BOT_TOKEN`.
2. Allowed Telegram user ID.
3. Pi working directory, defaulting to the current directory.

`init` refuses to overwrite an existing local config. `configure telegram` creates or updates `.pi-gateway/config.yaml` in the current directory unless `-c`/`-i` is supplied. Existing global configs remain the fallback for runtime commands when no local config exists. The registry at `~/.config/pi-gateway/instances.json` stores absolute local config paths for `instances`; missing configs are reported, not deleted.

## Important Code Locations

| Function | File | Purpose |
|----------|------|---------|
| `main()` | `pi_gateway/cli.py` | Top-level CLI dispatch |
| `build_parser()` | `pi_gateway/cli.py` | argparse tree |
| `run_gateway()` | `pi_gateway/cli.py` | Foreground daemon runtime |
| `start_background()` | `pi_gateway/cli.py` | Spawn background daemon |
| `stop_background()` | `pi_gateway/cli.py` | Stop background daemon |
| `configure_telegram()` | `pi_gateway/cli.py` | Config wizard |

## Related Documents

- [Configuration and Deployment](06-configuration-and-deployment.md)
- [Telegram Gateway](03-telegram-gateway.md)
