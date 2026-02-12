# Worklog & Handover Notes

**Last Updated:** 2026-02-12
**Branch:** `expand-universe`

## 1. Major Updates (Feb 2026)

### Schedulers & Automation (PST Timezone)
- **Morning Pipeline (7:00 AM PST):** Runs `_morning_pipeline_scheduler` to review overnight signals and queue limit orders *before* market open.
- **Midday Review (12:00 PM PST):** Runs `_midday_review_scheduler` to manage open positions:
  - **Take Profit:** >12% gain → Trim 50%
  - **Cut Loss:** <-8% loss → Exit Full
  - **Tighten Stop:** >5% gain → Update stop
- **Learning Cycle (2:45 PM PST):** Runs `_daily_learning_scheduler` to track signal outcomes, grade traders, and update pattern stats.

### Pattern Learning System
- **Risk-Adjusted Metrics:** Implemented `PatternStats` with Grade (A-F), EV, Profit Factor, Max Drawdown.
- **Backtesting:** Created `workspace/scripts/data_collection/backtest_patterns.py` to validate patterns against 1 year of historical data (`download_1year_history.py`).
- **Integration:** `signal_pipeline.py` now uses pattern grades for conviction scoring.

### GUI Enhancements (`tools/trading_gui.py`)
- **Accurate Day P&L:** Switched to IBKR's `reqPnL()` subscription for real-time daily profit/loss updates (replacing manual calculation).
- **Trade History:** Added `/api/executions` endpoint and a "Trade History" table in the dashboard.
- **Persistent Analysis:** Analysis button now opens a persistent detailed panel with technical indicators (RSI, MACD, ATR) and actionable advice.

### Security Cleanup
- **Removed hardcoded Discord bot token** from `claude_discord_agent.py` (now requires `DISCORD_BOT_TOKEN_CLAUDE` env var).

## 2. Verification Status
- **Unit Tests:** Passed (`tests/unit/legacy/test_basic.py`, `tests/unit/legacy/test_classification.py`).
- **Pipeline Logic:** Verified order parameter generation and midday review logic via `python -c` script.
- **Timezone Logic:** Confirmed PST calculations are correct.
- **CI Fix:** Guarded `ibkr_client` type hints when `ib_async` is missing (prevents `Contract` NameError).

## Feb 12, 2026 — Production Hotfixes (tested on AWS)

### Bugs Fixed (directly on AWS, verified live)
1. **`auto_executor.py` missing `import sys`** — Crashed `interactive_bot.py` on startup. Bot was in systemd restart loop.
2. **`~/.claude/settings.json` stale API key** — Claude agent was failing silently. Updated key + model to MiniMax-M2.5.
3. **`generate_order_params()` wrong field names** — Used `"symbol"` (should be `"ticker"`), `"price"` (should be `"limit_price"`). `!approve` always returned "Missing ticker."
4. **`!approve` only supported single ticker** — Now supports `!approve PBR HOOD NVDA` (multiple tickers).
5. **Signal extraction missed "Long: FCEL TER HWM TSM"** — `_collect_recent_messages()` only matched `$TICKER` format. Added direction-pattern matching and standalone ticker fallback.
6. **MiniMax model updated M2.1 → M2.5** — All references across 5 files + AWS `settings.json`.

### New Files
- `docs/DEBUG_LESSONS.md` — Production debugging lessons + deployment checklist.
- `scripts/services/interactive-bot.service` — Systemd service for trading bot.
- `scripts/services/claude-agent.service` — Systemd service for Claude agent.
- `scripts/bot_health_monitor.sh` — Health check script with Discord alerts.
- `scripts/deploy_bots_aws.sh` — One-command deploy for AWS.

### Key Lesson
**Always test on AWS, not just locally.** The AWS version of files can diverge from local. See `docs/DEBUG_LESSONS.md` for full checklist.

## 3. Next Steps for Next Agent
1. **Monitor Deployment:** Ensure the new schedulers run correctly on the server (check logs for "Morning pipeline triggered", etc.).
2. **Validate Live Trading:** Confirm that `reqPnL` updates correctly during market hours.
3. **Expand Pattern Library:** Add more patterns (e.g., Bull Flag, Double Bottom) to `pattern_library.py` and backtest them.
4. **Refine Midday Logic:** Consider adding trailing stops or more nuanced exit rules based on market regime.
5. **Read `docs/DEBUG_LESSONS.md`** before any production deployment.

## 4. Key Files
- `workspace/scripts/discord_bot/interactive_bot.py`: Main bot logic & schedulers.
- `workspace/scripts/discord_bot/signal_pipeline.py`: Pipeline logic, order generation, midday review.
- `tools/trading_gui.py`: Flask GUI, IBKR connection, P&L subscription.
- `workspace/scripts/discord_bot/pattern_library.py`: Pattern recognition & stats.
