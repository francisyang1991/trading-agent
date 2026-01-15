"""
Position Limits
===============

Per-position risk limits and checks.
"""

from typing import Dict, Optional
from dataclasses import dataclass


@dataclass
class PositionLimitsConfig:
    """Position limits configuration."""
    max_position_pct: float = 0.10  # Max 10% of capital per position
    max_risk_pct: float = 0.02  # Max 2% of capital at risk per position
    max_positions: int = 20  # Maximum number of positions
    min_position_value: float = 1000.0  # Minimum position value


class PositionLimits:
    """
    Position-level risk limits.
    
    Checks:
    - Maximum position size
    - Maximum risk per position
    - Maximum number of positions
    - Concentration limits
    """
    
    def __init__(
        self,
        max_position_pct: float = 0.10,
        max_risk_pct: float = 0.02,
        max_positions: int = 20
    ):
        self.max_position_pct = max_position_pct
        self.max_risk_pct = max_risk_pct
        self.max_positions = max_positions
    
    def check(
        self,
        symbol: str,
        size: float,
        risk: float,
        capital: float,
        current_positions: int
    ) -> Dict:
        """
        Check position against limits.
        
        Args:
            symbol: Stock symbol
            size: Proposed position value
            risk: Proposed risk amount
            capital: Total capital
            current_positions: Current number of positions
            
        Returns:
            Dict with pass/fail, reasons, and adjustments
        """
        reasons = []
        adjustments = {}
        passed = True
        
        # Check position count
        if current_positions >= self.max_positions:
            reasons.append(f"Max positions ({self.max_positions}) reached")
            passed = False
        
        # Check position size
        max_size = capital * self.max_position_pct
        if size > max_size:
            reasons.append(f"Size ${size:,.0f} exceeds max ${max_size:,.0f} ({self.max_position_pct:.0%})")
            adjustments['adjusted_size'] = max_size
            # Don't fail, just adjust
        
        # Check position risk
        max_risk = capital * self.max_risk_pct
        if risk > max_risk:
            reasons.append(f"Risk ${risk:,.0f} exceeds max ${max_risk:,.0f} ({self.max_risk_pct:.0%})")
            adjustments['adjusted_risk'] = max_risk
            
            # Calculate adjusted size based on risk limit
            if size > 0 and risk > 0:
                risk_ratio = max_risk / risk
                adjustments['adjusted_size'] = size * risk_ratio
        
        return {
            'passed': passed,
            'reasons': reasons,
            **adjustments
        }
    
    def get_max_size(self, capital: float) -> float:
        """Get maximum position size."""
        return capital * self.max_position_pct
    
    def get_max_risk(self, capital: float) -> float:
        """Get maximum risk per position."""
        return capital * self.max_risk_pct
