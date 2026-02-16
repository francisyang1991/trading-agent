# Worklog & Handover Notes

**Last Updated:** 2026-02-16
**Branch:** `feature/pst-scheduler-gui-final`

## Feb 15, 2026 23:17 PST (Sunday Pre-Open for Monday, Feb 16) — Live Cache Safety Validation

### Commands Run
- Technical strict gate (requested):
  - `python3 tools/cache_health_check.py --scope technical --repair-invalid-first --strict --output results/picker/cache_health_technical.json`
- Listed strict gate:
  - `python3 tools/cache_health_check.py --scope listed --repair-invalid-first --strict --output results/picker/cache_health_listed.json`
- Listed gate with active preflight thresholds (`min_minbars_pct=0.80`):
  - `python3 tools/cache_health_check.py --scope listed --repair-invalid-first --strict --max-invalid-rows 0 --max-missing-pct 0.02 --min-fresh-pct 0.98 --min-minbars-pct 0.8 --max-stale-days 7 --period-days 730 --min-bars 260 --output results/picker/cache_health_listed_preflight_thresholds.json`
- Earnings session timing for current picks:
  - `python3 tools/earnings_calendar.py --symbols-file results/picker/three_layer_picks.csv --days-ahead 30 --output results/picker/earnings_calendar_picks_30d.csv`

### Results
- Technical scope status: `PASS` (`751` symbols, invalid rows `0`, missing `0`, fresh `100%`, min-bars>=260 `99.73%`).
- Listed scope status under strict `min_minbars_pct=0.98`: `FAIL` (expected for full listed universe with newer listings; min-bars>=260 `84.80%`).
- Listed scope status under in-workflow preflight thresholds (`min_minbars_pct=0.80`): `PASS`.
- Earnings calendar for the 25 current picks over next 30 days:
  - total events found: `12`
  - `post-market`: `9`
  - `pre-market`: `3`
  - no parsing/runtime errors observed.
- Quarterly fundamentals file audit (`data/fundamental/*`):
  - total files: `6,851`
  - readable now: `1,240`
  - unreadable in current runtime: `5,611` (parquet engine missing: `pyarrow`/`fastparquet`)
  - among readable files, invalid date rows (`report_date` or `disclosure_date` parse to NaT): `0`
  - implication: `NaN` date is treated as a data bug (row dropped), while coverage is currently constrained by parquet-read support.

### Current Price-Cache Coverage Snapshot
- DB daily symbols: `6,868`
- Listed universe symbols: `6,848`
- Global DB date range: `2023-02-14` to `2026-02-13`
- Symbols with >=260 bars in last 2y (DB-wide): `5,838`
- Symbols with full 2y span + fresh within 7 days (DB-wide): `5,472`
- Conclusion:
  - Cache does **not** hold full 2-year tradeable history for every listed symbol.
  - It is sufficient for technical-universe signal generation under current preflight constraints.

## Feb 16, 2026 — Incremental Earnings Refresh + Earnings Session Labels + Cache DQ Hardening

### Incremental Earnings-Refresh Workflow Added

#### New Command
- Added `tools/incremental_earnings_refresh.py`.
- Workflow:
  - Detects newly published/revised quarterly earnings rows by comparing cached vs force-refreshed quarterly fundamentals per ticker (`ticker + disclosure_date`).
  - Refreshes quarterly fundamentals only for likely impacted tickers (calendar delta prefilter) unless `--force-refresh-all-candidates` is set.
  - Invalidates/recomputes point-in-time snapshot cache rows only for impacted tickers on affected as-of dates.
  - Re-runs only impacted walk-forward windows (`--months-back` subset).
  - Re-runs scanner for impacted tickers only.
  - Writes run/change audit tables:
    - `results/picker/earnings_refresh_runs.csv`
    - `results/picker/earnings_refresh_release_changes.csv`
    - `results/picker/earnings_refresh_snapshot_changes.csv`
    - `results/picker/earnings_refresh_validation_changes.csv`

#### Validation
- Dry-run smoke test:
  - `python3 tools/incremental_earnings_refresh.py --symbols AAPL NVDA MSFT --months-back 3,6 --skip-validation --skip-scanner --dry-run --progress-every 1`
- Non-dry run smoke test:
  - `python3 tools/incremental_earnings_refresh.py --symbols AAPL NVDA MSFT --months-back 3,6 --skip-validation --skip-scanner --progress-every 2`
- Result: command runs clean; second run reports zero deltas once baseline cache is refreshed.

### Earnings Calendar: Pre-Market / Post-Market Classification Added

#### Provider Enrichment
- Updated `src/data/providers/yfinance_provider.py`:
  - `get_earnings_calendar()` now outputs:
    - `release_time_et` (HH:MM ET)
    - `release_session` (`pre-market`, `post-market`, `in-market`, `unknown`)
- This uses timezone-aware earnings timestamps from yfinance and ET session bucketing.

#### New Command
- Added `tools/earnings_calendar.py` to fetch upcoming earnings with session labels.
- Example:
  - `python3 tools/earnings_calendar.py AAPL NVDA MSFT --days-ahead 120 --output results/picker/earnings_calendar_upcoming_sample.csv`
- Sample output (Feb 15, 2026 run):
  - NVDA 2026-02-25 16:00 ET -> post-market
  - MSFT 2026-04-29 17:00 ET -> post-market
  - AAPL 2026-04-30 17:00 ET -> post-market

### Data-Quality Hardening for Price Cache

#### Runtime Guards
- Updated `src/data_manager.py`:
  - Added `_sanitize_daily_frame()` to enforce OHLCV validity on both read/write paths.
  - `_save_daily_to_db()` now drops invalid rows before insert.
  - `_load_daily_from_db()` now filters invalid rows and re-sanitizes legacy data.
  - `_needs_daily_coverage()` now checks earliest valid cached bar (not corrupted rows).

#### Repair Command
- Added `DataManager.purge_invalid_daily_rows()` + CLI:
  - `python3 -m src.data_manager --purge-invalid-daily`

#### Current Cache Status (Post-Repair Validation)
- Repair execution:
  - `python3 -m src.data_manager --purge-invalid-daily`
  - Purged invalid daily rows: `492,585`
- Post-repair checks:
  - DB daily symbols: `6,868`
  - Listed symbols universe: `6,848`
  - Listed symbols present in DB: `6,836`
  - Listed symbols missing in DB: `12` (sample: `BSAAR`, `CHARR`, `CRACW`, `CRANR`, `EMISR`, `EURKR`, `KCHVR`, `NE.A`, `NOEMR`, `SSEAR`, `UYSCR`, `WSTNR`)
  - Listed symbols with valid 2y span: `5,454`
  - Listed symbols with >=450 bars in 2y window: `5,507`
  - Listed symbols with >=500 bars in 2y window: `5,452`
  - Invalid daily rows remaining: `0`
  - Global date coverage in DB: `2023-02-14` to `2026-02-13`

#### Signal-Safety Preflight Command Added
- Added `tools/cache_health_check.py` for one-command cache health checks with PASS/FAIL gates.
- Default scope is technical universe (signal-generation universe), with strict thresholds:
  - invalid rows <= 0
  - missing <= 2%
  - fresh >= 98%
  - min-bars pass >= 98% (`min-bars` default 260 over 2y window)
- Output JSON report (default):
  - `results/picker/cache_health_report.json`

#### Live Preflight Results (Feb 15, 2026 UTC Evening)
- Technical scope:
  - Command: `python3 tools/cache_health_check.py --scope technical --strict --output results/picker/cache_health_technical.json`
  - Status: `PASS`
  - Universe: `751`
  - Invalid rows: `0`
  - Missing: `0` (0.00%)
  - Fresh: `751` (100.00%)
  - Min bars >=260: `749` (99.73%)
  - Full 2y span: `730` (97.20%)
- Listed scope:
  - Command: `python3 tools/cache_health_check.py --scope listed --strict --output results/picker/cache_health_listed.json`
  - Status: `FAIL` (expected for full listed universe due many recent/new symbols)
  - Universe: `6848`
  - Invalid rows: `0`
  - Missing: `12` (0.18%)
  - Fresh: `6825` (99.66%)
  - Min bars >=260: `5807` (84.80%)
  - Full 2y span: `5454` (79.64%)

#### Operational Recommendation for Monday Open
- Use technical-scope health check as the final gate before generating signals:
  - `python3 tools/cache_health_check.py --scope technical --strict --output results/picker/cache_health_technical.json`
- If you want automatic repair first:
  - `python3 tools/cache_health_check.py --scope technical --repair-invalid-first --strict --output results/picker/cache_health_technical.json`

#### Embedded In-Workflow Preflight (No Manual Step Required)
- Updated `tools/run_three_layer_picker.py` to run cache-health preflight automatically before price ingestion.
- New config block in `config/picker_config.yaml`:
  - `preflight.enabled: true`
  - `preflight.repair_invalid_first: true`
  - `preflight.strict: true`
  - `preflight.on_fail: abort`
  - threshold knobs (`max_invalid_rows`, `min_fresh_pct`, `min_minbars_pct`, etc.)
- Behavior:
  - Writes temporary run universe symbols file.
  - Runs cache health check with strict gate.
  - Aborts picker on preflight failure unless `on_fail: warn`.
- Smoke test run:
  - `python3 tools/run_three_layer_picker.py --config config/picker_config_smoke.yaml`
  - Verified preflight executes automatically and pipeline continues only when preflight passes.
- Additional symbol-file gate validation:
  - `python3 tools/cache_health_check.py --symbols-file results/picker/three_layer_picks.csv --strict --output results/picker/cache_health_from_picks.json`
  - Status: `PASS` for the 25 current picks (100% fresh + min-bars coverage).

#### NaN Date vs None Policy (Bug Guardrails)
- **Date fields are hard-required** in canonical cached datasets:
  - Daily: `Date`
  - Fundamentals: `report_date`, `disclosure_date`
- If a date parses to NaT/NaN, that row is treated as invalid and dropped (not prefilled).
- `None` is acceptable only for genuinely unavailable optional numeric fundamentals (e.g., ROE/gross margin), not for primary keys or dates.
- We avoid synthetic backfilling of missing dates to prevent silent bug masking.
- `tools/cache_health_check.py`, `tools/earnings_calendar.py`, and `tools/incremental_earnings_refresh.py` now ignore CSV headers (`ticker`/`symbol`) in symbols files to prevent false-symbol bugs.

### Fundamentals Cache Status (Current Environment)
- Quarterly fundamentals coverage check:
  - `symbols_total=6848`
  - `symbols_with_quarterly_data=1240`
  - `latest_rows_count=1240`
  - `non_null_latest`: eps `1172`, revenue `1022`, roe `1017`, gross_margin `819`
- Important note:
  - In this runtime, many legacy quarterly files are parquet-only and not readable without parquet engine support, so cached quarterly coverage appears lower than file count. Newly refreshed tickers are persisted via CSV fallback and are readable.

### Planning Artifacts Added
- Added `docs/STOCK_PICKING_EXECUTION_PLAN.md`:
  - data validity policy (NaN date vs None rules),
  - automated no-manual-check daily workflow,
  - current picker constraints snapshot,
  - post-picking execution checklist,
  - phased refactor plan aligned with `.cursor/rules/trading-agent.md`.

## Feb 14, 2026 — Data Infrastructure & Three-Layer Picker Overhaul

### Historical Timestamp Runner + Local Historical Cache (3m/6m/9m)

#### What Was Added
- **Point-in-time fundamentals for lookback**: `FundamentalSnapshotService.load_for_tickers()` now supports `as_of_date` and reconstructs snapshots from quarterly fundamentals disclosed on/before that date (no lookahead on EPS/revenue growth).
- **Persistent historical snapshot cache**: Added local cache files under `data/picker/fundamental_snapshots/{YYYY-MM-DD}.csv` to store per-date, per-ticker snapshots and avoid re-fetching the same historical data.
- **Historical cache rebuild control**:
  - CLI: `--rebuild-historical-cache`
  - Config: `lookback.refresh_snapshot_cache`
- **Runner integration**:
  - `src/picker/lookback.py` now calls fundamentals service with `as_of_date`.
  - `tools/run_three_layer_picker.py` now saves per-window outputs:
    - `results/picker/lookback_now.csv`
    - `results/picker/lookback_3m_ago.csv`
    - `results/picker/lookback_6m_ago.csv`
    - `results/picker/lookback_9m_ago.csv`
    - `results/picker/lookback_comparison.csv`
- **Environment hardening**:
  - `src/data/mysql_store.py`: SQLAlchemy import is now optional (prevents crash when `sqlalchemy` is not installed and MySQL sink is unused).
  - `src/data/parquet_store.py`: Added CSV fallback for parquet read/write when `pyarrow/fastparquet` is unavailable.

#### Cache Validation (Same Command, Re-run)
- Command: `python3 -u tools/run_three_layer_picker.py --lookback`
- **Cold rebuild after fixes:** ~656.7s (~10.9 min)
- **Warm cache run:** ~34.7s
- Result: Historical cache is working and prevents repeated server work for the same as-of dates.

#### Latest Lookback Results
- `now`: 25 picks
- `3m_ago`: 6 picks
- `6m_ago`: 4 picks
- `9m_ago`: 4 picks
- Cross-window overlap (`appearances >= 2`): `RIGL`, `NGD`, `NVDA`, `SCCO`, `LRCX`

#### Missing-Field Refill Fixes
- **EPS/Revenue historical growth fallback (no lookahead):**
  - Primary: true YoY (requires 5+ visible quarters)
  - Fallback: annualized proxy from 2-3 visible quarters (still point-in-time)
- **Debt-to-equity fix for historical quality filter:**
  - Historical D/E from `DataManager.get_fundamentals()` had corrupted scales (often extreme values), causing all historical quality candidates to fail `D/E <= 1.2`.
  - Switched historical D/E proxy source to `YFinanceProvider.get_factor_fundamentals()` (with local per-run cache), then fallback to DataManager only if needed.

#### Residual Limitation (Still True)
- True historical YoY remains limited by available quarterly depth from active providers (currently mostly Yahoo in this environment).
- When only 2-3 historical quarters are visible, growth is a proxy (annualized QoQ), not exact same-quarter-last-year YoY.
- To fully eliminate this approximation, the stack needs a deeper historical fundamentals provider (e.g., funded FinancialDatasets/FMP/IBKR historical quarterly endpoints with enough back-history).

### Feb 15, 2026 — Validation Workflow Improvements

#### Lookback Filter Policy Update
- Added `lookback.skip_missing_yoy_checks` to `config/picker_config.yaml` (default `true`).
- In lookback mode only (`src/picker/lookback.py`), missing EPS/Revenue YoY no longer automatically fails a stock.
- Threshold checks are applied when values exist; missing values are allowed to pass Layer-2 as requested.
- Added unit tests: `tests/unit/test_picker/test_lookback_filters.py`.

#### New 2-Year Bull Run Investigation Tool
- Added `tools/analyze_two_year_bull_runs.py` to detect sustained 200%+ runs and analyze likely drivers.
- Detection rules:
  - minimum run duration (default 30 days)
  - post-peak retention window (default 30 days) to skip quick push/fade moves
  - configurable liquidity and market-cap filters
- Driver signals included:
  - point-in-time fundamentals at run start
  - abnormal run-period volume regime
  - earnings-catalyst overlap from `results/picker/earnings_catalysts.csv`
  - analyst upgrade/downgrade counts (best-effort)
  - headline keyword signals including options-related mentions (best-effort)
- Generated reports:
  - `results/picker/bull_runs_2y_analysis.csv`
  - `results/picker/bull_runs_2y_report.md`
  - liquid/large-cap filtered variants:
    - `results/picker/bull_runs_2y_analysis_liquid.csv`
    - `results/picker/bull_runs_2y_report_liquid.md`

### Feb 15, 2026 — Local Email Client for Trade-Idea Mining

#### What Was Added
- Added core local email ingestion + analysis module:
  - `src/email_analysis/email_trade_ideas.py`
  - Supports IMAP fetch (read-only), strict sender filtering, MIME body extraction, and Anthropic LLM extraction.
- Added Gmail OAuth reader module:
  - `src/email_analysis/gmail_reader.py`
  - Implements first-run browser consent flow and token caching (`token.json`) via Google OAuth.
  - Reads Gmail messages with `gmail.readonly` scope and strict sender filtering.
- Added package exports:
  - `src/email_analysis/__init__.py`
- Added thin CLI wrapper:
  - `tools/analyze_citrini_emails.py`
  - Defaults to sender `citrini@substack.com`
  - Supports providers: `--provider gmail` (default) or `--provider imap`
  - Gmail defaults:
    - client secret: `secret/client_secret_75860045039-mgs0h9aai4488pokfqgsqlb1doo41eh4.apps.googleusercontent.com.json`
    - token cache: `token.json`
  - Fetches latest emails, sends full email content to LLM, writes:
    - JSON: `results/email/citrini_trade_ideas.json`
    - Markdown: `results/email/citrini_trade_ideas.md`
  - Supports `--dry-run-fetch` to validate mailbox access before LLM calls.
- Added unit tests:
  - `tests/unit/test_email_analysis/test_email_trade_ideas.py`
  - `tests/unit/test_email_analysis/test_gmail_reader.py`
  - Covers sender matching, MIME extraction, and Gmail payload decode/extraction behavior.

#### Validation
- Unit tests passed:
  - `pytest -q tests/unit/test_email_analysis/test_email_trade_ideas.py tests/unit/test_email_analysis/test_gmail_reader.py`
- CLI smoke test passed:
  - `python3 tools/analyze_citrini_emails.py --help`
- Lint check:
  - No linter errors in newly added files.
- Dependency install note:
  - Direct `pip install --upgrade ...` failed in this macOS environment due to PEP 668 externally-managed Python.
  - Use a virtualenv for Google API package installation.

#### Next Steps
1. Create/activate virtualenv and install Google deps:
   - `python3 -m venv .venv && source .venv/bin/activate`
   - `python3 -m pip install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib`
2. Run Gmail OAuth dry fetch first (browser opens on first run and writes `token.json`):
   - `python3 tools/analyze_citrini_emails.py --provider gmail --dry-run-fetch --limit 10`
3. Run full analysis:
   - `python3 tools/analyze_citrini_emails.py --provider gmail --limit 20 --since-days 180`
4. Review extracted ideas in:
   - `results/email/citrini_trade_ideas.md`

### Feb 15, 2026 — Citrini Email → Discord Pipeline & Z.AI Support

#### What Was Added
- **Z.AI (GLM) LLM backend**: `src/email_analysis/email_trade_ideas.py`
  - `analyze_emails_with_zai()` and unified `analyze_emails_with_llm(provider="zai"|"anthropic")`
  - `tools/analyze_citrini_emails.py` defaults to `--llm-provider zai`; use `ZAI_API_KEY` or `ANTHROPIC_API_KEY`
- **Citrini → Discord pipeline**: `src/email_analysis/citrini_discord.py`
  - Fetches unseen emails from `citrini@substack.com`, runs LLM, formats trade ideas for Discord
  - Tracks processed message IDs in `data/email/citrini_processed.json` to avoid re-sending
- **Integration with `workspace/scripts/discord_bot/interactive_bot.py`**:
  - Scheduled task: runs every 60 min when `CITRINI_EMAIL_ENABLED=true`
  - Manual command: `!citrini` to trigger on demand
  - Posts to digest channel (`DIGEST_CHANNEL_ID`)
- **CLI test script**: `tools/run_citrini_email_check.py` — run without bot for testing
- **EmailDocument**: `gmail_id` field for tracking processed Gmail message IDs

#### AWS Deployment Context (from scripts/sync_to_aws.sh)

- **Host:** `ubuntu@35.90.4.89`
- **Key:** `~/.ssh/openclaw-key.pem`
- **Remote dir:** `/home/ubuntu/trading-agent`
- **Sync:** `./scripts/sync_to_aws.sh` — syncs workspace/, src/, tools/, config/, scripts/, requirements.txt
- **Deploy:** `bash scripts/deploy_bots_aws.sh` (run on AWS after sync)
- **Env:** `workspace/.env` (DISCORD_BOT_TOKEN, ANTHROPIC_API_KEY, etc.)

#### Deployment Options

**Option A: Discord bot on AWS (recommended)**

When running the interactive bot on AWS, set these env vars in `workspace/.env`:

```bash
export CITRINI_EMAIL_ENABLED=true
export CITRINI_EMAIL_INTERVAL_MIN=60   # optional, default 60
export ANTHROPIC_API_KEY=your_key      # or ZAI_API_KEY
# Ensure token.json exists (Gmail OAuth for citrinishared@gmail.com)
python workspace/scripts/discord_bot/interactive_bot.py
```

The bot must have access to:
- `token.json` (Gmail OAuth for citrinishared@gmail.com)
- `secret/client_secret_*.json` (Google OAuth client secret)
- `ANTHROPIC_API_KEY` or `ZAI_API_KEY`

**Option B: Standalone cron job**

Run the email check hourly without the Discord bot:

```bash
# cron (every hour at :05)
5 * * * * cd /path/to/trading_agent && source .venv/bin/activate && ANTHROPIC_API_KEY=xxx python tools/run_citrini_email_check.py
```

For standalone cron, you would need to add Discord webhook posting to `run_citrini_email_check.py` — currently it only prints to stdout. To post to Discord from cron, either:
- Use a Discord webhook URL and `POST` the formatted messages, or
- Run the bot with `CITRINI_EMAIL_ENABLED=true` (Option A) — scheduler handles it.

#### Manual Trigger

With the bot running: `!citrini` in Discord.

#### Files Modified
- `src/email_analysis/email_trade_ideas.py` — Z.AI backend, `gmail_id` on EmailDocument
- `src/email_analysis/gmail_reader.py` — set `gmail_id` to Gmail message ID
- `src/email_analysis/citrini_discord.py` — new
- `workspace/scripts/discord_bot/interactive_bot.py` — `_citrini_email_scheduler()` and `!citrini` command
- `tools/run_citrini_email_check.py` — new
- `requirements.txt` — `zai-sdk>=0.1.0`

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
6. **Citrini email pipeline:** See `docs/CITRINI_EMAIL_PIPELINE.md` for deployment. AWS context (host, sync script) is in the Citrini section above and in `scripts/sync_to_aws.sh`. Enable with `CITRINI_EMAIL_ENABLED=true` in `workspace/.env`.

## 4. Key Files
- `workspace/scripts/discord_bot/interactive_bot.py`: Main bot logic & schedulers.
- `workspace/scripts/discord_bot/signal_pipeline.py`: Pipeline logic, order generation, midday review.
- `tools/trading_gui.py`: Flask GUI, IBKR connection, P&L subscription.
- `workspace/scripts/discord_bot/pattern_library.py`: Pattern recognition & stats.
- `src/email_analysis/citrini_discord.py`: Citrini email → LLM → Discord pipeline.
