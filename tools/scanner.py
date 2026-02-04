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
    
    # Scan theme
    python tools/scanner.py --theme crypto
    python tools/scanner.py --theme wilson
    
    # Full universe
    python tools/scanner.py --all
    
    # Monday plan (detailed)
    python tools/scanner.py --plan IREN FLNC HOOD
    
    # Custom output file
    python tools/scanner.py AAPL NVDA --output my_scan.txt
    
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
from dataclasses import dataclass, asdict
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
    STAY_CASH = "Stay Cash"


class Action(Enum):
    STRONG_BUY = "🟢 STRONG BUY"
    BUY = "🟡 BUY"
    WAIT = "⚪ WAIT"
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
    
    (Regime.DOWNTREND, VolCategory.LOW): (Strategy.STAY_CASH, 0.00, 0.00, 0.00),
    (Regime.DOWNTREND, VolCategory.MODERATE): (Strategy.STAY_CASH, 0.00, 0.00, 0.00),
    (Regime.DOWNTREND, VolCategory.HIGH): (Strategy.STAY_CASH, 0.00, 0.00, 0.00),
    (Regime.DOWNTREND, VolCategory.ULTRA_HIGH): (Strategy.STAY_CASH, 0.00, 0.00, 0.00),
}


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
    volatility: float
    rsi: float
    atr_pct: float
    dist_ema21: float
    
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
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'stock_universe.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


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
        
        # Momentum
        momentum_6m = (close.iloc[-1] / close.iloc[-126] - 1) * 100 if len(close) >= 126 else 0
        momentum_3m = (close.iloc[-1] / close.iloc[-63] - 1) * 100 if len(close) >= 63 else 0
        
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
        
        # Calculate entry zones
        if regime in [Regime.PARABOLIC, Regime.STRONG_UP]:
            buy_zone_high = price
            buy_zone_low = max(ema9 * 0.98, price - 1.5 * atr)
            stop_loss = price - 2.5 * atr
        elif regime in [Regime.MODERATE_UP, Regime.WEAK_UP]:
            buy_zone_high = ema21 * 1.02
            buy_zone_low = ema21 * 0.98
            stop_loss = ema50 * 0.95
        else:
            buy_zone_high = ema21 * 0.98
            buy_zone_low = ema21 * 0.95
            stop_loss = ema21 * 0.90
        
        # Targets
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
                volatility=volatility,
                rsi=rsi,
                atr_pct=atr_pct,
                dist_ema21=dist_ema21,
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
        # STRICTER CONDITIONS - Be cautious, preserve capital
        
        # 1. AVOID: Downtrend or negative expected value
        if regime == Regime.DOWNTREND:
            action = Action.AVOID.value
            entry_signal = "NO_ENTRY"
            reasoning = f"Downtrend ({momentum_6m:+.1f}% 6M). Wait for regime change."
        elif ev < 0:
            action = Action.AVOID.value
            entry_signal = "NEG_EV"
            reasoning = f"Negative EV ({ev:.2f}%). Risk/reward unfavorable."
        
        # 2. WAIT: Overextended (lower thresholds for caution)
        elif rsi >= 70 or dist_ema21 > 10:
            action = Action.WAIT.value
            entry_signal = "OVEREXTENDED"
            reasoning = f"Overextended (RSI {rsi:.0f}, {dist_ema21:+.1f}% from EMA21). Wait for pullback."
        
        # 3. WAIT: Below buy zone (potential falling knife - DON'T chase)
        elif price < buy_zone_low:
            action = Action.WAIT.value
            entry_signal = "BELOW_ZONE"
            reasoning = f"Below buy zone - wait for stabilization. Could be falling knife."
        
        # 4. WAIT: Sideways regime with weak metrics
        elif regime == Regime.SIDEWAYS and (ev < 0.5 or rr < 1.5):
            action = Action.WAIT.value
            entry_signal = "WEAK_SETUP"
            reasoning = f"Sideways regime with weak EV ({ev:.2f}%) or R:R ({rr:.1f}x)."
        
        # 5. In buy zone - apply strict criteria
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
        
        # 6. Above buy zone - wait for pullback
        else:
            action = Action.WAIT.value
            entry_signal = "ABOVE_ZONE"
            reasoning = f"Above buy zone. Wait for pullback to ${buy_zone_high:.2f}."
        
        # Score (for ranking)
        score = 50
        score += min(20, momentum_6m / 5) if momentum_6m > 0 else max(-20, momentum_6m / 5)
        score += 10 if 30 < rsi < 70 else -5
        score += ev * 2
        score = max(0, min(100, score))
        
        return StockScan(
            symbol=symbol,
            price=price,
            regime=regime.value,
            vol_category=vol_cat.value,
            strategy=strategy.value,
            momentum_6m=momentum_6m,
            momentum_3m=momentum_3m,
            volatility=volatility,
            rsi=rsi,
            atr_pct=atr_pct,
            dist_ema21=dist_ema21,
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
            volume_pullback_data=None
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
    writer.write("=" * 110)
    writer.write(f"  {title}")
    writer.write(f"  Generated: {scan_time}")
    writer.write(f"  Stocks Scanned: {symbol_count}")
    writer.write("=" * 110)


def format_quick_scan(writer: OutputWriter, results: List[StockScan]):
    """Quick summary table."""
    writer.write("")
    writer.write("=" * 110)
    writer.write("  SCAN RESULTS - QUICK VIEW")
    writer.write("=" * 110)
    writer.write("")
    writer.write(f"{'Symbol':<8} {'Price':>10} {'Regime':<12} {'Vol':>6} {'RSI':>5} {'EV':>8} {'Action':<15} {'Entry':>12}")
    writer.write("-" * 110)
    
    for r in results:
        icon = "[STRONG]" if "STRONG" in r.action else ("[BUY]" if "BUY" in r.action else ("[WAIT]" if "WAIT" in r.action else "[AVOID]"))
        writer.write(f"{r.symbol:<8} ${r.price:>9.2f} {r.regime:<12} {r.volatility:>5.0f}% {r.rsi:>4.0f} "
                     f"{r.expected_value:>+7.2f}% {icon:<15} {r.entry_signal:>12}")


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
    print(f"\n{'='*110}")
    print(f"{'Symbol':<8} {'Price':>10} {'Regime':<12} {'Vol':>6} {'RSI':>5} {'EV':>8} {'Action':<15} {'Entry':>12}")
    print("-"*110)
    
    for r in results:
        icon = "🟢" if "STRONG" in r.action else ("🟡" if "BUY" in r.action else ("⚪" if "WAIT" in r.action else "🔴"))
        print(f"{r.symbol:<8} ${r.price:>9.2f} {r.regime:<12} {r.volatility:>5.0f}% {r.rsi:>4.0f} "
              f"{r.expected_value:>+7.2f}% {icon} {r.action.split()[-1]:<12} {r.entry_signal:>12}")


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
    theme_name: Optional[str] = None
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
    
    return output_file if output_file else ""


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Unified Stock Scanner")
    parser.add_argument("symbols", nargs="*", help="Symbols to scan")
    parser.add_argument("--theme", type=str, help="Theme from universe")
    parser.add_argument("--all", action="store_true", help="Scan full universe")
    parser.add_argument("--plan", nargs="*", help="Detailed plan for symbols")
    parser.add_argument("--quick", action="store_true", help="Quick output")
    parser.add_argument("--output", "-o", type=str, help="Output file path")
    parser.add_argument("--console", "-c", action="store_true", help="Print to console instead of file")
    
    args = parser.parse_args()
    
    universe = load_universe()
    theme_name = None
    
    # Determine symbols
    if args.plan:
        symbols = [s.upper() for s in args.plan]
        plan_mode = True
    else:
        plan_mode = False
        if args.symbols:
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
        theme_name=theme_name
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
