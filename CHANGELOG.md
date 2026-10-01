# Changelog

## 0.2.0

- Run and manage multiple Telegram gateways using per-directory configuration, state, and a CLI instance registry.
- Allow separate Pi agent directories for per-bot global skills and credentials.
- Isolate PID, log, and default SQLite state for explicit config files, including bots sharing a working directory.
- Add automated mypy, ruff, and pytest checks for supported Python versions.

When upgrading an existing custom `-c` configuration without `databasePath`, set `databasePath` to its former `pi-gateway.sqlite3` before upgrading to retain conversation mappings. For local instances inside Git repositories, add `.pi-gateway/` to `.gitignore`; prefer environment variables for bot tokens.
