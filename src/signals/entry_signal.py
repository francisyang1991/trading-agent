"""
Entry Signal Generator
Specialized entry signal detection following the design philosophy.

Entry Model: Big Rally → Pullback → Consolidation → Volume Breakout → EMA Retest → Continue
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import pandas as pd
import numpy as np
from loguru import logger

from ..indicators.vpes import VPES, VPESResult
from ..indicators.trend import TrendIndicators, TrendDirection, EMAAlignment
from ..indicators.volume import VolumeIndicators, VolumeState
from ..classifier.stock_classifier import ClassificationResult, StockType


@dataclass
class EntrySignal:
    """Entry signal details."""
    is_valid: bool
    signal_type: str              # "breakout", "pullback", "retest"
    confidence: float
    
    # Entry specifics
    entry_price: float
    suggested_stop: float
    suggested_target: float
    risk_reward: float
    
    # Confirmation details
    ema_confirmed: bool
    vpes_confirmed: bool
    volume_confirmed: bool
    multi_tf_confirmed: bool
    
    reasoning: str


class EntrySignalGenerator:
    """
    Generates entry signals based on the ideal entry model.
    """
    
    def __init__(
        self,
        trend_indicators: Optional[TrendIndicators] = None,
        vpes: Optional[VPES] = None,
        volume_indicators: Optional[VolumeIndicators] = None,
        min_vpes: float = 0.005,
        min_volume_ratio: float = 1.5
    ):
        """
        Initialize entry signal generator.
        
        Args:
            trend_indicators: Trend calculator
            vpes: VPES calculator
            volume_indicators: Volume calculator
            min_vpes: Minimum VPES for valid signal
            min_volume_ratio: Minimum volume ratio for confirmation
        """
        self.trend = trend_indicators or TrendIndicators()
        self.vpes = vpes or VPES()
        self.volume = volume_indicators or VolumeIndicators()
        
        self.min_vpes = min_vpes
        self.min_volume_ratio = min_volume_ratio
    
    def generate(
        self,
        symbol: str,
        data: pd.DataFrame,
        classification: ClassificationResult,
        multi_tf_data: Optional[Dict[str, pd.DataFrame]] = None
    ) -> EntrySignal:
        """
        Generate entry signal.
        
        Args:
            symbol: Stock ticker
            data: OHLCV data
            classification: Stock classification
            multi_tf_data: Multi-timeframe data
            
        Returns:
            EntrySignal with details
        """
        if data.empty or not classification.allow_trend_trade:
            return self._no_entry_signal("Not allowed to trade")
        
        current_price = data['close'].iloc[-1]
        
        # Check different entry scenarios
        breakout_signal = self._check_breakout_entry(data)
        pullback_signal = self._check_pullback_entry(data)
        retest_signal = self._check_ema_retest_entry(data)
        
        # Select best signal
        signals = [
            ('breakout', breakout_signal),
            ('pullback', pullback_signal),
            ('retest', retest_signal)
        ]
        
        best_signal = max(signals, key=lambda x: x[1]['score'])
        
        if best_signal[1]['score'] < 0.5:
            return self._no_entry_signal("No valid entry pattern")
        
        signal_type = best_signal[0]
        signal_data = best_signal[1]
        
        # Calculate stops and targets
        stop_price = self._calculate_stop(data, signal_type)
        target_price = self._calculate_target(data, signal_type, current_price, stop_price)
        
        risk = current_price - stop_price
        reward = target_price - current_price
        risk_reward = reward / risk if risk > 0 else 0
        
        # Check confirmations
        ema_confirmed = signal_data.get('ema_ok', False)
        vpes_result = self.vpes.get_current_vpes(data)
        vpes_confirmed = vpes_result.vpes >= self.min_vpes
        volume_confirmed = vpes_result.volume_ratio >= self.min_volume_ratio
        multi_tf_confirmed = self._check_multi_tf_alignment(multi_tf_data)
        
        # Calculate confidence
        confirmations = sum([ema_confirmed, vpes_confirmed, volume_confirmed, multi_tf_confirmed])
        confidence = signal_data['score'] * (0.5 + 0.125 * confirmations)
        
        return EntrySignal(
            is_valid=True,
            signal_type=signal_type,
            confidence=min(confidence, 1.0),
            entry_price=current_price,
            suggested_stop=stop_price,
            suggested_target=target_price,
            risk_reward=risk_reward,
            ema_confirmed=ema_confirmed,
            vpes_confirmed=vpes_confirmed,
            volume_confirmed=volume_confirmed,
            multi_tf_confirmed=multi_tf_confirmed,
            reasoning=signal_data.get('reasoning', '')
        )
    
    def _check_breakout_entry(self, data: pd.DataFrame) -> Dict:
        """Check for volume breakout entry."""
        result = {'score': 0.0, 'ema_ok': False, 'reasoning': ''}
        
        if len(data) < 20:
            return result
        
        current_close = data['close'].iloc[-1]
        recent_high = data['high'].tail(20).max()
        
        # Check breakout
        is_breakout = current_close >= recent_high * 0.99
        
        if not is_breakout:
            return result
        
        # Check volume
        vpes_result = self.vpes.get_current_vpes(data)
        
        if vpes_result.volume_ratio < self.min_volume_ratio:
            return result
        
        # Check EMA
        ema_struct = self.trend.get_ema_structure(data)
        ema_ok = ema_struct.alignment in [EMAAlignment.BULLISH, EMAAlignment.MIXED]
        
        # Score
        score = 0.5  # Base for breakout
        
        if vpes_result.vpes >= 0.02:
            score += 0.3
        elif vpes_result.vpes >= 0.01:
            score += 0.2
        
        if ema_ok:
            score += 0.2
        
        result['score'] = min(score, 1.0)
        result['ema_ok'] = ema_ok
        result['reasoning'] = f"Breakout above ${recent_high:.2f} with volume {vpes_result.volume_ratio:.1f}x"
        
        return result
    
    def _check_pullback_entry(self, data: pd.DataFrame) -> Dict:
        """Check for pullback/consolidation entry."""
        result = {'score': 0.0, 'ema_ok': False, 'reasoning': ''}
        
        if len(data) < 30:
            return result
        
        # Look for prior rally followed by pullback
        lookback = data.tail(30)
        
        # Find highest point
        high_idx = lookback['high'].idxmax()
        high_price = lookback.loc[high_idx, 'high']
        
        # Check pullback from high
        current_close = data['close'].iloc[-1]
        pullback_pct = (high_price - current_close) / high_price
        
        # Ideal pullback: 3-10%
        if pullback_pct < 0.03 or pullback_pct > 0.15:
            return result
        
        # Check volume contraction during pullback
        vol_analysis = self.volume.get_volume_analysis(data)
        volume_contracting = vol_analysis.trend_volume in ['decreasing', 'stable']
        
        # Check EMA support
        ema_struct = self.trend.get_ema_structure(data)
        near_ema = abs(ema_struct.price_vs_fast) < 3 or abs(ema_struct.price_vs_slow) < 3
        ema_ok = ema_struct.alignment != EMAAlignment.BEARISH and near_ema
        
        # Score
        score = 0.4  # Base for pullback
        
        if volume_contracting:
            score += 0.3
        
        if ema_ok:
            score += 0.3
        
        result['score'] = min(score, 1.0)
        result['ema_ok'] = ema_ok
        result['reasoning'] = f"Pullback {pullback_pct*100:.1f}% from ${high_price:.2f}"
        
        return result
    
    def _check_ema_retest_entry(self, data: pd.DataFrame) -> Dict:
        """Check for EMA retest entry."""
        result = {'score': 0.0, 'ema_ok': False, 'reasoning': ''}
        
        # Check EMA20 retest
        is_retesting_20, quality_20 = self.trend.detect_ema_retest(data, "fast", 2.0)
        
        # Check EMA50 retest
        is_retesting_50, quality_50 = self.trend.detect_ema_retest(data, "slow", 2.0)
        
        if not (is_retesting_20 or is_retesting_50):
            return result
        
        # Prefer EMA20 retest in uptrend
        ema_struct = self.trend.get_ema_structure(data)
        
        if is_retesting_20:
            ema_name = "EMA20"
            quality = quality_20
            ema_value = ema_struct.ema_fast
        else:
            ema_name = "EMA50"
            quality = quality_50
            ema_value = ema_struct.ema_slow
        
        # Check trend is intact
        trend_state = self.trend.get_trend_state(data)
        trend_ok = trend_state.direction in [TrendDirection.UP, TrendDirection.STRONG_UP]
        ema_ok = ema_struct.alignment == EMAAlignment.BULLISH
        
        if not trend_ok:
            return result
        
        # Check for bounce confirmation (current bar up)
        current_bar = data.iloc[-1]
        bar_up = current_bar['close'] > current_bar['open']
        
        # Score
        score = 0.5  # Base for retest
        
        if quality == 'exact':
            score += 0.2
        elif quality == 'close':
            score += 0.1
        
        if bar_up:
            score += 0.2
        
        if ema_ok:
            score += 0.1
        
        result['score'] = min(score, 1.0)
        result['ema_ok'] = ema_ok
        result['reasoning'] = f"Retest of {ema_name} at ${ema_value:.2f} ({quality})"
        
        return result
    
    def _calculate_stop(self, data: pd.DataFrame, signal_type: str) -> float:
        """Calculate suggested stop loss."""
        # Find recent swing low
        swing_lows = self.trend.find_swing_lows(data.tail(30))
        
        if swing_lows:
            recent_swing_low = swing_lows[-1][1]
        else:
            recent_swing_low = data['low'].tail(10).min()
        
        # Stop below swing low with buffer
        stop = recent_swing_low * 0.99
        
        # Alternative: ATR-based stop
        atr = data['high'].tail(14) - data['low'].tail(14)
        atr_value = atr.mean()
        current_price = data['close'].iloc[-1]
        atr_stop = current_price - 2 * atr_value
        
        # Use more conservative (lower) stop
        return max(stop, atr_stop)
    
    def _calculate_target(
        self,
        data: pd.DataFrame,
        signal_type: str,
        entry_price: float,
        stop_price: float
    ) -> float:
        """Calculate suggested price target."""
        risk = entry_price - stop_price
        
        # Minimum 2:1 reward to risk
        min_target = entry_price + 2 * risk
        
        # Find resistance levels
        swing_highs = self.trend.find_swing_highs(data.tail(60))
        
        if swing_highs:
            # Find nearest resistance above entry
            resistances = [sh[1] for sh in swing_highs if sh[1] > entry_price]
            if resistances:
                nearest_resistance = min(resistances)
                return max(min_target, nearest_resistance)
        
        # Default: 3:1 risk reward
        return entry_price + 3 * risk
    
    def _check_multi_tf_alignment(
        self,
        multi_tf_data: Optional[Dict[str, pd.DataFrame]]
    ) -> bool:
        """Check if multiple timeframes are aligned bullish."""
        if not multi_tf_data:
            return False
        
        aligned_count = 0
        
        for tf, data in multi_tf_data.items():
            if data.empty:
                continue
            
            trend_state = self.trend.get_trend_state(data)
            
            if trend_state.direction in [TrendDirection.UP, TrendDirection.STRONG_UP]:
                aligned_count += 1
        
        # Need at least 3 timeframes aligned
        return aligned_count >= 3
    
    def _no_entry_signal(self, reason: str) -> EntrySignal:
        """Create no-entry signal."""
        return EntrySignal(
            is_valid=False,
            signal_type="none",
            confidence=0.0,
            entry_price=0.0,
            suggested_stop=0.0,
            suggested_target=0.0,
            risk_reward=0.0,
            ema_confirmed=False,
            vpes_confirmed=False,
            volume_confirmed=False,
            multi_tf_confirmed=False,
            reasoning=reason
        )
