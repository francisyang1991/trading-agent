"""
Kelly Criterion Position Sizing
===============================

Optimal position sizing based on edge and win rate.

Kelly Formula: f* = (bp - q) / b
Where:
- f* = Kelly fraction
- b = odds (avg win / avg loss)
- p = probability of winning
- q = probability of losing (1-p)
"""

import numpy as np
from typing import Optional, Tuple
from dataclasses import dataclass


@dataclass
class KellyConfig:
    """Configuration for Kelly sizing."""
    # Kelly fraction (use partial Kelly)
    fraction: float = 0.25  # Quarter Kelly is safer
    
    # Bounds
    min_kelly: float = 0.0
    max_kelly: float = 0.25  # Max 25% position
    
    # Historical lookback
    min_trades_for_stats: int = 20


class KellySizer:
    """
    Kelly Criterion position sizing.
    
    Example:
        kelly = KellySizer()
        
        # Calculate optimal fraction
        fraction = kelly.calculate(
            win_rate=0.55,
            avg_win=150,
            avg_loss=100
        )
        
        # Get position size
        size = kelly.get_position_size(
            capital=100000,
            price=150,
            win_rate=0.55,
            avg_win=150,
            avg_loss=100
        )
    """
    
    def __init__(self, config: Optional[KellyConfig] = None):
        self.config = config or KellyConfig()
    
    def calculate(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float
    ) -> float:
        """
        Calculate Kelly fraction.
        
        Args:
            win_rate: Probability of winning (0-1)
            avg_win: Average winning trade amount
            avg_loss: Average losing trade amount (positive)
            
        Returns:
            Kelly fraction (optimal bet size as fraction of capital)
        """
        if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
            return 0.0
        
        # Odds ratio (b)
        b = avg_win / avg_loss
        
        # Kelly formula: f* = (bp - q) / b
        # Where p = win_rate, q = 1 - win_rate
        p = win_rate
        q = 1 - win_rate
        
        kelly = (b * p - q) / b
        
        # Apply fraction (partial Kelly)
        kelly = kelly * self.config.fraction
        
        # Apply bounds
        kelly = max(self.config.min_kelly, min(kelly, self.config.max_kelly))
        
        return kelly
    
    def calculate_from_returns(
        self,
        returns: list
    ) -> float:
        """
        Calculate Kelly from historical returns.
        
        Args:
            returns: List of trade returns (positive = win, negative = loss)
            
        Returns:
            Kelly fraction
        """
        if len(returns) < self.config.min_trades_for_stats:
            return 0.0
        
        returns = np.array(returns)
        
        wins = returns[returns > 0]
        losses = returns[returns < 0]
        
        if len(wins) == 0 or len(losses) == 0:
            return 0.0
        
        win_rate = len(wins) / len(returns)
        avg_win = np.mean(wins)
        avg_loss = abs(np.mean(losses))
        
        return self.calculate(win_rate, avg_win, avg_loss)
    
    def get_position_size(
        self,
        capital: float,
        price: float,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        max_position_pct: float = 0.10
    ) -> Tuple[int, float]:
        """
        Calculate position size using Kelly.
        
        Args:
            capital: Total capital
            price: Stock price
            win_rate: Historical win rate
            avg_win: Average win amount
            avg_loss: Average loss amount
            max_position_pct: Maximum position as % of capital
            
        Returns:
            (shares, position_value)
        """
        kelly = self.calculate(win_rate, avg_win, avg_loss)
        
        # Cap at max position
        kelly = min(kelly, max_position_pct)
        
        position_value = capital * kelly
        shares = int(position_value / price) if price > 0 else 0
        
        return shares, shares * price
    
    def get_expected_growth(
        self,
        win_rate: float,
        avg_win_pct: float,
        avg_loss_pct: float,
        kelly_fraction: float
    ) -> float:
        """
        Calculate expected growth rate using Kelly fraction.
        
        G = p * log(1 + b*f) + q * log(1 - f)
        
        Returns annualized expected growth rate.
        """
        if kelly_fraction <= 0:
            return 0.0
        
        p = win_rate
        q = 1 - win_rate
        f = kelly_fraction
        b = avg_win_pct / avg_loss_pct if avg_loss_pct > 0 else 1
        
        # Expected log growth per trade
        g = p * np.log(1 + b * f) + q * np.log(1 - f)
        
        return g
