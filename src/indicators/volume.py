"""
Volume Analysis Indicators
Volume patterns, accumulation/distribution, and volume-based signals.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import pandas as pd
import numpy as np
from loguru import logger


class VolumeState(Enum):
    """Volume state classification."""
    SURGE = "surge"            # Volume >> average
    HIGH = "high"              # Volume > average
    NORMAL = "normal"          # Volume ~ average
    LOW = "low"                # Volume < average
    DRY = "dry"               # Volume << average


@dataclass
class VolumeAnalysis:
    """Volume analysis results."""
    current_volume: float
    volume_ma: float
    volume_ratio: float
    state: VolumeState
    is_climax: bool           # Extremely high volume event
    trend_volume: str         # "increasing", "decreasing", "stable"
    up_volume_ratio: float    # Ratio of volume on up days
    down_volume_ratio: float  # Ratio of volume on down days


class VolumeIndicators:
    """
    Volume analysis and indicators.
    """
    
    def __init__(
        self,
        ma_period: int = 20,
        surge_multiplier: float = 2.0,
        low_multiplier: float = 0.5,
        dry_multiplier: float = 0.3
    ):
        """
        Initialize volume indicators.
        
        Args:
            ma_period: Period for volume moving average
            surge_multiplier: Multiplier for surge detection
            low_multiplier: Multiplier for low volume
            dry_multiplier: Multiplier for dry/no-volume
        """
        self.ma_period = ma_period
        self.surge_mult = surge_multiplier
        self.low_mult = low_multiplier
        self.dry_mult = dry_multiplier
    
    def calculate(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate volume indicators.
        
        Args:
            data: OHLCV DataFrame
            
        Returns:
            DataFrame with volume indicators added
        """
        if data.empty:
            return data
        
        df = data.copy()
        
        # Volume moving average
        df['volume_ma'] = df['volume'].rolling(window=self.ma_period).mean()
        
        # Volume ratio
        df['volume_ratio'] = df['volume'] / df['volume_ma']
        
        # Volume state
        df['volume_state'] = df['volume_ratio'].apply(self._classify_volume)
        
        # On Balance Volume (OBV)
        df['obv'] = self._calculate_obv(df)
        
        # Volume-price trend
        df['vpt'] = self._calculate_vpt(df)
        
        # Accumulation/Distribution Line
        df['ad_line'] = self._calculate_ad_line(df)
        
        # Volume momentum
        df['volume_momentum'] = df['volume'].diff(5)
        
        return df
    
    def get_volume_analysis(self, data: pd.DataFrame) -> VolumeAnalysis:
        """
        Get comprehensive volume analysis.
        
        Args:
            data: OHLCV DataFrame
            
        Returns:
            VolumeAnalysis with current state
        """
        df = self.calculate(data)
        
        if df.empty:
            return VolumeAnalysis(
                current_volume=0,
                volume_ma=0,
                volume_ratio=0,
                state=VolumeState.NORMAL,
                is_climax=False,
                trend_volume="stable",
                up_volume_ratio=0,
                down_volume_ratio=0
            )
        
        latest = df.iloc[-1]
        
        # Volume trend
        vol_trend = self._determine_volume_trend(df)
        
        # Up/Down volume ratios
        up_vol, down_vol = self._calculate_up_down_volume_ratio(df)
        
        # Check for climax
        is_climax = latest['volume_ratio'] >= self.surge_mult * 1.5
        
        return VolumeAnalysis(
            current_volume=latest['volume'],
            volume_ma=latest['volume_ma'],
            volume_ratio=latest['volume_ratio'],
            state=VolumeState(latest['volume_state']),
            is_climax=is_climax,
            trend_volume=vol_trend,
            up_volume_ratio=up_vol,
            down_volume_ratio=down_vol
        )
    
    def _classify_volume(self, ratio: float) -> str:
        """Classify volume based on ratio to average."""
        if pd.isna(ratio):
            return VolumeState.NORMAL.value
        
        if ratio >= self.surge_mult:
            return VolumeState.SURGE.value
        elif ratio >= 1.3:
            return VolumeState.HIGH.value
        elif ratio >= self.low_mult:
            return VolumeState.NORMAL.value
        elif ratio >= self.dry_mult:
            return VolumeState.LOW.value
        else:
            return VolumeState.DRY.value
    
    def _calculate_obv(self, data: pd.DataFrame) -> pd.Series:
        """Calculate On Balance Volume."""
        obv = [0]
        
        for i in range(1, len(data)):
            if data['close'].iloc[i] > data['close'].iloc[i-1]:
                obv.append(obv[-1] + data['volume'].iloc[i])
            elif data['close'].iloc[i] < data['close'].iloc[i-1]:
                obv.append(obv[-1] - data['volume'].iloc[i])
            else:
                obv.append(obv[-1])
        
        return pd.Series(obv, index=data.index)
    
    def _calculate_vpt(self, data: pd.DataFrame) -> pd.Series:
        """Calculate Volume Price Trend."""
        price_change = data['close'].pct_change()
        vpt = (price_change * data['volume']).cumsum()
        return vpt
    
    def _calculate_ad_line(self, data: pd.DataFrame) -> pd.Series:
        """Calculate Accumulation/Distribution Line."""
        clv = ((data['close'] - data['low']) - (data['high'] - data['close'])) / (data['high'] - data['low'])
        clv = clv.fillna(0)
        ad = (clv * data['volume']).cumsum()
        return ad
    
    def _determine_volume_trend(self, data: pd.DataFrame, lookback: int = 10) -> str:
        """Determine recent volume trend."""
        if len(data) < lookback:
            return "stable"
        
        recent_vol = data['volume'].tail(lookback)
        
        # Linear regression slope
        x = np.arange(len(recent_vol))
        slope, _ = np.polyfit(x, recent_vol.values, 1)
        
        # Normalize slope by average volume
        avg_vol = recent_vol.mean()
        normalized_slope = slope / avg_vol if avg_vol > 0 else 0
        
        if normalized_slope > 0.05:
            return "increasing"
        elif normalized_slope < -0.05:
            return "decreasing"
        else:
            return "stable"
    
    def _calculate_up_down_volume_ratio(
        self,
        data: pd.DataFrame,
        lookback: int = 20
    ) -> Tuple[float, float]:
        """Calculate ratio of volume on up days vs down days."""
        if len(data) < lookback:
            return 0.5, 0.5
        
        recent = data.tail(lookback)
        
        # Up days: close > open
        up_days = recent[recent['close'] > recent['open']]
        down_days = recent[recent['close'] <= recent['open']]
        
        total_vol = recent['volume'].sum()
        
        if total_vol == 0:
            return 0.5, 0.5
        
        up_vol_ratio = up_days['volume'].sum() / total_vol
        down_vol_ratio = down_days['volume'].sum() / total_vol
        
        return up_vol_ratio, down_vol_ratio
    
    def is_volume_confirming_breakout(
        self,
        data: pd.DataFrame,
        min_ratio: float = 1.5
    ) -> bool:
        """
        Check if volume confirms a breakout.
        
        Args:
            data: OHLCV DataFrame
            min_ratio: Minimum volume ratio for confirmation
            
        Returns:
            True if volume confirms
        """
        analysis = self.get_volume_analysis(data)
        
        return (
            analysis.volume_ratio >= min_ratio and
            analysis.state in [VolumeState.SURGE, VolumeState.HIGH]
        )
    
    def detect_volume_climax(
        self,
        data: pd.DataFrame,
        lookback: int = 50
    ) -> Tuple[bool, str]:
        """
        Detect volume climax (potential exhaustion).
        
        Args:
            data: OHLCV DataFrame
            lookback: Bars to analyze
            
        Returns:
            (is_climax, climax_type)
        """
        if len(data) < lookback:
            return False, "none"
        
        recent = data.tail(lookback)
        current = data.iloc[-1]
        
        # Check if current volume is extreme
        vol_percentile = (recent['volume'] < current['volume']).mean()
        
        if vol_percentile < 0.95:  # Not in top 5%
            return False, "none"
        
        # Determine type based on price action
        price_change = (current['close'] - current['open']) / current['open']
        
        if price_change > 0.02:  # 2%+ up on huge volume
            return True, "buying_climax"
        elif price_change < -0.02:  # 2%+ down on huge volume
            return True, "selling_climax"
        else:
            return True, "neutral_climax"
    
    def detect_volume_dry_up(
        self,
        data: pd.DataFrame,
        consecutive_bars: int = 3
    ) -> bool:
        """
        Detect volume drying up (potential consolidation end).
        
        Args:
            data: OHLCV DataFrame
            consecutive_bars: Required consecutive low-volume bars
            
        Returns:
            True if volume is drying up
        """
        df = self.calculate(data)
        
        if len(df) < consecutive_bars:
            return False
        
        recent_states = df['volume_state'].tail(consecutive_bars)
        
        # Check if all recent bars are low or dry
        low_states = [VolumeState.LOW.value, VolumeState.DRY.value]
        
        return all(state in low_states for state in recent_states)
    
    def get_volume_profile(
        self,
        data: pd.DataFrame,
        bins: int = 20
    ) -> Dict[str, float]:
        """
        Calculate simple volume profile.
        
        Args:
            data: OHLCV DataFrame
            bins: Number of price bins
            
        Returns:
            Dict with volume profile data
        """
        if data.empty:
            return {}
        
        price_min = data['low'].min()
        price_max = data['high'].max()
        
        price_range = price_max - price_min
        bin_size = price_range / bins
        
        profile = {}
        
        for i in range(bins):
            bin_low = price_min + i * bin_size
            bin_high = bin_low + bin_size
            
            # Sum volume for bars that traded in this range
            mask = (data['low'] <= bin_high) & (data['high'] >= bin_low)
            profile[f"{bin_low:.2f}-{bin_high:.2f}"] = data.loc[mask, 'volume'].sum()
        
        # Find POC (Point of Control) - highest volume price
        poc_level = max(profile.keys(), key=lambda x: profile[x])
        
        return {
            "profile": profile,
            "poc_level": poc_level,
            "value_area_high": price_max * 0.7 + price_min * 0.3,  # Simplified
            "value_area_low": price_max * 0.3 + price_min * 0.7   # Simplified
        }


def calculate_relative_volume(
    current_volume: float,
    average_volume: float
) -> float:
    """
    Calculate relative volume (RVOL).
    
    Args:
        current_volume: Current bar volume
        average_volume: Average volume
        
    Returns:
        Relative volume ratio
    """
    if average_volume <= 0:
        return 1.0
    return current_volume / average_volume


def is_volume_expansion(
    data: pd.DataFrame,
    threshold: float = 1.5,
    lookback: int = 1
) -> bool:
    """
    Check if recent bars show volume expansion.
    
    Args:
        data: OHLCV DataFrame
        threshold: Minimum ratio for expansion
        lookback: Bars to check
        
    Returns:
        True if volume expanding
    """
    if len(data) < 21:  # Need 20 for MA + lookback
        return False
    
    vol_ma = data['volume'].rolling(20).mean()
    recent_vol = data['volume'].tail(lookback)
    recent_ma = vol_ma.tail(lookback)
    
    return (recent_vol / recent_ma).mean() >= threshold
