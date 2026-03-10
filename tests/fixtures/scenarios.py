"""
Test scenarios for integration and E2E testing.

Provides pre-defined market scenarios that combine multiple symbols
with different characteristics to test system behavior.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from .market_data import MarketDataGenerator


def create_bull_market_scenario(
    n_bars: int = 252,
    n_symbols: int = 10,
    seed: int = 42
) -> Dict[str, pd.DataFrame]:
    """
    Create a bull market scenario.
    
    Most stocks are trending up with varying momentum.
    
    Args:
        n_bars: Number of bars (default 1 year)
        n_symbols: Number of symbols
        seed: Random seed
        
    Returns:
        Dict mapping symbol to DataFrame
    """
    np.random.seed(seed)
    gen = MarketDataGenerator(seed)
    
    data = {}
    patterns = {
        'parabolic': 2,      # 20% of stocks
        'trend_up': 5,       # 50% of stocks
        'consolidation': 2,  # 20% of stocks
        'sideways': 1,       # 10% of stocks
    }
    
    symbol_idx = 0
    for pattern, count in patterns.items():
        for i in range(count):
            symbol = f"BULL_{symbol_idx:02d}"
            start_price = np.random.uniform(50, 200)
            data[symbol] = gen.generate(n_bars, start_price, pattern)
            symbol_idx += 1
    
    return data


def create_bear_market_scenario(
    n_bars: int = 252,
    n_symbols: int = 10,
    seed: int = 42
) -> Dict[str, pd.DataFrame]:
    """
    Create a bear market scenario.
    
    Most stocks are declining.
    
    Returns:
        Dict mapping symbol to DataFrame
    """
    np.random.seed(seed)
    gen = MarketDataGenerator(seed)
    
    data = {}
    patterns = {
        'trend_down': 5,     # 50% declining
        'crash': 2,          # 20% crashing
        'sideways': 2,       # 20% sideways
        'reversal_up': 1,    # 10% starting to turn
    }
    
    symbol_idx = 0
    for pattern, count in patterns.items():
        for i in range(count):
            symbol = f"BEAR_{symbol_idx:02d}"
            start_price = np.random.uniform(50, 200)
            data[symbol] = gen.generate(n_bars, start_price, pattern)
            symbol_idx += 1
    
    return data


def create_choppy_market_scenario(
    n_bars: int = 252,
    n_symbols: int = 10,
    seed: int = 42
) -> Dict[str, pd.DataFrame]:
    """
    Create a choppy/uncertain market scenario.
    
    Mix of patterns with high volatility.
    
    Returns:
        Dict mapping symbol to DataFrame
    """
    np.random.seed(seed)
    gen = MarketDataGenerator(seed)
    
    data = {}
    patterns = {
        'volatile': 3,        # 30% high vol
        'sideways': 3,        # 30% range-bound
        'reversal_up': 2,     # 20% reversing up
        'reversal_down': 2,   # 20% reversing down
    }
    
    symbol_idx = 0
    for pattern, count in patterns.items():
        for i in range(count):
            symbol = f"CHOP_{symbol_idx:02d}"
            start_price = np.random.uniform(50, 200)
            volatility = 0.03 if pattern != 'volatile' else 0.05
            data[symbol] = gen.generate(n_bars, start_price, pattern, volatility)
            symbol_idx += 1
    
    return data


def create_crash_scenario(
    n_bars: int = 126,
    n_symbols: int = 10,
    seed: int = 42
) -> Dict[str, pd.DataFrame]:
    """
    Create a market crash scenario.
    
    All stocks experience sharp decline then recovery.
    
    Returns:
        Dict mapping symbol to DataFrame
    """
    np.random.seed(seed)
    gen = MarketDataGenerator(seed)
    
    data = {}
    
    for i in range(n_symbols):
        symbol = f"CRASH_{i:02d}"
        start_price = np.random.uniform(100, 300)
        # Most crash, some just decline
        if i < 7:
            data[symbol] = gen.generate(n_bars, start_price, "crash")
        else:
            data[symbol] = gen.generate(n_bars, start_price, "trend_down")
    
    return data


def create_recovery_scenario(
    n_bars: int = 126,
    n_symbols: int = 10,
    seed: int = 42
) -> Dict[str, pd.DataFrame]:
    """
    Create a market recovery scenario.
    
    Most stocks recovering from lows.
    
    Returns:
        Dict mapping symbol to DataFrame
    """
    np.random.seed(seed)
    gen = MarketDataGenerator(seed)
    
    data = {}
    patterns = {
        'recovery': 5,        # 50% V-recovery
        'reversal_up': 3,     # 30% reversal
        'trend_up': 2,        # 20% already trending
    }
    
    symbol_idx = 0
    for pattern, count in patterns.items():
        for i in range(count):
            symbol = f"RECV_{symbol_idx:02d}"
            start_price = np.random.uniform(50, 150)
            data[symbol] = gen.generate(n_bars, start_price, pattern)
            symbol_idx += 1
    
    return data


def create_sector_rotation_scenario(
    n_bars: int = 252,
    seed: int = 42
) -> Dict[str, pd.DataFrame]:
    """
    Create a sector rotation scenario.
    
    Different "sectors" lead at different times.
    
    Returns:
        Dict mapping symbol to DataFrame with sector info
    """
    np.random.seed(seed)
    gen = MarketDataGenerator(seed)
    
    sectors = {
        'TECH': ['TECH_01', 'TECH_02', 'TECH_03'],
        'ENERGY': ['ENRG_01', 'ENRG_02', 'ENRG_03'],
        'HEALTH': ['HLTH_01', 'HLTH_02', 'HLTH_03'],
    }
    
    data = {}
    
    # Tech leads first half
    for symbol in sectors['TECH']:
        prices = []
        current = np.random.uniform(100, 200)
        
        # First half: strong uptrend
        for i in range(n_bars // 2):
            current *= (1 + np.random.uniform(0.002, 0.010))
            prices.append(current)
        
        # Second half: consolidation
        for i in range(n_bars - n_bars // 2):
            current *= (1 + np.random.uniform(-0.005, 0.005))
            prices.append(current)
        
        df = _create_ohlcv_from_prices(np.array(prices))
        data[symbol] = df
    
    # Energy leads second half
    for symbol in sectors['ENERGY']:
        prices = []
        current = np.random.uniform(50, 100)
        
        # First half: sideways
        for i in range(n_bars // 2):
            current *= (1 + np.random.uniform(-0.005, 0.005))
            prices.append(current)
        
        # Second half: strong uptrend
        for i in range(n_bars - n_bars // 2):
            current *= (1 + np.random.uniform(0.002, 0.012))
            prices.append(current)
        
        df = _create_ohlcv_from_prices(np.array(prices))
        data[symbol] = df
    
    # Health steady throughout
    for symbol in sectors['HEALTH']:
        data[symbol] = gen.generate(n_bars, np.random.uniform(80, 150), "trend_up", 0.015)
    
    return data


def create_mixed_regime_scenario(
    n_bars: int = 252,
    seed: int = 42
) -> Dict[str, pd.DataFrame]:
    """
    Create a scenario with different regimes for different stocks.
    
    Useful for testing strategy selection logic.
    
    Returns:
        Dict mapping symbol to DataFrame
    """
    np.random.seed(seed)
    gen = MarketDataGenerator(seed)
    
    # Create one stock for each regime/pattern type
    scenarios = {
        'PARABOLIC_01': 'parabolic',
        'STRONG_UP_01': 'trend_up',
        'STRONG_UP_02': 'breakout',
        'MOD_UP_01': 'consolidation',
        'SIDEWAYS_01': 'sideways',
        'SIDEWAYS_02': 'sideways',
        'DOWN_01': 'trend_down',
        'REVERSAL_01': 'reversal_up',
        'VOLATILE_01': 'volatile',
        'CRASH_01': 'crash',
    }
    
    data = {}
    for symbol, pattern in scenarios.items():
        start_price = np.random.uniform(50, 200)
        data[symbol] = gen.generate(n_bars, start_price, pattern)
    
    return data


def _create_ohlcv_from_prices(prices: np.ndarray) -> pd.DataFrame:
    """Helper to create OHLCV DataFrame from price array."""
    n_bars = len(prices)
    dates = pd.date_range(end=datetime.now(), periods=n_bars, freq='D')
    
    return pd.DataFrame({
        'open': prices * (1 - np.random.uniform(0.002, 0.005, n_bars)),
        'high': prices * (1 + np.random.uniform(0.005, 0.015, n_bars)),
        'low': prices * (1 - np.random.uniform(0.005, 0.015, n_bars)),
        'close': prices,
        'volume': np.random.randint(1000000, 5000000, n_bars)
    }, index=dates)
