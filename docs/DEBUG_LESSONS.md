# Debug Lessons & Production Checklist

**Created:** 2026-02-12
**Purpose:** Prevent recurring production failures. Read this before deploying.

---

## Lesson 1: Always Import What You Use

**Incident:** `auto_executor.py` used `sys.path` but never imported `sys`. This crashed `interactive_bot.py` on startup because it imports `auto_executor` at module level.

**Impact:** Trading bot was down for hours. All commands (`!positions`, `!analyze`, `!daily`) stopped working.

**Root Cause:** Module was written/edited without running even a basic import test.

**Prevention:**
- After editing ANY Python file, run: `python -c "import <module>; print('OK')"`
- After editing files imported by `interactive_bot.py`, run the full chain:
  ```bash
  cd ~/trading-agent/workspace/scripts/discord_bot
  ~/trading-agent/venv/bin/python -c "import interactive_bot; print('OK')"
  ```
- Add all new modules to the import test in CI.

---

## Lesson 2: API Field Names Must Match Exactly

**Incident:** `generate_order_params()` in `signal_pipeline.py` returned `"symbol"` and `"price"` but the GCloud Trading API expects `"ticker"` and `"limit_price"`. The `!approve PBR` command would fail with "Missing ticker."

**Impact:** No pipeline trades could be executed via `!approve`.

**Root Cause:** The signal_pipeline was written with guessed field names instead of checking the actual API contract.

**Prevention:**
- The GCloud Trading API expects these fields (see `trade_executor.py`):
  ```python
  {
      "ticker": "AAPL",          # NOT "symbol"
      "action": "BUY",           # or "SELL"
      "limit_price": 150.00,     # NOT "price"
      "stop_loss": 145.00,
      "target": 165.00,          # NOT "take_profit"
      "source": "pipeline-auto",
  }
  ```
- Before changing any API-facing code, grep `trade_executor.py` for the actual field names:
  ```bash
  grep -n '"ticker"\|"limit_price"\|"stop_loss"\|"target"' trade_executor.py
  ```

---

## Lesson 3: Settings Files Override Environment Variables

**Incident:** `claude_discord_agent.py` was stuck because `~/.claude/settings.json` had a stale/wrong `ANTHROPIC_AUTH_TOKEN` that was different from the systemd service's env var. The Claude CLI reads `settings.json` FIRST, ignoring the env var.

**Impact:** Claude agent appeared "stuck forever" — actually was failing silently on every request.

**Root Cause:** Someone updated the API key in the systemd service but forgot to update `~/.claude/settings.json`.

**Prevention:**
- When changing any API key, update ALL locations:
  1. `/etc/systemd/system/claude-discord.service` (env vars)
  2. `~/.claude/settings.json` (Claude CLI config)
  3. `~/trading-agent/workspace/.env` (shared env file)
- After key changes, always test the CLI directly:
  ```bash
  echo "Say hi" | timeout 15 ~/.local/bin/claude -p --output-format text --dangerously-skip-permissions --no-session-persistence
  ```

---

## Lesson 4: Signal Patterns Need to Match Real Message Formats

**Incident:** Traders post signals like `Long: FCEL TER HWM TSM` (no `$` prefix). But `_collect_recent_messages()` only matched the `$TICKER` pattern, completely missing these signals.

**Impact:** The `!daily` command missed significant trading signals, reducing analysis quality.

**Root Cause:** The ticker extraction regex was too narrow — only `r'\$([A-Z]{1,5})\b'`.

**Fix:** Added multi-pattern extraction:
1. `$TICKER` format (original)
2. `Long/Short/Buy/Sell: TICKER TICKER` direction patterns
3. Standalone uppercase words (with exclusion list) as fallback

**Prevention:**
- When adding signal sources, check the actual message format first:
  ```bash
  # Look at real messages to understand format
  python3 -c "import json; data=json.load(open('../../data/real_discord_messages_goku_wilson_60d.txt')); [print(m['content'][:100]) for msgs in data.values() for m in msgs[:5]]"
  ```
- Test extraction against real samples, not just `$AAPL` format.

---

## Lesson 5: Systemd Environment Must Export ALL Required Vars

**Incident:** `run_interactive_bot.sh` only exported `DISCORD_BOT_TOKEN` and `MINIMAX_API_KEY` from `.env`, but the bot also needed `DISCORD_USER_TOKEN`, `TRADE_API_URL`, `TRADE_API_KEY`.

**Impact:** Signal backfill silently failed (no user token), trade commands failed (wrong API URL).

**Prevention:**
- Use `set -a` / `set +a` in shell scripts to auto-export all vars:
  ```bash
  set -a
  source ~/trading-agent/workspace/.env
  set +a
  ```
- Or use systemd's `EnvironmentFile=` directive (preferred):
  ```ini
  [Service]
  EnvironmentFile=/home/ubuntu/trading-agent/workspace/.env
  ```

---

## Lesson 6: Subprocess Cleanup Requires Process Group Kill

**Incident:** Claude CLI subprocesses hung, and `process.kill()` only killed the parent, leaving zombie children that blocked `process.wait()` forever.

**Prevention:**
- Start subprocesses with `start_new_session=True`
- Kill the entire process group, not just the PID:
  ```python
  import os, signal
  os.killpg(os.getpgid(process.pid), signal.SIGTERM)
  # Wait, then SIGKILL if still alive
  ```

---

## Production Deployment Checklist

Run this EVERY TIME before/after deploying:

```bash
# === PRE-DEPLOY (on local) ===
# 1. Syntax check all modified files
python3 -m py_compile workspace/scripts/discord_bot/interactive_bot.py
python3 -m py_compile workspace/scripts/discord_bot/signal_pipeline.py

# 2. Full import test
cd workspace/scripts/discord_bot
python3 -c "import interactive_bot; print('OK')"

# 3. Unit tests
cd ~/trading-agent
pytest -m unit

# === DEPLOY ===
./scripts/sync_to_aws.sh

# === POST-DEPLOY (on AWS via SSH) ===
ssh -i ~/.ssh/openclaw-key.pem ubuntu@35.90.4.89

# 4. Verify imports on AWS
cd ~/trading-agent/workspace/scripts/discord_bot
~/trading-agent/venv/bin/python -c "import interactive_bot; print('OK')"

# 5. Restart bots
sudo systemctl restart trading-bot.service
sudo systemctl restart claude-discord.service

# 6. Verify bots are running (not crash-looping)
sleep 10
systemctl is-active trading-bot.service    # should print: active
systemctl is-active claude-discord.service  # should print: active

# 7. Check logs for errors
journalctl -u trading-bot.service -n 20 --no-pager | grep -i error
journalctl -u claude-discord.service -n 20 --no-pager | grep -i error

# 8. Test API connectivity
curl -s http://34.75.9.166:8080/api/health | python3 -m json.tool

# 9. Test Claude CLI
echo "Say OK" | timeout 15 ~/.local/bin/claude -p --output-format text --dangerously-skip-permissions --no-session-persistence

# 10. Health check
bash ~/trading-agent/scripts/bot_health_monitor.sh
```

---

## Key Files Reference

| File | Location | Purpose |
|------|----------|---------|
| `interactive_bot.py` | `workspace/scripts/discord_bot/` | Main trading bot — all `!` commands |
| `signal_pipeline.py` | `workspace/scripts/discord_bot/` | Pipeline, order generation, candidates |
| `claude_discord_agent.py` | `workspace/scripts/discord_bot/` | Claude agent (@claudecode) |
| `trade_executor.py` | `workspace/scripts/discord_bot/` | API contract — field names defined here |
| `auto_executor.py` | `workspace/scripts/discord_bot/` | Market-open auto-execution |
| `trading-bot.service` | `/etc/systemd/system/` (AWS) | Systemd service for trading bot |
| `claude-discord.service` | `/etc/systemd/system/` (AWS) | Systemd service for Claude agent |
| `settings.json` | `~/.claude/` (AWS) | Claude CLI config — API key + model |
| `.env` | `workspace/` | Shared env vars for local dev |

---

## Common Commands (AWS)

```bash
# Status
systemctl status trading-bot claude-discord

# Logs (live follow)
journalctl -u trading-bot.service -f
journalctl -u claude-discord.service -f

# Restart
sudo systemctl restart trading-bot.service
sudo systemctl restart claude-discord.service

# Health check
bash ~/trading-agent/scripts/bot_health_monitor.sh

# Test MiniMax API directly
curl -s -X POST 'https://api.minimax.io/anthropic/v1/messages' \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: YOUR_KEY' \
  -d '{"model":"MiniMax-M2.5","max_tokens":50,"messages":[{"role":"user","content":"hi"}]}'
```
