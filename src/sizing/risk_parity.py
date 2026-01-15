"""
Risk Parity Position Sizing
===========================

Allocate positions so each contributes equal risk to portfolio.

Key concept: High vol positions get smaller weights to equalize risk
"""

import pandas as pd
import numpy as np
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass


@dataclass
class RiskParityConfig:
    """Configuration for risk parity sizing."""
    # Risk budget
    total_risk_budget: float = 1.0  # 100% of allowed risk
    
    # Bounds per position
    min_weight: float = 0.02  # 2% minimum
    max_weight: float = 0.20  # 20% maximum
    
    # Max positions
    max_positions: int = 20
    
    # Vol calculation
    vol_lookback: int = 20


class RiskParitySizer:
    """
    Risk parity position sizing.
    
    Each position contributes equal risk to portfolio.
    
    Example:
        rp = RiskParitySizer()
        
        weights = rp.calculate_weights(
            symbols=['NVDA', 'AAPL', 'JPM'],
            volatilities={'NVDA': 0.45, 'AAPL': 0.25, 'JPM': 0.20}
        )
        # NVDA gets smaller weight due to higher vol
    """
    
    def __init__(self, config: Optional[RiskParityConfig] = None):
        self.config = config or RiskParityConfig()
    
    def calculate_weights(
        self,
        symbols: List[str],
        volatilities: Dict[str, float]
    ) -> Dict[str, float]:
        """
        Calculate risk parity weights.
        
        Simple inverse volatility weighting for equal risk contribution.
        
        Args:
            symbols: List of symbols
            volatilities: Dict of symbol -> annualized volatility
            
        Returns:
            Dict of symbol -> portfolio weight
        """
        if not symbols:
            return {}
        
        # Calculate inverse volatility for each
        inv_vols = {}
        for symbol in symbols:
            vol = volatilities.get(symbol, 0.20)  # Default 20%
            if vol > 0:
                inv_vols[symbol] = 1 / vol
            else:
                inv_vols[symbol] = 0
        
        # Normalize to sum to 1
        total_inv_vol = sum(inv_vols.values())
        
        weights = {}
        for symbol in symbols:
            if total_inv_vol > 0:
                weight = inv_vols[symbol] / total_inv_vol
            else:
                weight = 1 / len(symbols)
            
            # Apply bounds
            weight = max(self.config.min_weight, min(weight, self.config.max_weight))
            weights[symbol] = weight
        
        # Re-normalize after applying bounds
        total = sum(weights.values())
        if total > 0:
            weights = {s: w / total for s, w in weights.items()}
        
        return weights
    
    def calculate_shares(
        self,
        capital: float,
        weights: Dict[str, float],
        prices: Dict[str, float]
    ) -> Dict[str, int]:
        """
        Convert weights to share counts.
        
        Args:
            capital: Total capital
            weights: Dict of symbol -> weight
            prices: Dict of symbol -> current price
            
        Returns:
            Dict of symbol -> shares
        """
        shares = {}
        
        for symbol, weight in weights.items():
            price = prices.get(symbol, 0)
            if price > 0:
                position_value = capital * weight
                shares[symbol] = int(position_value / price)
            else:
                shares[symbol] = 0
        
        return shares
    
    def get_risk_contributions(
        self,
        weights: Dict[str, float],
        volatilities: Dict[str, float]
    ) -> Dict[str, float]:
        """
        Calculate each position's risk contribution.
        
        Risk contribution = weight * volatility (simplified)
        
        Returns:
            Dict of symbol -> risk contribution (should be roughly equal)
        """
        contributions = {}
        
        for symbol, weight in weights.items():
            vol = volatilities.get(symbol, 0.20)
            contributions[symbol] = weight * vol
        
        # Normalize to percentages
        total = sum(contributions.values())
        if total > 0:
            contributions = {s: c / total for s, c in contributions.items()}
        
        return contributions
    
    def rebalance_weights(
        self,
        current_weights: Dict[str, float],
        target_weights: Dict[str, float],
        threshold: float = 0.03
    ) -> Dict[str, float]:
        """
        Calculate rebalancing needed.
        
        Only rebalance if drift exceeds threshold.
        
        Args:
            current_weights: Current portfolio weights
            target_weights: Target risk parity weights
            threshold: Minimum drift to trigger rebalance
            
        Returns:
            Dict of symbol -> weight adjustment needed
        """
        adjustments = {}
        
        all_symbols = set(current_weights.keys()) | set(target_weights.keys())
        
        for symbol in all_symbols:
            current = current_weights.get(symbol, 0)
            target = target_weights.get(symbol, 0)
            
            diff = target - current
            
            if abs(diff) >= threshold:
                adjustments[symbol] = diff
        
        return adjustments
