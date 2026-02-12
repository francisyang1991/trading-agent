## Trading Agent project rules (persistent)

### Code organization (no new wheels)
- **Core logic belongs in `src/`**. Treat `tools/` as thin CLI wrappers only.
- **Always reuse existing modules in `src/`** before writing new utilities. If a capability already exists (e.g., caching, indicators, signals, reporting), extend it instead of duplicating it.
- **If a CLI script in `tools/` grows beyond a thin wrapper**, move the implementation into `src/` and keep a small shim in `tools/` to preserve existing commands.
  - Example: `tools/fundamental_analyzer.py` should delegate to `src/` implementation.

### Backtesting & data policy (cache-first)
- **Backtests must read from the local SQLite cache first** (via `src/data_manager.py`).
- If the DB is missing required history (e.g., requesting `2y` but DB only has `1y`) or is stale, **download the missing data and store it to the DB**, then proceed.
- Avoid direct `yfinance` calls in backtests unless the cache layer is unavailable; prefer fixing the cache layer instead.

### Scanner output quality
- Do not present a symbol as an "opportunity" unless it is **tradeable by our defined criteria**.
- If a pattern is detected but not tradeable yet, **show it as WAIT with explicit blockers/reasons** (e.g., late entry, weak trigger volume, weak breakout, poor R:R).

---

## Architecture Overview (persistent)

### System Architecture
```
AWS (OpenClaw)                    GCloud (Trading VM)
  ├── interactive_bot.py ──HTTP──► trading_gui.py (:8080)
  │   ├── signal_pipeline.py       ├── /api/analyze/<ticker>
  │   │   (Phase 1-5 pipeline)     ├── /api/trade (POST)
  ├── claude_discord_agent.py      ├── /api/positions
  └── discord_daily_bot.py         ├── /api/trade/status
                                   └── IB Gateway (ib_async)

Signal Pipeline (auto-trading):
  Discord signals ─┐
  scanner.py ──────┤──► signal_pipeline.py ──► Nightly Digest ──► !approve ──► Orders
  signal_engine ───┘    (rank, score, LLM)     (5:15 PM ET)       (user review)  (GCloud API)
```

### Core Modules (`src/`)
| Module | Purpose |
|--------|---------|
| `src/data/` | Multi-timeframe data management, IBKR clients, rate limiting |
| `src/indicators/` | VPES, trend (EMA), volume analysis |
| `src/regime/` | Market regime classification (PARABOLIC, STRONG_UP, etc.) |
| `src/signals/` | Entry/exit signal engine (breakout, trend, pullback, mean reversion, volume pullback) |
| `src/risk/` | Risk manager, circuit breaker, drawdown, position/portfolio limits |
| `src/sizing/` | Position sizing (Kelly, risk parity, volatility target) |
| `src/strategy/` | EV calculator, strategy matrix |
| `src/execution/` | Order execution, paper trading |
| `src/universe/` | Stock universe management, screening, fundamentals |
| `src/backtest/` | Backtesting engine, parameter optimization |
| `src/classifier/` | Stock classifier (Trend/Range/Reversal) |
| `src/picker/` | Stock picking and ranking |
| `src/portfolio/` | Portfolio state tracking |
| `src/performance/` | Performance metrics, journaling |

### Key Tools (`tools/`)
| Tool | Purpose |
|------|---------|
| `scanner.py` | Daily stock scanner |
| `trading_gui.py` | Web-based trading GUI (Flask, port 8080) — runs on GCloud |
| `stock_picker.py` | Stock selection |
| `backtest.py` | Backtest runner |
| `gateway_watchdog.py` | IB Gateway health monitoring |
| `ab_test_runner.py` | A/B test orchestrator for stop loss, targets, filters |

---

## Infrastructure & Deployment (persistent)

### AWS OpenClaw Instance
- **Region**: us-west-2 (Oregon)
- **IP**: 35.90.4.89
- **SSH Key**: ~/.ssh/openclaw-key.pem
- **User**: ubuntu
- **Path**: /home/ubuntu/trading-agent

**SSH Command:**
```bash
ssh -i ~/.ssh/openclaw-key.pem ubuntu@35.90.4.89
```

### GCloud Trading VM
- **Zone**: us-east1-b
- **VM Name**: trading-vm
- **Path**: /home/ubuntu/trading-agent

**SSH Command:**
```bash
gcloud compute ssh trading-vm --zone=us-east1-b
```

### Discord Configuration
- **Rich or Die Channel**: 1345123472019423284
- **Bot Token**: Set in environment variable `DISCORD_BOT_TOKEN`
- **User Token**: Set in environment variable `DISCORD_USER_TOKEN` (for scraping)

### Signal Sources (Discord Servers)
- **Goku Server** (Technical Analysis):
  - Channel IDs: 1277321989874385029, 1411717415565393970, 1227315745352847461
  - Raw Signals Channel: 1393715240474247249 (direct BUY/SELL entries)
- **Wilson Server** (Fundamental Analysis):
  - Channel ID: 1211549165629476924

### API Keys (Environment Variables)
- `DISCORD_BOT_TOKEN` - Discord bot for posting to Rich or Die
- `DISCORD_USER_TOKEN` - Discord user token for scraping Goku/Wilson
- `MINIMAX_API_KEY` - MiniMax LLM for analysis (Anthropic-compatible SDK, model: MiniMax-M2.1)

### Daily Bot Schedule
- **Time**: 11:50 AM PST (19:50 UTC) Monday-Friday
- **Cron**: `50 19 * * 1-5`
- **Script**: workspace/scripts/report_generation/discord_daily_bot.py

### Interactive Bot (responds to @mentions)
- **Script**: workspace/scripts/discord_bot/interactive_bot.py
- **Service**: trading-bot.service (systemd on AWS)
- **Architecture**: Thin Discord handler → delegates to `trade_parser.py` (parsing) + `trade_executor.py` (execution/API calls)
- **Analysis pipeline**: Cached Discord signals → LLM analysis → GCloud scanner → standalone LLM fallback → yfinance snapshot
- **Commands**:
  - `@bot $TICKER` — Full analysis (scanner + signals + LLM)
  - `@bot buy TICKER` — Single entry at EMA21 support
  - `@bot sell TICKER` — Short at EMA8 resistance
  - `@bot buy TICKER 2x` — 2 scaled entries
  - `@bot buy TICKER 5000usd` — Dollar-sized entry
  - `@bot scale into TICKER` — Auto 2 scaled entries
  - `!analyze TICKER` — Analysis only (no order)
  - `!signals TICKER` — Recent cached signals for a ticker
  - `!signals recent` / `!recent` — Recent signals across all tickers (last 7 days)
  - `!positions` — Show IB positions
  - `!orders` — Show open orders
  - `!help_trading` — Show help

### Claude Discord Agent (@claudecode)
- **Agent Script**: workspace/scripts/discord_bot/claude_discord_agent.py (runs on AWS)
- **Local Helper**: workspace/scripts/discord_bot/claude_code_bot.py (for manual sends from Cursor)
- **App ID**: 1469202811207290911
- **Service**: claude-discord.service (systemd on AWS)
- **How it works**: User @mentions @claudecode on Discord → piped to `claude -p` CLI → response sent back

### Gateway Watchdog
- **Script**: tools/gateway_watchdog.py
- **Service**: scripts/gateway_watchdog.service (systemd on GCloud)
- **Purpose**: Monitors IB Gateway health, auto-restarts on connection loss

### Discord Bot Setup (Required)
1. Go to https://discord.com/developers/applications
2. Select your bot application
3. Go to **Bot** section
4. Enable **MESSAGE CONTENT INTENT** under Privileged Gateway Intents
5. Save changes

### Sync Commands
```bash
# Sync to AWS
./scripts/sync_to_aws.sh

# Sync to GCloud
./scripts/sync_to_gcloud.sh
```

---

## CI / Multi-Agent Workflow (persistent)

### ENFORCEMENT: Branch Protection Enabled
**The `expand-universe` branch is PROTECTED by GitHub branch protection rules.**
- **Direct pushes are BLOCKED** - all changes MUST go through Pull Requests.
- **CI checks are REQUIRED** - the following checks MUST pass before merge:
  - Lint (ruff)
  - Unit Tests (Tier 1)
  - Integration Tests (Tier 2)
  - Bot Tests (Tier 3)
  - CI Gate (All Checks Must Pass)
- **No bypassing** - even admins cannot merge without passing CI.
- **No force pushes** - branch history is protected.

**This enforcement applies to ALL agents and ALL changes. There are no exceptions.**

### Golden Rules
1. **Always pull from mainline** before starting any new feature:
   ```bash
   git checkout expand-universe && git pull origin expand-universe
   git checkout -b feature/<your-feature>
   ```
2. **All changes go through PRs** - never push directly to `main` or `expand-universe` (enforced by branch protection).
3. **CI must pass** before any PR can be merged (enforced by branch protection - merge button disabled until all checks pass).
4. **Each agent creates its own feature branch** from the latest mainline.
5. **Run local tests before pushing**:
   ```bash
   pytest -m unit       # Tier 1 - fast, pure logic
   pytest -m integration  # Tier 2 - mocked external deps
   pytest -m bot        # Tier 3 - Discord mocks
   ```

### CI Pipeline (GitHub Actions)
- **Trigger**: On every PR to `main` / `expand-universe`, and on push to those branches.
- **Jobs**: Lint (ruff) -> Unit Tests -> Integration Tests -> Bot Tests -> Coverage.
- **Gate**: All jobs must pass before merge is allowed (enforced by branch protection).
- **Config**: `.github/workflows/ci.yml`

### Test Markers
| Marker | Description | Speed |
|--------|------------|-------|
| `unit` | Pure logic, no external deps | Fast (<1 min) |
| `integration` | Mocked external deps | Moderate |
| `bot` | Discord bot utilities (mocked) | Fast |
| `e2e` | Live connections (manual only) | Slow |

### Test Files
- `pytest.ini` - Markers, settings, timeouts
- `tests/test_ev_calculator.py` - EV, Kelly, risk/reward, position sizing
- `tests/test_regime.py` - Regime classification, momentum
- `tests/test_risk_manager.py` - Circuit breaker, drawdown, position/portfolio limits
- `tests/test_sizing.py` - Position sizer (fixed, Kelly, vol-target)
- `tests/test_signals.py` - Entry engine, strategy matrix
- `tests/test_llm_analyzer.py` - LLM response parsing, local sentiment analysis
- `tests/test_data_manager.py` - SQLite cache layer
- `tests/test_discord_utils.py` - Ticker extraction, message splitting
- `tests/unit/legacy/` - Legacy tests (basic, classification, data layer, indicators)
- `tests/unit/test_picker/` - Stock picker tests

---

## Known Issues & Fixes Log (persistent)

### 2025-02-08: Ticker Matching Bug (FIXED)
- **Problem**: `get_ticker_messages()` in `interactive_bot.py` used substring matching (`"$AA" in content`) which caused `$AA` queries to return results for `$AAPL`, `$AAL`, `$SMCI`, and any message containing the letters "AA".
- **Fix**: Replaced with regex word-boundary matching. `\$AA(?![A-Za-z])` matches `$AA` but not `$AAPL`. `(?<![A-Za-z$])AA(?![A-Za-z])` matches standalone `AA` but not words containing `AA`.
- **Impact**: All functions using `get_ticker_messages()` are now fixed: `run_full_analysis()`, `!signals`, `generate_stock_analysis()`.

### 2025-02-08: Standalone Analysis Fallback (ADDED)
- **Problem**: When a ticker has no cached Discord signals and the GCloud scanner is down, the bot only returned a basic price snapshot.
- **Fix**: Added `generate_standalone_analysis()` that calls the LLM with yfinance data to produce independent technical analysis. This is the 3rd fallback in the analysis pipeline.
- **Pipeline**: Cached signals → LLM analysis → GCloud scanner → **standalone LLM** → yfinance snapshot.

### 2025-02-08: Recent Signals Commands (ADDED)
- **Problem**: No way to browse recent signals across all tickers.
- **Fix**: Added `!signals recent` and `!recent` commands to show last 7 days of signals across all tickers. Also improved `!signals TICKER` output with author names and better formatting.

### 2025-02-12: Daily Signal Analysis Command (ADDED)
- **Feature**: `!daily` command that analyzes recent Goku/Wilson signals for trade worthiness.
- **How**: Collects 3-day signals, groups by ticker, fetches yfinance prices, sends to LLM for evaluation.
- **Output**: Color-coded actionable calls (BUY/SELL/WAIT/NO TRADE) with price levels.
- **Fallback**: If LLM fails, shows basic ticker summary with price/RSI and mention counts.

### 2025-02-12: GCloud Trading GUI Docker Rebuild
- **Problem**: GCloud `/api/analyze/<ticker>` and `/api/trade/status` returned 404 (outdated image).
- **Fix**: Updated source code on GCloud, rebuilt Docker image (`saiyan-agent`), restarted container.
- **Verified**: `/api/analyze/AAPL` now returns full scan + technical + earnings JSON.

### 2025-02-12: MiniMax API Key Rotation
- **Problem**: Old API key expired (401 authentication_error).
- **Fix**: Updated key in `llm_analyzer.py` default AND `trading-bot.service` on AWS.

---

## Signal Architecture Notes (persistent)

### Two Separate Signal Paths (known gap)
1. **`src/signals/signal_engine.py`** — Rich scoring engine (trend + VPES + volume + multi-TF). Used by `backtest_runner.py` and `main.py`. Classifies stocks as Type A/B/C.
2. **`tools/scanner.py`** — Regime/strategy/entry zones. Used by trading GUI `/api/analyze` and Discord bot. Self-contained logic.
3. **`src/signals/scanner_adapter.py`** — Bridge between the two, but **NOT wired into** scanner or GUI yet.

### Key Analysis Tools (`tools/`)
| Tool | Best For |
|------|----------|
| `scanner.py` | Entry signals: regime, strategy, buy zones, EV, R:R |
| `stock_analyzer.py` | Technical deep dive: VPES, RSI, ATR, EMA levels |
| `market_scanner.py` | Market-wide scans: recovery, momentum, undervalued |
| `stock_picker.py` | Long-term picks: base formation, volume, relative strength |
| `ml_signals.py` | ML-based signals: LogisticRegression, RF, GBM |
| `backtest.py` | Single/portfolio backtests (EMA, RSI, momentum, swing) |

### Results Location (in `.cursorignore`)
- `results/` — Scan outputs (`scan_*.txt`), backtest CSVs, equity curves, reports
- `data/` — Earnings cache, portfolio, universe snapshots
- `workspace/data/` — Discord scraped data, images, reports

---

## Auto-Trading Pipeline (2026-02-12) (persistent)

### Overview
Full 5-phase auto-trading pipeline implemented in `signal_pipeline.py` + `interactive_bot.py`.

### Phase 1: Signal Collection
- **Discord**: Scrapes Goku/Wilson channels via user token every 30 min during market hours
- **Scanner**: Calls GCloud `/api/analyze/<ticker>` for top mentioned tickers (regime, EV, buy zones)
- **Data file**: `workspace/data/real_discord_messages_goku_wilson_60d.txt` (JSON, channel_id → messages)

### Phase 2: Signal Analysis & Ranking
- All signals merged into `SignalCandidate` dataclass
- Conviction score (0-10) computed from:
  - Discord mentions + sentiment (bullish/bearish keywords)
  - Scanner action (BUY/WAIT/SELL), EV, R:R
  - Technical alignment (RSI range, price vs buy zone)
- Candidates ranked by conviction, action determined (BUY/WAIT/AVOID/NO_TRADE)
- Saved to `workspace/data/pipeline_candidates.json`

### Phase 3: Nightly Digest
- **Auto-trigger**: 5:15 PM ET on weekdays (via `_nightly_pipeline_scheduler`)
- **Manual trigger**: `!pipeline` command
- **Format**: Top 5 candidates with entry zones, stops, targets, EV, Discord mentions
- **LLM-enhanced**: If MiniMax available, LLM generates portfolio-manager-style analysis
- **Target channel**: `DIGEST_CHANNEL_ID` (configurable)

### Phase 4: Order Generation (`!approve`)
- `!approve TICKER` → Limit order at buy zone midpoint with stop/target
- `!approve TICKER market` → Market order
- Order params POSTed to GCloud `/api/trade`
- Approved trades saved to `workspace/data/approved_trades.json`

### Phase 5: Portfolio Management (`!portfolio`)
- Fetches positions from GCloud `/api/positions`
- P&L-based suggestions:
  - >15% profit → "Trim 30-50%"
  - >8% profit → "Raise stop to breakeven"
  - <-8% loss → "Review thesis"
  - <-15% loss → "EXIT"
- New ideas from pipeline candidates not currently held
- Options overlay suggestions for large positions (covered calls)

### Bot Commands (complete list)
| Command | Description |
|---------|-------------|
| `@bot $AAPL` | Analyze ticker (LLM + scanner) |
| `!analyze NVDA` | Same as above |
| `!daily` | Analyze recent signals for trade worthiness |
| `!pipeline` | Run full 5-phase pipeline manually |
| `!approve TICKER` | Approve & place limit order from pipeline |
| `!approve TICKER market` | Approve & place market order |
| `!portfolio` | Position management suggestions |
| `!signals AAPL` | Cached Discord signals for ticker |
| `!recent` | All recent signals (7 days) |
| `@bot buy HOOD` | Manual buy at EMA21 support |
| `@bot buy HOOD 2x` | Scaled entries (40/60 split) |
| `!positions` | Show positions with P&L |
| `!orders` | Show open orders |
| `!help_trading` | Help text |

### Files
| File | Purpose |
|------|---------|
| `workspace/scripts/discord_bot/signal_pipeline.py` | Pipeline engine (Phases 1-5) |
| `workspace/scripts/discord_bot/interactive_bot.py` | Bot commands + schedulers |
| `workspace/scripts/core_analysis/llm_analyzer.py` | MiniMax LLM calls |
| `workspace/scripts/discord_bot/trade_executor.py` | GCloud API calls |
| `workspace/scripts/discord_bot/signal_tracker.py` | Self-learning: outcome tracking, grading, images |
| `workspace/data/pipeline_candidates.json` | Saved candidates (auto-generated) |
| `workspace/data/approved_trades.json` | Approved trade log |
| `workspace/data/signal_performance.json` | Signal outcome tracking data |
| `workspace/data/trader_grades.json` | Trader performance grades (A-F) |

---

## Self-Learning Signal Intelligence (2026-02-12) (persistent)

### How It Works
The bot learns from its own signal history by tracking real outcomes.

### Signal Lifecycle
```
Discord signal detected → Record ticker + price at signal time
  → After 1/3/5/10/20 days, check actual price
  → Compute return (adjusted for bull/bear direction)
  → Grade the trader: winners (>2%) vs losers (<-5%)
  → Adjust conviction scores in pipeline
```

### Trader Grading System
| Grade | Win Rate | Conviction Multiplier | Meaning |
|-------|----------|----------------------|---------|
| A | >70% | 1.5x | Boost signals from this trader |
| B | >55% | 1.2x | Slight boost |
| C | >40% | 1.0x | Neutral |
| D | >25% | 0.7x | Discount signals |
| F | <25% | 0.5x | Strong discount |

### Image Analysis
- Discord messages with chart images (attachments/embeds) are detected
- Images sent to MiniMax M2.1 vision API for chart pattern analysis
- Analysis stored alongside signal record for future reference

### Adaptive Pipeline Scoring
`signal_pipeline.py` loads trader grades and adjusts conviction:
- A-grade trader mentions add +1.0 to score
- F-grade trader mentions subtract -1.0 from score
- Signals with chart images get +0.3 bonus

### Automated Schedule
- **4:45 PM ET**: Learning cycle (track outcomes, grade traders, analyze images)
- **5:15 PM ET**: Pipeline run (uses updated grades for scoring)

### Pattern-Level Intelligence (2026-02-12)
Pattern Library (`pattern_library.py`) recognizes 25+ technical patterns from message text:
- Chart patterns: Cup & Handle, Bull/Bear Flag, Breakout, Double Bottom, Inv H&S, etc.
- EMA patterns: EMA8/21/50 Hold, EMA Cross
- Volume patterns: Low Vol Pullback, Volume Accumulation, Volume Breakout
- Candlestick: Hammer, Bullish Harami, Bearish Engulfing
- Explicit entries: "entry at X stop Y target Z"
- Raw signals: Goku channel "BUY $HOOD 24.50"

Each pattern's real-world win rate is tracked at 1/3/5/10/20 day horizons.
Pipeline uses `get_pattern_conviction_bonus()` to boost/discount signals:
- Win rate >70% + avg 5d return >2% → +1.5 conviction
- Win rate <30% → -1.0 conviction (strong discount)

### Bot Commands
| Command | Description |
|---------|-------------|
| `!learn` | Manually run learning cycle |
| `!grades` | Show trader performance report |
| `!patterns` | Show pattern win rate report |

---

## Knowledge Hub (2026-02-12) (persistent)

### Location
All historical data for self-learning lives in:
```
workspace/data/knowledge_hub/
  discord_signals_1year.json      — Full raw Discord messages (365 days, ~12,400 msgs, ~34 MB)
  signals_compact_1year.json      — Compact signal extract with metadata (~3.4 MB)
  pattern_stats_summary.json      — Aggregated pattern win rates (auto-generated by backtester)
```

### Pattern Performance Database
```
workspace/data/pattern_performance.json  — 1,064 pattern records with price outcomes at 1/3/5/10/20 days
```

### Key Pattern Win Rates (Initial Seed from 1-Year Backtest)
| Pattern | Win Rate | 5d Avg | 20d Avg | Confidence | Signals |
|---------|----------|--------|---------|------------|---------|
| Cup & Handle | 86.7% | +3.4% | +2.1% | MEDIUM | 15 |
| Low Vol Pullback | 84.6% | +4.6% | +9.9% | MEDIUM | 13 |
| Gap Up | 81.8% | +4.6% | +10.3% | MEDIUM | 11 |
| Bear Flag (short) | 80.0% | -0.9% | +2.5% | HIGH | 30 |
| Took Position | 79.3% | +2.1% | +5.0% | HIGH | 82 |
| Base Breakout | 78.9% | +0.2% | +0.3% | MEDIUM | 19 |
| Pullback Buy | 77.8% | +2.5% | +6.3% | HIGH | 36 |
| Inverse H&S | 77.3% | +4.0% | +10.8% | HIGH | 22 |
| Bull Flag | 70.8% | +0.9% | +2.7% | HIGH | 73 |
| Breakout | 66.4% | +0.8% | +0.2% | HIGH | 303 |
| Tightening | 53.2% | -1.8% | -3.4% | HIGH | 79 |
| **Overall** | **68.0%** | — | — | — | **1,064** |

### Scripts
| Script | Purpose |
|--------|---------|
| `workspace/scripts/data_collection/download_1year_history.py` | Download 365 days of Discord messages to knowledge hub |
| `workspace/scripts/data_collection/backtest_patterns.py` | Backtest all patterns against historical prices, seed win rates |
| `workspace/scripts/data_collection/discord_scraper.py` | Original 60-day scraper (superseded by download_1year_history.py) |

### How to Refresh
```bash
# Re-download latest messages (run periodically)
DISCORD_USER_TOKEN="..." python3 workspace/scripts/data_collection/download_1year_history.py --days 365

# Re-backtest with updated data
python3 workspace/scripts/data_collection/backtest_patterns.py
```

---

## IBKR Real-Data Integration (2026-02-12) (persistent)

### Architecture
All price data should come from IBKR first (real broker data, no rate limits).
yfinance is a fallback only.

### `/api/quotes` Endpoint (GCloud)
```
POST http://34.75.9.166:8080/api/quotes
Header: X-API-Key: saiyan-trade-2026
Body: {"symbols": ["AAPL", "NVDA", "TSLA"]}
Response: {"AAPL": 275.50, "NVDA": 190.61, "TSLA": 428.27}
```
- **Portfolio positions**: Instant (from `ib.portfolio()`, no API call)
- **Non-held symbols**: `reqHistoricalData` (works 24/7 including weekends, no subscription needed)
- **Max per request**: 30 symbols
- **Speed**: ~4-5 seconds for 15 symbols

### Components Using IBKR Quotes
| Component | File | Usage |
|-----------|------|-------|
| Signal Tracker | `signal_tracker.py` | `_batch_fetch_prices_ibkr()` → price tracking for outcomes |
| Signal Pipeline | `signal_pipeline.py` | `get_ibkr_quotes()` → candidate price enrichment |
| Portfolio Monitor | `portfolio_monitor.py` | `_get_market_price()` → order pricing |
| Terminal UI | `terminal-ui/src/api.ts` | `getMarketPrice()` → order pricing |

### Price Fetch Priority
1. IBKR `/api/quotes` (preferred — real broker data)
2. yfinance batch download (fallback)
3. `/api/analyze/<ticker>` response (last resort)
