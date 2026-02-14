# Worklog & Handover Notes

**Last Updated:** 2026-02-14
**Branch:** `feature/pst-scheduler-gui-final`

## Feb 14, 2026 — Data Infrastructure & Three-Layer Picker Overhaul

### Problem Statement
The three-layer stock picker had catastrophic fundamental data gaps:
- ROE: **0.0%** coverage (hardcoded `None` in Yahoo provider)
- EPS YoY: **11.6%** coverage
- Revenue Growth: **42.2%** coverage
- Result: 689/751 stocks dropped due to missing data, only 11 survived all filters

### Root Causes Found & Fixed

#### 1. FinancialDatasets API Never Actually Called (Critical)
- **Bug**: `urllib.request` sends `User-Agent: Python-urllib/3.x` which Cloudflare blocks with error 1010 (bot detection). API URLs also lacked trailing slash, causing 301 redirect that strips `X-API-KEY` header.
- **Fix**: Added `User-Agent: trading-agent/1.0` header and ensured trailing slash on all API paths.
- **File**: `src/data/providers/financialdatasets_provider.py`
- **Proof**: Tested curl vs Python — curl worked, Python got 403. After fix, both work. NVDA, AAPL, TSLA all return full income statements + balance sheets.

#### 2. Yahoo Provider Never Fetched Balance Sheet for ROE (Critical)
- **Bug**: `YFinanceProvider.get_quarterly_fundamentals()` had `"roe": None` hardcoded. It only fetched income statements, never the balance sheet (`quarterly_balance_sheet`).
- **Fix**: Added `t.quarterly_balance_sheet` fetch, built equity map by period, computed `ROE = net_income / shareholders_equity` per quarter.
- **File**: `src/data/providers/yfinance_provider.py`
- **Proof**: Tested 40 stocks (large/mid/small/micro cap) — ROE 40/40 (100%), Gross Margin 37/40 (92.5%).

#### 3. Stale Snapshot Cache Masked Real Coverage
- **Bug**: `FundamentalSnapshotService` cached raw snapshots in CSV. Once written with broken data, subsequent runs reused the stale cache (completed in 1.3s for 751 stocks — impossible for real API calls).
- **Fix**: Deleted stale `three_layer_fund_snapshot_raw.csv`, forced fresh Yahoo API calls.

### Results After Fix
| Field | Before | After | Target |
|-------|--------|-------|--------|
| EPS YoY | 87/751 (11.6%) | **750/751 (99.9%)** | 90% |
| Revenue Growth | 317/751 (42.2%) | **686/751 (91.3%)** | 90% |
| ROE | 96/751 (12.8%) | **750/751 (99.9%)** | 90% |
| Gross Margin Rank | 314/751 (41.8%) | **742/751 (98.8%)** | 90% |
| Debt to Equity | 89/751 (11.9%) | **683/751 (90.9%)** | 90% |

### Pipeline Funnel (Full Run, 6,848 US Stocks)
```
6,848 listed US symbols
  → 5,825 base (with sufficient price data)
  → 751 after technical filter (RS>80, >200MA, near 52w high)
  → 281 after fundamental filter (EPS>25%, Rev>10%)
  → 45 after quality/moat filter (ROE>10%, GM rank>60, D/E<1.2)
```

### Top 10 Picks (Feb 14, 2026)
| Ticker | RS Rank | EPS YoY | Rev Growth | ROE | GM%tile | Score |
|--------|---------|---------|------------|-----|---------|-------|
| ORLA | 94.5 | 133.3% | 176.9% | 10.2% | 85.2 | 108.9 |
| SEI | 92.3 | 6136.0% | 122.4% | 11.2% | 70.9 | 108.5 |
| NGD | 97.5 | 275.0% | 83.5% | 22.3% | 83.6 | 105.6 |
| CDE | 96.7 | 241.7% | 76.9% | 19.6% | 71.8 | 102.0 |
| OR | 92.4 | 528.5% | 70.6% | 11.3% | 95.4 | 101.9 |
| GFI | 95.9 | 163.3% | 63.7% | 35.2% | 74.9 | 93.7 |
| MU | 98.0 | 175.4% | 56.7% | 22.6% | 68.6 | 93.3 |
| AGI | 91.3 | 225.0% | 28.1% | 14.3% | 84.6 | 91.7 |
| AU | 97.0 | 147.2% | 62.1% | 38.7% | 69.0 | 90.0 |
| MTSI | 91.5 | 24547.0% | 24.5% | 12.9% | 77.4 | 89.8 |

### Financial Datasets API Status
- **API key**: `623f2ffe-5f32-489a-bab6-48e901829f5c`
- **Available tickers**: 17,817 (covers full US market)
- **Current credits**: $0.00 (free tier exhausted after initial testing)
- **Action needed**: Add credits at [financialdatasets.ai](https://financialdatasets.ai) to use as primary data source
- **MCP Server**: Configured in `~/.cursor/mcp.json` for Cursor integration (uses `npx mcp-remote`)

### Caching Architecture
```
API Call → validate_quarterly_fundamentals() → Parquet (data/fundamental/{TICKER}.parquet)
                                             → SQLite (data/stock_cache.db)
                                             → MySQL (optional, via MYSQL_URL env var)
```
- Parquet cached per-ticker: 5,611 files in `data/fundamental/`
- SQLite cached: 6,880 symbols with daily data, 438 fundamentals
- DataManager checks parquet cache first, only calls API if missing/stale

### Files Modified
- `src/data/providers/financialdatasets_provider.py` — Fixed trailing slash + User-Agent header
- `src/data/providers/yfinance_provider.py` — Added balance sheet fetch for ROE computation
- `~/.cursor/mcp.json` — Added Financial Datasets MCP server config

### Post-Processing: Deduplication + Sector Cap + Historical Analysis

#### Share-Class Deduplication
Removed 3 duplicates: GOOG (kept GOOGL), BELFA (kept BELFB), HEI.A (kept HEI)

#### Industry Concentration Cap (max 2 per industry)
Removed 15 stocks dominated by Gold mining: CDE, OR, GFI, AGI, AU, B, WPM, PAAS, FSM, NEM, KGC, DRD + 3 semiconductor/biotech extras.
Result: **27 diversified picks** across 9 sectors instead of 46 gold-heavy picks.

#### Historical Lookback (6 months ago, ~Aug 2025)
**Key question**: Could we have discovered today's winners 6 months ago?

| Finding | Value |
|---------|-------|
| Stocks that would pass tech filter 6m ago | **13/25 (52%)** |
| Avg gain of discoverable stocks | **71.1%** |
| Avg gain of ALL current picks | **81.7%** |
| Biggest miss: VICR (+227%) | Was below 200MA and 52w high back then |
| Biggest discoverable winner: MU (+241%) | Was above 200MA AND near 52w high |

**Conclusion**: About half of today's winners were already technically strong 6 months ago. The other half broke out AFTER a catalyst (earnings, sector rotation). This means running the picker monthly can catch ~50% of future winners.

#### Earnings Catalyst Analysis (18 months)
Major earnings breakouts (>15% 5-day reaction):
- **ISSC** Dec 2025: +77.6% (surprise 254.6%) — the biggest single earnings catalyst
- **VICR** Oct 2025: +55.1% (surprise 447.8%) — turned the stock from below 200MA to above
- **ISSC** May 2025: +37.5% (surprise 156.2%)
- **UI** Aug 2025: +36.3%, Nov 2024: +31.3%
- **SEI** Nov 2024: +32.7% — the initial breakout catalyst
- **TER** Feb 2026: +24.5%, Oct 2025: +24.1%
- **MU** Dec 2025: +18.8% (surprise 20.7%) — accelerated an existing uptrend

**Pattern**: The strongest performers (VICR +227%, MU +241%, TER +188%) had multiple consecutive positive earnings surprises. Each positive earnings acted as a stacking catalyst, building momentum.

#### Tool Created
`tools/post_process_picks.py` — Reusable script for dedup, sector cap, lookback, and earnings analysis. Run with:
```bash
python tools/post_process_picks.py --picks-csv results/picker/three_layer_picks.csv --max-per-industry 2 --top 25
```

### Known Issues / Next Steps
1. **Financial Datasets API credits**: $0.00 balance. Need to add credits for it to be the primary fundamental data source. Currently falls back to Yahoo.
2. **~1,000 symbols without quarterly data**: Many are likely inactive/shell companies. Consider enforcing $100M market cap minimum to exclude them.
3. **Quarterly ROE in parquet**: Existing 5,611 parquet files have ROE=None (from before fix). Need `--backfill-quarterly` run to refresh them all.
4. **EPS YoY outliers**: Some stocks show extreme values (e.g., SEI 6136%, MTSI 24547%) — these are turnaround stories going from near-zero to positive EPS. Consider capping or weighting differently.
5. **Disclosure date alignment**: Currently uses `report_date` as `disclosure_date` (Yahoo doesn't provide actual filing date). Financial Datasets API does provide proper dates.
6. **Revenue acceleration filter**: Disabled by default (`--min-revenue-acceleration None`). Enable with caution — very strict.
7. **Gold/mining stocks dominate picks**: ORLA, NGD, CDE, OR, GFI, AGI, AU, FSM, PAAS, WPM are all miners/gold. Consider sector diversification constraint.

---

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

## Feb 13, 2026 — Automation Workflow Recovery (Discord Reports)

### Root Cause
- **All schedulers sent to a deleted Discord channel**. `DIGEST_CHANNEL_ID` default pointed to `1345123472019423284`, which Discord returned as `404 Unknown Channel`. Result: every job silently skipped.

### Fixes Applied
1. **Updated default digest channel** to `#alerts` (`1469145961925312543`) in `interactive_bot.py`.
2. **Updated proactive channel** in `claude_discord_agent.py` to the same `#alerts` channel.
3. **Startup health report** now posts to Discord at every boot.
4. **Scheduler error reporting** now posts errors to Discord, not just logs.
5. **Pipeline timeout increased** to 20 minutes (`PIPELINE_TIMEOUT_SECONDS`, env override supported).
6. **Discord-only fallback digest** posts when scanner times out (ensures every job sends a report).

### Evidence (Live)
- Startup report was successfully sent to `#alerts` after restart.
- Manual pipeline report was sent to `#alerts` to validate end-to-end Discord delivery.

### Files Modified
- `workspace/scripts/discord_bot/interactive_bot.py`
- `workspace/scripts/discord_bot/claude_discord_agent.py`
- `workspace/scripts/discord_bot/trade_executor.py`
- `workspace/scripts/discord_bot/signal_pipeline.py`

## 3. Next Steps for Next Agent
1. **Monitor deployment:** Ensure schedulers post to `#alerts` at their scheduled times (6:35, 7:00, 12:00, 2:45, 3:15 PST).
2. **Confirm IBKR gateway stability:** `/api/health` should show `connected=true` and `trading_mode` populated during market hours.
3. **Address `!positions` crash:** Trade API is returning a string instead of a list; add response guard in `cmd_positions`.
4. **Expand pattern library** and backtest (`pattern_library.py` + `backtest_patterns.py`).
5. **Read `docs/DEBUG_LESSONS.md`** before any production deployment.

## 4. Key Files
- `workspace/scripts/discord_bot/interactive_bot.py`: Main bot logic & schedulers.
- `workspace/scripts/discord_bot/signal_pipeline.py`: Pipeline logic, order generation, midday review.
- `tools/trading_gui.py`: Flask GUI, IBKR connection, P&L subscription.
- `workspace/scripts/discord_bot/pattern_library.py`: Pattern recognition & stats.
