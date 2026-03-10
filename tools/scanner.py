#!/usr/bin/env python3
"""
UNIFIED STOCK SCANNER
=====================
Combines: universe_scanner + strategy_selector + monday_plan

Features:
1. Scan universe and classify stocks by regime/volatility
2. Select optimal strategy from matrix
3. Generate actionable trading plans with:
   - Buy zones & invalidation
   - Expected value (EV) calculation
   - Position sizing (Kelly, vol-targeting)
   - Risk/reward analysis
4. Output to formatted file (default) or console

Usage:
    # Quick scan (outputs to results/scan_YYYYMMDD_HHMMSS.txt)
    python tools/scanner.py AAPL NVDA IREN
    
    # Scan from picker output (modular pipeline)
    python tools/scanner.py --from-picker
    python tools/scanner.py --from-picker --picker-csv results/picker/three_layer_picks.csv
    
    # Scan theme
    python tools/scanner.py --theme crypto
    python tools/scanner.py --theme wilson
    
    # Full universe
    python tools/scanner.py --all
    
    # Monday plan (detailed)
    python tools/scanner.py --plan IREN FLNC HOOD
    
    # Custom output file
    python tools/scanner.py AAPL NVDA --output my_scan.txt
    
    # Output structured buy zones CSV for downstream pipeline
    python tools/scanner.py --from-picker --output-buy-zones-csv results/buy_zones.csv
    
    # Print to console instead of file
    python tools/scanner.py AAPL NVDA --console
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import yfinance as yf
import pandas as pd
import numpy as np
import yaml
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings('ignore')

# Import DataManager for cached data
try:
    from src.data_manager import DataManager
    DATA_MANAGER = DataManager()
    USE_CACHE = True
except ImportError:
    DATA_MANAGER = None
    USE_CACHE = False


# ── Pre-load fundamental scores from DB (offline-computed) ──
_FUND_SCORE_CACHE: Dict[str, float] = {}

def _load_fundamental_scores():
    """Load all pre-computed fundamental scores into memory at startup."""
    global _FUND_SCORE_CACHE
    try:
        import sqlite3
        db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'stock_cache.db')
        if not os.path.exists(db_path):
            return
        conn = sqlite3.connect(db_path)
        # Check if table exists
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fundamental_scores'"
        ).fetchone()
        if tables:
            rows = conn.execute("SELECT symbol, overall_score FROM fundamental_scores").fetchall()
            _FUND_SCORE_CACHE = {r[0]: r[1] for r in rows if r[1] is not None}
        conn.close()
    except Exception:
        pass

_load_fundamental_scores()  # Run once at import time


def _ibkr_fundamental_score(symbol: str) -> float:
    """Quick fundamental score from IBKR for tickers not in pre-computed DB.
    Uses simple heuristics on PE, ROE, gross margin, debt/equity."""
    try:
        import urllib.request
        import json
        url = f"http://34.75.9.166:8080/api/fundamentals/profile/{symbol}"
        req = urllib.request.Request(url, headers={"X-API-Key": "saiyan-trade-2026"})
        data = json.loads(urllib.request.urlopen(req, timeout=5).read())

        score = 50.0  # start neutral
        pe = data.get("pe_ratio")
        roe = data.get("roe")
        gm = data.get("gross_margin")
        de = data.get("debt_to_equity")

        if pe is not None:
            if 0 < pe < 30: score += 10
            elif pe > 60: score -= 10
        if roe is not None:
            if roe > 0.15: score += 10
            elif roe < 0: score -= 10
        if gm is not None:
            if gm > 0.4: score += 10
            elif gm < 0.2: score -= 5
        if de is not None:
            if de < 1: score += 5
            elif de > 3: score -= 5

        return max(0.0, min(100.0, score))
    except Exception:
        return 50.0


# Common ETF suffixes and known ETF tickers that lack fundamentals
_ETF_PATTERNS = {'SPY', 'QQQ', 'IWM', 'DIA', 'XLB', 'XLE', 'XLF', 'XLI', 'XLK', 'XLP',
    'XLU', 'XLV', 'XLY', 'XLRE', 'XLC', 'XME', 'XOP', 'XBI', 'XHB', 'XRT',
    'GLD', 'SLV', 'TLT', 'HYG', 'LQD', 'VTI', 'VOO', 'ARKK', 'ARKG', 'ARKW',
    'SOXL', 'SOXS', 'TQQQ', 'SQQQ', 'UVXY', 'VXX', 'IBIT', 'REMX', 'NLR',
    'IGV', 'FPS', 'SIZE', 'ALGOS', 'CRUSH', 'DIP', 'IV', 'DATED', 'ISM'}


def _is_etf(symbol: str) -> bool:
    """Check if symbol is likely an ETF (no individual fundamentals)."""
    if symbol in _ETF_PATTERNS:
        return True
    # Check if symbol has no fundamental data in cache
    try:
        import sqlite3
        db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'stock_cache.db')
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT sector FROM stock_fundamentals WHERE symbol = ?", (symbol,)
        ).fetchone()
        conn.close()
        if row and row[0] in (None, '', 'ETF', 'Exchange Traded Fund'):
            return True
    except Exception:
        pass
    return False


def get_fundamental_score(symbol: str) -> float:
    """Get fundamental score: from pre-computed DB cache, or IBKR fallback.
    For ETFs (no individual fundamentals), returns neutral 50 so tech factors dominate."""
    cached = _FUND_SCORE_CACHE.get(symbol)
    if cached is not None:
        return cached
    # ETFs don't have individual fundamentals — use neutral score
    if _is_etf(symbol):
        _FUND_SCORE_CACHE[symbol] = 50.0
        return 50.0
    # Fallback: quick IBKR score for unknown tickers
    score = _ibkr_fundamental_score(symbol)
    _FUND_SCORE_CACHE[symbol] = score  # cache for this session
    return score


# ============================================================================
# ENUMS
# ============================================================================

class Regime(Enum):
    PARABOLIC = "PARABOLIC"      # >100% 6M momentum
    STRONG_UP = "STRONG_UP"      # 50-100%
    MODERATE_UP = "MODERATE_UP"  # 20-50%
    WEAK_UP = "WEAK_UP"          # 0-20%
    SIDEWAYS = "SIDEWAYS"        # -20% to 0%
    DOWNTREND = "DOWNTREND"      # <-20%


class VolCategory(Enum):
    ULTRA_HIGH = "ULTRA_HIGH"    # >80%
    HIGH = "HIGH"                # 50-80%
    MODERATE = "MODERATE"        # 30-50%
    LOW = "LOW"                  # <30%


class Strategy(Enum):
    BUY_HOLD = "Buy & Hold"
    TRAILING_STOP = "Trailing Stop"
    TREND_FOLLOWING = "Trend Following"
    SWING_TRADE = "Swing Trade"
    MEAN_REVERSION = "Mean Reversion"
    SHORT_POPUP = "Short Pop-up"           # Short rallies in downtrend
    SHORT_MEAN_REVERT = "Short Mean Revert" # Short overbought in sideways
    STAY_CASH = "Stay Cash"


class Action(Enum):
    STRONG_BUY = "🟢 STRONG BUY"
    BUY = "🟡 BUY"
    WAIT = "⚪ WAIT"
    SHORT = "🟣 SHORT"
    AVOID = "🔴 AVOID"


# ============================================================================
# STRATEGY MATRIX
# ============================================================================

STRATEGY_MATRIX = {
    # (Regime, VolCategory) -> (Strategy, position_size, stop_loss, take_profit)
    (Regime.PARABOLIC, VolCategory.LOW): (Strategy.BUY_HOLD, 0.15, 0.10, 0.50),
    (Regime.PARABOLIC, VolCategory.MODERATE): (Strategy.TRAILING_STOP, 0.12, 0.12, 0.40),
    (Regime.PARABOLIC, VolCategory.HIGH): (Strategy.TRAILING_STOP, 0.08, 0.15, 0.35),
    (Regime.PARABOLIC, VolCategory.ULTRA_HIGH): (Strategy.TRAILING_STOP, 0.05, 0.20, 0.30),
    
    (Regime.STRONG_UP, VolCategory.LOW): (Strategy.TREND_FOLLOWING, 0.15, 0.08, 0.25),
    (Regime.STRONG_UP, VolCategory.MODERATE): (Strategy.TREND_FOLLOWING, 0.12, 0.10, 0.25),
    (Regime.STRONG_UP, VolCategory.HIGH): (Strategy.SWING_TRADE, 0.08, 0.12, 0.20),
    (Regime.STRONG_UP, VolCategory.ULTRA_HIGH): (Strategy.SWING_TRADE, 0.05, 0.15, 0.20),
    
    (Regime.MODERATE_UP, VolCategory.LOW): (Strategy.TREND_FOLLOWING, 0.15, 0.06, 0.15),
    (Regime.MODERATE_UP, VolCategory.MODERATE): (Strategy.TREND_FOLLOWING, 0.12, 0.08, 0.15),
    (Regime.MODERATE_UP, VolCategory.HIGH): (Strategy.SWING_TRADE, 0.08, 0.10, 0.15),
    (Regime.MODERATE_UP, VolCategory.ULTRA_HIGH): (Strategy.SWING_TRADE, 0.05, 0.12, 0.15),
    
    (Regime.WEAK_UP, VolCategory.LOW): (Strategy.SWING_TRADE, 0.12, 0.05, 0.10),
    (Regime.WEAK_UP, VolCategory.MODERATE): (Strategy.SWING_TRADE, 0.10, 0.06, 0.10),
    (Regime.WEAK_UP, VolCategory.HIGH): (Strategy.MEAN_REVERSION, 0.08, 0.08, 0.10),
    (Regime.WEAK_UP, VolCategory.ULTRA_HIGH): (Strategy.MEAN_REVERSION, 0.05, 0.10, 0.10),
    
    (Regime.SIDEWAYS, VolCategory.LOW): (Strategy.MEAN_REVERSION, 0.12, 0.04, 0.08),
    (Regime.SIDEWAYS, VolCategory.MODERATE): (Strategy.MEAN_REVERSION, 0.10, 0.05, 0.08),
    (Regime.SIDEWAYS, VolCategory.HIGH): (Strategy.MEAN_REVERSION, 0.06, 0.08, 0.10),
    (Regime.SIDEWAYS, VolCategory.ULTRA_HIGH): (Strategy.STAY_CASH, 0.00, 0.00, 0.00),

    # DOWNTREND: Short pop-ups (rallies into resistance) — smaller size, wider stops
    (Regime.DOWNTREND, VolCategory.LOW): (Strategy.SHORT_POPUP, 0.08, 0.06, 0.12),
    (Regime.DOWNTREND, VolCategory.MODERATE): (Strategy.SHORT_POPUP, 0.06, 0.08, 0.10),
    (Regime.DOWNTREND, VolCategory.HIGH): (Strategy.SHORT_POPUP, 0.04, 0.10, 0.10),
    (Regime.DOWNTREND, VolCategory.ULTRA_HIGH): (Strategy.STAY_CASH, 0.00, 0.00, 0.00),
}

# Short-specific strategy matrix (for SIDEWAYS near resistance)
SHORT_STRATEGY_MATRIX = {
    (Regime.SIDEWAYS, VolCategory.LOW): (Strategy.SHORT_MEAN_REVERT, 0.08, 0.05, 0.08),
    (Regime.SIDEWAYS, VolCategory.MODERATE): (Strategy.SHORT_MEAN_REVERT, 0.06, 0.06, 0.08),
    (Regime.SIDEWAYS, VolCategory.HIGH): (Strategy.SHORT_MEAN_REVERT, 0.04, 0.08, 0.08),
}


# ============================================================================
# MARKET CONTEXT (VIX + SPY/QQQ)
# ============================================================================

@dataclass
class MarketContext:
    """Market-wide context for scoring adjustments."""
    # VIX
    vix: float = 0.0
    vix_sma20: float = 0.0
    vix_regime: str = "NORMAL"       # CALM / NORMAL / ELEVATED / CRISIS
    vix_trend: str = "STABLE"        # RISING / FALLING / STABLE

    # SPY
    spy_price: float = 0.0
    spy_momentum_6m: float = 0.0
    spy_trend: str = "SIDEWAYS"      # UPTREND / SIDEWAYS / DOWNTREND
    spy_above_ema50: bool = True

    # QQQ
    qqq_price: float = 0.0
    qqq_momentum_6m: float = 0.0
    qqq_trend: str = "SIDEWAYS"

    # Derived
    market_regime: str = "NEUTRAL"   # BULL / NEUTRAL / BEAR / CRISIS
    risk_appetite: str = "BALANCED"  # RISK_ON / BALANCED / RISK_OFF / DEFENSIVE


# Module-level cache for market context (shared across all stocks in a scan)
_market_context_cache: Optional[MarketContext] = None


def _classify_trend(momentum_6m: float, price: float, ema50: float) -> str:
    """Classify trend based on momentum and EMA position."""
    if momentum_6m > 10 and price > ema50:
        return "UPTREND"
    elif momentum_6m < -10 or price < ema50 * 0.97:
        return "DOWNTREND"
    return "SIDEWAYS"


def fetch_market_context() -> MarketContext:
    """Fetch VIX, SPY, QQQ to determine market regime. Cached per session."""
    global _market_context_cache
    if _market_context_cache is not None:
        return _market_context_cache

    ctx = MarketContext()

    try:
        # Fetch VIX
        vix_data = yf.Ticker("^VIX").history(period="3mo")
        if vix_data is not None and len(vix_data) >= 20:
            ctx.vix = vix_data['Close'].iloc[-1]
            ctx.vix_sma20 = vix_data['Close'].rolling(20).mean().iloc[-1]

            # VIX regime
            if ctx.vix < 15:
                ctx.vix_regime = "CALM"
            elif ctx.vix < 20:
                ctx.vix_regime = "NORMAL"
            elif ctx.vix < 30:
                ctx.vix_regime = "ELEVATED"
            else:
                ctx.vix_regime = "CRISIS"

            # VIX trend
            if ctx.vix > ctx.vix_sma20 * 1.05:
                ctx.vix_trend = "RISING"
            elif ctx.vix < ctx.vix_sma20 * 0.95:
                ctx.vix_trend = "FALLING"
            else:
                ctx.vix_trend = "STABLE"

        # Fetch SPY
        spy_data = yf.Ticker("SPY").history(period="1y")
        if spy_data is not None and len(spy_data) >= 126:
            ctx.spy_price = spy_data['Close'].iloc[-1]
            ctx.spy_momentum_6m = (spy_data['Close'].iloc[-1] / spy_data['Close'].iloc[-126] - 1) * 100
            spy_ema50 = calculate_ema(spy_data['Close'], 50).iloc[-1]
            ctx.spy_above_ema50 = ctx.spy_price > spy_ema50
            ctx.spy_trend = _classify_trend(ctx.spy_momentum_6m, ctx.spy_price, spy_ema50)

        # Fetch QQQ
        qqq_data = yf.Ticker("QQQ").history(period="1y")
        if qqq_data is not None and len(qqq_data) >= 126:
            ctx.qqq_price = qqq_data['Close'].iloc[-1]
            ctx.qqq_momentum_6m = (qqq_data['Close'].iloc[-1] / qqq_data['Close'].iloc[-126] - 1) * 100
            qqq_ema50 = calculate_ema(qqq_data['Close'], 50).iloc[-1]
            ctx.qqq_trend = _classify_trend(ctx.qqq_momentum_6m, ctx.qqq_price, qqq_ema50)

        # Derive market regime (combined SPY + VIX)
        if ctx.vix_regime == "CRISIS" or ctx.spy_trend == "DOWNTREND":
            ctx.market_regime = "CRISIS"
        elif ctx.spy_trend == "UPTREND" and ctx.vix_regime in ("CALM", "NORMAL"):
            ctx.market_regime = "BULL"
        elif ctx.spy_trend == "SIDEWAYS" and ctx.vix_regime in ("ELEVATED",) or ctx.vix_trend == "RISING":
            ctx.market_regime = "BEAR"
        else:
            ctx.market_regime = "NEUTRAL"

        # Derive risk appetite
        if ctx.vix_regime == "CALM" and ctx.spy_trend == "UPTREND":
            ctx.risk_appetite = "RISK_ON"
        elif ctx.vix_regime == "CRISIS":
            ctx.risk_appetite = "DEFENSIVE"
        elif ctx.vix_regime == "ELEVATED" or ctx.spy_trend == "DOWNTREND":
            ctx.risk_appetite = "RISK_OFF"
        else:
            ctx.risk_appetite = "BALANCED"

        print(f"   📊 Market: VIX={ctx.vix:.1f} ({ctx.vix_regime}/{ctx.vix_trend}) | "
              f"SPY={ctx.spy_trend} ({ctx.spy_momentum_6m:+.1f}%) | "
              f"QQQ={ctx.qqq_trend} ({ctx.qqq_momentum_6m:+.1f}%) | "
              f"Regime={ctx.market_regime} | Appetite={ctx.risk_appetite}")

    except Exception as e:
        print(f"   ⚠️ Market context fetch error: {e}")

    _market_context_cache = ctx
    return ctx


def reset_market_context():
    """Reset the cached market context (for testing or new scan sessions)."""
    global _market_context_cache
    _market_context_cache = None


# ============================================================================
# TECHNICAL CONFIDENCE SCORE
# ============================================================================

# Strategy-specific weights for tech score components (6 components, sum = 1.0)
TECH_WEIGHTS = {
    "Trend Following":   {"ema_align": 0.25, "rsi": 0.10, "momentum": 0.20, "price_ema": 0.15, "vol": 0.10, "vpes": 0.20},
    "Swing Trade":       {"ema_align": 0.15, "rsi": 0.20, "momentum": 0.15, "price_ema": 0.20, "vol": 0.10, "vpes": 0.20},
    "Mean Reversion":    {"ema_align": 0.10, "rsi": 0.30, "momentum": 0.10, "price_ema": 0.25, "vol": 0.10, "vpes": 0.15},
    "Short Pop-up":      {"ema_align": 0.20, "rsi": 0.20, "momentum": 0.15, "price_ema": 0.20, "vol": 0.10, "vpes": 0.15},
    "Short Mean Revert": {"ema_align": 0.10, "rsi": 0.25, "momentum": 0.15, "price_ema": 0.25, "vol": 0.10, "vpes": 0.15},
    "Trailing Stop":     {"ema_align": 0.25, "rsi": 0.10, "momentum": 0.25, "price_ema": 0.10, "vol": 0.10, "vpes": 0.20},
    "Buy & Hold":        {"ema_align": 0.20, "rsi": 0.10, "momentum": 0.25, "price_ema": 0.15, "vol": 0.10, "vpes": 0.20},
    "Stay Cash":         {"ema_align": 0.17, "rsi": 0.17, "momentum": 0.17, "price_ema": 0.17, "vol": 0.16, "vpes": 0.16},
}


def calculate_vpes(df: pd.DataFrame) -> Tuple[float, float]:
    """
    Calculate Volume-Price Expansion Score (VPES).
    Measures buying/selling pressure quality by combining price changes with volume.

    Returns: (vpes_ema, vpes_cumulative)
      - vpes_ema: 3-bar EMA-smoothed VPES (positive = accumulation, negative = distribution)
      - vpes_cumulative: 5-bar cumulative VPES (magnitude of recent pressure)
    """
    if len(df) < 25:
        return 0.0, 0.0

    price_change = (df['Close'] - df['Open']) / df['Open']
    vol_avg = df['Volume'].rolling(20).mean()
    # Avoid division by zero
    vol_avg = vol_avg.replace(0, 1)
    vol_ratio = df['Volume'] / vol_avg
    vpes_raw = price_change * vol_ratio
    vpes_ema = vpes_raw.ewm(span=3).mean().iloc[-1]
    vpes_cum = vpes_raw.rolling(5).sum().iloc[-1]

    # Handle NaN
    if pd.isna(vpes_ema):
        vpes_ema = 0.0
    if pd.isna(vpes_cum):
        vpes_cum = 0.0

    return float(vpes_ema), float(vpes_cum)


def calculate_tech_score(
    strategy: str,
    action: str,
    rsi: float,
    ema9: float,
    ema21: float,
    ema50: float,
    price: float,
    momentum_3m: float,
    momentum_6m: float,
    volatility: float,
    vol_category: str,
    dist_ema21: float,
    vpes_ema: float = 0.0,
    vpes_cumulative: float = 0.0,
) -> Tuple[float, Dict[str, float]]:
    """
    Calculate technical confidence score (0-100).
    Score = CONFIDENCE, not direction.
    High score = high confidence in the signal, regardless of bull/bear.

    Returns: (tech_score, components_dict)
    """
    is_short = "SHORT" in action
    is_buy = action in ("STRONG BUY", "BUY") or "BUY" in action

    # ── Component 1: EMA Alignment (0-20) ──
    if is_short:
        # For shorts: bearish stack (EMA9 < EMA21 < EMA50) = high confidence
        if ema9 < ema21 < ema50:
            ema_score = 20  # Perfect bearish alignment
        elif ema9 < ema21 or ema21 < ema50:
            ema_score = 12  # Partial bearish
        elif ema9 > ema21 > ema50:
            ema_score = 3   # Bullish stack = contradicts short
        else:
            ema_score = 8   # Mixed
    else:
        # For longs: bullish stack (EMA9 > EMA21 > EMA50) = high confidence
        if ema9 > ema21 > ema50:
            ema_score = 20
        elif ema9 > ema21 or ema21 > ema50:
            ema_score = 12
        elif ema9 < ema21 < ema50:
            ema_score = 3   # Bearish stack = contradicts long
        else:
            ema_score = 8

    # ── Component 2: RSI Sweet Spot (0-20) ──
    if is_short:
        # For shorts: high RSI (overbought) = better entry for short
        if rsi > 75:
            rsi_score = 20
        elif rsi > 65:
            rsi_score = 16
        elif rsi > 55:
            rsi_score = 12
        elif rsi > 45:
            rsi_score = 6
        else:
            rsi_score = 2  # RSI low = bad for shorting
    elif strategy == "Mean Reversion":
        # Mean reversion longs: low RSI = oversold = good entry
        if rsi < 30:
            rsi_score = 20
        elif rsi < 40:
            rsi_score = 16
        elif rsi < 50:
            rsi_score = 12
        elif rsi < 60:
            rsi_score = 8
        else:
            rsi_score = 3
    else:
        # Trend / Swing / Buy&Hold longs: mid RSI = healthy, not overbought
        if 35 <= rsi <= 55:
            rsi_score = 20  # Sweet spot
        elif 30 <= rsi <= 65:
            rsi_score = 14
        elif rsi < 30:
            rsi_score = 10  # Oversold can bounce but risky
        elif rsi > 70:
            rsi_score = 4   # Overbought = risky for longs
        else:
            rsi_score = 8

    # ── Component 3: Momentum Confirmation (0-20) ──
    avg_momentum = (momentum_3m + momentum_6m) / 2
    if is_short:
        # For shorts: negative momentum = confirms short direction
        if avg_momentum < -15:
            mom_score = 20
        elif avg_momentum < -5:
            mom_score = 16
        elif avg_momentum < 0:
            mom_score = 12
        elif avg_momentum < 10:
            mom_score = 6
        else:
            mom_score = 2  # Strong bullish momentum = contradicts short
    else:
        # For longs: positive momentum = confirms long
        if avg_momentum > 30:
            mom_score = 20
        elif avg_momentum > 15:
            mom_score = 17
        elif avg_momentum > 5:
            mom_score = 14
        elif avg_momentum > 0:
            mom_score = 10
        elif avg_momentum > -10:
            mom_score = 5
        else:
            mom_score = 2

    # ── Component 4: Price vs EMA21 (0-20) ──
    abs_dist = abs(dist_ema21)
    if is_short:
        # For shorts: price ABOVE EMA21 = overextended = good short entry
        if dist_ema21 > 8:
            price_score = 20
        elif dist_ema21 > 4:
            price_score = 16
        elif dist_ema21 > 2:
            price_score = 12
        elif dist_ema21 > 0:
            price_score = 8
        else:
            price_score = 4  # Below EMA21 = not ideal for new short
    elif strategy == "Mean Reversion":
        # Mean reversion: price below EMA21 = oversold = good long entry
        if dist_ema21 < -5:
            price_score = 20
        elif dist_ema21 < -2:
            price_score = 16
        elif dist_ema21 < 0:
            price_score = 12
        elif dist_ema21 < 3:
            price_score = 8
        else:
            price_score = 4
    else:
        # Trend following: near EMA21 = good pullback entry
        if abs_dist < 2:
            price_score = 20  # Near EMA21 = ideal pullback entry
        elif abs_dist < 4:
            price_score = 15
        elif abs_dist < 6:
            price_score = 10
        elif abs_dist < 10:
            price_score = 6
        else:
            price_score = 3  # Too far from mean

    # ── Component 5: Volatility Fit (0-20) ──
    if strategy in ("Trend Following", "Buy & Hold", "Trailing Stop"):
        # Trend strategies prefer low-moderate vol
        if vol_category == "LOW":
            vol_score = 20
        elif vol_category == "MODERATE":
            vol_score = 15
        elif vol_category == "HIGH":
            vol_score = 8
        else:
            vol_score = 3
    elif strategy in ("Swing Trade", "Mean Reversion"):
        # Swing/mean reversion can handle moderate vol
        if vol_category == "MODERATE":
            vol_score = 20
        elif vol_category == "LOW":
            vol_score = 15
        elif vol_category == "HIGH":
            vol_score = 10
        else:
            vol_score = 5
    elif strategy in ("Short Pop-up", "Short Mean Revert"):
        # Shorts: moderate vol ideal, ultra-high too risky
        if vol_category == "MODERATE":
            vol_score = 18
        elif vol_category == "HIGH":
            vol_score = 14
        elif vol_category == "LOW":
            vol_score = 12
        else:
            vol_score = 5
    else:
        vol_score = 10

    # ── Component 6: VPES — Volume-Price Expansion Score (0-20) ──
    if is_short:
        # For shorts: negative VPES = distribution = confirms weakness
        if vpes_ema < -0.02:
            vpes_score = 20
        elif vpes_ema < -0.01:
            vpes_score = 16
        elif vpes_ema < -0.005:
            vpes_score = 12
        elif vpes_ema < 0:
            vpes_score = 8
        else:
            vpes_score = 3  # Positive VPES = accumulation, bad for shorts
    else:
        # For longs: positive VPES = accumulation = buying pressure
        if vpes_ema > 0.02 and vpes_cumulative > 0.05:
            vpes_score = 20  # Strong accumulation
        elif vpes_ema > 0.01 and vpes_cumulative > 0.02:
            vpes_score = 16
        elif vpes_ema > 0.005:
            vpes_score = 12
        elif vpes_ema > 0:
            vpes_score = 8
        else:
            vpes_score = 3  # Distribution, bad for longs

    # ── Weighted total (6 components) ──
    weights = TECH_WEIGHTS.get(strategy, TECH_WEIGHTS["Swing Trade"])
    components = {
        "ema_align": ema_score,
        "rsi": rsi_score,
        "momentum": mom_score,
        "price_ema": price_score,
        "vol": vol_score,
        "vpes": vpes_score,
    }

    raw_score = (
        components["ema_align"] * weights["ema_align"] +
        components["rsi"] * weights["rsi"] +
        components["momentum"] * weights["momentum"] +
        components["price_ema"] * weights["price_ema"] +
        components["vol"] * weights["vol"] +
        components["vpes"] * weights["vpes"]
    )

    # Scale: each component is 0-20, weights sum to 1.0, so raw = 0-20
    # Multiply by 5 to get 0-100
    tech_score = min(100, max(0, raw_score * 5))

    return tech_score, components


def apply_market_adjustment(tech_score: float, action: str, market: MarketContext) -> float:
    """Adjust tech_score based on VIX/SPY/QQQ market context. Returns adjusted score."""
    adj = 0.0

    # ── VIX regime adjustment ──
    if market.vix_regime == "CRISIS":
        if "SHORT" in action:
            adj += 15
        elif "BUY" in action:
            adj -= 20
        else:
            adj -= 10
    elif market.vix_regime == "ELEVATED":
        if "SHORT" in action:
            adj += 8
        elif "BUY" in action:
            adj -= 10
    elif market.vix_regime == "CALM":
        if "SHORT" in action:
            adj -= 10
        elif "BUY" in action:
            adj += 8
    # NORMAL → no adjustment

    # ── VIX trend adjustment (smaller, directional) ──
    if market.vix_trend == "RISING":
        if "SHORT" in action:
            adj += 5
        elif "BUY" in action:
            adj -= 5
    elif market.vix_trend == "FALLING":
        if "SHORT" in action:
            adj -= 5
        elif "BUY" in action:
            adj += 5

    # ── SPY/QQQ trend confirmation ──
    if market.market_regime == "BULL":
        if "BUY" in action:
            adj += 5
        if "SHORT" in action:
            adj -= 5
    elif market.market_regime in ("BEAR", "CRISIS"):
        if "SHORT" in action:
            adj += 5
        if "BUY" in action:
            adj -= 5

    return max(0, min(100, tech_score + adj))


# ============================================================================
# DATA CLASS
# ============================================================================

@dataclass
class StockScan:
    # Basic info
    symbol: str
    price: float
    
    # Classification
    regime: str
    vol_category: str
    strategy: str
    
    # Metrics
    momentum_6m: float
    momentum_3m: float
    momentum_1m: float
    momentum_1w: float
    volatility: float
    rsi: float
    atr_pct: float
    dist_ema21: float
    macd_line: float
    macd_signal: float
    macd_histogram: float
    fundamental_score: float

    # Entry/Exit (for trading plan)
    buy_zone_low: float
    buy_zone_high: float
    stop_loss: float
    target_1: float
    target_2: float

    # Expected value analysis
    win_rate: float
    avg_win: float
    avg_loss: float
    expected_value: float
    risk_reward: float

    # Position sizing
    position_size_pct: float
    kelly_pct: float

    # Action
    action: str
    entry_signal: str
    reasoning: str
    score: float

    # HIGH PRIORITY: Volume-Confirmed EMA9 Pullback Pattern
    volume_pullback_signal: bool = False
    volume_pullback_data: Optional[Dict] = None

    # Technical confidence score (0-100 = confidence, direction-agnostic)
    tech_score: float = 0.0
    tech_signal: str = "NEUTRAL"         # STRONG_BULL / BULL / NEUTRAL / BEAR / STRONG_BEAR
    tech_confidence: str = "NONE"        # HIGH / MODERATE / LOW / NONE
    tech_components: Dict = field(default_factory=dict)

    # VPES (Volume-Price Expansion Score)
    vpes_value: float = 0.0              # 3-bar EMA smoothed VPES
    vpes_cumulative: float = 0.0         # 5-bar cumulative VPES

    # Market context
    market_vix: Optional[float] = None
    market_regime: Optional[str] = None      # BULL / NEUTRAL / BEAR / CRISIS
    market_risk_appetite: Optional[str] = None  # RISK_ON / BALANCED / RISK_OFF / DEFENSIVE


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def calculate_ema(data: pd.Series, period: int) -> pd.Series:
    return data.ewm(span=period, adjust=False).mean()


def calculate_rsi(data: pd.Series, period: int = 14) -> pd.Series:
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def estimate_distribution(data: pd.DataFrame, forward_days: int = 5) -> Dict:
    """Estimate forward return distribution."""
    close = data['Close']
    forward_returns = (close.shift(-forward_days) / close - 1) * 100
    forward_returns = forward_returns.dropna()
    
    if len(forward_returns) < 50:
        return {'win_rate': 0.5, 'avg_win': 5, 'avg_loss': 5}
    
    wins = forward_returns[forward_returns > 0]
    losses = forward_returns[forward_returns <= 0]
    
    return {
        'win_rate': len(wins) / len(forward_returns),
        'avg_win': wins.mean() if len(wins) > 0 else 0,
        'avg_loss': abs(losses.mean()) if len(losses) > 0 else 0
    }


def calculate_ev(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Expected Value = P(win)*E(win) - P(loss)*E(loss)"""
    return win_rate * avg_win - (1 - win_rate) * avg_loss


def calculate_kelly(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Kelly Criterion: f* = (p*b - q) / b"""
    if avg_loss == 0:
        return 0
    b = avg_win / avg_loss
    q = 1 - win_rate
    kelly = (win_rate * b - q) / b
    return max(0, min(0.25, kelly))


def load_universe() -> Dict:
    """Load stock universe from config. Re-export from src for backward compatibility."""
    from src.universe.universe_config import load_stock_universe
    return load_stock_universe()


# ============================================================================
# HIGH PRIORITY: VOLUME-CONFIRMED EMA9 PULLBACK PATTERN
# ============================================================================
# Pattern (Trend Reversal Breakout + Pullback):
# 1. PRIOR DOWNTREND: Stock was in downtrend with a resistance high (5-10 days ago)
# 2. BREAKOUT PUSH: 3+ green candles with increasing volume BREAKING ABOVE that high
# 3. PULLBACK: Higher low near EMA9 with DECREASING volume (healthy consolidation)
# 4. TRIGGER: Green candle with volume EXPANSION = ENTRY
#
# Exit Strategy:
# - Target 1: Previous push high (sell 50%)
# - Wait for consolidation
# - Re-add near EMA9
# ============================================================================

def detect_volume_pullback_pattern(data: pd.DataFrame) -> Optional[Dict]:
    """
    HIGH PRIORITY: Detect Volume-Confirmed EMA9 Pullback Pattern.
    
    This is the premium pattern - check this FIRST!
    
    Pattern (Trend Reversal + Pullback):
    1. PRIOR DOWNTREND: Stock had a resistance high 5-10 days before push
    2. BREAKOUT PUSH: 3+ green candles with increasing volume breaking above prior high
    3. PULLBACK: Higher low near EMA9 with DECREASING volume (no selling pressure)
    4. TRIGGER: Green candle with volume EXPANSION
    
    Returns dict with signal data if pattern found, None otherwise.
    """
    if len(data) < 40:
        return None
    
    close = data['Close']
    open_price = data['Open']
    high = data['High']
    low = data['Low']
    volume = data['Volume']
    
    # Calculate EMAs
    ema9 = calculate_ema(close, 9)
    ema21 = calculate_ema(close, 21)
    
    # Current candle must be green
    current_close = close.iloc[-1]
    current_open = open_price.iloc[-1]
    current_volume = volume.iloc[-1]
    current_high = high.iloc[-1]
    current_low = low.iloc[-1]
    current_ema9 = ema9.iloc[-1]
    
    is_green = current_close > current_open
    if not is_green:
        return None
    
    # === STEP 1: Find the push phase (3+ green candles with increasing volume) ===
    push_info = None
    
    for end_offset in range(2, 8):  # Look for push ending 2-7 bars ago
        end_idx = len(close) - 1 - end_offset
        if end_idx < 15:  # Need more history for prior high check
            continue
            
        # Count consecutive green candles backward
        green_count = 0
        volumes = []
        push_high = 0
        push_start_idx = end_idx
        
        for i in range(end_idx, max(end_idx - 10, 0), -1):
            if close.iloc[i] > open_price.iloc[i]:
                green_count += 1
                volumes.append(volume.iloc[i])
                push_high = max(push_high, high.iloc[i])
                push_start_idx = i
            else:
                break
        
        if green_count >= 3:
            volumes = volumes[::-1]  # Chronological order
            # Check for generally increasing volume
            increasing = sum(1 for i in range(1, len(volumes)) if volumes[i] > volumes[i-1] * 0.9)
            if increasing >= len(volumes) // 2:
                push_info = {
                    'start_idx': push_start_idx,
                    'end_idx': end_idx,
                    'push_high': push_high,
                    'avg_volume': sum(volumes) / len(volumes),
                    'green_candles': green_count
                }
                break
    
    if push_info is None:
        return None
    
    # === STEP 1.5 (NEW): Check for prior downtrend with resistance high ===
    # Look 5-10 days before the push started for a prior high (resistance)
    prior_period_start = max(0, push_info['start_idx'] - 15)
    prior_period_end = push_info['start_idx'] - 5  # At least 5 days before push
    
    if prior_period_end <= prior_period_start:
        return None
    
    prior_highs = high.iloc[prior_period_start:prior_period_end]
    prior_closes = close.iloc[prior_period_start:prior_period_end]
    
    if len(prior_highs) < 3:
        return None
    
    # Find the prior resistance high (highest high in the period 5-10+ days before push)
    prior_resistance_high = prior_highs.max()
    prior_resistance_idx = prior_highs.idxmax()
    
    # Check if there was a downtrend before the push:
    # The close at push start should be BELOW the prior resistance high
    close_at_push_start = close.iloc[push_info['start_idx']]
    
    # The push must have BROKEN ABOVE the prior resistance
    broke_resistance = push_info['push_high'] > prior_resistance_high
    
    # Verify downtrend: closes should have been declining toward push start
    # Check if closes were generally below the prior high before the push
    was_downtrend = close_at_push_start < prior_resistance_high * 0.98  # At least 2% below
    
    if not broke_resistance:
        return None  # Push didn't break prior resistance - not a breakout
    
    if not was_downtrend:
        return None  # Wasn't in a downtrend before the push
    
    # Calculate breakout strength (how much above prior resistance)
    breakout_pct = (push_info['push_high'] - prior_resistance_high) / prior_resistance_high * 100
    
    # === STEP 2: Check pullback phase ===
    pullback_start = push_info['end_idx'] + 1
    pullback_end = len(close) - 2  # Exclude current candle
    
    if pullback_end <= pullback_start:
        return None
    
    pullback_bars = pullback_end - pullback_start + 1
    if pullback_bars > 5:  # Pullback too long
        return None
    
    pullback_lows = low.iloc[pullback_start:pullback_end + 1]
    pullback_volumes = volume.iloc[pullback_start:pullback_end + 1]
    
    pullback_low = pullback_lows.min()
    pullback_avg_volume = pullback_volumes.mean()
    
    # Check pullback near EMA9 (RELAXED based on backtest: 10% works)
    min_low_idx = pullback_lows.idxmin()
    ema9_at_low = ema9.loc[min_low_idx]
    dist_to_ema9_pct = abs(pullback_low - ema9_at_low) / ema9_at_low * 100
    
    if dist_to_ema9_pct > 10.0:  # Within 10% of EMA9 (backtest validated)
        return None
    
    # Check pullback depth (RELAXED based on backtest: 3-15% works)
    pullback_pct = (push_info['push_high'] - pullback_low) / push_info['push_high'] * 100
    if pullback_pct > 15.0:  # Pullback too deep
        return None
    if pullback_pct < 3.0:  # Not a real pullback yet
        return None
    
    # Pullback should stay ABOVE the prior resistance (now support)
    # Backtest: 98% of successes held support
    pullback_held_support = pullback_low >= prior_resistance_high * 0.92  # Allow 8% tolerance
    
    # === STEP 3: Volume divergence check ===
    # Backtest finding: Volume divergence is less critical than green trigger
    vol_divergence_ratio = pullback_avg_volume / push_info['avg_volume']
    # Relaxed: don't filter on vol divergence alone, use it for confidence scoring
    
    # === STEP 4: Check trigger candle (current) has volume expansion ===
    # CRITICAL: Backtest shows Trigger Vol > 1.2x boosts win rate to 87%!
    breakout_vol_ratio = current_volume / pullback_avg_volume
    
    # Minimum 1.0x (at least normal volume), ideal > 1.2x for 87% win rate
    if breakout_vol_ratio < 0.8:  # Very weak volume - skip
        return None
    
    # === STEP 5: Check higher low structure ===
    pre_push_lows = low.iloc[max(0, push_info['start_idx'] - 10):push_info['start_idx']]
    pre_push_low = pre_push_lows.min() if len(pre_push_lows) > 0 else 0
    higher_low = pullback_low > pre_push_low
    
    # === CALCULATE LEVELS ===
    atr = calculate_atr(high, low, close).iloc[-1]
    
    stop_loss = min(pullback_low - atr * 0.3, current_ema9 * 0.97)
    # More conservative stop: below the prior resistance (now support)
    stop_loss = min(stop_loss, prior_resistance_high * 0.95)
    
    target_1 = push_info['push_high']  # Previous push high - SELL 50% HERE
    push_height = push_info['push_high'] - pullback_low
    target_2 = pullback_low + push_height * 1.5
    
    risk = current_close - stop_loss
    reward = target_1 - current_close
    risk_reward = reward / risk if risk > 0 else 0

    # === TRADEABILITY GATING ===
    # We distinguish "pattern detected" vs "tradeable A+ setup".
    blockers: List[str] = []
    if target_1 <= current_close:
        blockers.append("Late: price already at/above T1 (previous push high)")
    if breakout_vol_ratio < 1.2:
        blockers.append(f"Trigger vol {breakout_vol_ratio:.1f}x < 1.2x (not optimal)")
    if breakout_pct < 2.0:
        blockers.append(f"Weak breakout (+{breakout_pct:.1f}%)")
    if not pullback_held_support:
        blockers.append("Pullback did not hold prior resistance (support failed)")
    if risk_reward < 1.0:
        blockers.append(f"Low R:R ({risk_reward:.1f}x)")

    is_tradeable = len(blockers) == 0
    setup_grade = "A" if is_tradeable else ("B" if ("Late:" not in " ".join(blockers)) else "C")
    
    # === CONFIDENCE SCORE (Based on Backtest Results) ===
    # Backtest: green trigger is a big edge; volume expansion > 1.2x is the best filter.
    confidence = 0.70
    if breakout_vol_ratio >= 1.2:
        confidence += 0.15
    if pullback_held_support:
        confidence += 0.05
    if vol_divergence_ratio < 0.8:
        confidence += 0.03
    if higher_low:
        confidence += 0.02
    if breakout_pct >= 3.0:
        confidence += 0.05
    if target_1 > current_close:
        confidence += 0.05
    confidence = min(1.0, confidence)
    
    return {
        'pattern': 'VOLUME_PULLBACK_EMA9',
        'confidence': confidence,
        'is_tradeable': is_tradeable,
        'setup_grade': setup_grade,
        'blockers': blockers,
        # Push phase details
        'push_candles': push_info['green_candles'],
        'push_high': push_info['push_high'],
        'push_avg_volume': push_info['avg_volume'],
        # Prior resistance (breakout context)
        'prior_resistance': prior_resistance_high,
        'breakout_pct': breakout_pct,
        'was_downtrend': was_downtrend,
        'pullback_held_support': pullback_held_support,
        # Pullback phase details
        'pullback_low': pullback_low,
        'pullback_bars': pullback_bars,
        'pullback_avg_volume': pullback_avg_volume,
        'volume_divergence_ratio': vol_divergence_ratio,
        'breakout_volume_ratio': breakout_vol_ratio,
        'higher_low': higher_low,
        'dist_to_ema9_pct': dist_to_ema9_pct,
        # Levels
        'stop_loss': stop_loss,
        'target_1': target_1,  # Sell 50% here
        'target_2': target_2,
        'risk_reward': risk_reward,
        'ema9': current_ema9,
        'exit_strategy': 'SELL 50% at T1 (push high), wait for consolidation, re-add at EMA9'
    }


# ============================================================================
# MAIN SCAN FUNCTION
# ============================================================================

def scan_stock(symbol: str) -> Optional[StockScan]:
    """Comprehensive stock scan - combines all analysis."""
    try:
        # Use cached data if available (much faster)
        if USE_CACHE and DATA_MANAGER:
            data = DATA_MANAGER.get_daily_data(symbol, period="1y")
        else:
            ticker = yf.Ticker(symbol)
            data = ticker.history(period="1y")
        
        if data is None or data.empty or len(data) < 100:
            return None
        
        close = data['Close']
        high = data['High']
        low = data['Low']
        
        price = close.iloc[-1]
        
        # Technical indicators
        rsi = calculate_rsi(close).iloc[-1]
        atr = calculate_atr(high, low, close).iloc[-1]
        atr_pct = atr / price * 100
        
        ema9 = calculate_ema(close, 9).iloc[-1]
        ema21 = calculate_ema(close, 21).iloc[-1]
        ema50 = calculate_ema(close, 50).iloc[-1]
        
        dist_ema21 = (price / ema21 - 1) * 100
        
        # Momentum (multi-timeframe)
        momentum_6m = (close.iloc[-1] / close.iloc[-126] - 1) * 100 if len(close) >= 126 else 0
        momentum_3m = (close.iloc[-1] / close.iloc[-63] - 1) * 100 if len(close) >= 63 else 0
        momentum_1m = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) >= 21 else 0
        momentum_1w = (close.iloc[-1] / close.iloc[-5] - 1) * 100 if len(close) >= 5 else 0

        # MACD (12/26/9)
        ema12_series = calculate_ema(close, 12)
        ema26_series = calculate_ema(close, 26)
        macd_series = ema12_series - ema26_series
        signal_series = calculate_ema(macd_series, 9)
        macd_line = macd_series.iloc[-1]
        macd_signal_val = signal_series.iloc[-1]
        macd_histogram = macd_line - macd_signal_val
        macd_cross_bullish = macd_line > macd_signal_val
        macd_cross_bearish = macd_line < macd_signal_val

        # Fundamental score (from pre-computed DB or IBKR fallback)
        fundamental_score = get_fundamental_score(symbol)

        # Volatility (annualized)
        volatility = close.pct_change().std() * np.sqrt(252) * 100
        
        # Classify regime
        if momentum_6m > 100:
            regime = Regime.PARABOLIC
        elif momentum_6m > 50:
            regime = Regime.STRONG_UP
        elif momentum_6m > 20:
            regime = Regime.MODERATE_UP
        elif momentum_6m > 0:
            regime = Regime.WEAK_UP
        elif momentum_6m > -20:
            regime = Regime.SIDEWAYS
        else:
            regime = Regime.DOWNTREND
        
        # Classify volatility
        if volatility > 80:
            vol_cat = VolCategory.ULTRA_HIGH
        elif volatility > 50:
            vol_cat = VolCategory.HIGH
        elif volatility > 30:
            vol_cat = VolCategory.MODERATE
        else:
            vol_cat = VolCategory.LOW
        
        # Get strategy from matrix
        strategy, base_size, stop_pct, tp_pct = STRATEGY_MATRIX.get(
            (regime, vol_cat), (Strategy.STAY_CASH, 0, 0, 0)
        )
        
        # Calculate entry zones — STRATEGY-AWARE
        # Each strategy has a different entry philosophy based on price vs EMA position
        if strategy in [Strategy.TREND_FOLLOWING, Strategy.TRAILING_STOP, Strategy.BUY_HOLD]:
            # Trend: enter near current price, ride the momentum
            buy_zone_high = price
            buy_zone_low = max(ema9 * 0.98, price - 1.0 * atr)
            stop_loss = price - 2.0 * atr
        elif strategy == Strategy.SWING_TRADE:
            # Swing: enter on pullback to EMA support zone
            if price > ema9:
                # Extended above EMA9 — wait for pullback into EMA9-EMA21 zone
                buy_zone_high = ema9
                buy_zone_low = ema21
            elif price > ema21:
                # Between EMA9 and EMA21 — in the ideal pullback zone
                buy_zone_high = ema9
                buy_zone_low = ema21 * 0.98
            else:
                # Below EMA21 — already at discount, entry near current price
                buy_zone_high = price
                buy_zone_low = max(price - 1.0 * atr, ema50)
            stop_loss = min(ema21, buy_zone_low) - 1.5 * atr
        elif strategy == Strategy.MEAN_REVERSION:
            # Mean reversion: enter at deeper discount, target the mean
            if price > ema21:
                # Above mean — wait for pullback to EMA21-EMA50
                buy_zone_high = ema21
                buy_zone_low = ema50
            else:
                # Below mean — entry zone near current price down to EMA50
                buy_zone_high = min(price, ema21)
                buy_zone_low = max(ema50, price - 2.0 * atr)
            stop_loss = min(ema50, buy_zone_low) - 1.5 * atr
        else:
            # SHORT strategies or STAY_CASH
            buy_zone_high = ema21 * 0.98
            buy_zone_low = ema21 * 0.95
            stop_loss = ema21 * 0.90

        # Guard: ensure buy_zone_low <= buy_zone_high
        if buy_zone_low > buy_zone_high:
            buy_zone_low, buy_zone_high = buy_zone_high, buy_zone_low

        # Targets — strategy-aware
        if strategy in [Strategy.TREND_FOLLOWING, Strategy.TRAILING_STOP, Strategy.BUY_HOLD]:
            target_1 = price + 2.5 * atr
            target_2 = price + 4.5 * atr
        elif strategy == Strategy.SWING_TRADE:
            target_1 = price + 2.0 * atr
            target_2 = price + 3.5 * atr
        elif strategy == Strategy.MEAN_REVERSION:
            # Target: revert back to the mean (EMA21/EMA9)
            target_1 = max(ema21, price + 1.0 * atr)
            target_2 = max(ema9, price + 2.0 * atr)
        else:
            target_1 = price + 1.5 * atr
            target_2 = price + 3 * atr
        
        # Expected value
        dist = estimate_distribution(data)
        ev = calculate_ev(dist['win_rate'], dist['avg_win'], dist['avg_loss'])
        kelly = calculate_kelly(dist['win_rate'], dist['avg_win'], dist['avg_loss'])
        
        # Risk/reward
        entry = (buy_zone_high + buy_zone_low) / 2
        risk = entry - stop_loss
        reward = target_1 - entry
        rr = reward / risk if risk > 0 else 0
        
        # Position sizing (vol-adjusted)
        vol_adj_size = min(base_size, 0.15 / (volatility / 100)) if volatility > 0 else base_size
        position_size = min(vol_adj_size, kelly, 0.15)
        
        # ================================================================
        # HIGH PRIORITY: Check for Volume-Confirmed EMA9 Pullback Pattern
        # This is the premium pattern - check FIRST!
        # ================================================================
        volume_pullback = detect_volume_pullback_pattern(data)
        volume_pullback_signal = volume_pullback is not None
        
        if volume_pullback_signal:
            is_tradeable = bool(volume_pullback.get("is_tradeable", False))
            blockers = volume_pullback.get("blockers", []) or []

            if is_tradeable:
                # Tradeable A+ setup
                action = Action.STRONG_BUY.value
                entry_signal = "⚡ VOL_PULLBACK"
                reasoning = (
                    f"🎯 A+ Volume Pullback (grade {volume_pullback.get('setup_grade','A')}): "
                    f"Trigger vol {volume_pullback['breakout_volume_ratio']:.1f}x, "
                    f"Held support {'YES' if volume_pullback['pullback_held_support'] else 'NO'}, "
                    f"R:R {volume_pullback['risk_reward']:.1f}x"
                )
            else:
                # Pattern shape detected, but NOT tradeable yet (avoid false opportunities like IBM)
                action = Action.WAIT.value
                entry_signal = "⚡ VOL_PULLBACK_WAIT"
                blocker_str = "; ".join(blockers) if blockers else "Not tradeable yet"
                reasoning = f"Volume Pullback detected but WAIT: {blocker_str}"
            
            # Override levels with pattern-specific levels
            stop_loss = volume_pullback['stop_loss']
            target_1 = volume_pullback['target_1']
            target_2 = volume_pullback['target_2']
            buy_zone_low = volume_pullback['ema9'] * 0.98
            buy_zone_high = price
            rr = volume_pullback['risk_reward']
            
            # Score: only boost heavily when tradeable
            base = 90 if is_tradeable else 65
            score = base + volume_pullback['confidence'] * 10
            
            return StockScan(
                symbol=symbol,
                price=price,
                regime=regime.value,
                vol_category=vol_cat.value,
                strategy=strategy.value,
                momentum_6m=momentum_6m,
                momentum_3m=momentum_3m,
                momentum_1m=momentum_1m,
                momentum_1w=momentum_1w,
                volatility=volatility,
                rsi=rsi,
                atr_pct=atr_pct,
                dist_ema21=dist_ema21,
                macd_line=macd_line,
                macd_signal=macd_signal_val,
                macd_histogram=macd_histogram,
                fundamental_score=fundamental_score,
                buy_zone_low=buy_zone_low,
                buy_zone_high=buy_zone_high,
                stop_loss=stop_loss,
                target_1=target_1,
                target_2=target_2,
                win_rate=dist['win_rate'] * 100,
                avg_win=dist['avg_win'],
                avg_loss=dist['avg_loss'],
                expected_value=ev,
                risk_reward=rr,
                position_size_pct=position_size * 100,
                kelly_pct=kelly * 100,
                action=action,
                entry_signal=entry_signal,
                reasoning=reasoning,
                score=score,
                volume_pullback_signal=True,
                volume_pullback_data=volume_pullback
            )
        
        # ================================================================
        # Standard signal logic (if no volume pullback pattern)
        # ================================================================
        
        # Determine action and entry signal
        # Multi-regime strategy: LONG + SHORT + MEAN REVERSION
        #
        # Strategy overview:
        #   DOWNTREND  → SHORT pop-ups (rallies into EMA resistance)
        #   SIDEWAYS   → SHORT near resistance / BUY near support (mean reversion)
        #   UPTREND    → BUY pullbacks (trend following)
        #   PARABOLIC  → STRONG BUY in zone (momentum)

        # --- SHORT SIGNALS: Downtrend regime ---
        # Backtest validated (2Y, 48 stocks, 209 trades):
        #   SHORT_POPUP:      58% WR, PF 1.09, -70% MaxDD → TIGHTENED RSI>60, EMA 2.5%/3.5%
        #   SHORT_OVERBOUGHT: 64% WR, PF 2.01, -11% MaxDD → TIGHTENED RSI>75, reduced size
        #   SHORT_MEAN_REVERT: 54% WR, PF 1.16, -63% MaxDD → TIGHTENED RSI>70, dist>4%
        # RSI bucket analysis: 80+ best (+1.85%), 75-80 good (+0.98%), 60-65 worst (-0.59%)
        if regime == Regime.DOWNTREND:
            # Short pop-up: stock rallied into EMA resistance in a downtrend
            # Tightened: RSI > 60 (was 55), narrower EMA proximity bands
            # Backtest: RSI 55-60 avg PnL was only +0.24% — not worth the risk
            is_near_ema_resistance = (
                (abs(dist_ema21) < 2.5) or                      # Within 2.5% of EMA21 (was 3%)
                (ema50 > 0 and abs((price - ema50) / ema50 * 100) < 3.5)  # Within 3.5% of EMA50 (was 4%)
            )

            if rsi > 60 and is_near_ema_resistance and vol_cat != VolCategory.ULTRA_HIGH:
                # Shortable pop-up: overbought bounce into resistance
                action = Action.SHORT.value
                entry_signal = "SHORT_POPUP"
                # Compute short-specific levels
                short_entry = price
                short_stop = price + 2.0 * atr   # Tighter stop (was 2.5 ATR)
                short_target_1 = price - 1.5 * atr
                short_target_2 = price - 3.0 * atr
                short_rr = (price - short_target_1) / (short_stop - price) if short_stop > price else 0
                buy_zone_low = short_target_2      # Repurpose as target (short profit zone)
                buy_zone_high = short_target_1
                stop_loss = short_stop
                target_1 = short_target_1
                target_2 = short_target_2
                # Use short strategy from matrix
                short_strat = STRATEGY_MATRIX.get((regime, vol_cat), (Strategy.SHORT_POPUP, 0.06, 0.08, 0.10))
                strategy = short_strat[0]
                position_size = short_strat[1]
                reasoning = (
                    f"SHORT pop-up: Downtrend ({momentum_6m:+.1f}% 6M), RSI {rsi:.0f} "
                    f"bounced into EMA resistance ({dist_ema21:+.1f}% from EMA21). "
                    f"Short R:R {short_rr:.1f}x."
                )
            elif rsi > 75:
                # Extremely overbought in downtrend — high conviction short
                # Tightened: RSI > 75 (was 70). Backtest: best bucket is RSI 75+
                # Only 11 trades in backtest → reduced position size for safety
                action = Action.SHORT.value
                entry_signal = "SHORT_OVERBOUGHT"
                short_stop = price + 2.0 * atr
                target_1 = price - 2.0 * atr
                target_2 = price - 4.0 * atr
                buy_zone_low = target_2
                buy_zone_high = target_1
                stop_loss = short_stop
                short_strat = STRATEGY_MATRIX.get((regime, vol_cat), (Strategy.SHORT_POPUP, 0.06, 0.08, 0.10))
                strategy = short_strat[0]
                position_size = short_strat[1] * 0.75  # 25% smaller due to small sample size
                reasoning = (
                    f"SHORT overbought: Downtrend + RSI {rsi:.0f} extremely overbought (>75). "
                    f"High probability mean reversion. Reduced size (rare signal)."
                )
            elif rsi > 50 and dist_ema21 > 0:
                # Downtrend + RSI above neutral + above EMA21 → SHORT (moderate conviction)
                action = Action.SHORT.value
                entry_signal = "SHORT_DOWNTREND"
                short_stop = price + 2.0 * atr
                target_1 = ema21 if ema21 < price else price - 1.5 * atr
                target_2 = price - 3.0 * atr
                buy_zone_low = target_2
                buy_zone_high = target_1
                stop_loss = short_stop
                short_strat = STRATEGY_MATRIX.get((regime, vol_cat), (Strategy.SHORT_POPUP, 0.04, 0.08, 0.10))
                strategy = short_strat[0]
                position_size = short_strat[1] * 0.5  # Half size — moderate setup
                reasoning = (
                    f"SHORT downtrend: {momentum_6m:+.1f}% 6M momentum, RSI {rsi:.0f} above neutral, "
                    f"{dist_ema21:+.1f}% above EMA21. Reduced size."
                )
            else:
                # Downtrend but RSI too low or below EMA — wait for bounce
                action = Action.WAIT.value
                entry_signal = "NO_ENTRY"
                reasoning = f"Downtrend ({momentum_6m:+.1f}% 6M). RSI {rsi:.0f} too low to short. Wait."

        # --- SHORT SIGNALS: Sideways regime near resistance ---
        # Tightened: RSI > 70 (was 65), dist_ema21 > 4% (was 3%)
        # Backtest: 44% stop-loss rate at old thresholds → higher bar reduces false signals
        elif regime == Regime.SIDEWAYS and rsi > 70 and dist_ema21 > 4.0:
            # Overbought within sideways range — short mean reversion
            short_strat = SHORT_STRATEGY_MATRIX.get(
                (regime, vol_cat),
                (Strategy.SHORT_MEAN_REVERT, 0.06, 0.06, 0.08)
            )
            action = Action.SHORT.value
            entry_signal = "SHORT_MEAN_REVERT"
            strategy = short_strat[0]
            position_size = short_strat[1]
            short_stop = price + 2.0 * atr
            target_1 = ema21  # Revert to mean (EMA21)
            target_2 = price - 2.5 * atr
            buy_zone_low = target_2
            buy_zone_high = target_1
            stop_loss = short_stop
            reasoning = (
                f"SHORT mean reversion: Sideways regime, RSI {rsi:.0f} overbought (>70), "
                f"{dist_ema21:+.1f}% above EMA21 (>4%). Target reversion to ${ema21:.2f}."
            )

        # --- LONG SIGNALS: existing logic enhanced ---
        # Negative EV — not good for longs, but consider SHORT in weak regimes
        elif ev < 0:
            if regime in [Regime.DOWNTREND, Regime.SIDEWAYS] and rsi > 50:
                action = Action.SHORT.value
                entry_signal = "SHORT_NEG_EV"
                short_stop = price + 2.0 * atr
                target_1 = price - 2.0 * atr
                target_2 = price - 3.5 * atr
                buy_zone_low = target_2
                buy_zone_high = target_1
                stop_loss = short_stop
                reasoning = f"SHORT: Negative EV ({ev:.2f}%) + {regime.value} regime, RSI {rsi:.0f}."
            else:
                action = Action.WAIT.value
                entry_signal = "NEG_EV"
                reasoning = f"Negative EV ({ev:.2f}%). Not shortable (RSI {rsi:.0f}, {regime.value}). Wait."

        # PARABOLIC/STRONG_UP momentum-following: RSI healthy + near EMA9 → BUY
        elif (regime in [Regime.PARABOLIC, Regime.STRONG_UP]
              and rsi < 80
              and abs((price / ema9 - 1) * 100) < 3
              and ev > 0 and rr > 1.0):
            action = Action.BUY.value
            entry_signal = "MOMENTUM_FOLLOW"
            reasoning = (
                f"Momentum follow ({regime.value}): RSI {rsi:.0f} healthy (<80), "
                f"price near EMA9 ({(price/ema9-1)*100:+.1f}%), R:R {rr:.1f}x. "
                f"Riding trend with tight stop."
            )

        # WAIT: Overextended — thresholds relaxed for PARABOLIC/STRONG_UP regimes
        elif ((regime in [Regime.PARABOLIC, Regime.STRONG_UP] and (rsi >= 80 or dist_ema21 > 15))
              or (regime not in [Regime.PARABOLIC, Regime.STRONG_UP] and (rsi >= 70 or dist_ema21 > 10))):
            action = Action.WAIT.value
            entry_signal = "OVEREXTENDED"
            reasoning = f"Overextended (RSI {rsi:.0f}, {dist_ema21:+.1f}% from EMA21). Wait for pullback."

        # WAIT: Below buy zone (potential falling knife - DON'T chase)
        elif price < buy_zone_low:
            action = Action.WAIT.value
            entry_signal = "BELOW_ZONE"
            reasoning = f"Below buy zone - wait for stabilization. Could be falling knife."

        # Sideways with BUY opportunity near support (mean reversion long)
        elif regime == Regime.SIDEWAYS and rsi < 35 and dist_ema21 < -3.0:
            if ev > 0.3:
                action = Action.BUY.value
                entry_signal = "MR_LONG"
                reasoning = (
                    f"Mean reversion LONG: Sideways regime, RSI {rsi:.0f} oversold, "
                    f"{dist_ema21:+.1f}% below EMA21. Buy the dip, target reversion."
                )
            else:
                action = Action.WAIT.value
                entry_signal = "WEAK_SETUP"
                reasoning = f"Sideways oversold but weak EV ({ev:.2f}%). Wait for better setup."

        # WAIT: Sideways regime with weak metrics
        elif regime == Regime.SIDEWAYS and (ev < 0.5 or rr < 1.5):
            action = Action.WAIT.value
            entry_signal = "WEAK_SETUP"
            reasoning = f"Sideways regime with weak EV ({ev:.2f}%) or R:R ({rr:.1f}x)."

        # In buy zone - apply strict criteria (LONG)
        elif price >= buy_zone_low and price <= buy_zone_high:
            # STRONG BUY: High conviction - strong trend + good EV + good R:R + healthy RSI
            if (ev > 1.5 and rr > 2.5 and
                momentum_6m > 10 and
                30 <= rsi <= 65 and
                regime in [Regime.PARABOLIC, Regime.STRONG_UP, Regime.MODERATE_UP]):
                action = Action.STRONG_BUY.value
                entry_signal = "BUY_NOW"
                reasoning = f"High conviction: EV {ev:.2f}%, R:R {rr:.1f}x, {regime.value}."

            # BUY: Good setup - requires meaningful EV AND R:R
            elif ev > 0.5 and rr > 1.5:
                action = Action.BUY.value
                entry_signal = "BUY_NOW"
                reasoning = f"Good setup: EV {ev:.2f}%, R:R {rr:.1f}x."

            # WAIT: In zone but metrics too weak
            else:
                action = Action.WAIT.value
                entry_signal = "WEAK_METRICS"
                reasoning = f"In zone but weak EV ({ev:.2f}%) or R:R ({rr:.1f}x). Need EV>0.5%, R:R>1.5x."

        # Above buy zone - wait for pullback
        else:
            action = Action.WAIT.value
            entry_signal = "ABOVE_ZONE"
            reasoning = f"Above buy zone. Wait for pullback to ${buy_zone_high:.2f}."

        # Score (for ranking) — fundamental base + technical signals
        # Breakdown: Fundamental(50) + Momentum(20) + RSI(10) + MACD(10) + R:R(10) = 100
        fundamental_score = get_fundamental_score(symbol)
        base_pts = fundamental_score / 2  # Scale 0-100 → 0-50 pts

        if "SHORT" in action:
            # Short: reward negative momentum, bearish MACD
            mom_1w_pts = max(-5, min(5, -momentum_1w / 2))
            mom_1m_pts = max(-7, min(7, -momentum_1m / 4))
            mom_3m_pts = max(-8, min(8, -momentum_3m / 6))
            rsi_pts = 10 if rsi > 60 else (5 if rsi > 50 else -5)
            macd_pts = 10 if macd_cross_bearish else (5 if macd_histogram < 0 else 0)
        else:
            mom_1w_pts = max(-5, min(5, momentum_1w / 2))     # ±5 pts
            mom_1m_pts = max(-7, min(7, momentum_1m / 4))      # ±7 pts
            mom_3m_pts = max(-8, min(8, momentum_3m / 6))      # ±8 pts
            rsi_pts = 10 if 30 < rsi < 70 else -5
            macd_pts = 10 if macd_cross_bullish else (5 if macd_histogram > 0 else 0)

        momentum_pts = mom_1w_pts + mom_1m_pts + mom_3m_pts  # ±20 pts total
        rr_pts = min(10, max(0, (rr - 1) * 5))  # 1:1=0, 2:1=5, 3:1=10

        score = base_pts + momentum_pts + rsi_pts + macd_pts + rr_pts
        score = max(0, min(100, score))

        # ── VPES (Volume-Price Expansion Score) ──
        vpes_val, vpes_cum = calculate_vpes(data)

        # ── Technical Confidence Score (6 components incl. VPES) ──
        market = fetch_market_context()
        raw_tech, tech_components = calculate_tech_score(
            strategy=strategy.value,
            action=action,
            rsi=rsi,
            ema9=ema9, ema21=ema21, ema50=ema50,
            price=price,
            momentum_3m=momentum_3m,
            momentum_6m=momentum_6m,
            volatility=volatility,
            vol_category=vol_cat.value,
            dist_ema21=dist_ema21,
            vpes_ema=vpes_val,
            vpes_cumulative=vpes_cum,
        )
        adjusted_tech = apply_market_adjustment(raw_tech, action, market)

        # Confidence mapping (direction-agnostic)
        if adjusted_tech >= 80:
            tech_confidence = "HIGH"
        elif adjusted_tech >= 60:
            tech_confidence = "MODERATE"
        elif adjusted_tech >= 40:
            tech_confidence = "LOW"
        else:
            tech_confidence = "NONE"

        # Signal mapping (combines action direction + confidence)
        if "SHORT" in action:
            tech_signal = "STRONG_BEAR" if tech_confidence == "HIGH" else (
                "BEAR" if tech_confidence in ("MODERATE", "LOW") else "NEUTRAL")
        elif "BUY" in action:
            tech_signal = "STRONG_BULL" if tech_confidence == "HIGH" else (
                "BULL" if tech_confidence in ("MODERATE", "LOW") else "NEUTRAL")
        else:
            tech_signal = "NEUTRAL"

        return StockScan(
            symbol=symbol,
            price=price,
            regime=regime.value,
            vol_category=vol_cat.value,
            strategy=strategy.value,
            momentum_6m=momentum_6m,
            momentum_3m=momentum_3m,
            momentum_1m=momentum_1m,
            momentum_1w=momentum_1w,
            volatility=volatility,
            rsi=rsi,
            atr_pct=atr_pct,
            dist_ema21=dist_ema21,
            macd_line=macd_line,
            macd_signal=macd_signal_val,
            macd_histogram=macd_histogram,
            fundamental_score=fundamental_score,
            buy_zone_low=buy_zone_low,
            buy_zone_high=buy_zone_high,
            stop_loss=stop_loss,
            target_1=target_1,
            target_2=target_2,
            win_rate=dist['win_rate'] * 100,
            avg_win=dist['avg_win'],
            avg_loss=dist['avg_loss'],
            expected_value=ev,
            risk_reward=rr,
            position_size_pct=position_size * 100,
            kelly_pct=kelly * 100,
            action=action,
            entry_signal=entry_signal,
            reasoning=reasoning,
            score=score,
            volume_pullback_signal=False,
            volume_pullback_data=None,
            tech_score=adjusted_tech,
            tech_signal=tech_signal,
            tech_confidence=tech_confidence,
            tech_components=tech_components,
            vpes_value=vpes_val,
            vpes_cumulative=vpes_cum,
            market_vix=market.vix,
            market_regime=market.market_regime,
            market_risk_appetite=market.risk_appetite,
        )
        
    except Exception as e:
        print(f"   ⚠️ Error: {symbol} - {e}")
        return None


def scan_symbols(symbols: List[str], use_preload: bool = True) -> List[StockScan]:
    """Scan multiple symbols in parallel."""
    results = []
    
    # Preload data for all symbols (bulk cache fill)
    if USE_CACHE and DATA_MANAGER and use_preload and len(symbols) > 5:
        DATA_MANAGER.preload_symbols(symbols, period="1y")
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(scan_stock, s): s for s in symbols}
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)
    
    results.sort(key=lambda x: x.score, reverse=True)
    return results


# ============================================================================
# OUTPUT FUNCTIONS
# ============================================================================

class OutputWriter:
    """Handles output to file or console with pretty formatting."""
    
    def __init__(self, output_file: Optional[str] = None, to_console: bool = False):
        self.lines = []
        self.output_file = output_file
        self.to_console = to_console
    
    def write(self, text: str = ""):
        """Add a line to output."""
        self.lines.append(text)
        if self.to_console:
            print(text)
    
    def save(self):
        """Save output to file."""
        if self.output_file and not self.to_console:
            os.makedirs(os.path.dirname(self.output_file), exist_ok=True)
            with open(self.output_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(self.lines))
            print(f"\n✅ Scan results saved to: {self.output_file}")
    
    def get_content(self) -> str:
        """Get all content as string."""
        return '\n'.join(self.lines)


def format_header(writer: OutputWriter, title: str, scan_time: str, symbol_count: int):
    """Write report header."""
    writer.write("=" * 120)
    writer.write(f"  {title}")
    writer.write(f"  Generated: {scan_time}")
    writer.write(f"  Stocks Scanned: {symbol_count}")
    # Show market context
    market = fetch_market_context()
    writer.write(f"  Market: VIX={market.vix:.1f} ({market.vix_regime}/{market.vix_trend}) | "
                 f"SPY={market.spy_trend} ({market.spy_momentum_6m:+.1f}%) | "
                 f"QQQ={market.qqq_trend} ({market.qqq_momentum_6m:+.1f}%) | "
                 f"Regime={market.market_regime} | Risk={market.risk_appetite}")
    writer.write("=" * 120)


def format_quick_scan(writer: OutputWriter, results: List[StockScan]):
    """Quick summary table."""
    writer.write("")
    writer.write("=" * 110)
    writer.write("  SCAN RESULTS - QUICK VIEW")
    writer.write("=" * 110)
    writer.write("")
    writer.write(f"{'Symbol':<8} {'Price':>10} {'Regime':<12} {'Vol':>6} {'RSI':>5} {'EV':>8} {'Tech':>5} {'Conf':>8} {'Action':<15} {'Entry':>12}")
    writer.write("-" * 120)

    for r in results:
        icon = "[STRONG]" if "STRONG" in r.action else ("[BUY]" if "BUY" in r.action else ("[SHORT]" if "SHORT" in r.action else ("[WAIT]" if "WAIT" in r.action else "[AVOID]")))
        writer.write(f"{r.symbol:<8} ${r.price:>9.2f} {r.regime:<12} {r.volatility:>5.0f}% {r.rsi:>4.0f} "
                     f"{r.expected_value:>+7.2f}% {r.tech_score:>4.0f} {r.tech_confidence:>8} {icon:<15} {r.entry_signal:>12}")


def format_trading_plan(writer: OutputWriter, r: StockScan):
    """Detailed trading plan for one stock."""
    writer.write("")
    writer.write("=" * 70)
    
    # Special header for Volume Pullback signals
    if r.volume_pullback_signal:
        writer.write(f"  ⚡ {r.symbol} TRADING PLAN - HIGH PRIORITY PATTERN ⚡")
    else:
        writer.write(f"  {r.symbol} TRADING PLAN")
    writer.write("=" * 70)
    
    # Volume Pullback Pattern Details (if applicable)
    if r.volume_pullback_signal and r.volume_pullback_data:
        vpd = r.volume_pullback_data
        writer.write("")
        writer.write("  🎯 TREND REVERSAL + VOLUME PULLBACK PATTERN DETECTED!")
        writer.write("  " + "-" * 50)
        writer.write("")
        writer.write("  PHASE 1 - PRIOR DOWNTREND:")
        writer.write(f"  • Stock had resistance at ${vpd['prior_resistance']:.2f} (5-10 days before push)")
        writer.write(f"  • Was in downtrend: {'YES ✅' if vpd['was_downtrend'] else 'NO'}")
        writer.write("")
        writer.write("  PHASE 2 - BREAKOUT PUSH:")
        writer.write(f"  • {vpd['push_candles']} green candles with increasing volume")
        writer.write(f"  • Broke above resistance by +{vpd['breakout_pct']:.1f}%")
        writer.write(f"  • Push high: ${vpd['push_high']:.2f}")
        writer.write("")
        writer.write("  PHASE 3 - PULLBACK:")
        writer.write(f"  • Pullback to EMA9 ({vpd['pullback_bars']} bars) with LOW volume")
        writer.write(f"  • Volume Divergence: {vpd['volume_divergence_ratio']:.2f}x (pullback vol / push vol)")
        writer.write(f"  • Pullback held above prior resistance (now support): {'YES ✅' if vpd['pullback_held_support'] else 'NO ⚠️'}")
        writer.write(f"  • Higher Low: {'YES ✅' if vpd['higher_low'] else 'NO'}")
        writer.write("")
        writer.write("  PHASE 4 - TRIGGER (NOW):")
        writer.write(f"  • GREEN candle with volume EXPANSION = ENTRY")
        writer.write(f"  • Trigger Volume: {vpd['breakout_volume_ratio']:.1f}x (vs pullback avg)")
        writer.write(f"  • Pattern Confidence: {vpd['confidence']*100:.0f}%")
        writer.write("")
        writer.write("  EXIT STRATEGY:")
        writer.write(f"  → T1 ${vpd['target_1']:.2f}: SELL 50% (push high = first resistance)")
        writer.write(f"  → Wait for consolidation after T1")
        writer.write(f"  → Re-add position near EMA9 (${vpd['ema9']:.2f})")
        writer.write("")
    
    writer.write("")
    writer.write(f"  CURRENT STATUS")
    writer.write(f"  Price: ${r.price:.2f}")
    writer.write(f"  Regime: {r.regime}")
    writer.write(f"  Volatility: {r.volatility:.0f}%")
    writer.write(f"  RSI: {r.rsi:.0f}")
    writer.write(f"  6M Momentum: {r.momentum_6m:+.1f}%")
    writer.write(f"  Distance from EMA21: {r.dist_ema21:+.1f}%")
    
    writer.write("")
    writer.write(f"  ACTION: {r.action}")
    writer.write(f"  Reasoning: {r.reasoning}")
    
    writer.write("")
    writer.write("  ENTRY PARAMETERS")
    writer.write(f"  Buy Zone: ${r.buy_zone_low:.2f} - ${r.buy_zone_high:.2f}")
    writer.write(f"  Stop Loss: ${r.stop_loss:.2f}")
    writer.write(f"  Target 1: ${r.target_1:.2f} ({(r.target_1/r.price-1)*100:+.1f}%)")
    writer.write(f"  Target 2: ${r.target_2:.2f} ({(r.target_2/r.price-1)*100:+.1f}%)")
    
    writer.write("")
    writer.write("  EXPECTED VALUE ANALYSIS")
    writer.write(f"  Win Rate: {r.win_rate:.1f}%")
    writer.write(f"  Avg Win: +{r.avg_win:.2f}%")
    writer.write(f"  Avg Loss: -{r.avg_loss:.2f}%")
    ev_status = "POSITIVE" if r.expected_value > 0 else "NEGATIVE"
    writer.write(f"  EV: {r.expected_value:+.2f}% ({ev_status})")
    writer.write(f"  Risk/Reward: 1:{r.risk_reward:.1f}")
    
    writer.write("")
    writer.write("  POSITION SIZING")
    writer.write(f"  Recommended Size: {r.position_size_pct:.1f}% of portfolio")
    writer.write(f"  Kelly Criterion: {r.kelly_pct:.1f}%")
    writer.write(f"  Strategy: {r.strategy}")

    writer.write("")
    writer.write("  TECHNICAL CONFIDENCE")
    writer.write(f"  Tech Score: {r.tech_score:.0f}/100 ({r.tech_confidence})")
    writer.write(f"  Signal: {r.tech_signal}")
    if r.tech_components:
        writer.write(f"  Components: EMA={r.tech_components.get('ema_align', 0):.0f} RSI={r.tech_components.get('rsi', 0):.0f} "
                     f"Mom={r.tech_components.get('momentum', 0):.0f} PrEMA={r.tech_components.get('price_ema', 0):.0f} "
                     f"Vol={r.tech_components.get('vol', 0):.0f} (each 0-20)")
    if r.market_vix is not None:
        writer.write(f"  Market: VIX={r.market_vix:.1f} | Regime={r.market_regime} | Appetite={r.market_risk_appetite}")


def format_summary(writer: OutputWriter, results: List[StockScan]):
    """Summary with grouping by action."""
    buys = [r for r in results if "BUY" in r.action]
    waits = [r for r in results if "WAIT" in r.action]
    avoids = [r for r in results if "AVOID" in r.action]
    
    writer.write("")
    writer.write("=" * 110)
    writer.write(f"  SUMMARY: {len(buys)} BUY | {len(waits)} WAIT | {len(avoids)} AVOID")
    writer.write("=" * 110)
    
    if buys:
        writer.write("")
        writer.write(f"  ACTIONABLE OPPORTUNITIES ({len(buys)})")
        writer.write("-" * 90)
        for r in buys:
            entry = (r.buy_zone_low + r.buy_zone_high) / 2
            writer.write(
                f"  {r.symbol:<6} Price ${r.price:>7.2f} | Entry ${r.buy_zone_low:.2f}-${r.buy_zone_high:.2f} "
                f"(mid ${entry:.2f})"
            )
            writer.write(
                f"        Stop ${r.stop_loss:.2f} | T1 ${r.target_1:.2f} | T2 ${r.target_2:.2f} | "
                f"EV {r.expected_value:+.2f}% | R:R 1:{r.risk_reward:.1f} | Size {r.position_size_pct:.1f}%"
            )
    
    if waits:
        writer.write("")
        writer.write(f"  WAIT LIST ({len(waits)})")
        writer.write("-" * 90)
        for r in waits:
            writer.write(f"  {r.symbol:<8} - {r.reasoning}")
    
    if avoids:
        writer.write("")
        writer.write(f"  AVOID ({len(avoids)})")
        writer.write("-" * 90)
        for r in avoids:
            writer.write(f"  {r.symbol:<8} - {r.reasoning}")


def format_top_picks(writer: OutputWriter, results: List[StockScan]):
    """Format top picks section."""
    buys = [r for r in results if "BUY" in r.action]
    strong_buys = [r for r in buys if "STRONG" in r.action]
    
    # HIGH PRIORITY: Volume Pullback signals get their own section at the very top
    # Only show TRADEABLE setups here; non-tradeable go to WAIT watchlist.
    vol_pullbacks = [
        r for r in results
        if r.volume_pullback_signal
        and r.volume_pullback_data
        and r.volume_pullback_data.get("is_tradeable")
    ]
    
    writer.write("")
    writer.write("=" * 110)
    writer.write("  TOP PICKS")
    writer.write("=" * 110)
    
    # ⚡ HIGHEST PRIORITY: Volume-Confirmed EMA9 Pullback Pattern
    if vol_pullbacks:
        writer.write("")
        writer.write("  ⚡⚡⚡ HIGH PRIORITY: TREND REVERSAL + VOLUME PULLBACK PATTERN ⚡⚡⚡")
        writer.write("  (Breakout from prior high during downtrend → 3+ green candles with increasing vol")
        writer.write("   → pullback to EMA9 with LOW vol → volume expansion trigger)")
        writer.write("")
        for r in vol_pullbacks:
            vpd = r.volume_pullback_data
            writer.write(f"  🎯 {r.symbol:<6} @ ${r.price:.2f} | R:R 1:{vpd['risk_reward']:.1f} | Confidence: {vpd['confidence']*100:.0f}%")
            writer.write(f"     BREAKOUT: Broke above ${vpd['prior_resistance']:.2f} resistance (+{vpd['breakout_pct']:.1f}%)")
            writer.write(f"     PUSH: {vpd['push_candles']} green candles to ${vpd['push_high']:.2f}")
            writer.write(f"     PULLBACK: To EMA9, held support: {'YES ✅' if vpd['pullback_held_support'] else 'NO ⚠️'}")
            writer.write(f"     Volume Divergence: {vpd['volume_divergence_ratio']:.2f}x (lower = better) | Trigger Vol: {vpd['breakout_volume_ratio']:.1f}x")
            writer.write(f"     Entry: NOW @ ${r.price:.2f} | Stop: ${vpd['stop_loss']:.2f} | T1: ${vpd['target_1']:.2f} (sell 50%) | T2: ${vpd['target_2']:.2f}")
            writer.write(f"     Exit Strategy: {vpd['exit_strategy']}")
            writer.write("")
    
    # Standard Strong Buys
    non_vol_strong = [r for r in strong_buys if not r.volume_pullback_signal]
    if non_vol_strong:
        writer.write("")
        writer.write("  STRONG BUY (High Conviction)")
        for r in non_vol_strong[:5]:
            writer.write(f"  -> {r.symbol:<6} @ ${r.price:.2f} | EV: {r.expected_value:+.2f}% | R:R 1:{r.risk_reward:.1f}")
            writer.write(f"     Buy Zone: ${r.buy_zone_low:.2f}-${r.buy_zone_high:.2f} | Stop: ${r.stop_loss:.2f}")
    
    # Standard Buys
    regular_buys = [r for r in buys if "STRONG" not in r.action]
    if regular_buys:
        writer.write("")
        writer.write("  BUY (Good Setups)")
        for r in regular_buys[:5]:
            writer.write(f"  -> {r.symbol:<6} @ ${r.price:.2f} | EV: {r.expected_value:+.2f}% | R:R 1:{r.risk_reward:.1f}")


def build_symbol_theme_map(universe: Dict) -> Dict[str, str]:
    """Build a symbol -> theme name map using first match."""
    symbol_theme = {}
    for theme_key, theme in universe.get("themes", {}).items():
        theme_name = theme.get("name", theme_key)
        for symbol in theme.get("symbols", []):
            if symbol not in symbol_theme:
                symbol_theme[symbol] = theme_name
    return symbol_theme


def format_wait_watchlist(
    writer: OutputWriter,
    results: List[StockScan],
    symbol_theme_map: Dict[str, str],
    max_items: int = 10,
):
    """Show top WAIT names, one per theme, ranked by score."""
    waits = [r for r in results if "WAIT" in r.action]
    if not waits:
        return

    theme_best: Dict[str, StockScan] = {}
    for r in waits:
        theme = symbol_theme_map.get(r.symbol, "Other")
        if theme not in theme_best or r.score > theme_best[theme].score:
            theme_best[theme] = r

    ranked = sorted(theme_best.items(), key=lambda item: item[1].score, reverse=True)
    if max_items > 0:
        ranked = ranked[:max_items]

    writer.write("")
    writer.write("=" * 110)
    writer.write(f"  WAIT WATCHLIST (Top {len(ranked)} | 1 per theme)")
    writer.write("=" * 110)

    for theme, r in ranked:
        entry = (r.buy_zone_low + r.buy_zone_high) / 2
        writer.write(f"  {theme}")
        writer.write(
            f"    {r.symbol:<6} @ ${r.price:.2f} | Entry ${r.buy_zone_low:.2f}-${r.buy_zone_high:.2f} "
            f"(mid ${entry:.2f}) | RSI {r.rsi:.0f} | dEMA21 {r.dist_ema21:+.1f}%"
        )
        writer.write(f"    Reason: {r.reasoning}")


def format_regime_breakdown(writer: OutputWriter, results: List[StockScan]):
    """Format regime breakdown section."""
    writer.write("")
    writer.write("=" * 110)
    writer.write("  MARKET REGIME BREAKDOWN")
    writer.write("=" * 110)
    
    regime_counts = {}
    for r in results:
        regime_counts[r.regime] = regime_counts.get(r.regime, 0) + 1
    
    writer.write("")
    for regime, count in sorted(regime_counts.items(), key=lambda x: -x[1]):
        pct = count / len(results) * 100
        bar = "█" * int(pct / 2)
        writer.write(f"  {regime:<15} {count:>3} ({pct:>5.1f}%) {bar}")


# Legacy print functions for backwards compatibility
def print_quick_scan(results: List[StockScan]):
    """Quick summary table (console output)."""
    print(f"\n{'='*120}")
    print(f"{'Symbol':<8} {'Price':>10} {'Regime':<12} {'Vol':>6} {'RSI':>5} {'EV':>8} {'Tech':>5} {'Conf':>8} {'Action':<15} {'Entry':>12}")
    print("-"*120)

    for r in results:
        icon = "🟢" if "STRONG" in r.action else ("🟡" if "BUY" in r.action else ("🟣" if "SHORT" in r.action else ("⚪" if "WAIT" in r.action else "🔴")))
        print(f"{r.symbol:<8} ${r.price:>9.2f} {r.regime:<12} {r.volatility:>5.0f}% {r.rsi:>4.0f} "
              f"{r.expected_value:>+7.2f}% {r.tech_score:>4.0f} {r.tech_confidence:>8} {icon} {r.action.split()[-1]:<12} {r.entry_signal:>12}")


def print_trading_plan(r: StockScan):
    """Detailed trading plan for one stock (console output)."""
    print(f"\n{'='*70}")
    print(f"📊 {r.symbol} TRADING PLAN")
    print(f"{'='*70}")
    
    print(f"\n💰 Price: ${r.price:.2f} | Regime: {r.regime} | Vol: {r.volatility:.0f}%")
    print(f"📊 RSI: {r.rsi:.0f} | 6M: {r.momentum_6m:+.1f}% | dEMA21: {r.dist_ema21:+.1f}%")
    
    print(f"\n🎯 ACTION: {r.action}")
    print(f"   {r.reasoning}")
    
    print(f"\n📍 ENTRY:")
    print(f"   Buy Zone: ${r.buy_zone_low:.2f} - ${r.buy_zone_high:.2f}")
    print(f"   Stop Loss: ${r.stop_loss:.2f}")
    print(f"   Target 1: ${r.target_1:.2f} ({(r.target_1/r.price-1)*100:+.1f}%)")
    print(f"   Target 2: ${r.target_2:.2f} ({(r.target_2/r.price-1)*100:+.1f}%)")
    
    print(f"\n📊 EXPECTED VALUE:")
    print(f"   Win Rate: {r.win_rate:.1f}%")
    print(f"   Avg Win: +{r.avg_win:.2f}% | Avg Loss: -{r.avg_loss:.2f}%")
    ev_icon = "✅" if r.expected_value > 0 else "❌"
    print(f"   EV: {r.expected_value:+.2f}% {ev_icon}")
    print(f"   R:R: 1:{r.risk_reward:.1f}")
    
    print(f"\n💼 POSITION:")
    print(f"   Recommended: {r.position_size_pct:.1f}% of portfolio")
    print(f"   Strategy: {r.strategy}")

    print(f"\n🔬 TECHNICAL CONFIDENCE:")
    print(f"   Tech Score: {r.tech_score:.0f}/100 ({r.tech_confidence})")
    print(f"   Signal: {r.tech_signal}")
    if r.tech_components:
        print(f"   Components: EMA={r.tech_components.get('ema_align', 0):.0f} RSI={r.tech_components.get('rsi', 0):.0f} "
              f"Mom={r.tech_components.get('momentum', 0):.0f} PrEMA={r.tech_components.get('price_ema', 0):.0f} "
              f"Vol={r.tech_components.get('vol', 0):.0f} (each 0-20)")
    if r.market_vix is not None:
        print(f"   Market: VIX={r.market_vix:.1f} | Regime={r.market_regime} | Appetite={r.market_risk_appetite}")


def print_summary(results: List[StockScan]):
    """Summary with grouping by action (console output)."""
    buys = [r for r in results if "BUY" in r.action]
    waits = [r for r in results if "WAIT" in r.action]
    avoids = [r for r in results if "AVOID" in r.action]
    
    print(f"\n{'='*90}")
    print(f"📋 SUMMARY: {len(buys)} BUY | {len(waits)} WAIT | {len(avoids)} AVOID")
    print(f"{'='*90}")
    
    if buys:
        print(f"\n🟢 ACTIONABLE ({len(buys)}):")
        print(f"{'Symbol':<8} {'Price':>10} {'Buy Zone':>22} {'Stop':>10} {'EV':>8} {'Size':>6}")
        print("-"*70)
        for r in buys[:10]:
            print(f"{r.symbol:<8} ${r.price:>9.2f} ${r.buy_zone_low:.2f}-${r.buy_zone_high:.2f} "
                  f"${r.stop_loss:>9.2f} {r.expected_value:>+7.2f}% {r.position_size_pct:>5.1f}%")
    
    if waits:
        print(f"\n⚪ WAIT ({len(waits)}):")
        for r in waits[:5]:
            print(f"   {r.symbol:<6} - {r.reasoning}")
    
    if avoids:
        print(f"\n🔴 AVOID ({len(avoids)}):")
        for r in avoids[:5]:
            print(f"   {r.symbol:<6} - {r.reasoning}")


# ============================================================================
# MAIN
# ============================================================================

def generate_output_filename() -> str:
    """Generate default output filename with timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join(os.path.dirname(__file__), '..', 'results')
    return os.path.join(results_dir, f"scan_{timestamp}.txt")


def run_scan(
    symbols: List[str],
    output_file: Optional[str] = None,
    to_console: bool = False,
    plan_mode: bool = False,
    quick_mode: bool = False,
    theme_name: Optional[str] = None,
    output_buy_zones_csv: Optional[str] = None,
) -> str:
    """
    Run scan and output to file or console.
    Returns the output file path.
    """
    # Default output file
    if output_file is None and not to_console:
        output_file = generate_output_filename()
    
    scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"\n🔍 Scanning {len(symbols)} stocks...")
    results = scan_symbols(symbols)
    
    if not results:
        print("❌ No results found!")
        return ""
    
    # Create output writer
    writer = OutputWriter(output_file, to_console)
    universe = load_universe()
    symbol_theme_map = build_symbol_theme_map(universe)
    
    # Write header
    title = "STOCK SCANNER REPORT"
    if theme_name:
        title += f" - {theme_name.upper()}"
    format_header(writer, title, scan_time, len(symbols))
    
    if plan_mode:
        # Detailed trading plans
        for r in results:
            format_trading_plan(writer, r)
    elif quick_mode:
        # Quick scan only
        format_quick_scan(writer, results)
    else:
        # Full report
        format_top_picks(writer, results)
        format_wait_watchlist(writer, results, symbol_theme_map, max_items=10)
        format_quick_scan(writer, results)
        format_summary(writer, results)
        format_regime_breakdown(writer, results)
    
    # Footer
    writer.write("")
    writer.write("=" * 110)
    writer.write(f"  END OF REPORT - {len(results)} stocks analyzed")
    writer.write("=" * 110)
    
    # Save to file
    writer.save()

    # Output structured buy zones CSV for downstream (modular pipeline)
    if output_buy_zones_csv:
        actionable = [r for r in results if "BUY" in r.action]
        if actionable:
            entry_date = datetime.now().strftime("%Y-%m-%d")
            rows = []
            for r in actionable:
                entry_price = (r.buy_zone_low + r.buy_zone_high) / 2
                rows.append({
                    "symbol": r.symbol,
                    "entry_price": round(entry_price, 2),
                    "entry_date": entry_date,
                    "stop_loss": round(r.stop_loss, 2),
                    "target_1": round(r.target_1, 2),
                    "target_2": round(r.target_2, 2),
                    "risk_reward": round(r.risk_reward, 2),
                })
            df = pd.DataFrame(rows)
            out_path = output_buy_zones_csv
            if not os.path.isabs(out_path):
                out_path = os.path.join(os.path.dirname(__file__), "..", out_path)
            parent = os.path.dirname(out_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            df.to_csv(out_path, index=False)
            print(f"\n📋 Buy zones CSV: {out_path} ({len(rows)} actionable)")
    
    return output_file if output_file else ""


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Unified Stock Scanner")
    parser.add_argument("symbols", nargs="*", help="Symbols to scan")
    parser.add_argument(
        "--from-picker",
        action="store_true",
        help="Load symbols from fundamental picker output (three_layer_picks.csv)",
    )
    parser.add_argument(
        "--picker-csv",
        type=str,
        default="results/picker/three_layer_picks.csv",
        help="Path to picker output CSV when using --from-picker (default: results/picker/three_layer_picks.csv)",
    )
    parser.add_argument("--theme", type=str, help="Theme from universe")
    parser.add_argument("--all", action="store_true", help="Scan full universe")
    parser.add_argument("--plan", nargs="*", help="Detailed plan for symbols")
    parser.add_argument("--quick", action="store_true", help="Quick output")
    parser.add_argument("--output", "-o", type=str, help="Output file path")
    parser.add_argument("--console", "-c", action="store_true", help="Print to console instead of file")
    parser.add_argument(
        "--output-buy-zones-csv",
        type=str,
        help="Output structured buy zones CSV for downstream (symbol, entry_price, entry_date, stop_loss, target_1, target_2, risk_reward)",
    )
    
    args = parser.parse_args()
    
    universe = load_universe()
    theme_name = None
    
    # Determine symbols
    if args.plan:
        symbols = [s.upper() for s in args.plan]
        plan_mode = True
    else:
        plan_mode = False
        if args.from_picker:
            # Load symbols from fundamental picker output (modular pipeline)
            picker_path = os.path.join(os.path.dirname(__file__), '..', args.picker_csv)
            if not os.path.isfile(picker_path):
                print(f"❌ Picker output not found: {picker_path}")
                print("   Run: python tools/run_three_layer_picker.py [--fundamental-only]")
                return
            df = pd.read_csv(picker_path)
            col = "ticker" if "ticker" in df.columns else df.columns[0]
            symbols = [str(s).upper().strip() for s in df[col].dropna().unique() if str(s).strip()]
            theme_name = "PICKER OUTPUT"
        elif args.symbols:
            symbols = [s.upper() for s in args.symbols]
        elif args.theme:
            if args.theme in universe['themes']:
                symbols = universe['themes'][args.theme]['symbols']
                theme_name = universe['themes'][args.theme].get('name', args.theme)
            else:
                print(f"❌ Theme '{args.theme}' not found!")
                print(f"   Available themes: {', '.join(universe['themes'].keys())}")
                return
        elif args.all:
            symbols = universe.get('all_symbols', [])
            theme_name = "FULL UNIVERSE"
        else:
            # Default: high conviction
            symbols = universe.get('high_conviction', ['NVDA', 'GOOGL', 'MSFT', 'PLTR', 'CRWD'])
            theme_name = "HIGH CONVICTION"
    
    # Run scan
    output_file = run_scan(
        symbols=symbols,
        output_file=args.output,
        to_console=args.console,
        plan_mode=plan_mode,
        quick_mode=args.quick,
        theme_name=theme_name,
        output_buy_zones_csv=getattr(args, "output_buy_zones_csv", None),
    )
    
    # If output to file, show preview
    if output_file and not args.console:
        print(f"\n📄 Preview of top results:")
        buys = []
        with open(output_file, 'r') as f:
            content = f.read()
            # Show first few actionable items
            lines = content.split('\n')
            in_actionable = False
            count = 0
            for line in lines:
                if 'ACTIONABLE' in line:
                    in_actionable = True
                if in_actionable and count < 8:
                    print(line)
                    count += 1


if __name__ == "__main__":
    main()
