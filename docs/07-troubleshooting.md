# Troubleshooting

This guide lists common Pi Gateway issues and how to debug them.

## Command Not Found: `pi-gateway`

If installed with uv:

```bash
uv tool install --force .
```

Make sure uv's tool bin directory is on PATH. Common location:

```text
~/.local/bin
```

Check:

```bash
which pi-gateway
pi-gateway --help
```

## Telegram Is Not Configured

Error:

```text
Telegram is not configured. Run `pi-gateway configure telegram` or set TELEGRAM_BOT_TOKEN.
```

Fix:

```bash
pi-gateway configure telegram
```

or:

```bash
export TELEGRAM_BOT_TOKEN=123:abc
pi-gateway run
```

## Bot Does Not Respond

Check status/logs:

```bash
pi-gateway status
pi-gateway logs -f
```

If using systemd:

```bash
systemctl status pi-gateway
journalctl -u pi-gateway -f
```

Common causes:

1. Wrong Telegram bot token.
2. Your Telegram user id is not in `allowedUserIds`.
3. Pi is not installed or authenticated for the service user.
4. `pi.command` is not on PATH.
5. The configured Pi working directory does not exist or is not accessible.

## Messages From User Are Ignored

This is usually the allowlist doing its job.

Check config:

```bash
cat ~/.config/pi-gateway/config.yaml
```

Verify:

```yaml
telegram:
  allowedUserIds:
    - YOUR_NUMERIC_TELEGRAM_ID
```

Find your ID by messaging `@userinfobot` or `@RawDataBot` on Telegram.

## Pi RPC Fails to Start

The gateway spawns:

```bash
pi --mode rpc
```

Test manually as the same user:

```bash
cd <configured pi.cwd>
pi --mode rpc
```

If that fails:

- install Pi
- authenticate Pi with `/login`
- fix PATH or use absolute `pi.command`

Example config:

```yaml
pi:
  command: /home/agent/.local/bin/pi
  cwd: /home/agent/pi-workspace
```

## Background Process Is Stale

If `pi-gateway status` reports a stale PID:

```bash
pi-gateway stop
```

If needed, remove the PID file manually:

```bash
rm ~/.local/state/pi-gateway/pi-gateway.pid
```

Then restart:

```bash
pi-gateway start
```

## See Logs

Background mode:

```bash
pi-gateway logs
pi-gateway logs -f
```

Log file:

```text
~/.local/state/pi-gateway/pi-gateway.log
```

systemd mode:

```bash
journalctl -u pi-gateway -f
```

## Telegram Lifecycle Message Not Sent

Startup/shutdown messages are sent only to configured `allowedUserIds`.

Check:

```yaml
telegram:
  allowedUserIds:
    - 123456789
```

Also ensure the user has started a chat with the bot. Telegram bots usually cannot message a user until that user has initiated a conversation.

## Sessions Resume Incorrectly

Check the SQLite conversation mapping.

Default database path:

```text
~/.local/share/pi-gateway/pi-gateway.sqlite3
```

Inspect with sqlite:

```bash
sqlite3 ~/.local/share/pi-gateway/pi-gateway.sqlite3 \
  'select id, gateway_session_key, pi_session_id, pi_session_file, pi_session_name from conversations;'
```

Important field:

```text
pi_session_file
```

That is what allows the gateway to resume with:

```bash
pi --mode rpc --session <pi_session_file>
```

## Useful Debug Commands

```bash
pi-gateway --help
pi-gateway configure --help
pi-gateway configure telegram --help
pi-gateway config-path
pi-gateway status
pi-gateway logs -n 200
```

Python syntax check from checkout:

```bash
python3 -m compileall pi_gateway main.py
```

## Related Documents

- [Startup and CLI Flow](02-startup-and-cli-flow.md)
- [Pi RPC Integration](04-pi-rpc-integration.md)
- [Configuration and Deployment](06-configuration-and-deployment.md)
