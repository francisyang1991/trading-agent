# Lessons Learned

Accumulated trading insights from daily reviews.


## 2026-01-16

Scanner: v1.1.0 - Added HIGH PRIORITY Volume Pullback Pattern

### 📊 BACKTESTED RESULTS: Volume-Confirmed EMA9 Pullback Pattern

**Historical Backtest (241 stocks, 1 year data, 3,591 patterns):**

| Filter Criteria | Count | Win Rate | Avg Gain | Avg Loss | EV/Trade |
|-----------------|-------|----------|----------|----------|----------|
| All Patterns | 3,591 | 67.3% | +6.3% | -7.3% | +2.3% |
| **Green Trigger** | 1,838 | **81.0%** | +5.1% | -8.9% | +2.9% |
| Red Trigger | 1,753 | 53.0% | +8.1% | -6.7% | +0.8% |
| **Green + Vol > 1.2x** | 468 | **87.0%** | +5.0% | -7.9% | **+3.3%** |
| ⭐ OPTIMAL SETUP | 260 | **86.5%** | +5.0% | -8.8% | **+3.2%** |

### ⚡ KEY FINDING: GREEN TRIGGER CANDLE IS CRITICAL!

> **The GREEN trigger candle is the single most important factor!**
> - Green trigger: 81% win rate  
> - Red trigger: 53% win rate
> - **28 percentage point difference!**

### ⭐ OPTIMAL ENTRY CRITERIA (87% Win Rate)

**Phase 1 - Breakout Push:**
- 8%+ move up in 3-7 bars
- At least 3 green candles (50%+ of bars)
- Breaks above prior resistance (from 3-20 days before)

**Phase 2 - Pullback:**
- 3-15% pullback from push high
- Low touches near EMA9 (within 10%)
- Holds above prior resistance (92%+)

**Phase 3 - TRIGGER (CRITICAL):**
- ✅ **GREEN candle** (increases win rate from 67% → 81%)
- ✅ **Volume expansion > 1.2x** (increases win rate to 87%)

**Exit Strategy:**
- T1: Prior push high (average +5% gain in 3 days)
- Stop: Below pullback low (-5% cushion)
- After T1: Sell 50%, wait for consolidation
- Re-entry: Add back near EMA9 when pullback completes

**This pattern is checked FIRST in scanner - highest priority!**

---

## 2026-01-14

Scanner: v1.0.0

- CRITICAL: Win rate below 45% - review signal criteria urgently
- Low EV (<0.5%) - barely profitable after commissions

