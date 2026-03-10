"""
Volatility Target Position Sizing
=================================

Size positions to achieve target portfolio volatility.

Key concept: Scale position size inversely with volatility
- High vol stock = smaller position
- Low vol stock = larger position
"""

import pandas as pd
import numpy as np
from typing import Optional, Tuple, Dict
from dataclasses import dataclass


@dataclass
class VolTargetConfig:
    """Configuration for volatility targeting."""
    # Target annualized volatility
    target_vol: float = 0.15  # 15%
    
    # Vol calculation
    vol_lookback: int = 20  # Days for vol calculation
    vol_annualization: float = 252  # Trading days per year
    
    # Bounds
    min_scalar: float = 0.25
    max_scalar: float = 2.0
    
    # Decay (reduce exposure over time)
    vol_decay_factor: float = 0.94  # EWMA decay


class VolatilityTargetSizer:
    """
    Volatility-targeted position sizing.
    
    Example:
        vol_sizer = VolatilityTargetSizer(target_vol=0.15)
        
        # Get position size for 30% vol stock
        shares, value = vol_sizer.size_position(
            capital=100000,
            price=150,
            stock_vol=0.30
        )
        # Returns smaller position due to high vol
    """
    
    def __init__(self, config: Optional[VolTargetConfig] = None):
        self.config = config or VolTargetConfig()
    
    def calculate_volatility(self, data: pd.DataFrame) -> float:
        """
        Calculate annualized volatility from price data.
        
        Args:
            data: OHLCV DataFrame
            
        Returns:
            Annualized volatility
        """
        close = data['close'] if 'close' in data.columns else data['Close']
        
        if len(close) < self.config.vol_lookback:
            return 0.20  # Default
        
        # Daily returns
        returns = close.pct_change().dropna()
        
        # Exponentially weighted volatility
        vol = returns.ewm(span=self.config.vol_lookback).std().iloc[-1]
        
        # Annualize
        annual_vol = vol * np.sqrt(self.config.vol_annualization)
        
        return annual_vol
    
    def get_vol_scalar(self, stock_vol: float) -> float:
        """
        Get volatility scaling factor.
        
        scalar = target_vol / stock_vol
        
        High vol -> lower scalar -> smaller position
        Low vol -> higher scalar -> larger position
        """
        if stock_vol <= 0:
            stock_vol = self.config.target_vol
        
        scalar = self.config.target_vol / stock_vol
        
        # Apply bounds
        scalar = max(self.config.min_scalar, min(scalar, self.config.max_scalar))
        
        return scalar
    
    def size_position(
        self,
        capital: float,
        price: float,
        stock_vol: float,
        base_position_pct: float = 0.05,
        max_position_pct: float = 0.10
    ) -> Tuple[int, float]:
        """
        Calculate position size based on volatility target.
        
        Args:
            capital: Total capital
            price: Stock price
            stock_vol: Stock's annualized volatility
            base_position_pct: Base position size (before vol scaling)
            max_position_pct: Maximum position size
            
        Returns:
            (shares, position_value)
        """
        scalar = self.get_vol_scalar(stock_vol)
        
        # Scale base position
        target_pct = base_position_pct * scalar
        target_pct = min(target_pct, max_position_pct)
        
        position_value = capital * target_pct
        shares = int(position_value / price) if price > 0 else 0
        
        return shares, shares * price
    
    def calculate_portfolio_weights(
        self,
        symbols: list,
        volatilities: Dict[str, float],
        correlations: Optional[pd.DataFrame] = None
    ) -> Dict[str, float]:
        """
        Calculate portfolio weights targeting specific volatility.
        
        Simple version: inverse volatility weighting
        Advanced version: accounts for correlations
        
        Args:
            symbols: List of symbols
            volatilities: Dict of symbol -> volatility
            correlations: Optional correlation matrix
            
        Returns:
            Dict of symbol -> weight
        """
        weights = {}
        
        if correlations is None:
            # Simple inverse volatility weighting
            inv_vols = {}
            for symbol in symbols:
                vol = volatilities.get(symbol, self.config.target_vol)
                inv_vols[symbol] = 1 / vol if vol > 0 else 0
            
            total_inv_vol = sum(inv_vols.values())
            
            for symbol in symbols:
                weights[symbol] = inv_vols[symbol] / total_inv_vol if total_inv_vol > 0 else 0
        else:
            # Risk parity approach (equal risk contribution)
            # This is a simplified version
            for symbol in symbols:
                vol = volatilities.get(symbol, self.config.target_vol)
                weights[symbol] = (1 / vol) if vol > 0 else 0
            
            total = sum(weights.values())
            weights = {s: w / total for s, w in weights.items()} if total > 0 else weights
        
        return weights
