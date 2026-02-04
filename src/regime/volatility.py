"""
Volatility Classification
=========================

Classifies stock volatility into categories for position sizing and strategy selection.

Categories (annualized volatility):
- ULTRA_HIGH: >80% (crypto proxies, meme stocks)
- HIGH: 50-80% (growth stocks, small caps)
- MODERATE: 30-50% (typical stocks)
- LOW: <30% (large caps, utilities)

Usage:
    from src.regime import VolCategory, classify_volatility
    
    vol_cat = classify_volatility(volatility_pct=55.0)  # Returns VolCategory.HIGH
    
    # From price series
    vol_cat = classify_volatility_from_prices(df['Close'])
"""

from enum import Enum
from typing import Optional
import pandas as pd
import numpy as np


class VolCategory(Enum):
    """Volatility category based on annualized volatility."""
    ULTRA_HIGH = "ULTRA_HIGH"    # >80% annualized
    HIGH = "HIGH"                # 50-80%
    MODERATE = "MODERATE"        # 30-50%
    LOW = "LOW"                  # <30%


# Volatility thresholds (annualized percentages)
VOL_THRESHOLDS = {
    VolCategory.ULTRA_HIGH: (80, float('inf')),
    VolCategory.HIGH: (50, 80),
    VolCategory.MODERATE: (30, 50),
    VolCategory.LOW: (0, 30),
}


def classify_volatility(volatility_pct: float) -> VolCategory:
    """
    Classify volatility based on annualized volatility percentage.
    
    Args:
        volatility_pct: Annualized volatility as percentage (e.g., 55.0 for 55%)
        
    Returns:
        VolCategory enum value
        
    Example:
        >>> classify_volatility(85.0)
        VolCategory.ULTRA_HIGH
        >>> classify_volatility(55.0)
        VolCategory.HIGH
        >>> classify_volatility(25.0)
        VolCategory.LOW
    """
    if volatility_pct > 80:
        return VolCategory.ULTRA_HIGH
    elif volatility_pct > 50:
        return VolCategory.HIGH
    elif volatility_pct > 30:
        return VolCategory.MODERATE
    else:
        return VolCategory.LOW


def calculate_volatility(close: pd.Series, annualize: bool = True) -> float:
    """
    Calculate volatility from price series.
    
    Args:
        close: Close price series
        annualize: If True, annualize (multiply by sqrt(252))
        
    Returns:
        Volatility as percentage
        
    Example:
        >>> vol = calculate_volatility(df['Close'])
        >>> print(f"Annualized volatility: {vol:.1f}%")
    """
    if len(close) < 2:
        return 0.0
    
    returns = close.pct_change().dropna()
    vol = returns.std()
    
    if annualize:
        vol = vol * np.sqrt(252)
    
    return vol * 100


def classify_volatility_from_prices(close: pd.Series) -> VolCategory:
    """
    Classify volatility from a price series.
    
    Args:
        close: Close price series
        
    Returns:
        VolCategory enum value
    """
    vol_pct = calculate_volatility(close)
    return classify_volatility(vol_pct)


def get_volatility_position_adjustment(vol_category: VolCategory) -> float:
    """
    Get position size adjustment factor based on volatility.
    
    Higher volatility = smaller position to maintain consistent risk.
    
    Args:
        vol_category: VolCategory enum value
        
    Returns:
        Position adjustment factor (0.0 to 1.0)
    """
    adjustments = {
        VolCategory.ULTRA_HIGH: 0.33,   # 1/3 normal size
        VolCategory.HIGH: 0.50,         # 1/2 normal size
        VolCategory.MODERATE: 0.75,     # 3/4 normal size
        VolCategory.LOW: 1.00,          # Full size
    }
    return adjustments.get(vol_category, 0.5)


def get_atr_stop_multiplier(vol_category: VolCategory) -> float:
    """
    Get ATR multiplier for stop loss based on volatility category.
    
    Higher volatility = wider stops to avoid whipsaws.
    
    Args:
        vol_category: VolCategory enum value
        
    Returns:
        ATR multiplier for stop loss
    """
    multipliers = {
        VolCategory.ULTRA_HIGH: 3.0,
        VolCategory.HIGH: 2.5,
        VolCategory.MODERATE: 2.0,
        VolCategory.LOW: 1.5,
    }
    return multipliers.get(vol_category, 2.0)
