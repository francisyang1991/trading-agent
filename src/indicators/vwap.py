"""
VWAP indicator helpers for daily trend filtering.
"""

from __future__ import annotations

import pandas as pd


def calculate_rolling_vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    window: int = 20,
) -> pd.Series:
    """
    Calculate rolling VWAP using typical price and rolling volume sums.
    """
    typical_price = (high + low + close) / 3.0
    pv = typical_price * volume
    vol_sum = volume.rolling(window=window, min_periods=window).sum()
    pv_sum = pv.rolling(window=window, min_periods=window).sum()
    return pv_sum / vol_sum


def calculate_vwap_slope(vwap: pd.Series, slope_lookback: int = 5) -> pd.Series:
    """
    Simple slope proxy: lookback difference, positive means rising trend.
    """
    return vwap - vwap.shift(slope_lookback)
