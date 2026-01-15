"""
Pullback Entry Signal
=====================

Entry signals for buying pullbacks in an uptrend.
Works best when trend is established but price pulled back.

Entry Criteria:
1. Overall trend is up (price above 50 EMA)
2. Pullback to support (near 21 EMA or Fibonacci level)
3. RSI showing oversold bounce (30-45 range)
4. Volume declining during pullback
5. Bullish candlestick pattern at support
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from ...core.types import Signal, SignalType, Regime, VolatilityLevel, Strategy


@dataclass
class PullbackEntryConfig:
    """Configuration for pullback entry signals."""
    # EMAs
    ema_trend: int = 50
    ema_support: int = 21
    ema_fast: int = 8
    
    # Pullback depth
    min_pullback_pct: float = 3.0  # Minimum pullback %
    max_pullback_pct: float = 15.0  # Maximum pullback %
    
    # RSI
    rsi_period: int = 14
    rsi_oversold: float = 35.0
    rsi_bounce_max: float = 50.0
    
    # Support proximity
    support_tolerance_pct: float = 2.0
    
    # Signal confidence
    min_confidence: float = 0.6


class PullbackEntrySignal:
    """
    Generate entry signals for pullback buying strategy.
    
    Best for:
    - UPTREND regime (buying dips)
    - MEDIUM volatility
    - Quality stocks with strong fundamentals
    
    Example:
        signal_gen = PullbackEntrySignal()
        signal = signal_gen.generate('AAPL', price_data)
        
        if signal and signal.confidence > 0.65:
            # Good pullback entry
            execute_entry(signal)
    """
    
    def __init__(self, config: Optional[PullbackEntryConfig] = None):
        self.config = config or PullbackEntryConfig()
    
    def generate(
        self,
        symbol: str,
        data: pd.DataFrame,
        regime: Regime = Regime.UNKNOWN,
        volatility: VolatilityLevel = VolatilityLevel.MEDIUM
    ) -> Optional[Signal]:
        """
        Generate pullback entry signal.
        
        Args:
            symbol: Stock symbol
            data: OHLCV DataFrame
            regime: Current market regime
            volatility: Current volatility level
            
        Returns:
            Signal if entry criteria met, None otherwise
        """
        if len(data) < self.config.ema_trend + 20:
            return None
        
        # Get price data
        close = data['close'] if 'close' in data.columns else data['Close']
        high = data['high'] if 'high' in data.columns else data['High']
        low = data['low'] if 'low' in data.columns else data['Low']
        open_price = data['open'] if 'open' in data.columns else data['Open']
        volume = data['volume'] if 'volume' in data.columns else data['Volume']
        
        # Calculate indicators
        ema_trend = close.ewm(span=self.config.ema_trend, adjust=False).mean()
        ema_support = close.ewm(span=self.config.ema_support, adjust=False).mean()
        ema_fast = close.ewm(span=self.config.ema_fast, adjust=False).mean()
        rsi = self._calculate_rsi(close, self.config.rsi_period)
        
        # Current values
        current_price = close.iloc[-1]
        current_ema_trend = ema_trend.iloc[-1]
        current_ema_support = ema_support.iloc[-1]
        current_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50
        
        # Recent high (for pullback measurement)
        lookback = 20
        recent_high = high.iloc[-lookback:].max()
        pullback_pct = (recent_high - current_price) / recent_high * 100
        
        # Score each criteria
        scores = {}
        reasons = []
        
        # 1. Trend Confirmation (25 points)
        # Price should be above long-term EMA (uptrend)
        trend_ok = current_price > current_ema_trend * 0.97  # Allow 3% tolerance
        ema_rising = ema_trend.iloc[-1] > ema_trend.iloc[-10]
        
        if trend_ok and ema_rising:
            scores['trend'] = 25
            reasons.append("Uptrend intact")
        elif trend_ok:
            scores['trend'] = 15
        else:
            scores['trend'] = 0
        
        # 2. Pullback Depth (25 points)
        if self.config.min_pullback_pct <= pullback_pct <= self.config.max_pullback_pct:
            scores['pullback'] = 25
            reasons.append(f"Good pullback depth ({pullback_pct:.1f}%)")
        elif pullback_pct < self.config.min_pullback_pct:
            scores['pullback'] = 5  # Not enough pullback
        elif pullback_pct <= self.config.max_pullback_pct * 1.5:
            scores['pullback'] = 15  # Deeper but acceptable
        else:
            scores['pullback'] = 0  # Too deep, might be broken
        
        # 3. Support Level (20 points)
        dist_to_support = abs(current_price - current_ema_support) / current_price * 100
        
        if dist_to_support <= self.config.support_tolerance_pct:
            scores['support'] = 20
            reasons.append("At support (21 EMA)")
        elif dist_to_support <= self.config.support_tolerance_pct * 2:
            scores['support'] = 15
            reasons.append("Near support")
        else:
            scores['support'] = 5
        
        # 4. RSI Oversold Bounce (15 points)
        rsi_prev = rsi.iloc[-3] if len(rsi) > 3 else 50
        rsi_bouncing = current_rsi > rsi_prev
        
        if current_rsi <= self.config.rsi_oversold:
            scores['rsi'] = 15
            reasons.append(f"Oversold (RSI {current_rsi:.0f})")
        elif current_rsi <= self.config.rsi_bounce_max and rsi_bouncing:
            scores['rsi'] = 12
            reasons.append(f"RSI bouncing ({current_rsi:.0f})")
        else:
            scores['rsi'] = 5
        
        # 5. Bullish Candle Pattern (15 points)
        candle_score = self._check_bullish_candle(
            open_price.iloc[-1], 
            high.iloc[-1], 
            low.iloc[-1], 
            close.iloc[-1]
        )
        scores['candle'] = candle_score
        if candle_score >= 10:
            reasons.append("Bullish candle")
        
        # Total score
        total_score = sum(scores.values())
        confidence = total_score / 100.0
        
        # Regime adjustment
        if regime in [Regime.STRONG_UP, Regime.PARABOLIC, Regime.MODERATE_UP]:
            confidence *= 1.15  # Pullbacks work great in uptrends
        elif regime == Regime.DOWNTREND:
            confidence *= 0.6  # Risky in downtrends
        elif regime == Regime.SIDEWAYS:
            confidence *= 0.9
        
        # Volatility adjustment
        if volatility == VolatilityLevel.MEDIUM:
            confidence *= 1.05  # Ideal
        elif volatility == VolatilityLevel.HIGH:
            confidence *= 0.85
        elif volatility == VolatilityLevel.EXTREME:
            confidence *= 0.6
        
        confidence = min(1.0, confidence)
        
        if confidence < self.config.min_confidence:
            return None
        
        # Calculate stop loss and target
        atr = self._calculate_atr(high, low, close, 14).iloc[-1]
        
        # Stop below recent low or support
        recent_low = low.iloc[-5:].min()
        stop_loss = min(recent_low - atr * 0.5, current_price - atr * 1.5)
        
        # Target: previous high or 2R
        risk = current_price - stop_loss
        target = max(recent_high, current_price + risk * 2)
        
        return Signal(
            symbol=symbol,
            signal_type=SignalType.ENTRY,
            direction=1,
            price=current_price,
            confidence=confidence,
            stop_loss=stop_loss,
            take_profit=target,
            strategy=Strategy.SWING_TRADE,
            regime=regime,
            volatility=volatility,
            metadata={
                'entry_type': 'pullback',
                'scores': scores,
                'reasons': reasons,
                'pullback_pct': pullback_pct,
                'recent_high': recent_high,
                'support_level': current_ema_support,
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
    
    def _check_bullish_candle(
        self, 
        open_p: float, 
        high: float, 
        low: float, 
        close: float
    ) -> int:
        """
        Check for bullish candlestick patterns.
        Returns score 0-15.
        """
        body = close - open_p
        range_size = high - low
        
        if range_size == 0:
            return 5
        
        body_pct = abs(body) / range_size
        
        # Bullish candle (close > open)
        if body > 0:
            # Hammer or bullish engulfing
            lower_wick = open_p - low
            upper_wick = high - close
            
            if lower_wick > body * 2 and upper_wick < body * 0.5:
                return 15  # Hammer
            elif body_pct > 0.6:
                return 12  # Strong bullish candle
            else:
                return 8
        else:
            # Doji or indecision
            if body_pct < 0.1:
                return 5  # Doji at support can be reversal
            return 3  # Bearish candle
