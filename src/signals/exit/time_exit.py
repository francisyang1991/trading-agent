"""
Time-Based Exit Signal
======================

Exit signals based on time in trade.

Methods:
1. Maximum holding period
2. End of day exit
3. Event-based exits (earnings, FOMC)
4. Decay exits (reduce position over time)
"""

import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Optional
from dataclasses import dataclass

from ...core.types import Signal, SignalType, Position, Strategy


@dataclass
class TimeExitConfig:
    """Configuration for time-based exits."""
    # Maximum holding periods by strategy
    max_days_swing: int = 10
    max_days_trend: int = 30
    max_days_mean_reversion: int = 5
    
    # Intraday exit
    exit_before_close_minutes: int = 15
    
    # Decay (reduce exposure over time)
    enable_decay: bool = False
    decay_start_day: int = 5
    decay_pct_per_day: float = 20.0
    
    # Event avoidance
    exit_before_earnings: bool = True
    earnings_exit_days: int = 1


class TimeBasedExit:
    """
    Generate exit signals based on time factors.
    
    Example:
        time_exit = TimeBasedExit()
        
        # Check if should exit based on time
        signal = time_exit.check(position, current_time)
        
        if signal:
            execute_exit(signal)
    """
    
    def __init__(self, config: Optional[TimeExitConfig] = None):
        self.config = config or TimeExitConfig()
    
    def check(
        self,
        position: Position,
        current_time: datetime,
        current_price: float,
        earnings_date: Optional[datetime] = None
    ) -> Optional[Signal]:
        """
        Check if position should exit based on time.
        
        Args:
            position: Current position
            current_time: Current datetime
            current_price: Current price
            earnings_date: Optional upcoming earnings date
            
        Returns:
            Exit signal if time exit triggered, None otherwise
        """
        entry_time = position.entry_time
        days_held = (current_time - entry_time).days
        
        # Get max days for strategy
        max_days = self._get_max_days(position.strategy)
        
        # Check maximum holding period
        if days_held >= max_days:
            return self._create_exit_signal(
                position,
                current_price,
                f"Max holding period ({days_held} days)",
                exit_pct=1.0
            )
        
        # Check earnings avoidance
        if (self.config.exit_before_earnings and 
            earnings_date and 
            earnings_date - current_time <= timedelta(days=self.config.earnings_exit_days)):
            return self._create_exit_signal(
                position,
                current_price,
                "Exit before earnings",
                exit_pct=1.0
            )
        
        # Check decay
        if self.config.enable_decay and days_held >= self.config.decay_start_day:
            decay_days = days_held - self.config.decay_start_day + 1
            exit_pct = min(1.0, decay_days * self.config.decay_pct_per_day / 100)
            
            if exit_pct > 0:
                return self._create_exit_signal(
                    position,
                    current_price,
                    f"Time decay (day {days_held})",
                    exit_pct=exit_pct
                )
        
        return None
    
    def should_exit_eod(
        self,
        position: Position,
        current_time: datetime,
        market_close: datetime,
        current_price: float
    ) -> Optional[Signal]:
        """
        Check if should exit before market close.
        
        For intraday positions or specific strategies.
        """
        time_to_close = (market_close - current_time).total_seconds() / 60
        
        if time_to_close <= self.config.exit_before_close_minutes:
            # Check if position is intraday or should exit EOD
            days_held = (current_time - position.entry_time).days
            
            if days_held == 0:  # Same day entry
                return self._create_exit_signal(
                    position,
                    current_price,
                    "End of day exit",
                    exit_pct=1.0
                )
        
        return None
    
    def _get_max_days(self, strategy: Strategy) -> int:
        """Get maximum holding days for strategy."""
        if strategy == Strategy.SWING_TRADE:
            return self.config.max_days_swing
        elif strategy == Strategy.TREND_FOLLOWING:
            return self.config.max_days_trend
        elif strategy == Strategy.MEAN_REVERSION:
            return self.config.max_days_mean_reversion
        else:
            return self.config.max_days_swing  # Default
    
    def _create_exit_signal(
        self,
        position: Position,
        current_price: float,
        reason: str,
        exit_pct: float
    ) -> Signal:
        """Create time-based exit signal."""
        profit_pct = (current_price - position.entry_price) / position.entry_price * 100
        days_held = (datetime.now() - position.entry_time).days
        
        return Signal(
            symbol=position.symbol,
            signal_type=SignalType.EXIT,
            direction=-position.direction,
            price=current_price,
            confidence=0.7,  # Time exits are rule-based
            strategy=position.strategy,
            metadata={
                'exit_type': 'time_based',
                'exit_reason': reason,
                'exit_pct': exit_pct,
                'days_held': days_held,
                'profit_pct': profit_pct,
                'entry_price': position.entry_price,
            }
        )
