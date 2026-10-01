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
pi-gateway instances           # list registered instances (including custom -c configs)
pi-gateway instances forget <name>  # unregister stopped bot without deleting files
pi-gateway remove <name> [--dry-run|--stop|--yes]  # delete only its config
pi-gateway status -i <name|directory>  # manage a bot from elsewhere
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

A user-supplied gateway name resolves through the per-user registry to an absolute config path; `-c` remains an explicit override. Names do not alter Pi sessions, SQLite paths, or PID/log locations. `start` prints exact stop/log commands with the resolved absolute config path so they remain correct from another directory. Both `-c` and `-i` can appear before or after `run`, `start`, `stop`, `status`, `logs`, `remove`, and `config-path`, or after `configure telegram`. Their subparser defaults are suppressed so a flag before the command is not overwritten; `-c` takes priority over `-i` when both are supplied.

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

## Safe Removal

`pi-gateway remove <name>` (or `pi-gateway remove -c /path/to/config.yaml` for an unnamed bot) resolves only a registered config. It prints the exact config path and status, refuses a live background PID unless `--stop` is given, and asks for confirmation unless `--yes` is passed. `--dry-run` never stops or deletes anything. On `--stop`, removal checks again that the background PID is no longer running before unlinking the config. A shared registry lock serializes background starts, registration, and removal to prevent starting a config while it is being removed. Foreground and systemd processes are not tracked by the background PID file and must be stopped separately. SQLite data, logs, Pi sessions, and parent directories are preserved. `instances forget` remains the non-deleting alternative.

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
4. Optional Pi model and thinking level. Setup reads the model catalog from the configured Pi executable (`pi --list-models`) with the configured working/agent directory, then provides a fuzzy-search picker: Tab/arrow keys select, Enter confirms. Empty keeps the current model (or Pi default on new configs); `default` clears a prior model choice. If the catalog cannot be read, manual `provider/model-id` entry remains available. `--model` skips the picker for automation; catalog membership is not an authentication check.
5. Optional unique gateway name; blank keeps the existing name when updating a config.

Both `init` and `configure telegram` accept `--model` and `--thinking` for non-interactive setup. The model is split at the first `/` into `pi.defaultProvider` and `pi.defaultModel`; `pi.defaultThinking` holds the selected reasoning level. Invalid values are rejected before the config is written.

`init` refuses to overwrite an existing local config. `configure telegram` creates or updates `.pi-gateway/config.yaml` in the current directory unless `-c`/`-i` is supplied. Both accept `--name` to set or rename `instanceName` in that YAML file. Existing global configs remain the fallback for runtime commands when no local config exists. The registry at `~/.config/pi-gateway/instances.json` indexes *all* configured gateway files by name/path; old path-only registries are migrated on read. Registration and uniqueness checks use a file lock so a duplicate name cannot overwrite an existing entry. Missing configs are shown as missing, not silently removed; `instances forget <name>` unregisters a stopped gateway without deleting its files.

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
