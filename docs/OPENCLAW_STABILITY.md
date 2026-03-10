# OpenClaw Stability & Auto-Heal

## Why OpenClaw Fails Day-to-Day

From `~/.openclaw/logs/gateway.err.log`, common failure modes:

| Error | Cause | Auto-fix |
|-------|-------|----------|
| **API rate limit reached** | Gemini/Anthropic quota exceeded | Restart clears request state |
| **LLM request timed out** | Slow API or network; request hangs | Restart kills stuck request |
| **Request to 'getUpdates' timed out** | Telegram long-poll stuck (500s) | Restart reconnects |
| **No API key for provider** | Config/auth missing | Manual: `openclaw agents add <id>` |
| **Port not responding** | Process hung or crashed | Restart recovers |

**Why launchd KeepAlive isn't enough:** The gateway process may **hang** (not exit) when a request blocks. launchd only restarts on exit, so a hung process stays hung until something detects it and restarts.

---

## Auto-Heal Workflow

The **OpenClaw Watchdog** runs every 5 minutes and:

1. **Health check** — Is `http://localhost:18789` returning 200?
2. **Process check** — Is the launchd job running?
3. **Error buildup** — Are there 3+ "rate limit" or 5+ "timed out" in the last 500 log lines?
4. **Auto-restart** — If unhealthy, restart via `launchctl bootout` + `bootstrap`

### Install Watchdog

```bash
cd ~/trading-agent  # or your repo path
bash scripts/setup_mac_mini.sh   # Installs all plists including watchdog
```

Or manually:

```bash
# Patch path and install
REPO_DIR="$(pwd)"
sed "s|__REPO_DIR__|$REPO_DIR|g" scripts/services/com.saiyan.openclaw-watchdog.plist" \
  > ~/Library/LaunchAgents/com.saiyan.openclaw-watchdog.plist

launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.saiyan.openclaw-watchdog.plist
```

### Test

```bash
# Dry run (check only, no restart)
bash scripts/openclaw_watchdog.sh --dry-run

# One-shot with auto-fix
bash scripts/openclaw_watchdog.sh
```

### Logs

- Watchdog log: `logs/openclaw_watchdog.log`
- Watchdog stdout/stderr: `logs/openclaw_watchdog_stdout.log`, `openclaw_watchdog_stderr.log`

### Discord Alerts

Set `WATCHDOG_DISCORD_WEBHOOK` in `workspace/.env` to receive alerts when the watchdog restarts OpenClaw or when restart fails.

---

## Reducing Failures (Prevention)

1. **Rate limits** — Use multiple providers (Gemini + Anthropic) so one can fail over
2. **Timeouts** — Ensure `openclaw.json` has reasonable model timeouts
3. **Auth** — Run `openclaw doctor --fix` and `openclaw agents add main` if you see "No API key" errors
