"""
Profit Target Exit Signal
=========================

Exit signals based on profit targets.

Methods:
1. Fixed R:R target (e.g., 2R, 3R)
2. Fibonacci extension levels
3. Key resistance/support levels
4. Partial profit taking
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from ...core.types import Signal, SignalType, Position, Regime, VolatilityLevel, Strategy


@dataclass
class ProfitTargetConfig:
    """Configuration for profit target exits."""
    # R-multiple targets
    target_1r: float = 1.0
    target_2r: float = 2.0
    target_3r: float = 3.0
    
    # Partial exits
    partial_at_1r: float = 0.33  # Take 33% at 1R
    partial_at_2r: float = 0.33  # Take another 33% at 2R
    
    # Extension levels
    use_fib_extensions: bool = True
    fib_levels: List[float] = None
    
    # ATR-based
    atr_period: int = 14
    atr_target_multiple: float = 3.0


class ProfitTargetExit:
    """
    Generate exit signals based on profit targets.
    
    Example:
        exit_gen = ProfitTargetExit()
        
        # Check if position should take profit
        signal = exit_gen.check(position, current_price, price_data)
        
        if signal:
            execute_exit(signal)
    """
    
    def __init__(self, config: Optional[ProfitTargetConfig] = None):
        self.config = config or ProfitTargetConfig()
        if self.config.fib_levels is None:
            self.config.fib_levels = [1.0, 1.272, 1.618, 2.0, 2.618]
    
    def check(
        self,
        position: Position,
        current_price: float,
        data: Optional[pd.DataFrame] = None
    ) -> Optional[Signal]:
        """
        Check if position should exit at profit target.
        
        Args:
            position: Current position
            current_price: Current market price
            data: Optional price data for dynamic targets
            
        Returns:
            Exit signal if target hit, None otherwise
        """
        if position.direction != 1:  # Only long positions for now
            return None
        
        entry_price = position.entry_price
        stop_loss = position.stop_loss or (entry_price * 0.95)
        
        # Calculate R (risk)
        risk = entry_price - stop_loss
        if risk <= 0:
            risk = entry_price * 0.05
        
        # Current profit in R
        profit = current_price - entry_price
        profit_r = profit / risk if risk > 0 else 0
        
        # Check targets
        exit_signal = None
        exit_reason = None
        exit_pct = 0.0
        
        if profit_r >= self.config.target_3r:
            exit_reason = f"3R target hit ({profit_r:.1f}R)"
            exit_pct = 1.0  # Full exit
            confidence = 0.95
        elif profit_r >= self.config.target_2r:
            exit_reason = f"2R target hit ({profit_r:.1f}R)"
            exit_pct = self.config.partial_at_2r
            confidence = 0.85
        elif profit_r >= self.config.target_1r:
            exit_reason = f"1R target hit ({profit_r:.1f}R)"
            exit_pct = self.config.partial_at_1r
            confidence = 0.75
        else:
            return None
        
        return Signal(
            symbol=position.symbol,
            signal_type=SignalType.EXIT,
            direction=-1,  # Sell
            price=current_price,
            confidence=confidence,
            strategy=position.strategy,
            metadata={
                'exit_type': 'profit_target',
                'exit_reason': exit_reason,
                'exit_pct': exit_pct,
                'profit_r': profit_r,
                'profit_pct': profit / entry_price * 100,
                'entry_price': entry_price,
                'position_id': position.position_id if hasattr(position, 'position_id') else None,
            }
        )
    
    def calculate_targets(
        self,
        entry_price: float,
        stop_loss: float,
        data: Optional[pd.DataFrame] = None
    ) -> Dict[str, float]:
        """
        Calculate all profit targets.
        
        Returns dict with target levels.
        """
        risk = entry_price - stop_loss
        if risk <= 0:
            risk = entry_price * 0.05
        
        targets = {
            'target_1r': entry_price + risk * self.config.target_1r,
            'target_2r': entry_price + risk * self.config.target_2r,
            'target_3r': entry_price + risk * self.config.target_3r,
        }
        
        # Add Fibonacci extensions
        if self.config.use_fib_extensions:
            for fib in self.config.fib_levels:
                targets[f'fib_{fib}'] = entry_price + risk * fib
        
        return targets
