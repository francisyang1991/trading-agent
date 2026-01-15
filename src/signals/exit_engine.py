"""
Exit Engine
===========

Orchestrates all exit signal generators.
Manages stop losses, profit targets, and time exits.
"""

import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass

from ..core.types import Signal, SignalType, Position, Regime, VolatilityLevel, Strategy
from .exit.profit_target import ProfitTargetExit
from .exit.trailing_stop import TrailingStopExit
from .exit.time_exit import TimeBasedExit


@dataclass
class ExitEngineConfig:
    """Configuration for exit engine."""
    # Enable/disable exit types
    enable_profit_target: bool = True
    enable_trailing_stop: bool = True
    enable_time_exit: bool = True
    enable_hard_stop: bool = True
    
    # Hard stop (initial stop loss)
    hard_stop_enabled: bool = True
    
    # Priority (which exit to use if multiple trigger)
    # Higher priority = checked first
    priority_order: List[str] = None


class ExitEngine:
    """
    Exit Signal Engine - Manages all exit logic for positions.
    
    Features:
    - Multiple exit strategies (target, trailing, time)
    - Hard stop loss management
    - Partial exit support
    - Exit signal ranking
    
    Example:
        engine = ExitEngine()
        
        # Check all exit conditions for position
        signal = engine.check_exit(
            position=my_position,
            current_price=100.0,
            data=price_data
        )
        
        if signal:
            execute_exit(signal)
    """
    
    def __init__(self, config: Optional[ExitEngineConfig] = None):
        self.config = config or ExitEngineConfig()
        if self.config.priority_order is None:
            self.config.priority_order = ['hard_stop', 'trailing_stop', 'profit_target', 'time_exit']
        
        # Initialize exit generators
        self.profit_target = ProfitTargetExit()
        self.trailing_stop = TrailingStopExit()
        self.time_exit = TimeBasedExit()
    
    def check_exit(
        self,
        position: Position,
        current_price: float,
        data: pd.DataFrame,
        current_time: Optional[datetime] = None,
        earnings_date: Optional[datetime] = None
    ) -> Optional[Signal]:
        """
        Check all exit conditions for a position.
        
        Args:
            position: Current position
            current_price: Current market price
            data: OHLCV DataFrame
            current_time: Current datetime
            earnings_date: Optional upcoming earnings date
            
        Returns:
            Exit signal if any condition triggered, None otherwise
        """
        if current_time is None:
            current_time = datetime.now()
        
        signals = []
        
        # Check exits in priority order
        for exit_type in self.config.priority_order:
            signal = None
            
            if exit_type == 'hard_stop' and self.config.enable_hard_stop:
                signal = self._check_hard_stop(position, current_price)
            
            elif exit_type == 'trailing_stop' and self.config.enable_trailing_stop:
                signal = self.trailing_stop.check(position, current_price, data)
            
            elif exit_type == 'profit_target' and self.config.enable_profit_target:
                signal = self.profit_target.check(position, current_price, data)
            
            elif exit_type == 'time_exit' and self.config.enable_time_exit:
                signal = self.time_exit.check(
                    position, current_time, current_price, earnings_date
                )
            
            if signal:
                # Hard stop and trailing stop get immediate execution
                if exit_type in ['hard_stop', 'trailing_stop']:
                    return signal
                signals.append(signal)
        
        # Return highest confidence signal
        if signals:
            return max(signals, key=lambda s: s.confidence)
        
        return None
    
    def check_exits_batch(
        self,
        positions: List[Position],
        prices: Dict[str, float],
        data_dict: Dict[str, pd.DataFrame],
        current_time: Optional[datetime] = None
    ) -> Dict[str, Signal]:
        """
        Check exits for multiple positions.
        
        Args:
            positions: List of positions
            prices: Current prices by symbol
            data_dict: Price data by symbol
            current_time: Current datetime
            
        Returns:
            Dictionary of symbol -> exit signal
        """
        exit_signals = {}
        
        for position in positions:
            symbol = position.symbol
            
            if symbol not in prices or symbol not in data_dict:
                continue
            
            signal = self.check_exit(
                position=position,
                current_price=prices[symbol],
                data=data_dict[symbol],
                current_time=current_time
            )
            
            if signal:
                exit_signals[symbol] = signal
        
        return exit_signals
    
    def _check_hard_stop(
        self,
        position: Position,
        current_price: float
    ) -> Optional[Signal]:
        """Check if hard stop loss is hit."""
        if position.stop_loss is None:
            return None
        
        # Long position
        if position.direction == 1:
            if current_price <= position.stop_loss:
                return self._create_stop_signal(
                    position, current_price, position.stop_loss, 'hard_stop_hit'
                )
        # Short position
        else:
            if current_price >= position.stop_loss:
                return self._create_stop_signal(
                    position, current_price, position.stop_loss, 'hard_stop_hit'
                )
        
        return None
    
    def _create_stop_signal(
        self,
        position: Position,
        current_price: float,
        stop_level: float,
        reason: str
    ) -> Signal:
        """Create stop loss exit signal."""
        profit_pct = (current_price - position.entry_price) / position.entry_price * 100
        
        return Signal(
            symbol=position.symbol,
            signal_type=SignalType.EXIT,
            direction=-position.direction,
            price=current_price,
            confidence=1.0,  # Maximum confidence for stop hits
            stop_loss=stop_level,
            strategy=position.strategy,
            metadata={
                'exit_type': 'stop_loss',
                'exit_reason': reason,
                'exit_pct': 1.0,
                'stop_level': stop_level,
                'profit_pct': profit_pct,
                'entry_price': position.entry_price,
            }
        )
    
    def update_trailing_stops(
        self,
        positions: List[Position],
        prices: Dict[str, float],
        data_dict: Dict[str, pd.DataFrame]
    ) -> Dict[str, float]:
        """
        Update trailing stops for all positions.
        
        Returns dictionary of symbol -> new stop level
        """
        new_stops = {}
        
        for position in positions:
            symbol = position.symbol
            
            if symbol not in prices or symbol not in data_dict:
                continue
            
            new_stop = self.trailing_stop.update_stop(
                position,
                prices[symbol],
                data_dict[symbol]
            )
            
            if new_stop:
                new_stops[symbol] = new_stop
        
        return new_stops
    
    def reset_position_tracking(self, symbol: str):
        """Reset tracking data for a closed position."""
        self.trailing_stop.reset(symbol)


def create_exit_engine(
    enable_trailing: bool = True,
    enable_targets: bool = True,
    enable_time: bool = True
) -> ExitEngine:
    """
    Factory function to create exit engine.
    
    Args:
        enable_trailing: Enable trailing stops
        enable_targets: Enable profit targets
        enable_time: Enable time-based exits
        
    Returns:
        Configured ExitEngine
    """
    config = ExitEngineConfig(
        enable_profit_target=enable_targets,
        enable_trailing_stop=enable_trailing,
        enable_time_exit=enable_time
    )
    
    return ExitEngine(config)
