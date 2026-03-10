"""
Mean Reversion Entry Signal
===========================

Entry signals for mean reversion strategy.
Works best in ranging/sideways markets.

Entry Criteria:
1. Price at extreme deviation from mean (>2 std)
2. RSI oversold (<30) or overbought (>70)
3. Bollinger Band touch/pierce
4. Volume spike (capitulation)
5. Reversal candlestick pattern
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from ...core.types import Signal, SignalType, Regime, VolatilityLevel, Strategy


@dataclass
class MeanReversionEntryConfig:
    """Configuration for mean reversion entry signals."""
    # Mean calculation
    ma_period: int = 20
    
    # Deviation threshold
    min_deviation_std: float = 1.5  # Min std devs from mean
    ideal_deviation_std: float = 2.0
    
    # Bollinger Bands
    bb_period: int = 20
    bb_std: float = 2.0
    
    # RSI
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    
    # Volume
    volume_ma_period: int = 20
    volume_spike_threshold: float = 1.5
    
    # Signal confidence
    min_confidence: float = 0.60


class MeanReversionEntrySignal:
    """
    Generate entry signals for mean reversion strategy.
    
    Best for:
    - SIDEWAYS regime
    - HIGH volatility (creates opportunities)
    - Range-bound stocks
    
    Example:
        signal_gen = MeanReversionEntrySignal()
        
        # Long when oversold
        signal = signal_gen.generate('COIN', price_data)
        
        if signal and signal.direction == 1:
            # Buy oversold condition
            execute_entry(signal)
    """
    
    def __init__(self, config: Optional[MeanReversionEntryConfig] = None):
        self.config = config or MeanReversionEntryConfig()
    
    def generate(
        self,
        symbol: str,
        data: pd.DataFrame,
        regime: Regime = Regime.UNKNOWN,
        volatility: VolatilityLevel = VolatilityLevel.MEDIUM
    ) -> Optional[Signal]:
        """
        Generate mean reversion entry signal.
        
        Args:
            symbol: Stock symbol
            data: OHLCV DataFrame
            regime: Current market regime
            volatility: Current volatility level
            
        Returns:
            Signal if entry criteria met, None otherwise
        """
        if len(data) < self.config.ma_period + 20:
            return None
        
        # Get price data
        close = data['close'] if 'close' in data.columns else data['Close']
        high = data['high'] if 'high' in data.columns else data['High']
        low = data['low'] if 'low' in data.columns else data['Low']
        open_price = data['open'] if 'open' in data.columns else data['Open']
        volume = data['volume'] if 'volume' in data.columns else data['Volume']
        
        # Calculate indicators
        ma = close.rolling(window=self.config.ma_period).mean()
        std = close.rolling(window=self.config.ma_period).std()
        
        bb_upper = ma + self.config.bb_std * std
        bb_lower = ma - self.config.bb_std * std
        
        rsi = self._calculate_rsi(close, self.config.rsi_period)
        volume_ma = volume.rolling(window=self.config.volume_ma_period).mean()
        
        # Current values
        current_price = close.iloc[-1]
        current_ma = ma.iloc[-1]
        current_std = std.iloc[-1]
        current_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50
        current_bb_upper = bb_upper.iloc[-1]
        current_bb_lower = bb_lower.iloc[-1]
        current_volume = volume.iloc[-1]
        current_volume_ma = volume_ma.iloc[-1]
        
        # Calculate deviation from mean
        if current_std > 0:
            deviation_std = (current_price - current_ma) / current_std
        else:
            deviation_std = 0
        
        # Determine direction
        # Oversold = Long, Overbought = Short (but we focus on long)
        is_oversold = deviation_std <= -self.config.min_deviation_std
        is_overbought = deviation_std >= self.config.min_deviation_std
        
        if not (is_oversold or is_overbought):
            return None  # Not at extreme
        
        direction = 1 if is_oversold else -1  # 1=Long, -1=Short
        
        # Score each criteria
        scores = {}
        reasons = []
        
        # 1. Deviation Extremity (30 points)
        abs_deviation = abs(deviation_std)
        if abs_deviation >= self.config.ideal_deviation_std:
            scores['deviation'] = 30
            reasons.append(f"Extreme deviation ({abs_deviation:.1f} std)")
        elif abs_deviation >= self.config.min_deviation_std:
            scores['deviation'] = 20
            reasons.append(f"Good deviation ({abs_deviation:.1f} std)")
        else:
            scores['deviation'] = 10
        
        # 2. RSI Confirmation (25 points)
        if is_oversold:
            if current_rsi <= 25:
                scores['rsi'] = 25
                reasons.append(f"Deeply oversold (RSI {current_rsi:.0f})")
            elif current_rsi <= self.config.rsi_oversold:
                scores['rsi'] = 20
                reasons.append(f"Oversold (RSI {current_rsi:.0f})")
            elif current_rsi <= 40:
                scores['rsi'] = 10
            else:
                scores['rsi'] = 5
        else:  # Overbought
            if current_rsi >= 75:
                scores['rsi'] = 25
                reasons.append(f"Extremely overbought (RSI {current_rsi:.0f})")
            elif current_rsi >= self.config.rsi_overbought:
                scores['rsi'] = 20
                reasons.append(f"Overbought (RSI {current_rsi:.0f})")
            else:
                scores['rsi'] = 10
        
        # 3. Bollinger Band Touch (20 points)
        if is_oversold:
            touched_lower = current_price <= current_bb_lower
            pierced_lower = low.iloc[-1] < current_bb_lower
            if pierced_lower:
                scores['bb'] = 20
                reasons.append("Pierced lower Bollinger")
            elif touched_lower:
                scores['bb'] = 15
                reasons.append("At lower Bollinger")
            else:
                scores['bb'] = 8
        else:
            touched_upper = current_price >= current_bb_upper
            pierced_upper = high.iloc[-1] > current_bb_upper
            if pierced_upper:
                scores['bb'] = 20
                reasons.append("Pierced upper Bollinger")
            elif touched_upper:
                scores['bb'] = 15
            else:
                scores['bb'] = 8
        
        # 4. Volume Spike (15 points)
        volume_ratio = current_volume / current_volume_ma if current_volume_ma > 0 else 1
        if volume_ratio >= 2.0:
            scores['volume'] = 15
            reasons.append("Capitulation volume")
        elif volume_ratio >= self.config.volume_spike_threshold:
            scores['volume'] = 10
            reasons.append("Elevated volume")
        else:
            scores['volume'] = 5
        
        # 5. Reversal Candle (10 points)
        candle_score = self._check_reversal_candle(
            open_price.iloc[-1],
            high.iloc[-1],
            low.iloc[-1],
            close.iloc[-1],
            is_oversold
        )
        scores['candle'] = candle_score
        if candle_score >= 8:
            reasons.append("Reversal candle")
        
        # Total score
        total_score = sum(scores.values())
        confidence = total_score / 100.0
        
        # Regime adjustment
        if regime == Regime.SIDEWAYS:
            confidence *= 1.2  # Mean reversion works best
        elif regime in [Regime.STRONG_UP, Regime.MODERATE_UP] and is_oversold:
            confidence *= 1.1  # Buying dips in uptrend
        elif regime == Regime.DOWNTREND and is_oversold:
            confidence *= 0.7  # Catching falling knife
        elif regime in [Regime.STRONG_UP, Regime.MODERATE_UP] and is_overbought:
            confidence *= 0.6  # Shorting uptrend risky
        
        # Volatility adjustment
        if volatility == VolatilityLevel.HIGH:
            confidence *= 1.05  # More opportunities
        elif volatility == VolatilityLevel.EXTREME:
            confidence *= 0.8  # Too volatile
        elif volatility == VolatilityLevel.LOW:
            confidence *= 0.9  # Less reversion
        
        confidence = min(1.0, confidence)
        
        if confidence < self.config.min_confidence:
            return None
        
        # Calculate stop loss and target
        atr = self._calculate_atr(high, low, close, 14).iloc[-1]
        
        if is_oversold:
            # Long position
            stop_loss = current_price - atr * 1.5
            target = current_ma  # Target mean
        else:
            # Short position (if allowed)
            stop_loss = current_price + atr * 1.5
            target = current_ma
        
        return Signal(
            symbol=symbol,
            signal_type=SignalType.ENTRY,
            direction=direction,
            price=current_price,
            confidence=confidence,
            stop_loss=stop_loss,
            take_profit=target,
            strategy=Strategy.MEAN_REVERSION,
            regime=regime,
            volatility=volatility,
            metadata={
                'entry_type': 'mean_reversion',
                'scores': scores,
                'reasons': reasons,
                'deviation_std': deviation_std,
                'mean': current_ma,
                'bb_upper': current_bb_upper,
                'bb_lower': current_bb_lower,
                'rsi': current_rsi,
                'volume_ratio': volume_ratio,
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
    
    def _check_reversal_candle(
        self, 
        open_p: float, 
        high: float, 
        low: float, 
        close: float,
        looking_for_bullish: bool
    ) -> int:
        """
        Check for reversal candlestick patterns.
        Returns score 0-10.
        """
        body = close - open_p
        range_size = high - low
        
        if range_size == 0:
            return 3
        
        body_pct = abs(body) / range_size
        
        if looking_for_bullish:
            # Looking for bullish reversal
            if body > 0:
                lower_wick = open_p - low
                if lower_wick > abs(body) * 2:
                    return 10  # Hammer
                elif body_pct > 0.6:
                    return 8  # Strong bullish
                return 5
            else:
                # Doji at support
                if body_pct < 0.1:
                    return 6
                return 2
        else:
            # Looking for bearish reversal
            if body < 0:
                upper_wick = high - open_p
                if upper_wick > abs(body) * 2:
                    return 10  # Shooting star
                elif body_pct > 0.6:
                    return 8  # Strong bearish
                return 5
            else:
                if body_pct < 0.1:
                    return 6  # Doji at resistance
                return 2
