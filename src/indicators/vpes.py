"""
Volume-Price Expansion Score (VPES) Indicator
Core custom indicator for measuring price-volume conviction.

Principle: Reliable rallies are accompanied by volume expansion.
No-volume rallies have reduced credibility.
"""

from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np
from dataclasses import dataclass
from loguru import logger


@dataclass
class VPESResult:
    """Container for VPES calculation results."""
    vpes: float                    # Current VPES value
    vpes_ma: float                 # Smoothed VPES
    price_change: float            # Price change percentage
    volume_ratio: float            # Volume relative to average
    signal_strength: str           # "strong", "moderate", "weak", "none"
    cumulative_vpes: float         # Rolling cumulative VPES
    

class VPES:
    """
    Volume-Price Expansion Score Calculator.
    
    VPES = PriceChange * VolumeRatio
    
    Where:
    - PriceChange = (Close - Open) / Open
    - VolumeRatio = Volume / MA(Volume, N)
    
    High VPES + trend alignment = strong signal
    Low or negative VPES = weak/no signal
    """
    
    def __init__(
        self,
        volume_ma_period: int = 20,
        smoothing_period: int = 3,
        cumulative_window: int = 5
    ):
        """
        Initialize VPES calculator.
        
        Args:
            volume_ma_period: Period for volume moving average
            smoothing_period: Period for VPES smoothing
            cumulative_window: Window for cumulative VPES
        """
        self.volume_ma_period = volume_ma_period
        self.smoothing_period = smoothing_period
        self.cumulative_window = cumulative_window
    
    def calculate(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate VPES for entire dataset.
        
        Args:
            data: DataFrame with OHLCV columns
            
        Returns:
            DataFrame with VPES columns added
        """
        if data.empty:
            return data
        
        df = data.copy()
        
        # Ensure required columns exist
        required = ['open', 'high', 'low', 'close', 'volume']
        for col in required:
            if col not in df.columns:
                logger.error(f"Missing required column: {col}")
                return data
        
        # Calculate price change (percentage)
        df['price_change'] = (df['close'] - df['open']) / df['open']
        
        # Calculate volume moving average
        df['volume_ma'] = df['volume'].rolling(window=self.volume_ma_period).mean()
        
        # Calculate volume ratio
        df['volume_ratio'] = df['volume'] / df['volume_ma']
        df['volume_ratio'] = df['volume_ratio'].fillna(1.0)
        
        # Calculate raw VPES
        df['vpes'] = df['price_change'] * df['volume_ratio']
        
        # Smoothed VPES (EMA)
        df['vpes_ema'] = df['vpes'].ewm(span=self.smoothing_period, adjust=False).mean()
        
        # Cumulative VPES (sum over rolling window)
        df['vpes_cumulative'] = df['vpes'].rolling(window=self.cumulative_window).sum()
        
        # VPES momentum (change in VPES)
        df['vpes_momentum'] = df['vpes_ema'].diff()
        
        # Signal strength classification
        df['vpes_signal'] = df['vpes'].apply(self._classify_signal)
        
        return df
    
    def get_current_vpes(self, data: pd.DataFrame) -> VPESResult:
        """
        Get current VPES reading.
        
        Args:
            data: DataFrame with OHLCV data
            
        Returns:
            VPESResult with current values
        """
        if data.empty:
            return VPESResult(
                vpes=0.0,
                vpes_ma=0.0,
                price_change=0.0,
                volume_ratio=0.0,
                signal_strength="none",
                cumulative_vpes=0.0
            )
        
        df = self.calculate(data)
        
        if df.empty or 'vpes' not in df.columns:
            return VPESResult(
                vpes=0.0,
                vpes_ma=0.0,
                price_change=0.0,
                volume_ratio=0.0,
                signal_strength="none",
                cumulative_vpes=0.0
            )
        
        latest = df.iloc[-1]
        
        return VPESResult(
            vpes=latest.get('vpes', 0.0),
            vpes_ma=latest.get('vpes_ema', 0.0),
            price_change=latest.get('price_change', 0.0),
            volume_ratio=latest.get('volume_ratio', 1.0),
            signal_strength=latest.get('vpes_signal', 'none'),
            cumulative_vpes=latest.get('vpes_cumulative', 0.0)
        )
    
    def _classify_signal(self, vpes: float) -> str:
        """Classify VPES signal strength."""
        if pd.isna(vpes):
            return "none"
        
        abs_vpes = abs(vpes)
        
        if abs_vpes >= 0.02:  # 2%+ expansion
            return "strong"
        elif abs_vpes >= 0.01:  # 1%+ expansion
            return "moderate"
        elif abs_vpes >= 0.005:  # 0.5%+ expansion
            return "weak"
        else:
            return "none"
    
    def is_volume_confirming(
        self,
        data: pd.DataFrame,
        direction: str = "up",
        min_volume_ratio: float = 1.5
    ) -> bool:
        """
        Check if volume confirms price direction.
        
        Args:
            data: OHLCV DataFrame
            direction: "up" or "down"
            min_volume_ratio: Minimum volume ratio for confirmation
            
        Returns:
            True if volume confirms direction
        """
        result = self.get_current_vpes(data)
        
        if result.volume_ratio < min_volume_ratio:
            return False
        
        if direction == "up":
            return result.vpes > 0 and result.price_change > 0
        else:
            return result.vpes < 0 and result.price_change < 0
    
    def detect_divergence(
        self,
        data: pd.DataFrame,
        lookback: int = 10
    ) -> Tuple[bool, str]:
        """
        Detect price-volume divergence.
        
        Args:
            data: OHLCV DataFrame
            lookback: Number of bars to analyze
            
        Returns:
            (has_divergence, divergence_type)
        """
        if len(data) < lookback:
            return False, "none"
        
        df = self.calculate(data)
        recent = df.tail(lookback)
        
        # Price trend
        price_slope = (recent['close'].iloc[-1] - recent['close'].iloc[0]) / recent['close'].iloc[0]
        
        # VPES trend
        vpes_slope = recent['vpes_ema'].iloc[-1] - recent['vpes_ema'].iloc[0]
        
        # Bearish divergence: price up, VPES down
        if price_slope > 0.02 and vpes_slope < -0.005:
            return True, "bearish"
        
        # Bullish divergence: price down, VPES up
        if price_slope < -0.02 and vpes_slope > 0.005:
            return True, "bullish"
        
        return False, "none"


def calculate_vpes(
    open_price: float,
    close_price: float,
    volume: float,
    volume_ma: float
) -> float:
    """
    Calculate single-bar VPES.
    
    Args:
        open_price: Bar open price
        close_price: Bar close price
        volume: Bar volume
        volume_ma: Volume moving average
        
    Returns:
        VPES value
    """
    if open_price <= 0 or volume_ma <= 0:
        return 0.0
    
    price_change = (close_price - open_price) / open_price
    volume_ratio = volume / volume_ma
    
    return price_change * volume_ratio


def calculate_multi_tf_vpes(
    data_dict: Dict[str, pd.DataFrame],
    weights: Optional[Dict[str, float]] = None
) -> Dict[str, VPESResult]:
    """
    Calculate VPES across multiple timeframes.
    
    Args:
        data_dict: Dict mapping timeframe to DataFrame
        weights: Optional weights for each timeframe
        
    Returns:
        Dict mapping timeframe to VPESResult
    """
    vpes_calc = VPES()
    results = {}
    
    # Default weights if not provided
    if weights is None:
        weights = {tf: 1.0 / len(data_dict) for tf in data_dict}
    
    for tf, data in data_dict.items():
        results[tf] = vpes_calc.get_current_vpes(data)
    
    return results


def get_weighted_vpes_score(
    vpes_results: Dict[str, VPESResult],
    weights: Dict[str, float]
) -> float:
    """
    Calculate weighted VPES score across timeframes.
    
    Args:
        vpes_results: Dict of VPES results per timeframe
        weights: Timeframe weights
        
    Returns:
        Weighted VPES score
    """
    total_weight = 0.0
    weighted_sum = 0.0
    
    for tf, result in vpes_results.items():
        w = weights.get(tf, 0.0)
        weighted_sum += result.vpes * w
        total_weight += w
    
    if total_weight == 0:
        return 0.0
    
    return weighted_sum / total_weight


class VPESAnalyzer:
    """
    Advanced VPES analysis with pattern detection.
    """
    
    def __init__(self, vpes: Optional[VPES] = None):
        """Initialize analyzer."""
        self.vpes = vpes or VPES()
    
    def detect_accumulation(
        self,
        data: pd.DataFrame,
        lookback: int = 20
    ) -> Tuple[bool, float]:
        """
        Detect accumulation phase (building position before breakout).
        
        Characteristics:
        - Price relatively flat
        - Volume gradually increasing
        - Positive VPES on up days stronger than negative on down days
        
        Args:
            data: OHLCV DataFrame
            lookback: Analysis window
            
        Returns:
            (is_accumulating, confidence_score)
        """
        if len(data) < lookback:
            return False, 0.0
        
        df = self.vpes.calculate(data.tail(lookback))
        
        # Check price flatness
        price_range = (df['high'].max() - df['low'].min()) / df['close'].mean()
        is_flat = price_range < 0.10  # Less than 10% range
        
        # Check volume trend
        vol_first_half = df['volume'].iloc[:lookback//2].mean()
        vol_second_half = df['volume'].iloc[lookback//2:].mean()
        volume_increasing = vol_second_half > vol_first_half * 1.1
        
        # Check VPES asymmetry
        positive_vpes = df[df['vpes'] > 0]['vpes'].mean()
        negative_vpes = abs(df[df['vpes'] < 0]['vpes'].mean())
        
        if pd.isna(positive_vpes):
            positive_vpes = 0
        if pd.isna(negative_vpes):
            negative_vpes = 0
        
        vpes_favorable = positive_vpes > negative_vpes * 1.2
        
        # Calculate confidence
        confidence = 0.0
        if is_flat:
            confidence += 0.3
        if volume_increasing:
            confidence += 0.4
        if vpes_favorable:
            confidence += 0.3
        
        is_accumulating = confidence >= 0.6
        
        return is_accumulating, confidence
    
    def detect_distribution(
        self,
        data: pd.DataFrame,
        lookback: int = 20
    ) -> Tuple[bool, float]:
        """
        Detect distribution phase (selling before breakdown).
        
        Characteristics:
        - Price at highs but struggling
        - High volume on down days
        - Negative VPES on down days stronger than positive on up days
        
        Args:
            data: OHLCV DataFrame
            lookback: Analysis window
            
        Returns:
            (is_distributing, confidence_score)
        """
        if len(data) < lookback:
            return False, 0.0
        
        df = self.vpes.calculate(data.tail(lookback))
        
        # Check if price near highs
        current_price = df['close'].iloc[-1]
        period_high = df['high'].max()
        near_highs = current_price >= period_high * 0.95
        
        # Check volume on down vs up days
        up_days = df[df['close'] > df['open']]
        down_days = df[df['close'] <= df['open']]
        
        avg_up_volume = up_days['volume'].mean() if len(up_days) > 0 else 0
        avg_down_volume = down_days['volume'].mean() if len(down_days) > 0 else 0
        
        high_down_volume = avg_down_volume > avg_up_volume * 1.2
        
        # Check VPES asymmetry (opposite of accumulation)
        positive_vpes = df[df['vpes'] > 0]['vpes'].mean()
        negative_vpes = abs(df[df['vpes'] < 0]['vpes'].mean())
        
        if pd.isna(positive_vpes):
            positive_vpes = 0
        if pd.isna(negative_vpes):
            negative_vpes = 0
        
        vpes_unfavorable = negative_vpes > positive_vpes * 1.2
        
        # Calculate confidence
        confidence = 0.0
        if near_highs:
            confidence += 0.2
        if high_down_volume:
            confidence += 0.4
        if vpes_unfavorable:
            confidence += 0.4
        
        is_distributing = confidence >= 0.6
        
        return is_distributing, confidence
    
    def find_vpes_breakout(
        self,
        data: pd.DataFrame,
        threshold: float = 0.02
    ) -> Tuple[bool, int]:
        """
        Find recent VPES breakout (sudden volume-price expansion).
        
        Args:
            data: OHLCV DataFrame
            threshold: VPES threshold for breakout
            
        Returns:
            (has_breakout, bars_ago)
        """
        df = self.vpes.calculate(data)
        
        # Find bars with VPES above threshold
        breakout_mask = df['vpes'] >= threshold
        
        if not breakout_mask.any():
            return False, -1
        
        # Find most recent breakout
        last_breakout_idx = breakout_mask[::-1].idxmax()
        bars_ago = len(df) - df.index.get_loc(last_breakout_idx) - 1
        
        # Only count recent breakouts
        if bars_ago <= 10:
            return True, bars_ago
        
        return False, -1
