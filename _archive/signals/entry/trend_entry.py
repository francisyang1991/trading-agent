"""
Trend Following Entry Signal
============================

Entry signals for trend-following strategy.
Works best in TRENDING regime with moderate volatility.

Entry Criteria:
1. Price above rising EMA stack (8 > 21 > 50)
2. ADX > 25 (strong trend)
3. RSI between 40-70 (not overbought)
4. Volume confirmation
5. Recent higher highs / higher lows
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from ...core.types import Signal, SignalType, Regime, VolatilityLevel, Strategy


@dataclass
class TrendEntryConfig:
    """Configuration for trend entry signals."""
    # EMA periods
    ema_fast: int = 8
    ema_mid: int = 21
    ema_slow: int = 50
    
    # Trend strength
    adx_period: int = 14
    min_adx: float = 25.0
    
    # RSI bounds
    rsi_period: int = 14
    rsi_min: float = 40.0
    rsi_max: float = 70.0
    
    # Volume
    volume_ma_period: int = 20
    min_volume_ratio: float = 0.8
    
    # Signal confidence
    min_confidence: float = 0.6


class TrendEntrySignal:
    """
    Generate entry signals for trend-following strategy.
    
    Best for:
    - UPTREND regime
    - LOW to MEDIUM volatility
    - Strong momentum stocks
    
    Example:
        signal_gen = TrendEntrySignal()
        signal = signal_gen.generate('NVDA', price_data)
        
        if signal and signal.confidence > 0.7:
            # Strong trend entry opportunity
            execute_entry(signal)
    """
    
    def __init__(self, config: Optional[TrendEntryConfig] = None):
        self.config = config or TrendEntryConfig()
    
    def generate(
        self,
        symbol: str,
        data: pd.DataFrame,
        regime: Regime = Regime.UNKNOWN,
        volatility: VolatilityLevel = VolatilityLevel.MEDIUM
    ) -> Optional[Signal]:
        """
        Generate trend entry signal.
        
        Args:
            symbol: Stock symbol
            data: OHLCV DataFrame
            regime: Current market regime
            volatility: Current volatility level
            
        Returns:
            Signal if entry criteria met, None otherwise
        """
        if len(data) < self.config.ema_slow + 10:
            return None
        
        # Calculate indicators
        close = data['close'] if 'close' in data.columns else data['Close']
        high = data['high'] if 'high' in data.columns else data['High']
        low = data['low'] if 'low' in data.columns else data['Low']
        volume = data['volume'] if 'volume' in data.columns else data['Volume']
        
        # EMAs
        ema_fast = close.ewm(span=self.config.ema_fast, adjust=False).mean()
        ema_mid = close.ewm(span=self.config.ema_mid, adjust=False).mean()
        ema_slow = close.ewm(span=self.config.ema_slow, adjust=False).mean()
        
        # ADX
        adx = self._calculate_adx(high, low, close, self.config.adx_period)
        
        # RSI
        rsi = self._calculate_rsi(close, self.config.rsi_period)
        
        # Volume MA
        volume_ma = volume.rolling(window=self.config.volume_ma_period).mean()
        
        # Current values
        current_price = close.iloc[-1]
        current_ema_fast = ema_fast.iloc[-1]
        current_ema_mid = ema_mid.iloc[-1]
        current_ema_slow = ema_slow.iloc[-1]
        current_adx = adx.iloc[-1] if not pd.isna(adx.iloc[-1]) else 0
        current_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50
        current_volume = volume.iloc[-1]
        current_volume_ma = volume_ma.iloc[-1]
        
        # Score each criteria
        scores = {}
        reasons = []
        
        # 1. EMA Stack (25 points)
        ema_aligned = (current_ema_fast > current_ema_mid > current_ema_slow)
        price_above_emas = current_price > current_ema_fast
        
        if ema_aligned and price_above_emas:
            scores['ema_stack'] = 25
            reasons.append("Price above rising EMA stack")
        elif ema_aligned:
            scores['ema_stack'] = 15
            reasons.append("EMA stack aligned")
        else:
            scores['ema_stack'] = 0
        
        # 2. ADX Trend Strength (25 points)
        if current_adx >= 30:
            scores['adx'] = 25
            reasons.append(f"Strong trend (ADX {current_adx:.1f})")
        elif current_adx >= self.config.min_adx:
            scores['adx'] = 20
            reasons.append(f"Good trend (ADX {current_adx:.1f})")
        elif current_adx >= 20:
            scores['adx'] = 10
        else:
            scores['adx'] = 0
        
        # 3. RSI Zone (20 points)
        if self.config.rsi_min <= current_rsi <= self.config.rsi_max:
            scores['rsi'] = 20
            reasons.append(f"RSI in zone ({current_rsi:.1f})")
        elif current_rsi < self.config.rsi_min:
            scores['rsi'] = 10  # Might be too weak
        else:
            scores['rsi'] = 5  # Overbought risk
        
        # 4. Volume Confirmation (15 points)
        volume_ratio = current_volume / current_volume_ma if current_volume_ma > 0 else 1
        if volume_ratio >= 1.2:
            scores['volume'] = 15
            reasons.append("Strong volume")
        elif volume_ratio >= self.config.min_volume_ratio:
            scores['volume'] = 10
        else:
            scores['volume'] = 5
        
        # 5. Higher Highs/Lows (15 points)
        hh_hl = self._check_higher_highs_lows(high, low, lookback=10)
        if hh_hl:
            scores['structure'] = 15
            reasons.append("Making higher highs/lows")
        else:
            scores['structure'] = 5
        
        # Total score
        total_score = sum(scores.values())
        confidence = total_score / 100.0
        
        # Apply regime adjustment
        if regime in [Regime.STRONG_UP, Regime.PARABOLIC, Regime.MODERATE_UP]:
            confidence *= 1.1
        elif regime == Regime.DOWNTREND:
            confidence *= 0.7
        elif regime == Regime.SIDEWAYS:
            confidence *= 0.85
        
        # Volatility adjustment
        if volatility == VolatilityLevel.LOW:
            confidence *= 1.05
        elif volatility == VolatilityLevel.HIGH:
            confidence *= 0.9
        elif volatility == VolatilityLevel.EXTREME:
            confidence *= 0.7
        
        confidence = min(1.0, confidence)
        
        # Only generate signal if above threshold
        if confidence < self.config.min_confidence:
            return None
        
        # Calculate stop loss and target
        atr = self._calculate_atr(high, low, close, 14).iloc[-1]
        
        stop_loss = current_price - (2.0 * atr)
        target = current_price + (3.0 * atr)
        
        return Signal(
            symbol=symbol,
            signal_type=SignalType.ENTRY,
            direction=1,  # Long
            price=current_price,
            confidence=confidence,
            stop_loss=stop_loss,
            take_profit=target,
            strategy=Strategy.TREND_FOLLOWING,
            regime=regime,
            volatility=volatility,
            metadata={
                'entry_type': 'trend',
                'scores': scores,
                'reasons': reasons,
                'ema_fast': current_ema_fast,
                'ema_mid': current_ema_mid,
                'ema_slow': current_ema_slow,
                'adx': current_adx,
                'rsi': current_rsi,
                'volume_ratio': volume_ratio,
                'atr': atr,
            }
        )
    
    def _calculate_adx(
        self, 
        high: pd.Series, 
        low: pd.Series, 
        close: pd.Series, 
        period: int
    ) -> pd.Series:
        """Calculate ADX indicator."""
        # True Range
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.ewm(span=period, adjust=False).mean()
        
        # Directional Movement
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        
        plus_di = 100 * pd.Series(plus_dm).ewm(span=period, adjust=False).mean() / atr
        minus_di = 100 * pd.Series(minus_dm).ewm(span=period, adjust=False).mean() / atr
        
        # ADX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.ewm(span=period, adjust=False).mean()
        
        return adx
    
    def _calculate_rsi(self, close: pd.Series, period: int) -> pd.Series:
        """Calculate RSI indicator."""
        delta = close.diff()
        gain = delta.where(delta > 0, 0).ewm(span=period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(span=period, adjust=False).mean()
        rs = gain / (loss + 1e-10)
        return 100 - (100 / (1 + rs))
    
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
    
    def _check_higher_highs_lows(
        self, 
        high: pd.Series, 
        low: pd.Series, 
        lookback: int
    ) -> bool:
        """Check if making higher highs and higher lows."""
        recent_high = high.iloc[-lookback:]
        recent_low = low.iloc[-lookback:]
        
        # Find swing points
        mid = lookback // 2
        
        prev_high = recent_high.iloc[:mid].max()
        curr_high = recent_high.iloc[mid:].max()
        
        prev_low = recent_low.iloc[:mid].min()
        curr_low = recent_low.iloc[mid:].min()
        
        return curr_high > prev_high and curr_low > prev_low
