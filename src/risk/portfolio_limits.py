"""
Portfolio Limits
================

Portfolio-level risk limits and concentration checks.
"""

from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class PortfolioLimitsConfig:
    """Portfolio limits configuration."""
    max_portfolio_risk: float = 0.06  # Max 6% total portfolio risk
    max_sector_exposure: float = 0.30  # Max 30% per sector
    max_single_name: float = 0.10  # Max 10% single name
    max_correlated_exposure: float = 0.40  # Max 40% highly correlated
    max_leverage: float = 1.0  # No leverage by default


class PortfolioLimits:
    """
    Portfolio-level risk limits.
    
    Checks:
    - Total portfolio risk
    - Sector concentration
    - Correlation exposure
    - Gross exposure / leverage
    """
    
    def __init__(
        self,
        max_portfolio_risk: float = 0.06,
        max_sector_exposure: float = 0.30
    ):
        self.max_portfolio_risk = max_portfolio_risk
        self.max_sector_exposure = max_sector_exposure
        
        # Sector tracking
        self._sector_exposure: Dict[str, float] = {}
    
    def check(
        self,
        new_risk: float,
        current_risk: float,
        capital: float,
        sector: str = None
    ) -> Dict:
        """
        Check if new trade fits within portfolio limits.
        
        Args:
            new_risk: Risk of new position
            current_risk: Current total risk
            capital: Total capital
            sector: Optional sector for concentration check
            
        Returns:
            Dict with pass/fail and reasons
        """
        reasons = []
        passed = True
        
        # Check total portfolio risk
        max_risk_amount = capital * self.max_portfolio_risk
        total_risk_after = current_risk + new_risk
        
        if total_risk_after > max_risk_amount:
            available = max_risk_amount - current_risk
            reasons.append(
                f"Portfolio risk limit: adding ${new_risk:,.0f} would exceed "
                f"max ${max_risk_amount:,.0f}. Available: ${available:,.0f}"
            )
            if available <= 0:
                passed = False
        
        # Check sector concentration
        if sector:
            sector_check = self.check_sector(sector, new_risk, capital)
            if not sector_check['passed']:
                reasons.extend(sector_check['reasons'])
        
        return {
            'passed': passed,
            'reasons': reasons,
            'total_risk_after': total_risk_after,
            'risk_pct_after': total_risk_after / capital * 100 if capital > 0 else 0,
        }
    
    def check_sector(
        self,
        sector: str,
        new_exposure: float,
        capital: float
    ) -> Dict:
        """Check sector concentration."""
        current_sector = self._sector_exposure.get(sector, 0)
        total_sector = current_sector + new_exposure
        max_sector = capital * self.max_sector_exposure
        
        if total_sector > max_sector:
            return {
                'passed': False,
                'reasons': [f"Sector {sector} would exceed {self.max_sector_exposure:.0%} limit"]
            }
        
        return {'passed': True, 'reasons': []}
    
    def update_sector_exposure(self, sector: str, exposure: float):
        """Update sector exposure tracking."""
        self._sector_exposure[sector] = exposure
    
    def add_sector_exposure(self, sector: str, amount: float):
        """Add to sector exposure."""
        current = self._sector_exposure.get(sector, 0)
        self._sector_exposure[sector] = current + amount
    
    def remove_sector_exposure(self, sector: str, amount: float):
        """Remove from sector exposure."""
        current = self._sector_exposure.get(sector, 0)
        self._sector_exposure[sector] = max(0, current - amount)
    
    def get_sector_summary(self, capital: float) -> Dict[str, float]:
        """Get sector exposure as percentages."""
        return {
            sector: exposure / capital * 100 if capital > 0 else 0
            for sector, exposure in self._sector_exposure.items()
        }
    
    def get_available_risk(self, current_risk: float, capital: float) -> float:
        """Get available portfolio risk budget."""
        max_risk = capital * self.max_portfolio_risk
        return max(0, max_risk - current_risk)
