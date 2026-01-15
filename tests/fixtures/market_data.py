"""
Market data generators for testing.

Provides realistic market data with various characteristics:
- Trending (up/down)
- Range-bound
- Volatile
- Consolidation patterns
- Reversal patterns
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple


class MarketDataGenerator:
    """
    Generate realistic market data for testing.
    
    Supports creating data with specific market characteristics
    for testing different strategy behaviors.
    """
    
    def __init__(self, seed: int = 42):
        """Initialize with random seed for reproducibility."""
        self.seed = seed
        np.random.seed(seed)
    
    def reset_seed(self):
        """Reset random seed."""
        np.random.seed(self.seed)
    
    def generate(
        self,
        n_bars: int = 200,
        start_price: float = 100.0,
        pattern: str = "trend_up",
        volatility: float = 0.02,
        volume_base: int = 1000000
    ) -> pd.DataFrame:
        """
        Generate OHLCV data with specified pattern.
        
        Args:
            n_bars: Number of bars
            start_price: Starting price
            pattern: Market pattern type
            volatility: Daily volatility
            volume_base: Base volume level
            
        Returns:
            DataFrame with OHLCV data
        """
        patterns = {
            'trend_up': self._trend_up,
            'trend_down': self._trend_down,
            'sideways': self._sideways,
            'volatile': self._volatile,
            'consolidation': self._consolidation,
            'breakout': self._breakout,
            'reversal_up': self._reversal_up,
            'reversal_down': self._reversal_down,
            'parabolic': self._parabolic,
            'crash': self._crash,
            'recovery': self._recovery,
        }
        
        if pattern not in patterns:
            raise ValueError(f"Unknown pattern: {pattern}")
        
        return patterns[pattern](n_bars, start_price, volatility, volume_base)
    
    def _create_dataframe(
        self,
        prices: np.ndarray,
        volumes: np.ndarray
    ) -> pd.DataFrame:
        """Create DataFrame from price and volume arrays."""
        n_bars = len(prices)
        dates = pd.date_range(end=datetime.now(), periods=n_bars, freq='D')
        
        # Create OHLC with realistic intraday ranges
        daily_range = np.random.uniform(0.005, 0.015, n_bars)
        
        data = pd.DataFrame({
            'open': prices * (1 - np.random.uniform(0.002, 0.005, n_bars)),
            'high': prices * (1 + daily_range),
            'low': prices * (1 - daily_range),
            'close': prices,
            'volume': volumes.astype(int)
        }, index=dates)
        
        return data
    
    def _trend_up(
        self,
        n_bars: int,
        start_price: float,
        volatility: float,
        volume_base: int
    ) -> pd.DataFrame:
        """Generate uptrending data."""
        drift = 0.003  # ~0.3% daily drift
        returns = np.random.normal(drift, volatility, n_bars)
        prices = start_price * np.exp(np.cumsum(returns))
        
        # Volume increases on up days
        volume_mult = 1 + 0.2 * (returns > 0).astype(float)
        volumes = volume_base * np.random.uniform(0.8, 1.2, n_bars) * volume_mult
        
        return self._create_dataframe(prices, volumes)
    
    def _trend_down(
        self,
        n_bars: int,
        start_price: float,
        volatility: float,
        volume_base: int
    ) -> pd.DataFrame:
        """Generate downtrending data."""
        drift = -0.002
        returns = np.random.normal(drift, volatility, n_bars)
        prices = start_price * np.exp(np.cumsum(returns))
        
        # Volume increases on down days
        volume_mult = 1 + 0.3 * (returns < 0).astype(float)
        volumes = volume_base * np.random.uniform(0.8, 1.2, n_bars) * volume_mult
        
        return self._create_dataframe(prices, volumes)
    
    def _sideways(
        self,
        n_bars: int,
        start_price: float,
        volatility: float,
        volume_base: int
    ) -> pd.DataFrame:
        """Generate sideways/range-bound data."""
        # Mean-reverting process
        prices = [start_price]
        for i in range(1, n_bars):
            reversion = 0.1 * (start_price - prices[-1])
            noise = np.random.normal(0, volatility * start_price)
            prices.append(prices[-1] + reversion + noise)
        
        prices = np.array(prices)
        volumes = volume_base * np.random.uniform(0.7, 1.0, n_bars)
        
        return self._create_dataframe(prices, volumes)
    
    def _volatile(
        self,
        n_bars: int,
        start_price: float,
        volatility: float,
        volume_base: int
    ) -> pd.DataFrame:
        """Generate high volatility data."""
        # Double the normal volatility
        high_vol = volatility * 2.5
        returns = np.random.normal(0.001, high_vol, n_bars)
        prices = start_price * np.exp(np.cumsum(returns))
        
        # Higher volume on volatile days
        abs_returns = np.abs(returns)
        volume_mult = 1 + 2 * abs_returns / abs_returns.max()
        volumes = volume_base * np.random.uniform(1.0, 2.0, n_bars) * volume_mult
        
        return self._create_dataframe(prices, volumes)
    
    def _consolidation(
        self,
        n_bars: int,
        start_price: float,
        volatility: float,
        volume_base: int
    ) -> pd.DataFrame:
        """Generate rally then consolidation pattern."""
        prices = []
        volumes = []
        
        # Rally phase (40% of bars)
        rally_bars = int(n_bars * 0.4)
        for i in range(rally_bars):
            if i == 0:
                prices.append(start_price)
            else:
                prices.append(prices[-1] * (1 + np.random.uniform(0.002, 0.01)))
            volumes.append(volume_base * np.random.uniform(1.2, 1.8))
        
        # Consolidation phase (60% of bars)
        consolidation_center = prices[-1]
        consolidation_range = consolidation_center * 0.05  # 5% range
        
        for i in range(n_bars - rally_bars):
            price = consolidation_center + np.random.uniform(-consolidation_range, consolidation_range)
            prices.append(price)
            volumes.append(volume_base * np.random.uniform(0.6, 1.0))
        
        return self._create_dataframe(np.array(prices), np.array(volumes))
    
    def _breakout(
        self,
        n_bars: int,
        start_price: float,
        volatility: float,
        volume_base: int
    ) -> pd.DataFrame:
        """Generate consolidation then breakout pattern."""
        prices = []
        volumes = []
        
        # Consolidation phase (70% of bars)
        consolidation_bars = int(n_bars * 0.7)
        for i in range(consolidation_bars):
            price = start_price * (1 + np.random.uniform(-0.03, 0.03))
            prices.append(price)
            volumes.append(volume_base * np.random.uniform(0.7, 1.0))
        
        # Breakout phase (30% of bars)
        breakout_start = prices[-1]
        for i in range(n_bars - consolidation_bars):
            if i == 0:
                # Breakout bar - high volume
                prices.append(breakout_start * 1.05)
                volumes.append(volume_base * 3)
            else:
                # Continuation
                prices.append(prices[-1] * (1 + np.random.uniform(0.002, 0.015)))
                volumes.append(volume_base * np.random.uniform(1.2, 2.0))
        
        return self._create_dataframe(np.array(prices), np.array(volumes))
    
    def _reversal_up(
        self,
        n_bars: int,
        start_price: float,
        volatility: float,
        volume_base: int
    ) -> pd.DataFrame:
        """Generate downtrend then reversal up pattern."""
        prices = []
        volumes = []
        
        # Downtrend phase (60% of bars)
        down_bars = int(n_bars * 0.6)
        current = start_price
        for i in range(down_bars):
            current = current * (1 - np.random.uniform(0.001, 0.005))
            prices.append(current)
            volumes.append(volume_base * np.random.uniform(0.8, 1.2))
        
        # Bottom and reversal (40% of bars)
        bottom = prices[-1]
        for i in range(n_bars - down_bars):
            if i < 5:
                # Bottoming
                prices.append(bottom * (1 + np.random.uniform(-0.02, 0.02)))
                volumes.append(volume_base * np.random.uniform(1.5, 2.5))
            else:
                # Recovery
                prices.append(prices[-1] * (1 + np.random.uniform(0.002, 0.008)))
                volumes.append(volume_base * np.random.uniform(1.0, 1.5))
        
        return self._create_dataframe(np.array(prices), np.array(volumes))
    
    def _reversal_down(
        self,
        n_bars: int,
        start_price: float,
        volatility: float,
        volume_base: int
    ) -> pd.DataFrame:
        """Generate uptrend then reversal down pattern."""
        prices = []
        volumes = []
        
        # Uptrend phase (60% of bars)
        up_bars = int(n_bars * 0.6)
        current = start_price
        for i in range(up_bars):
            current = current * (1 + np.random.uniform(0.002, 0.008))
            prices.append(current)
            volumes.append(volume_base * np.random.uniform(1.0, 1.5))
        
        # Top and reversal (40% of bars)
        top = prices[-1]
        for i in range(n_bars - up_bars):
            if i < 5:
                # Topping
                prices.append(top * (1 + np.random.uniform(-0.02, 0.01)))
                volumes.append(volume_base * np.random.uniform(1.5, 2.5))
            else:
                # Decline
                prices.append(prices[-1] * (1 - np.random.uniform(0.003, 0.010)))
                volumes.append(volume_base * np.random.uniform(1.2, 2.0))
        
        return self._create_dataframe(np.array(prices), np.array(volumes))
    
    def _parabolic(
        self,
        n_bars: int,
        start_price: float,
        volatility: float,
        volume_base: int
    ) -> pd.DataFrame:
        """Generate parabolic move (exponential growth)."""
        # Accelerating returns
        t = np.linspace(0, 1, n_bars)
        growth = 0.5  # 50% total gain
        base_returns = growth * 3 * t ** 2  # Parabolic shape
        
        # Add noise
        noise = np.random.normal(0, volatility, n_bars)
        cumulative = base_returns + np.cumsum(noise)
        prices = start_price * (1 + cumulative)
        
        # Volume increases exponentially
        volumes = volume_base * (1 + 2 * t ** 2) * np.random.uniform(0.8, 1.2, n_bars)
        
        return self._create_dataframe(prices, volumes)
    
    def _crash(
        self,
        n_bars: int,
        start_price: float,
        volatility: float,
        volume_base: int
    ) -> pd.DataFrame:
        """Generate market crash scenario."""
        prices = []
        volumes = []
        
        # Normal period (50% of bars)
        normal_bars = int(n_bars * 0.5)
        current = start_price
        for i in range(normal_bars):
            current = current * (1 + np.random.uniform(-0.005, 0.005))
            prices.append(current)
            volumes.append(volume_base * np.random.uniform(0.8, 1.2))
        
        # Crash period (20% of bars) - lose 30%
        crash_bars = int(n_bars * 0.2)
        for i in range(crash_bars):
            drop = np.random.uniform(0.01, 0.03)
            prices.append(prices[-1] * (1 - drop))
            volumes.append(volume_base * np.random.uniform(3, 5))
        
        # Bounce/recovery (30% of bars)
        for i in range(n_bars - normal_bars - crash_bars):
            prices.append(prices[-1] * (1 + np.random.uniform(-0.01, 0.02)))
            volumes.append(volume_base * np.random.uniform(1.5, 2.5))
        
        return self._create_dataframe(np.array(prices), np.array(volumes))
    
    def _recovery(
        self,
        n_bars: int,
        start_price: float,
        volatility: float,
        volume_base: int
    ) -> pd.DataFrame:
        """Generate V-shaped recovery pattern."""
        prices = []
        volumes = []
        
        # Initial decline (30% of bars)
        decline_bars = int(n_bars * 0.3)
        current = start_price
        for i in range(decline_bars):
            current = current * (1 - np.random.uniform(0.005, 0.015))
            prices.append(current)
            volumes.append(volume_base * np.random.uniform(1.2, 2.0))
        
        # Bottom (10% of bars)
        bottom = prices[-1]
        bottom_bars = int(n_bars * 0.1)
        for i in range(bottom_bars):
            prices.append(bottom * (1 + np.random.uniform(-0.02, 0.02)))
            volumes.append(volume_base * np.random.uniform(1.5, 2.5))
        
        # Recovery (60% of bars)
        for i in range(n_bars - decline_bars - bottom_bars):
            prices.append(prices[-1] * (1 + np.random.uniform(0.003, 0.012)))
            volumes.append(volume_base * np.random.uniform(1.0, 1.5))
        
        return self._create_dataframe(np.array(prices), np.array(volumes))


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_trend_data(
    n_bars: int = 120,
    start_price: float = 100.0,
    direction: str = "up",
    seed: int = 42
) -> pd.DataFrame:
    """Create trending data."""
    gen = MarketDataGenerator(seed)
    pattern = "trend_up" if direction == "up" else "trend_down"
    return gen.generate(n_bars, start_price, pattern)


def create_range_data(
    n_bars: int = 120,
    start_price: float = 100.0,
    seed: int = 42
) -> pd.DataFrame:
    """Create range-bound data."""
    gen = MarketDataGenerator(seed)
    return gen.generate(n_bars, start_price, "sideways")


def create_volatile_data(
    n_bars: int = 120,
    start_price: float = 100.0,
    seed: int = 42
) -> pd.DataFrame:
    """Create high volatility data."""
    gen = MarketDataGenerator(seed)
    return gen.generate(n_bars, start_price, "volatile", volatility=0.03)


def create_consolidation_data(
    n_bars: int = 120,
    start_price: float = 100.0,
    seed: int = 42
) -> pd.DataFrame:
    """Create consolidation pattern data."""
    gen = MarketDataGenerator(seed)
    return gen.generate(n_bars, start_price, "consolidation")


def create_reversal_data(
    n_bars: int = 120,
    start_price: float = 100.0,
    direction: str = "up",
    seed: int = 42
) -> pd.DataFrame:
    """Create reversal pattern data."""
    gen = MarketDataGenerator(seed)
    pattern = "reversal_up" if direction == "up" else "reversal_down"
    return gen.generate(n_bars, start_price, pattern)


def create_parabolic_data(
    n_bars: int = 120,
    start_price: float = 100.0,
    seed: int = 42
) -> pd.DataFrame:
    """Create parabolic move data."""
    gen = MarketDataGenerator(seed)
    return gen.generate(n_bars, start_price, "parabolic")
