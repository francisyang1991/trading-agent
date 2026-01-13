# 🎯 Strategy Selection Matrix

## Quick Decision Guide

Use this matrix to select the right strategy based on **Regime** (momentum) and **Volatility**.

## 📊 The Matrix

| Regime \ Volatility | LOW (<30%) | MODERATE (30-50%) | HIGH (50-80%) | ULTRA HIGH (>80%) |
|---------------------|------------|-------------------|---------------|-------------------|
| **PARABOLIC** (>100%) | Buy & Hold | Trailing Stop | Trailing Stop | Trailing Stop |
| **STRONG UP** (50-100%) | Trend Following | Trend Following | Swing Trade | Swing Trade |
| **MODERATE UP** (20-50%) | Trend Following | Trend Following | Swing Trade | Swing Trade |
| **WEAK UP** (0-20%) | Swing Trade | Swing Trade | Mean Reversion | Mean Reversion |
| **SIDEWAYS** (-20% to 0%) | Mean Reversion | Mean Reversion | Mean Reversion | **STAY CASH** |
| **DOWNTREND** (<-20%) | **STAY CASH** | **STAY CASH** | **STAY CASH** | **STAY CASH** |

---

## 📋 Strategy Descriptions

### 1. Buy & Hold 📈
**Best for:** Low-volatility stocks in parabolic runs

| Metric | Value |
|--------|-------|
| Position Size | 15% |
| Stop Loss | 10% |
| Take Profit | 50% |

**Rules:**
- Entry: Buy on any pullback to EMA21
- Exit: Hold until regime changes
- Example: GOOGL in strong uptrend

---

### 2. Trailing Stop 🛡️
**Best for:** Volatile stocks in strong uptrends (capture gains, protect profits)

| Volatility | Position Size | Trailing Stop |
|------------|---------------|---------------|
| Moderate | 12% | 12% (2x ATR) |
| High | 8% | 15% (2.5x ATR) |
| Ultra High | 5% | 20% (3x ATR) |

**Rules:**
- Entry: Buy on EMA crossover or breakout
- Exit: When price drops 2-3x ATR from recent high
- Example: IREN, RKLB, HOOD in parabolic runs

---

### 3. Trend Following 📊
**Best for:** Clear uptrends with moderate volatility

| Metric | Value |
|--------|-------|
| Position Size | 12-15% |
| Stop Loss | 6-10% |
| Take Profit | 15-25% |

**Rules:**
- Entry: Buy when EMA9 > EMA21 > EMA50
- Exit: When EMA9 crosses below EMA21
- Example: AAPL, MSFT, NVDA, AMZN

---

### 4. Swing Trade 🔄
**Best for:** Choppy stocks within overall uptrend

| Metric | Value |
|--------|-------|
| Position Size | 8-10% |
| Stop Loss | 10-12% |
| Take Profit | 15-20% |

**Rules:**
- Entry: Buy on RSI < 40 pullback to EMA21/support
- Exit: Sell at RSI > 70 or prior swing high
- Example: PLTR, CRWD (volatile but uptrending)

---

### 5. Mean Reversion ↩️
**Best for:** Sideways/range-bound stocks

| Metric | Value |
|--------|-------|
| Position Size | 6-12% |
| Stop Loss | 4-8% |
| Take Profit | 8-10% |

**Rules:**
- Entry: Buy when RSI < 30 AND price below lower Bollinger Band
- Exit: Sell when RSI > 70 OR price at upper Bollinger Band
- Example: CRM, KO, PG (sideways markets)

---

### 6. Stay in Cash 💵
**Best for:** Downtrends or ultra-volatile sideways

**Rules:**
- Do NOT enter
- Wait for regime change to uptrend
- Preserve capital
- Example: MSTR (in downtrend)

---

## 🎯 Position Sizing by Volatility

The key to not being "stupid" with volatile stocks:

| Volatility | Position Size | Rationale |
|------------|---------------|-----------|
| LOW (<30%) | 15% | Low risk, can size up |
| MODERATE (30-50%) | 12% | Standard sizing |
| HIGH (50-80%) | 8% | Reduced for risk control |
| ULTRA HIGH (>80%) | 5% | Minimal size, big swings |

**Formula:** `position_size = base_size × (target_vol / actual_vol)`

Example: If target volatility is 20% and stock has 80% volatility:
- Adjustment = 20% / 80% = 0.25
- Position = 15% × 0.25 = 3.75% → round to 5%

---

## 🚀 Quick Start

```bash
# Analyze single stock
python tools/scanner.py NVDA

# Analyze portfolio
python tools/scanner.py AAPL MSFT NVDA PLTR IREN

# Run with default watchlist
python tools/scanner.py
```

---

## 📈 Current Recommendations (2026-01-10)

| Stock | Regime | Vol | Strategy | Size |
|-------|--------|-----|----------|------|
| GOOGL | Parabolic | Low | **Buy & Hold** | 15% |
| IREN | Parabolic | Ultra | **Trailing Stop** | 5% |
| RKLB | Parabolic | Ultra | **Trailing Stop** | 5% |
| HOOD | Parabolic | High | **Trailing Stop** | 8% |
| NVDA | Strong Up | Mod | **Trend Following** | 12% |
| PLTR | Strong Up | High | **Swing Trade** | 8% |
| AAPL | Mod Up | Low | **Trend Following** | 15% |
| MSFT | Mod Up | Low | **Trend Following** | 15% |
| COIN | Weak Up | High | **Mean Reversion** | 8% |
| MSTR | Downtrend | High | **STAY CASH** | 0% |

---

## 🔑 Key Takeaways

1. **High volatility = smaller positions** (not zero, just smaller)
2. **Downtrend = stay out** (no strategy beats a falling knife)
3. **Parabolic runs = just hold** (can't beat with trading)
4. **Sideways = mean reversion** (buy oversold, sell overbought)
5. **Uptrend = trend following** (ride the wave)

---

## 📁 Related Files

- `tools/scanner.py` - Unified scanner (universe scan + strategy selection + entry plan)
- `tools/backtest.py` - Unified backtester (single stock + portfolio vs SPY)
- `config/stock_universe.yaml` - Stock themes
