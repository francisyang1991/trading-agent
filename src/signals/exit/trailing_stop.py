"""
Trailing Stop Exit Signal
=========================

Dynamic stop loss that trails price as it moves in your favor.

Methods:
1. ATR-based trailing (2-3 ATR from high)
2. Percentage trailing (e.g., 5% from high)
3. Chandelier exit
4. Parabolic SAR style
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass

from ...core.types import Signal, SignalType, Position, Regime, VolatilityLevel, Strategy


@dataclass 
class TrailingStopConfig:
    """Configuration for trailing stop."""
    # ATR-based
    atr_period: int = 14
    atr_multiplier: float = 2.5
    
    # Percentage-based
    trail_pct: float = 5.0
    
    # Method selection
    method: str = 'atr'  # 'atr', 'percent', 'chandelier'
    
    # Activation
    activate_at_r: float = 1.0  # Start trailing after 1R profit
    
    # Ratchet (only move stop up)
    ratchet: bool = True


class TrailingStopExit:
    """
    Generate exit signals using trailing stop.
    
    Example:
        trailing = TrailingStopExit()
        
        # Update trailing stop
        new_stop = trailing.update_stop(
            position, current_price, price_data
        )
        
        # Check if stopped out
        signal = trailing.check(position, current_price, price_data)
    """
    
    def __init__(self, config: Optional[TrailingStopConfig] = None):
        self.config = config or TrailingStopConfig()
        self._high_water_marks: Dict[str, float] = {}
        self._trailing_stops: Dict[str, float] = {}
    
    def check(
        self,
        position: Position,
        current_price: float,
        data: pd.DataFrame
    ) -> Optional[Signal]:
        """
        Check if trailing stop is hit.
        
        Args:
            position: Current position
            current_price: Current market price
            data: OHLCV DataFrame
            
        Returns:
            Exit signal if stop hit, None otherwise
        """
        # Get or calculate trailing stop
        trail_stop = self.update_stop(position, current_price, data)
        
        if trail_stop is None:
            return None
        
        # Check if price hit trailing stop
        if position.direction == 1:  # Long
            if current_price <= trail_stop:
                return self._create_exit_signal(
                    position, current_price, trail_stop, 'trailing_stop_hit'
                )
        else:  # Short
            if current_price >= trail_stop:
                return self._create_exit_signal(
                    position, current_price, trail_stop, 'trailing_stop_hit'
                )
        
        return None
    
    def update_stop(
        self,
        position: Position,
        current_price: float,
        data: pd.DataFrame
    ) -> Optional[float]:
        """
        Update trailing stop level.
        
        Returns new stop level.
        """
        symbol = position.symbol
        entry_price = position.entry_price
        initial_stop = position.stop_loss or (entry_price * 0.95)
        
        # Calculate profit
        risk = abs(entry_price - initial_stop)
        profit = (current_price - entry_price) * position.direction
        profit_r = profit / risk if risk > 0 else 0
        
        # Only activate trailing after threshold
        if profit_r < self.config.activate_at_r:
            return initial_stop
        
        # Track high water mark
        if symbol not in self._high_water_marks:
            self._high_water_marks[symbol] = current_price
        else:
            if position.direction == 1:
                self._high_water_marks[symbol] = max(
                    self._high_water_marks[symbol], 
                    current_price
                )
            else:
                self._high_water_marks[symbol] = min(
                    self._high_water_marks[symbol],
                    current_price
                )
        
        hwm = self._high_water_marks[symbol]
        
        # Calculate new trailing stop based on method
        if self.config.method == 'atr':
            new_stop = self._calculate_atr_stop(data, hwm, position.direction)
        elif self.config.method == 'percent':
            new_stop = self._calculate_percent_stop(hwm, position.direction)
        else:  # chandelier
            new_stop = self._calculate_chandelier_stop(data, hwm, position.direction)
        
        # Ratchet: only move stop in favorable direction
        if self.config.ratchet and symbol in self._trailing_stops:
            old_stop = self._trailing_stops[symbol]
            if position.direction == 1:
                new_stop = max(new_stop, old_stop)
            else:
                new_stop = min(new_stop, old_stop)
        
        # Never move stop below initial
        if position.direction == 1:
            new_stop = max(new_stop, initial_stop)
        else:
            new_stop = min(new_stop, initial_stop)
        
        self._trailing_stops[symbol] = new_stop
        return new_stop
    
    def _calculate_atr_stop(
        self, 
        data: pd.DataFrame, 
        hwm: float, 
        direction: int
    ) -> float:
        """Calculate ATR-based trailing stop."""
        close = data['close'] if 'close' in data.columns else data['Close']
        high = data['high'] if 'high' in data.columns else data['High']
        low = data['low'] if 'low' in data.columns else data['Low']
        
        # ATR calculation
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.ewm(span=self.config.atr_period, adjust=False).mean().iloc[-1]
        
        if direction == 1:  # Long
            return hwm - (atr * self.config.atr_multiplier)
        else:  # Short
            return hwm + (atr * self.config.atr_multiplier)
    
    def _calculate_percent_stop(self, hwm: float, direction: int) -> float:
        """Calculate percentage-based trailing stop."""
        if direction == 1:
            return hwm * (1 - self.config.trail_pct / 100)
        else:
            return hwm * (1 + self.config.trail_pct / 100)
    
    def _calculate_chandelier_stop(
        self, 
        data: pd.DataFrame, 
        hwm: float, 
        direction: int
    ) -> float:
        """Calculate Chandelier exit stop."""
        high = data['high'] if 'high' in data.columns else data['High']
        low = data['low'] if 'low' in data.columns else data['Low']
        close = data['close'] if 'close' in data.columns else data['Close']
        
        # ATR
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.ewm(span=self.config.atr_period, adjust=False).mean().iloc[-1]
        
        # Chandelier uses highest high / lowest low
        period = self.config.atr_period
        
        if direction == 1:
            highest = high.iloc[-period:].max()
            return highest - (atr * self.config.atr_multiplier)
        else:
            lowest = low.iloc[-period:].min()
            return lowest + (atr * self.config.atr_multiplier)
    
    def _create_exit_signal(
        self,
        position: Position,
        current_price: float,
        stop_level: float,
        reason: str
    ) -> Signal:
        """Create exit signal."""
        profit_pct = (current_price - position.entry_price) / position.entry_price * 100
        
        return Signal(
            symbol=position.symbol,
            signal_type=SignalType.EXIT,
            direction=-position.direction,
            price=current_price,
            confidence=0.9,  # High confidence for stop hits
            stop_loss=stop_level,
            strategy=position.strategy,
            metadata={
                'exit_type': 'trailing_stop',
                'exit_reason': reason,
                'exit_pct': 1.0,  # Full exit on stop
                'stop_level': stop_level,
                'profit_pct': profit_pct,
                'entry_price': position.entry_price,
            }
        )
    
    def reset(self, symbol: str):
        """Reset tracking for a symbol (after position closed)."""
        if symbol in self._high_water_marks:
            del self._high_water_marks[symbol]
        if symbol in self._trailing_stops:
            del self._trailing_stops[symbol]
