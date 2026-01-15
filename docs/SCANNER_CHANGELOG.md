# Scanner Changelog

Version history and rationale for scanner parameter changes.
Each change is data-driven based on signal performance analysis.

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
