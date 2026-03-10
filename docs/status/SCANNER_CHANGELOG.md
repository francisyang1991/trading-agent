# Scanner Changelog

Version history and rationale for scanner parameter changes.
Each change is data-driven based on signal performance analysis.

---

## v1.1.0 (2026-01-16)

### HIGH PRIORITY: Trend Reversal + Volume Pullback Pattern

**New Feature:**
Added highest-priority pattern detection for Trend Reversal + Volume Pullback.
This pattern is checked FIRST before all other signal logic.

**Pattern Description:**
1. PRIOR DOWNTREND: Stock has resistance high 5-10 days before push
2. BREAKOUT PUSH: 3+ green candles with increasing volume BREAKING ABOVE prior resistance
3. PULLBACK: Higher low near EMA9 with DECREASING volume (key!)
4. TRIGGER: Green candle with volume EXPANSION

**Pattern Parameters:**
- Prior resistance lookback: 5-15 days before push
- Must break above prior resistance (breakout confirmation)
- Pullback should hold above prior resistance (now support)
- Min green candles in push: 3
- Max pullback bars: 5
- EMA9 proximity tolerance: 4%
- Max pullback depth: 10%
- Volume divergence threshold: < 0.85 (pullback vol / push vol)
- Breakout volume threshold: > 1.2x (vs pullback avg)

**Exit Strategy (built into signal):**
- T1: Previous push high (sell 50%)
- Wait for consolidation
- Re-entry: Near EMA9

**Reasoning:**
- User identified this pattern from UMAC chart (2026-01-16)
- Breakout from prior resistance confirms trend reversal
- Prior resistance becomes support
- Volume divergence confirms healthy pullback vs distribution
- High win-rate pattern with clear entry/exit levels
- Used successfully in momentum stocks breaking out of bases

**Files Changed:**
- `tools/scanner.py`: Added `detect_volume_pullback_pattern()` function
- `src/signals/entry/volume_pullback_entry.py`: New entry signal class
- `docs/LESSONS_LEARNED.md`: Documented pattern

---

## v1.0.0 (2026-01-14)

### Initial Release

**Core Features:**
- Scans 241 stocks from stock_universe.yaml
- Regime classification (PARABOLIC, STRONG_UP, MODERATE_UP, WEAK_UP, SIDEWAYS, DOWNTREND)
- Volatility categorization (ULTRA_HIGH, HIGH, MODERATE, LOW)
- Strategy matrix mapping regime+vol to optimal strategy
- Expected Value (EV) calculation
- Risk/Reward ratio calculation
- Position sizing (Kelly criterion, volatility targeting)

**Signal Criteria:**
- BUY: In buy zone + EV > 0.5% + R:R > 1.5x
- STRONG BUY: EV > 1.5% + R:R > 2.5x + 30 ≤ RSI ≤ 65 + Uptrend regime
- WAIT: Overextended (RSI > 70 or >10% above EMA21) or weak metrics
- AVOID: Downtrend or negative EV

**Parameters:**
- EV threshold for BUY: 0.5%
- R:R threshold for BUY: 1.5x
- RSI overbought: 70
- EMA21 extension limit: 10%

---

## Planned Improvements

Based on signal journal analysis, consider:
1. Adjusting EV threshold based on win rate by EV bucket
2. Regime-specific filters if certain regimes underperform
3. Volume confirmation requirements
4. News/earnings proximity filters
