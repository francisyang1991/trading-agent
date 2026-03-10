"""
Breakout Entry Signal
=====================

Entry signals for breakout trading strategy.
Works best when price breaks out of consolidation/range.

Entry Criteria:
1. Clear consolidation period (low volatility squeeze)
2. Breakout above resistance with volume
3. Momentum confirmation (RSI, MACD)
4. Not overextended from base
5. Volume surge on breakout
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from ...core.types import Signal, SignalType, Regime, VolatilityLevel, Strategy


@dataclass
class BreakoutEntryConfig:
    """Configuration for breakout entry signals."""
    # Consolidation detection
    consolidation_bars: int = 20
    max_range_pct: float = 10.0  # Max price range during consolidation
    
    # Breakout threshold
    breakout_threshold_pct: float = 1.5  # Min % above resistance
    
    # Volume
    volume_ma_period: int = 20
    min_volume_surge: float = 1.5  # Volume must be 1.5x average
    
    # Bollinger squeeze
    bb_period: int = 20
    bb_std: float = 2.0
    squeeze_threshold: float = 4.0  # Band width % for squeeze
    
    # RSI
    rsi_period: int = 14
    rsi_min: float = 50.0  # Momentum should be positive
    
    # Signal confidence
    min_confidence: float = 0.65


class BreakoutEntrySignal:
    """
    Generate entry signals for breakout strategy.
    
    Best for:
    - Consolidation ending (volatility squeeze)
    - SIDEWAYS regime transitioning to TREND
    - Stocks with strong fundamentals ready to move
    
    Example:
        signal_gen = BreakoutEntrySignal()
        signal = signal_gen.generate('TSLA', price_data)
        
        if signal and signal.confidence > 0.7:
            # Breakout confirmed
            execute_entry(signal)
    """
    
    def __init__(self, config: Optional[BreakoutEntryConfig] = None):
        self.config = config or BreakoutEntryConfig()
    
    def generate(
        self,
        symbol: str,
        data: pd.DataFrame,
        regime: Regime = Regime.UNKNOWN,
        volatility: VolatilityLevel = VolatilityLevel.MEDIUM
    ) -> Optional[Signal]:
        """
        Generate breakout entry signal.
        
        Args:
            symbol: Stock symbol
            data: OHLCV DataFrame
            regime: Current market regime
            volatility: Current volatility level
            
        Returns:
            Signal if entry criteria met, None otherwise
        """
        if len(data) < self.config.consolidation_bars + 10:
            return None
        
        # Get price data
        close = data['close'] if 'close' in data.columns else data['Close']
        high = data['high'] if 'high' in data.columns else data['High']
        low = data['low'] if 'low' in data.columns else data['Low']
        volume = data['volume'] if 'volume' in data.columns else data['Volume']
        
        # Calculate indicators
        rsi = self._calculate_rsi(close, self.config.rsi_period)
        bb_upper, bb_lower, bb_mid, bb_width = self._calculate_bollinger(
            close, self.config.bb_period, self.config.bb_std
        )
        volume_ma = volume.rolling(window=self.config.volume_ma_period).mean()
        
        # Find consolidation range
        consolidation_period = data.iloc[-self.config.consolidation_bars-1:-1]
        cons_high = consolidation_period['high' if 'high' in consolidation_period else 'High'].max()
        cons_low = consolidation_period['low' if 'low' in consolidation_period else 'Low'].min()
        cons_range_pct = (cons_high - cons_low) / cons_low * 100
        
        # Current values
        current_price = close.iloc[-1]
        current_high = high.iloc[-1]
        current_volume = volume.iloc[-1]
        current_volume_ma = volume_ma.iloc[-1]
        current_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50
        current_bb_width = bb_width.iloc[-1] if not pd.isna(bb_width.iloc[-1]) else 10
        
        # Check for breakout
        breakout_price = cons_high * (1 + self.config.breakout_threshold_pct / 100)
        is_breaking_out = current_high >= breakout_price
        
        if not is_breaking_out:
            return None  # No breakout yet
        
        # Score each criteria
        scores = {}
        reasons = []
        
        # 1. Consolidation Quality (25 points)
        if cons_range_pct <= self.config.max_range_pct * 0.6:
            scores['consolidation'] = 25
            reasons.append(f"Tight consolidation ({cons_range_pct:.1f}%)")
        elif cons_range_pct <= self.config.max_range_pct:
            scores['consolidation'] = 20
            reasons.append(f"Good consolidation ({cons_range_pct:.1f}%)")
        else:
            scores['consolidation'] = 10
        
        # 2. Volume Surge (25 points)
        volume_ratio = current_volume / current_volume_ma if current_volume_ma > 0 else 1
        if volume_ratio >= 2.0:
            scores['volume'] = 25
            reasons.append(f"Strong volume surge ({volume_ratio:.1f}x)")
        elif volume_ratio >= self.config.min_volume_surge:
            scores['volume'] = 20
            reasons.append(f"Good volume ({volume_ratio:.1f}x)")
        elif volume_ratio >= 1.2:
            scores['volume'] = 10
        else:
            scores['volume'] = 5
        
        # 3. Bollinger Squeeze (20 points)
        min_bb_width = bb_width.iloc[-self.config.consolidation_bars:].min()
        was_squeezed = min_bb_width <= self.config.squeeze_threshold
        expanding = current_bb_width > bb_width.iloc[-5:].mean()
        
        if was_squeezed and expanding:
            scores['squeeze'] = 20
            reasons.append("Volatility expansion from squeeze")
        elif was_squeezed:
            scores['squeeze'] = 15
        else:
            scores['squeeze'] = 10
        
        # 4. Momentum Confirmation (15 points)
        if current_rsi >= 60:
            scores['momentum'] = 15
            reasons.append(f"Strong momentum (RSI {current_rsi:.0f})")
        elif current_rsi >= self.config.rsi_min:
            scores['momentum'] = 10
        else:
            scores['momentum'] = 5
        
        # 5. Breakout Quality (15 points)
        breakout_strength = (current_price - cons_high) / cons_high * 100
        if breakout_strength >= 3.0:
            scores['breakout'] = 15
            reasons.append(f"Strong breakout ({breakout_strength:.1f}%)")
        elif breakout_strength >= 1.5:
            scores['breakout'] = 12
            reasons.append("Clean breakout")
        else:
            scores['breakout'] = 8
        
        # Total score
        total_score = sum(scores.values())
        confidence = total_score / 100.0
        
        # Regime adjustment
        if regime == Regime.SIDEWAYS:
            confidence *= 1.1  # Breakouts from consolidation
        elif regime in [Regime.STRONG_UP, Regime.PARABOLIC, Regime.MODERATE_UP]:
            confidence *= 1.05  # Continuation breakouts
        elif regime == Regime.DOWNTREND:
            confidence *= 0.7  # Counter-trend breakouts are risky
        
        # Volatility adjustment
        if volatility == VolatilityLevel.LOW:
            confidence *= 1.1  # Best for breakouts
        elif volatility == VolatilityLevel.HIGH:
            confidence *= 0.8  # More false breakouts
        elif volatility == VolatilityLevel.EXTREME:
            confidence *= 0.6
        
        confidence = min(1.0, confidence)
        
        if confidence < self.config.min_confidence:
            return None
        
        # Calculate stop loss and target
        atr = self._calculate_atr(high, low, close, 14).iloc[-1]
        
        # Stop below consolidation low or ATR-based
        stop_loss = max(cons_low - atr * 0.3, current_price - atr * 2)
        
        # Target: measured move (range height from breakout)
        measured_move = cons_high - cons_low
        target = current_price + measured_move
        
        return Signal(
            symbol=symbol,
            signal_type=SignalType.ENTRY,
            direction=1,
            price=current_price,
            confidence=confidence,
            stop_loss=stop_loss,
            take_profit=target,
            strategy=Strategy.TREND_FOLLOWING,
            regime=regime,
            volatility=volatility,
            metadata={
                'entry_type': 'breakout',
                'scores': scores,
                'reasons': reasons,
                'consolidation_high': cons_high,
                'consolidation_low': cons_low,
                'consolidation_range_pct': cons_range_pct,
                'volume_ratio': volume_ratio,
                'bb_width': current_bb_width,
                'was_squeezed': was_squeezed,
                'breakout_strength': breakout_strength,
                'rsi': current_rsi,
                'atr': atr,
            }
        )
    
    def _calculate_rsi(self, close: pd.Series, period: int) -> pd.Series:
        """Calculate RSI indicator."""
        delta = close.diff()
        gain = delta.where(delta > 0, 0).ewm(span=period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(span=period, adjust=False).mean()
        rs = gain / (loss + 1e-10)
        return 100 - (100 / (1 + rs))
    
    def _calculate_bollinger(
        self, 
        close: pd.Series, 
        period: int, 
        std: float
    ) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
        """Calculate Bollinger Bands and width."""
        mid = close.rolling(window=period).mean()
        std_dev = close.rolling(window=period).std()
        upper = mid + std * std_dev
        lower = mid - std * std_dev
        width = (upper - lower) / mid * 100  # Width as % of middle band
        return upper, lower, mid, width
    
    def _calculate_atr(
        self, 
        high: pd.Series, 
        low: pd.Series, 
        close: pd.Series, 
        period: int
    ) -> pd.Series:
        """Calculate ATR."""
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()
