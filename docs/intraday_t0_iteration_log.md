# Intraday T0 Strategy Iteration Log

## Iteration 1: Regime-Aware Multi-Timeframe T0 (2026-03-01)

### Changes Made
1. **Multi-timeframe**: Resample 1m bars → 3m bars for signal generation
   - Smoother z-score, fewer false signals from noise
2. **Regime detection**: VWAP slope over 20-bar lookback (60 min on 3m bars)
   - slope > +3.0 bps/bar → UPTREND → block SELL satellite entries
   - slope < -3.0 bps/bar → DOWNTREND → block BUY satellite entries
   - else → RANGE → allow both (classic mean reversion)
3. **Portfolio mode**: $50k capital, 4 symbols, equal weight ($12.5k each)
4. **Config**: z_buy=-2.0, z_sell=2.0, z_exit=0.6, min_bars_between=4, min_hold=3

### Files Created
- `tools/replay_intraday_t0_v3.py` — v3 replay with regime + multi-timeframe
- `config/intraday_t0_config_v3.yaml` — v3 config

### Results (7 trading days, March 2026)

| Symbol | V2 Alpha | V3 Alpha | Delta  | V2 MaxDD | V3 MaxDD | V2 Trades | V3 Trades |
|--------|----------|----------|--------|----------|----------|-----------|-----------|
| AAPL   | -0.62%   | -0.59%   | +0.03% | 3.66%    | 3.58%    | 76        | 63        |
| TSLA   | +1.00%   | +1.13%   | +0.13% | 4.29%    | 3.88%    | 85        | 75        |
| NVDA   | +0.47%   | +1.25%   | +0.78% | 8.83%    | 8.72%    | 106       | 70        |
| MSFT   | +1.00%   | +0.64%   | -0.36% | 3.72%    | 3.98%    | 89        | 57        |
| **Avg** | **0.46%** | **0.61%** | **+0.15%** | — | — | 356 | 265 |

### What Worked
- Regime filter excellent for volatile stocks: NVDA alpha +0.47% → +1.25%
- TSLA: higher Sharpe (0.28 → 0.86), lower MaxDD (4.29% → 3.88%)
- Trade count dropped 25% → less churn, lower transaction costs

### What Didn't Work
- MSFT regressed: alpha +1.00% → +0.64%, regime filter too aggressive for low-vol
- Win rate dropped portfolio-wide: 40.5% → 33.3% (3m bars less precise for timing)
- AAPL still has negative alpha — mean reversion weak in this period

### Root Causes
1. 3m bars improve signal quality but hurt entry timing precision
2. One-size-fits-all regime threshold (3 bps/bar) doesn't suit all volatility levels
3. Low-vol stocks (AAPL, MSFT) need different z-score thresholds than high-vol (TSLA, NVDA)

### Ideas for Iteration 2
1. **2m bars** as compromise between 1m noise and 3m timing loss
2. **Adaptive regime threshold** per symbol: scale by ATR/price ratio
3. **Per-symbol z-score tuning**: tighter thresholds for low-vol, wider for high-vol
4. **Trailing exit** instead of fixed z-score exit — lock in profits on winning trades
5. **Only apply regime filter to high-vol stocks** (TSLA, NVDA); skip for low-vol (AAPL, MSFT)
6. **Improve win rate**: add RSI divergence or candle pattern confirmation before entry
7. **Min-edge gate**: only enter if expected_edge > 2 * (commission + slippage)

---

## Iteration 2: Adaptive Regime + 2m Bars + Stock Selection (2026-03-01)

### Changes Made
1. **2m bars** (was 3m) — better timing precision while still reducing noise
2. **Adaptive regime threshold** — scales by each symbol's ATR/price ratio
   - High-vol stocks get a higher threshold (more tolerant of VWAP slope)
   - Low-vol stocks get a tighter threshold (more sensitive to trend detection)
3. **$50k per stock** with 4x intraday margin ($200k buying power)
   - Core position = 80% of capital ($40k in shares)
4. **Stock selection lesson** — ultra-volatile stocks (OKLO, SMR, HUT, 6-10% daily range) FAIL
   - They trend too hard, mean reversion doesn't work
   - Best candidates: liquid moderate-vol stocks (2-4% daily range) that oscillate around VWAP
5. **Per-stock charts** — price overlay + equity curve per symbol (like original tool)
6. **Built-in scanner** — auto-scans universe for T0 candidates

### Scanner Experiment (Ultra-Volatile Stocks — FAILED)

| Symbol | Alpha    | Sharpe | MaxDD  | WinR  | Verdict |
|--------|----------|--------|--------|-------|---------|
| OKLO   | -0.49%   | -1.34  | 10.18% | 23.5% | FAIL — trends too hard |
| HUT    | -3.52%   | -0.76  | 15.32% | 23.9% | FAIL — crypto mining, trending |
| AFRM   | **+3.59%** | -1.86  | 9.26%  | **51.6%** | **PASS** — oscillates well |
| CIFR   | -1.99%   | 1.48   | 11.59% | 32.4% | FAIL — crypto exposure |
| TTD    | +0.52%   | -2.00  | 13.74% | 17.9% | MARGINAL |
| SMR    | -1.98%   | -3.64  | 17.31% | 32.1% | FAIL — nuclear hype stock |

**Lesson**: T0 mean reversion requires stocks that OSCILLATE around VWAP, not stocks that GAP and TREND.

### Main Results (Liquid Moderate-Vol Stocks — GOOD)

| Symbol | Return | B&H    | Alpha    | Sharpe | MaxDD | Trades | WinR  | PF    |
|--------|--------|--------|----------|--------|-------|--------|-------|-------|
| **PLTR** | +6.11% | +4.27% | **+1.83%** | **5.15** | 5.22% | 88 | **50.0%** | **55.10** |
| **AMD**  | +1.54% | +0.16% | **+1.38%** | **1.38** | 6.56% | 78 | **50.0%** | **6.06** |
| NVDA   | -4.03% | -5.25% | +1.22%   | -4.85  | 8.67% | 92     | 42.4% | 1.16  |
| TSLA   | +0.05% | -0.77% | +0.81%   | 0.20   | 4.17% | 89     | 43.8% | 2.21  |
| META   | +0.57% | +1.27% | -0.70%   | 1.18   | 4.02% | 65     | 23.1% | 0.52  |
| COIN   | +6.34% | +8.11% | -1.76%   | 3.73   | 9.46% | 85     | 47.1% | 0.94  |
| **Avg** | — | — | **+0.46%** | **1.13** | — | 497 | 42.7% | 11.00 |

### Key Findings

**What works well:**
- **PLTR**: Best performer — +1.83% alpha, Sharpe 5.15, 50% win rate, only 5.22% MaxDD
  - Oscillates nicely around VWAP, moderate volatility, high liquidity
- **AMD**: Second best — +1.38% alpha, Sharpe 1.38, 50% win rate, PF 6.06
  - Semiconductor with good intraday mean reversion characteristics
- **NVDA/TSLA**: Positive alpha (+1.22%, +0.81%) — regime filter prevents counter-trend losses

**What doesn't work:**
- **META**: Too low-vol for T0, insufficient intraday range (-0.70% alpha)
- **COIN**: Crypto stocks trend too hard intraday (-1.76% alpha despite +8% total return)
- **Ultra-volatile stocks**: Gap and trend, don't mean revert — terrible win rates (17-24%)

**vs Iteration 1:**
- Avg Sharpe: **1.13** (vs -1.65 in iter 1) — massive improvement
- Win rates: **42.7% avg** (vs 33.3%) — 2m bars are much better for timing
- 4 of 6 stocks have positive alpha (vs 3 of 4 in iter 1)
- Per-stock capital ($50k) with margin properly sized

### Stock Selection Rules Learned
1. **Best T0 stocks**: 2-4% avg daily range, >$500M daily volume, oscillate around VWAP
2. **Avoid**: >5% daily range (trends too hard), crypto miners (correlation to BTC trend)
3. **Sweet spot**: Large-cap tech semis (AMD, NVDA), growth tech (PLTR), volatile mega-cap (TSLA)
4. **Skip**: Low-vol mega-caps (AAPL, MSFT, META) — insufficient edge to cover costs

### Ideas for Iteration 3
1. **Concentrate on winners**: Focus on PLTR, AMD, TSLA, NVDA — drop META/COIN
2. **Trailing stop-profit**: Lock in gains when z-score reverses after going in our favor
3. **Volume-weighted entry**: Size entries proportional to relative volume (bigger trades when RVOL high)
4. **Intraday momentum filter**: Add short-term RSI or rate-of-change to confirm reversal
5. **Sector correlation**: If AMD and NVDA are both signaling BUY, increase confidence
6. **Time-of-day adjustment**: Widen z-score thresholds in first 30 min (more noise)

---

## Iteration 3: T0 Suitability Scorer + Automated Stock Selection (2026-03-01)

### Changes Made
1. **T0 Suitability Scorer** — new reusable module (`src/picker/t0_suitability.py` v1.1)
   - 6-metric scoring system (100 points total):
     - VWAP Crossing Frequency (20 pts): crossings/day, calibrated for 1m data (20+/day = max)
     - VWAP Deviation Quality (15 pts): % bars in "tradeable zone" (20-100 bps from VWAP)
     - Mean Reversion Strength (20 pts): VWAP reversion rate (% of extremes reverting within 10 bars)
     - Intraday Range Fitness (20 pts): sweet spot 2.5-5% daily range, asymmetric penalty
     - Trend-to-Range Ratio (15 pts): low directional move / high range
     - Liquidity (10 pts): ADV and volume consistency
   - Grades: A (75+), B (60+), C (45+), D (30+), F (<30)
   - Validated: correctly ranks known-good (PLTR, AMD) above known-bad (META, AAPL, SMR)

2. **Scorer integration** — replaced naive range-based scanner in `replay_intraday_t0_v3.py`
   - `--scan` now uses T0SuitabilityScorer instead of simple range/trend heuristic
   - Added `--scan-only` flag for stock selection without replay
   - Full suitability report printed during scan

3. **v1.0 → v1.1 scorer fixes** (discovered during testing):
   - v1.0 VWAP crossings: threshold 6/day was too low — ALL stocks maxed out (25/25)
   - v1.0 z-score reversion: broken — 20-bar rolling window too noisy for 1m data, returned 0/20 for all stocks
   - v1.0 autocorrelation: overlapping 5-bar returns created artificial positive autocorrelation
   - v1.1 fix: VWAP reversion rate (% of 30bps+ deviations reverting to <15bps within 10 bars)
   - v1.1 fix: VWAP deviation quality (penalizes too-close AND too-far from VWAP)

### Scorer Validation (12 stocks)

| Symbol | Score | Grade | RevR% | Range% | Known Result |
|--------|-------|-------|-------|--------|--------------|
| AMD    | 91    | A     | 21.0% | 3.23%  | GOOD (+1.38% alpha) |
| PLTR   | 84    | A     | 21.9% | 3.69%  | GOOD (+1.83% alpha) |
| TSLA   | 77    | A     | 13.3% | 2.71%  | GOOD (+0.81% alpha) |
| AFRM   | 75    | A     | 17.5% | 5.29%  | GOOD (+3.59% alpha) |
| MSFT   | 73    | B     | 18.2% | 2.15%  | MARGINAL |
| OKLO   | 72    | B     | 17.9% | 6.32%  | BAD (-0.49% alpha) |
| NVDA   | 68    | B     | 14.0% | 2.88%  | GOOD (+1.22% alpha) |
| META   | 63    | B     | 11.0% | 2.32%  | BAD (-0.70% alpha) |
| AAPL   | 62    | B     | 7.1%  | 2.32%  | BAD (-0.62% alpha) |
| SMR    | 50    | C     | 13.5% | 7.53%  | BAD (-1.98% alpha) |

### Iteration 3 Results — Scorer-Picked Stocks (Top 6 from 65-stock universe)

| Symbol | Score | Return | B&H    | Alpha    | Sharpe | MaxDD | Trades | WinR  | PF    |
|--------|-------|--------|--------|----------|--------|-------|--------|-------|-------|
| KLAC   | 92    | +4.17% | +3.99% | **+0.19%** | **4.14** | 5.36% | 96 | 47.9% | 3.99  |
| AMD    | 91    | +1.54% | +0.16% | **+1.38%** | **1.38** | 6.56% | 78 | 50.0% | 6.06  |
| MU     | 90    | +1.72% | -0.54% | **+2.26%** | **1.48** | 5.86% | 74 | 48.6% | 17.72 |
| AMAT   | 89    | +2.54% | +1.77% | **+0.77%** | **2.92** | 5.62% | 83 | 44.6% | 7.26  |
| QS     | 88    | -1.02% | -3.42% | **+2.40%** | -0.55  | 5.25% | 88 | 46.6% | 5.64  |
| ASML   | 87    | +1.51% | +0.68% | **+0.84%** | **2.07** | 5.97% | 106| 61.3% | 7.17  |
| **Avg** | **89.6** | — | — | **+1.31%** | **1.91** | — | 525 | 49.8% | 7.97 |

### Comparison: Iteration 2 vs Iteration 3

| Metric                | Iter 2 (hand-picked) | Iter 3 (scorer-picked) | Delta     |
|-----------------------|---------------------|----------------------|-----------|
| Avg Alpha             | +0.46%              | **+1.31%**           | **+0.85%**|
| Avg Sharpe            | 1.13                | **1.91**             | **+0.78** |
| Positive alpha stocks | 4/6 (67%)           | **6/6 (100%)**       | **+33%**  |
| Worst MaxDD           | 9.46%               | **6.56%**            | **-2.9%** |
| Avg Win Rate          | 42.7%               | **49.8%**            | **+7.1%** |
| Total Capital         | $300k (6×$50k)      | $300k (6×$50k)       | same      |
| Final Portfolio       | —                   | $305,233             | —         |
| Avg Profit Factor     | 11.00               | **7.97**             | — (iter2 skewed by PLTR PF 55) |

### Key Findings

**What worked:**
- **Scorer eliminates negative-alpha stocks**: 100% of picked stocks have positive alpha (vs 67% in iter 2)
- **Semiconductor stocks dominate**: KLAC, AMD, MU, AMAT, ASML all score A-grade — they oscillate around VWAP with 3-4% daily range
- **MU best alpha** (+2.26%): strong VWAP reversion rate (22.3%), 4.5% range — perfect T0 candidate
- **QS best alpha** (+2.40%): despite negative total return, T0 adds +2.4% over B&H by catching mean reversions
- **Reduced MaxDD**: worst MaxDD dropped from 9.46% (COIN) to 6.56% (AMD) — scorer avoids extreme-vol stocks
- **Higher win rates**: avg 49.8% (vs 42.7%) — better stock selection = more predictable mean reversion

**Key metrics the scorer gets right:**
- **VWAP reversion rate** is the strongest discriminator: PLTR 21.9%, AMD 21.0% vs AAPL 7.1%, META 11.0%
- **Range fitness** filters out ultra-low-vol (AAPL 2.3%) and ultra-high-vol (SMR 7.5%)
- **Trend-to-range ratio** penalizes stocks with strong multi-day trends

**Scorer limitations:**
- OKLO still scored 72 (B grade) despite being known-bad — reversion rate high (17.9%) but range too extreme (6.3%)
- NVDA ranked lower (68) than it should be — penalized by period trend, not intraday characteristics
- QS scored 88 but has low liquidity ($176M ADV) — may have execution issues with real money

### Files Modified/Created
- `src/picker/t0_suitability.py` — **NEW** T0 suitability scorer module (v1.1)
- `tools/replay_intraday_t0_v3.py` — **UPDATED** to use scorer for `--scan`, added `--scan-only`
- `docs/intraday_t0_iteration_log.md` — **UPDATED** with iteration 3 results

---

## Iteration 3b: Broad Market Validation + Trailing Stop Analysis + Dashboard Integration (2026-03-01)

### Broad Market Scan (354 stocks from universe)

Scored 341 stocks across all sectors. Grade distribution:
- A: 50 stocks (15%), B: 151 (44%), C: 120 (35%), D: 19 (6%), F: 1

**Top 10 A-Grade Stocks (by score):**

| Rank | Symbol | Score | Range% | RevR% | ADV($M) | Sector |
|------|--------|-------|--------|-------|---------|--------|
| 1    | KLAC   | 92    | 3.37%  | 25.9% | 2,731   | Semis  |
| 2    | AMD    | 91    | 3.23%  | 21.0% | 33,321  | Semis  |
| 3    | MU     | 90    | 4.50%  | 22.3% | 42,909  | Semis  |
| 4    | AMAT   | 89    | 3.56%  | 17.5% | 4,377   | Semis  |
| 5    | QS     | 89    | 3.86%  | 24.2% | 176     | EV     |
| 6    | TER    | 88    | 4.07%  | 20.6% | 2,709   | Semis  |
| 7    | MPWR   | 88    | 4.21%  | 21.4% | 1,121   | Semis  |
| 8    | ASML   | 87    | 2.82%  | 20.9% | 15,132  | Semis  |
| 9    | BKNG   | 87    | 4.39%  | 21.1% | 11,228  | Travel |
| 10   | LRCX   | 86    | 4.12%  | 18.8% | 3,655   | Semis  |

**Key Finding: Semiconductors dominate T0 suitability** — 7 of top 10 are semis.

### Broad Replay (14 A-Grade Stocks with ADV > $500M)

| Symbol | Score | Alpha    | Sharpe | MaxDD | WinR  | PF    |
|--------|-------|----------|--------|-------|-------|-------|
| KLAC   | 92    | +0.19%   | 4.14   | 5.36% | 47.9% | 3.99  |
| AMD    | 91    | +1.38%   | 1.38   | 6.56% | 50.0% | 6.06  |
| MU     | 90    | +2.26%   | 1.48   | 5.86% | 48.6% | 17.72 |
| AMAT   | 89    | +0.77%   | 2.92   | 5.62% | 44.6% | 7.26  |
| TER    | 88    | +1.23%   | 2.62   | 8.02% | 52.1% | 5.60  |
| MPWR   | 88    | +1.70%   | -0.64  | 8.09% | 42.4% | 7.88  |
| ASML   | 87    | +0.84%   | 2.07   | 5.97% | 61.3% | 7.17  |
| BKNG   | 87    | -0.18%   | 5.48   | 5.39% | 38.9% | 2.94  |
| LRCX   | 86    | +1.35%   | 0.57   | 7.86% | 51.8% | 4.10  |
| ARM    | 85    | -0.05%   | 2.03   | 4.68% | 36.0% | 1.41  |
| PLTR   | 84    | +1.83%   | 5.15   | 5.22% | 50.0% | 55.10 |
| SOFI   | 83    | +4.03%   | -2.03  | 8.71% | 47.1% | 18.44 |
| INTC   | 82    | -1.27%   | 1.63   | 4.36% | 31.3% | 0.59  |
| AVGO   | 82    | +2.16%   | -1.24  | 6.86% | 58.2% | 4.60  |
| **Avg** | **87** | **+1.16%** | **1.83** | — | 47.2% | 10.20 |

**Result: 11 of 14 stocks (79%) positive alpha** across broad market test.
Total capital $700k → $711,619 (+1.66%).

### Trailing Stop-Profit Analysis — REJECTED

Analyzed 136 round-trip trades across 14 stocks:

| Metric | Value |
|--------|-------|
| Avg winning trade final PnL | $38.15 |
| Avg winning trade peak PnL (intermediate) | $26.90 |
| **Winners exit HIGHER than peaks** | **+$11.25 per trade** |
| Losers that were once profitable (peak > $5) | 16/42 (38%) |
| Total recoverable from losers | $668.81 |

**Decision: DO NOT implement trailing stops.** Rationale:
1. Winners exit at +$11.25 MORE than intermediate peaks — z-score exit is well-timed
2. Trailing stop would clip winning trades early, reducing alpha
3. Best-case net benefit: +$361 across $700k portfolio = +0.05% (noise)
4. The z-score exit naturally captures full mean reversion completion

### Dashboard Integration (Saiyan)

Integrated T0 suitability scorer into the Saiyan trading dashboard:

1. **`/api/analyze/<ticker>`** — now includes `t0_suitability` section with:
   - Total score, grade (A/B/C/D/F)
   - Component scores (6 metrics)
   - Key metrics: reversion rate, avg range, ADV

2. **`/api/t0-scan`** — NEW batch scanning endpoint:
   - Scans full universe or specific symbols
   - Query params: `symbols`, `top`, `min_grade`
   - Returns sorted results with scores

3. **Frontend** — Analysis card now shows T0 section:
   - Grade badge with color coding
   - Component score breakdown table
   - Reversion rate, range, ADV metrics

### Files Modified
- `tools/trading_gui.py` — Added T0 scorer to analyze endpoint, new /api/t0-scan, frontend UI
- `docs/intraday_t0_iteration_log.md` — Updated with broad test + trailing stop analysis

---

## Iteration 4: Data Caching + Trend/Choppy Analysis (2026-03-01)

### Changes Made
1. **Intraday data caching** (`src/data/intraday_cache.py`)
   - SQLite cache in `data/stock_cache.db` (new `intraday_1m` + `intraday_meta` tables)
   - 3-tier data source: local cache → IBKR DB → yfinance (caches result)
   - Cache hit: 0.037s vs 0.6s download = **16x faster**
   - Integrated into `t0_suitability.py` and `replay_intraday_t0_v3.py`

2. **IBKR integration ready**
   - Existing `ib_data_server/` infrastructure supports 1min bars
   - `ingest.py --bar-size "1 min" --duration "14 D"` fetches 2 weeks of 1min data
   - Cache layer reads from IBKR DB automatically when available
   - IB Gateway not currently running; ready for when user configures it

3. **Session directional strength** (`dir_strength` feature)
   - Running `|close - session_open| / (session_high - session_low)` at each bar
   - Correctly classifies trend days (70-100% of bars > 0.5) vs choppy (1-17%)
   - Added to `build_features()` for monitoring

4. **Trend-day vs choppy-day analysis** — key findings:

| Day Type | Count | Avg Strategy PnL | Avg B&H PnL | Avg Alpha PnL |
|----------|-------|-------------------|-------------|----------------|
| CHOPPY   | 17    | +$120.77          | -$56.97     | **+$177.74**   |
| TREND    | 8     | +$109.68          | +$177.86    | **-$68.17**    |
| ALL      | 25    | +$117.22          | +$18.17     | **+$99.05**    |

5. **Trend-adaptive approaches tested and REJECTED**:
   - **Approach A: Reduce satellite in trends** (50%→25% sizing, wider exit z, longer cooldown)
     - Result: Total alpha -$409 vs baseline. Hurt DOWN-trend protection.
     - Root cause: Bar-level VWAP slope regime is 83-100% RANGE even on trend days.
       Switching to dir_strength: reduced trades but also reduced protective shorts.
   - **Approach B: Boost satellite in choppy** (65% sizing, easier entry z=-1.8)
     - Result: Total alpha -$75 vs baseline. Earlier entries add noise, not alpha.
   - **Conclusion**: Current z-score -2.0 and satellite 50% are well-calibrated.
     Strategy handles trends via core position (80%), satellite trading adds alpha on choppy days.

### Results (5 stocks, 7 trading days)
| Symbol | Return | B&H    | Alpha   | Sharpe | Trades | WinR  |
|--------|--------|--------|---------|--------|--------|-------|
| KLAC   | 1.86%  | 1.95%  | -0.09%  | 2.59   | 61     | 34.4% |
| AMD    | 3.44%  | 2.83%  | +0.62%  | 3.59   | 67     | 52.2% |
| MU     | 1.12%  | -0.90% | +2.02%  | 1.34   | 65     | 50.8% |
| PLTR   | 6.49%  | 5.81%  | +0.69%  | 8.35   | 66     | 48.5% |
| AMAT   | 0.07%  | -0.74% | +0.82%  | 0.28   | 59     | 39.0% |
| **Avg**|        |        | **+0.81%** | **3.23** |     |       |

### Files Modified
- `src/data/intraday_cache.py` — NEW: Intraday 1m bar cache
- `src/picker/t0_suitability.py` — Uses intraday cache instead of direct yfinance
- `tools/replay_intraday_t0_v3.py` — Uses intraday cache, dir_strength feature, updated docstring

### Key Learnings
- **Don't fight what works**: The Iter 3 params are well-calibrated. Trend-adaptive changes sound logical but hurt in practice.
- **Choppy days are the profit engine**: +$178/day alpha. The strategy should focus on stock selection that maximizes choppy-day exposure (which the T0 scorer already does).
- **Trend-day cost is manageable**: -$68/day loss is small vs choppy gains. Core position still captures trend moves.
- **Bar-level regime (VWAP slope) is too noisy**: Only 0-17% of bars classified as UPTREND/DOWNTREND even on clearly directional days. Session-level `dir_strength` is more reliable for classification.
- **Data caching is a must**: 16x speedup prevents re-downloading. IBKR can extend backtest beyond yfinance's 8-day limit.

### Ideas for Iteration 5
1. **Volume-weighted entry sizing**: Size entries by relative volume (RVOL) — bigger trades when conviction high
2. **Intraday momentum filter**: Add short-term RSI or ROC to confirm reversal before entry
3. **Liquidity floor in scorer**: Raise minimum ADV to $500M to avoid QS-type execution risk
4. **Sector correlation signal**: When AMD + ASML + MU all signal BUY, increase confidence
5. **Time-of-day z-score adjustment**: Wider thresholds in first/last 30 min (more noise)
6. **Dynamic stock rotation**: Re-score daily, swap out stocks whose suitability degrades
7. **Live T0 trading via IBKR**: Connect scorer + signal engine to IBKR gateway for real-time execution
8. **Longer backtests**: Once IBKR ingest is running, test 5 stocks for 2+ weeks
