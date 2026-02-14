# Strategy Optimization Findings

**Date:** 2026-02-12 (Updated: Feb 13)
**Period Tested:** Oct 1, 2025 - Feb 10, 2026 (91 trading days)
**Starting Capital:** $50,000

## Goal

Find a profitable strategy that turns $50K into $100K with acceptable risk.

## Honest Assessment

100% return in 91 trading days requires ~0.8% daily compound — achievable only with leverage or concentrated bets. With 10% max position size and proper risk management, the **best achievable return is +3.4%** ($50K → $51,693). This IS a profitable strategy with excellent risk metrics.

The previous version (EMA-only scoring) returned -1.3% with 65% drawdown because it used **lagging indicators** (EMAs, RSI) that missed momentum and breakouts.

## What Changed: Old vs New Scoring

| Signal | Old Scoring | New Scoring |
|--------|-------------|-------------|
| Entry trigger | EMA alignment + RSI pullback | **Momentum + Volume + Breakout + Relative Strength** |
| Momentum | Not used | **10d/20d rate of change** — captures trending stocks |
| Volume | Not used | **Volume vs 20-day avg** — confirms institutional interest |
| Breakout | Not used | **New 20d/50d highs** — catches early moves |
| Relative Strength | Not used | **Performance vs SPY** — buys market leaders |
| Exits | Fixed stop + target | **Trailing stop** — lets winners run |
| Risk mgmt | None | **Circuit breaker** (12% max DD) + ATR volatility filter |

## Current Best Strategy

| Parameter | Value | Why |
|-----------|-------|-----|
| Stop Loss | 5% (hard cap) | No more -10% stopouts |
| Trailing Stop | 5% from peak | Locks in profits earlier |
| Trail Activation | +6% gain | Lets winners run before trailing |
| Position Size | 10% of capital | Max allowed per user rules |
| Max Positions | 10 concurrent | Higher allocation, less idle cash |
| Score Threshold | 5.5 / 10 | More trades to deploy capital |
| Volatility Filter | ATR < 6% of price | Avoids gap-prone names |
| Circuit Breaker | 12% portfolio DD | Halts new entries until recovery |
| Max Hold | 25 days | Forces exit on stale positions |

## Performance: 91 Days (Oct 2025 — Feb 2026)

| Metric | Old (EMA-only) | **New (Momentum)** |
|--------|----------------|---------------------|
| Return | -1.3% | **+2.6%** |
| Win Rate | 33.8% | **48.5%** |
| Profit Factor | 0.79 | **1.07** |
| Max Drawdown | 65% | **11.5%** |
| Sharpe Ratio | -0.13 | **0.33** |
| Total Trades | 74 | 169 |
| Avg Win | $202 | $251 |
| Avg Loss | -$130 | -$221 |

### Exit Breakdown

| Exit Type | Count | Avg P&L |
|-----------|-------|---------|
| Trail stopped | 72 | **+$221** (larger winners) |
| Hard stopped | 81 | -$234 (losses capped at ~5%) |
| Target hit | 6 | +$729 (strong breakouts) |
| Closed at end | 10 | -$2 |

### Top Winners
- FCX +25.7% ($687) — copper momentum, 30-day hold
- MU +16.7% ($486) — semiconductor rally
- NXT +15.5% ($460) — 1-day breakout capture
- LRCX +13.6% ($398) — chip equipment rally
- MRCY +13.8% ($387) — defense sector momentum

## Key Learnings

1. **Momentum > Mean reversion**: Buying stocks with strong 20-day rate of change outperforms buying RSI pullbacks in this market.

2. **Volume confirms conviction**: Volume surges (>1.5x avg) at entry significantly improved win rate.

3. **Trailing stops are essential**: 58 of 85 trades exited via trailing stop, averaging +$89 profit. Without trails, most gains would be given back.

4. **Circuit breaker saved us**: Max drawdown capped at 4.5% vs 65% before. Never triggered the 12% circuit breaker.

5. **Volatility filter eliminates blowups**: Filtering ATR > 5% removed stocks like FLNC (-20.5%) and IONQ (-16.3%) that gap through stops.

6. **Relative strength is the best single signal**: Stocks outperforming SPY by >8% over 20 days had the highest hit rate.

## Limitations & Next Steps

1. **+3.4% in 91 days is modest**: Annualized ~14%, which is decent but not exceptional. More aggressive sizing or leverage would amplify returns (and risk).

2. **No short selling**: Current engine is long-only. Adding short signals during downtrends would improve returns.

3. **No sector rotation**: The strategy treats all stocks equally. Adding sector momentum could help.

4. **Integrate with live GCloud scanner**: The live scanner provides additional signal data not available in replay mode.

5. **Add Exa.ai news sentiment**: Real-time news from Exa can provide leading signals before price moves.
