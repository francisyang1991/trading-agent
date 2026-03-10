"""
Position Sizer
==============

Main position sizing engine that combines multiple sizing methods.

Sizing Methods:
1. Fixed Fractional - Risk fixed % of capital per trade
2. Kelly Criterion - Optimal sizing based on edge and win rate
3. Volatility Target - Size based on target portfolio volatility
4. Risk Parity - Equal risk contribution across positions
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from ..core.types import Signal, Regime, VolatilityLevel


class SizingMethod(Enum):
    """Position sizing methods."""
    FIXED_FRACTIONAL = "fixed_fractional"
    KELLY = "kelly"
    VOLATILITY_TARGET = "volatility_target"
    RISK_PARITY = "risk_parity"


@dataclass
class SizingResult:
    """Result from position sizing calculation."""
    symbol: str
    shares: int
    position_value: float
    position_pct: float  # % of portfolio
    risk_amount: float
    risk_pct: float  # % of portfolio at risk
    
    # Sizing details
    method: SizingMethod = SizingMethod.FIXED_FRACTIONAL
    kelly_fraction: float = 0.0
    volatility_scalar: float = 1.0
    
    # Constraints applied
    capped: bool = False
    cap_reason: str = ""


@dataclass
class SizingConfig:
    """Configuration for position sizing."""
    # Capital
    total_capital: float = 100000.0
    
    # Risk limits
    max_risk_per_trade: float = 0.02  # 2% max risk per trade
    max_position_pct: float = 0.10  # 10% max position size
    max_portfolio_risk: float = 0.06  # 6% max portfolio risk
    
    # Method weights (for combined sizing)
    kelly_weight: float = 0.3
    volatility_weight: float = 0.4
    fixed_weight: float = 0.3
    
    # Kelly adjustments
    kelly_fraction: float = 0.25  # Use 25% of Kelly (half-Kelly)
    
    # Volatility targeting
    target_volatility: float = 0.15  # 15% annual vol target
    
    # Regime adjustments
    reduce_in_high_vol: bool = True
    vol_reduction_factor: float = 0.5


class PositionSizer:
    """
    Position Sizing Engine - Calculate optimal position sizes.
    
    Features:
    - Multiple sizing methods
    - Risk-based constraints
    - Regime-aware adjustments
    - Portfolio-level limits
    
    Example:
        sizer = PositionSizer(capital=100000)
        
        result = sizer.calculate_size(
            signal=entry_signal,
            current_price=150.0,
            stop_loss=142.0,
            volatility=0.25
        )
        
        print(f"Buy {result.shares} shares (${result.position_value})")
        print(f"Risk: ${result.risk_amount} ({result.risk_pct:.1%})")
    """
    
    def __init__(self, config: Optional[SizingConfig] = None):
        self.config = config or SizingConfig()
        self._current_positions_risk: float = 0.0
    
    def calculate_size(
        self,
        signal: Signal,
        current_price: float,
        stop_loss: Optional[float] = None,
        volatility: float = 0.20,
        win_rate: float = 0.55,
        avg_win_loss_ratio: float = 1.5,
        method: SizingMethod = SizingMethod.FIXED_FRACTIONAL
    ) -> SizingResult:
        """
        Calculate position size.
        
        Args:
            signal: Entry signal
            current_price: Current stock price
            stop_loss: Stop loss price (uses signal's stop if None)
            volatility: Stock volatility (annualized)
            win_rate: Historical win rate (for Kelly)
            avg_win_loss_ratio: Average win/loss ratio (for Kelly)
            method: Sizing method to use
            
        Returns:
            SizingResult with position size details
        """
        symbol = signal.symbol
        
        # Use signal's stop loss if not provided
        if stop_loss is None:
            stop_loss = signal.stop_loss or (current_price * 0.95)
        
        # Calculate risk per share
        risk_per_share = abs(current_price - stop_loss)
        risk_pct_per_share = risk_per_share / current_price
        
        # Calculate base position size based on method
        if method == SizingMethod.FIXED_FRACTIONAL:
            shares, position_value = self._fixed_fractional_size(
                current_price, risk_per_share
            )
        elif method == SizingMethod.KELLY:
            shares, position_value = self._kelly_size(
                current_price, win_rate, avg_win_loss_ratio, risk_pct_per_share
            )
        elif method == SizingMethod.VOLATILITY_TARGET:
            shares, position_value = self._volatility_target_size(
                current_price, volatility
            )
        else:  # Combined
            shares, position_value = self._combined_size(
                current_price, risk_per_share, volatility,
                win_rate, avg_win_loss_ratio
            )
        
        # Apply regime-based adjustments
        if signal.volatility == VolatilityLevel.HIGH and self.config.reduce_in_high_vol:
            shares = int(shares * self.config.vol_reduction_factor)
            position_value = shares * current_price
        elif signal.volatility == VolatilityLevel.EXTREME:
            shares = int(shares * 0.25)  # Minimal sizing
            position_value = shares * current_price
        
        # Apply signal confidence scaling
        confidence_scalar = 0.5 + (signal.confidence * 0.5)  # 50-100% of base size
        shares = int(shares * confidence_scalar)
        position_value = shares * current_price
        
        # Apply constraints
        capped = False
        cap_reason = ""
        
        # Max position size constraint
        max_position_value = self.config.total_capital * self.config.max_position_pct
        if position_value > max_position_value:
            shares = int(max_position_value / current_price)
            position_value = shares * current_price
            capped = True
            cap_reason = f"Max position ({self.config.max_position_pct:.0%})"
        
        # Max risk per trade constraint
        risk_amount = shares * risk_per_share
        max_risk = self.config.total_capital * self.config.max_risk_per_trade
        if risk_amount > max_risk:
            shares = int(max_risk / risk_per_share)
            position_value = shares * current_price
            risk_amount = shares * risk_per_share
            capped = True
            cap_reason = f"Max risk ({self.config.max_risk_per_trade:.0%})"
        
        # Portfolio risk constraint
        total_risk_after = self._current_positions_risk + risk_amount
        max_portfolio_risk = self.config.total_capital * self.config.max_portfolio_risk
        if total_risk_after > max_portfolio_risk:
            available_risk = max_portfolio_risk - self._current_positions_risk
            if available_risk > 0:
                shares = int(available_risk / risk_per_share)
                position_value = shares * current_price
                risk_amount = shares * risk_per_share
            else:
                shares = 0
                position_value = 0
                risk_amount = 0
            capped = True
            cap_reason = "Portfolio risk limit"
        
        # Calculate percentages
        position_pct = position_value / self.config.total_capital if self.config.total_capital > 0 else 0
        risk_pct = risk_amount / self.config.total_capital if self.config.total_capital > 0 else 0
        
        return SizingResult(
            symbol=symbol,
            shares=max(0, shares),
            position_value=position_value,
            position_pct=position_pct,
            risk_amount=risk_amount,
            risk_pct=risk_pct,
            method=method,
            kelly_fraction=self._calculate_kelly_fraction(win_rate, avg_win_loss_ratio),
            volatility_scalar=self.config.target_volatility / volatility if volatility > 0 else 1,
            capped=capped,
            cap_reason=cap_reason
        )
    
    def _fixed_fractional_size(
        self,
        price: float,
        risk_per_share: float
    ) -> Tuple[int, float]:
        """Calculate size using fixed fractional method."""
        if risk_per_share <= 0:
            return 0, 0.0
        
        risk_capital = self.config.total_capital * self.config.max_risk_per_trade
        shares = int(risk_capital / risk_per_share)
        position_value = shares * price
        
        return shares, position_value
    
    def _kelly_size(
        self,
        price: float,
        win_rate: float,
        avg_win_loss_ratio: float,
        risk_pct_per_share: float
    ) -> Tuple[int, float]:
        """Calculate size using Kelly Criterion."""
        kelly = self._calculate_kelly_fraction(win_rate, avg_win_loss_ratio)
        
        # Apply Kelly fraction (use partial Kelly)
        kelly_pct = kelly * self.config.kelly_fraction
        kelly_pct = max(0, min(kelly_pct, self.config.max_position_pct))
        
        position_value = self.config.total_capital * kelly_pct
        shares = int(position_value / price) if price > 0 else 0
        
        return shares, shares * price
    
    def _volatility_target_size(
        self,
        price: float,
        volatility: float
    ) -> Tuple[int, float]:
        """Calculate size using volatility targeting."""
        if volatility <= 0:
            volatility = 0.20  # Default
        
        # Scale position inversely with volatility
        vol_scalar = self.config.target_volatility / volatility
        vol_scalar = max(0.25, min(vol_scalar, 2.0))  # Cap scalar
        
        # Base position size
        base_pct = self.config.max_position_pct * 0.5  # 50% of max as base
        target_pct = base_pct * vol_scalar
        
        position_value = self.config.total_capital * target_pct
        shares = int(position_value / price) if price > 0 else 0
        
        return shares, shares * price
    
    def _combined_size(
        self,
        price: float,
        risk_per_share: float,
        volatility: float,
        win_rate: float,
        avg_win_loss_ratio: float
    ) -> Tuple[int, float]:
        """Calculate size using weighted combination of methods."""
        # Get sizes from each method
        fixed_shares, _ = self._fixed_fractional_size(price, risk_per_share)
        kelly_shares, _ = self._kelly_size(
            price, win_rate, avg_win_loss_ratio, 
            risk_per_share / price if price > 0 else 0.05
        )
        vol_shares, _ = self._volatility_target_size(price, volatility)
        
        # Weighted average
        combined_shares = (
            fixed_shares * self.config.fixed_weight +
            kelly_shares * self.config.kelly_weight +
            vol_shares * self.config.volatility_weight
        )
        
        shares = int(combined_shares)
        return shares, shares * price
    
    def _calculate_kelly_fraction(
        self,
        win_rate: float,
        avg_win_loss_ratio: float
    ) -> float:
        """
        Calculate Kelly fraction.
        
        Kelly = W - (1-W)/R
        Where W = win rate, R = avg win/loss ratio
        """
        if avg_win_loss_ratio <= 0:
            return 0.0
        
        kelly = win_rate - (1 - win_rate) / avg_win_loss_ratio
        return max(0, kelly)
    
    def update_portfolio_risk(self, current_risk: float):
        """Update current portfolio risk level."""
        self._current_positions_risk = current_risk
    
    def update_capital(self, new_capital: float):
        """Update total capital."""
        self.config.total_capital = new_capital
