"""
Multi-Timeframe Data Manager
Handles data aggregation, caching, and synchronization across timeframes.
"""

import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import pandas as pd
import numpy as np
from loguru import logger

from .ibkr_client import IBKRClient


class DataManager:
    """
    Manages multi-timeframe OHLCV data.
    - Fetches data from IBKR or local cache
    - Aggregates lower timeframes to higher ones
    - Ensures time consistency across all timeframes
    """
    
    # Timeframe hierarchy (in minutes)
    TIMEFRAME_MINUTES = {
        "1 min": 1,
        "5 mins": 5,
        "15 mins": 15,
        "30 mins": 30,
        "1 hour": 60,
        "4 hours": 240,
        "1 day": 1440,
        "1 week": 10080,
    }
    
    def __init__(
        self,
        ibkr_client: Optional[IBKRClient] = None,
        cache_dir: str = "data/cache",
        base_timeframe: str = "5 mins"
    ):
        """
        Initialize DataManager.
        
        Args:
            ibkr_client: IBKR client for live data
            cache_dir: Directory for caching data
            base_timeframe: Base timeframe for aggregation
        """
        self.ibkr = ibkr_client
        self.cache_dir = Path(cache_dir)
        self.base_timeframe = base_timeframe
        
        # Create cache directory
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # In-memory data store: {symbol: {timeframe: DataFrame}}
        self._data: Dict[str, Dict[str, pd.DataFrame]] = {}
        
    def get_data(
        self,
        symbol: str,
        timeframe: str,
        lookback_bars: int = 200,
        end_date: Optional[datetime] = None
    ) -> pd.DataFrame:
        """
        Get OHLCV data for a symbol and timeframe.
        
        Args:
            symbol: Stock ticker
            timeframe: Timeframe string
            lookback_bars: Number of bars to retrieve
            end_date: End date (None = now)
            
        Returns:
            DataFrame with OHLCV data
        """
        # Check in-memory cache first
        if symbol in self._data and timeframe in self._data[symbol]:
            cached = self._data[symbol][timeframe]
            if len(cached) >= lookback_bars:
                return cached.tail(lookback_bars).copy()
        
        # Try loading from disk cache
        cached = self._load_from_cache(symbol, timeframe)
        if cached is not None and len(cached) >= lookback_bars:
            self._store_in_memory(symbol, timeframe, cached)
            return cached.tail(lookback_bars).copy()
        
        # Fetch from IBKR if connected
        if self.ibkr and self.ibkr.is_connected:
            data = self._fetch_from_ibkr(symbol, timeframe, lookback_bars, end_date)
            if not data.empty:
                self._store_in_memory(symbol, timeframe, data)
                self._save_to_cache(symbol, timeframe, data)
                return data.tail(lookback_bars).copy()
        
        # Return whatever we have
        if cached is not None:
            return cached.tail(lookback_bars).copy()
        
        logger.warning(f"No data available for {symbol} {timeframe}")
        return pd.DataFrame()
    
    def get_multi_timeframe_data(
        self,
        symbol: str,
        timeframes: List[str],
        lookback_bars: int = 200,
        end_date: Optional[datetime] = None
    ) -> Dict[str, pd.DataFrame]:
        """
        Get data for multiple timeframes.
        
        Args:
            symbol: Stock ticker
            timeframes: List of timeframe strings
            lookback_bars: Bars per timeframe
            end_date: End date
            
        Returns:
            Dict mapping timeframe to DataFrame
        """
        result = {}
        for tf in timeframes:
            result[tf] = self.get_data(symbol, tf, lookback_bars, end_date)
        return result
    
    def aggregate_timeframe(
        self,
        data: pd.DataFrame,
        target_timeframe: str
    ) -> pd.DataFrame:
        """
        Aggregate data from a lower timeframe to a higher one.
        
        Args:
            data: Source OHLCV data
            target_timeframe: Target timeframe to aggregate to
            
        Returns:
            Aggregated DataFrame
        """
        if data.empty:
            return pd.DataFrame()
        
        # Get resample rule
        rule = self._get_resample_rule(target_timeframe)
        
        # Ensure datetime index
        if not isinstance(data.index, pd.DatetimeIndex):
            if 'datetime' in data.columns:
                data = data.set_index('datetime')
            else:
                logger.error("Data must have datetime index or column")
                return pd.DataFrame()
        
        # Resample OHLCV
        agg_dict = {
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }
        
        # Only aggregate columns that exist
        agg_dict = {k: v for k, v in agg_dict.items() if k in data.columns}
        
        resampled = data.resample(rule).agg(agg_dict)
        resampled = resampled.dropna()
        
        return resampled
    
    def _get_resample_rule(self, timeframe: str) -> str:
        """Convert timeframe to pandas resample rule."""
        rules = {
            "1 min": "1min",
            "5 mins": "5min",
            "15 mins": "15min",
            "30 mins": "30min",
            "1 hour": "1h",
            "4 hours": "4h",
            "1 day": "1D",
            "1 week": "1W",
        }
        return rules.get(timeframe, "1D")
    
    def _fetch_from_ibkr(
        self,
        symbol: str,
        timeframe: str,
        lookback_bars: int,
        end_date: Optional[datetime]
    ) -> pd.DataFrame:
        """Fetch data from IBKR API."""
        # Calculate required duration
        minutes_per_bar = self.TIMEFRAME_MINUTES.get(timeframe, 1440)
        total_minutes = lookback_bars * minutes_per_bar
        
        # Add buffer for weekends/holidays
        days_needed = int(total_minutes / 390) + 10  # 390 = trading minutes per day
        
        if days_needed > 365:
            duration = f"{int(days_needed / 365) + 1} Y"
        else:
            duration = f"{days_needed} D"
        
        return self.ibkr.get_historical_data(
            symbol=symbol,
            bar_size=timeframe,
            duration=duration,
            end_datetime=end_date
        )
    
    def _store_in_memory(self, symbol: str, timeframe: str, data: pd.DataFrame):
        """Store data in memory cache."""
        if symbol not in self._data:
            self._data[symbol] = {}
        self._data[symbol][timeframe] = data.copy()
    
    def _load_from_cache(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        """Load data from disk cache."""
        cache_file = self._get_cache_path(symbol, timeframe)
        
        if cache_file.exists():
            try:
                data = pd.read_parquet(cache_file)
                logger.debug(f"Loaded {symbol} {timeframe} from cache")
                return data
            except Exception as e:
                logger.warning(f"Error loading cache: {e}")
        
        return None
    
    def _save_to_cache(self, symbol: str, timeframe: str, data: pd.DataFrame):
        """Save data to disk cache."""
        cache_file = self._get_cache_path(symbol, timeframe)
        
        try:
            data.to_parquet(cache_file)
            logger.debug(f"Saved {symbol} {timeframe} to cache")
        except Exception as e:
            logger.warning(f"Error saving cache: {e}")
    
    def _get_cache_path(self, symbol: str, timeframe: str) -> Path:
        """Get cache file path for symbol/timeframe."""
        tf_clean = timeframe.replace(" ", "_")
        return self.cache_dir / f"{symbol}_{tf_clean}.parquet"
    
    def clear_cache(self, symbol: Optional[str] = None):
        """Clear cached data."""
        if symbol:
            # Clear specific symbol
            if symbol in self._data:
                del self._data[symbol]
            for f in self.cache_dir.glob(f"{symbol}_*.parquet"):
                f.unlink()
        else:
            # Clear all
            self._data.clear()
            for f in self.cache_dir.glob("*.parquet"):
                f.unlink()
        
        logger.info(f"Cleared cache for {symbol or 'all symbols'}")
    
    def update_realtime(self, symbol: str, timeframe: str, bar: Dict):
        """
        Update data with real-time bar.
        
        Args:
            symbol: Stock ticker
            timeframe: Timeframe
            bar: New bar data dict
        """
        if symbol not in self._data:
            self._data[symbol] = {}
        
        if timeframe not in self._data[symbol]:
            self._data[symbol][timeframe] = pd.DataFrame()
        
        # Create new row
        new_row = pd.DataFrame([{
            'open': bar.get('open'),
            'high': bar.get('high'),
            'low': bar.get('low'),
            'close': bar.get('close'),
            'volume': bar.get('volume', 0)
        }], index=[pd.Timestamp(bar.get('datetime', datetime.now()))])
        
        # Append or update
        df = self._data[symbol][timeframe]
        
        if new_row.index[0] in df.index:
            # Update existing bar
            df.loc[new_row.index[0]] = new_row.iloc[0]
        else:
            # Append new bar
            self._data[symbol][timeframe] = pd.concat([df, new_row])
    
    def get_latest_bar(self, symbol: str, timeframe: str) -> Optional[Dict]:
        """Get the most recent bar for a symbol/timeframe."""
        if symbol in self._data and timeframe in self._data[symbol]:
            df = self._data[symbol][timeframe]
            if not df.empty:
                return df.iloc[-1].to_dict()
        return None
    
    def synchronize_timeframes(
        self,
        symbol: str,
        timeframes: List[str]
    ) -> Dict[str, pd.DataFrame]:
        """
        Ensure all timeframes have synchronized end times.
        Higher timeframes should not have bars newer than lower ones.
        
        Args:
            symbol: Stock ticker
            timeframes: List of timeframes to sync
            
        Returns:
            Synchronized data dict
        """
        # Sort timeframes by granularity
        sorted_tfs = sorted(
            timeframes,
            key=lambda x: self.TIMEFRAME_MINUTES.get(x, 0)
        )
        
        # Get data for all timeframes
        data = {tf: self.get_data(symbol, tf) for tf in sorted_tfs}
        
        # Find the latest common timestamp
        min_end_times = []
        for tf, df in data.items():
            if not df.empty:
                min_end_times.append(df.index[-1])
        
        if not min_end_times:
            return data
        
        # Use the earliest end time across all timeframes
        sync_time = min(min_end_times)
        
        # Truncate each timeframe to sync time
        for tf in data:
            if not data[tf].empty:
                data[tf] = data[tf][data[tf].index <= sync_time]
        
        return data


class BacktestDataManager(DataManager):
    """
    Data manager specialized for backtesting.
    Loads historical data from files and simulates real-time updates.
    """
    
    def __init__(
        self,
        data_dir: str = "data/historical",
        base_timeframe: str = "5 mins"
    ):
        """
        Initialize backtest data manager.
        
        Args:
            data_dir: Directory containing historical data
            base_timeframe: Base timeframe
        """
        super().__init__(ibkr_client=None, cache_dir=data_dir, base_timeframe=base_timeframe)
        self.data_dir = Path(data_dir)
        self._current_index: Dict[str, int] = {}
    
    def load_historical_data(
        self,
        symbol: str,
        filepath: str,
        timeframe: str = "1 day"
    ) -> bool:
        """
        Load historical data from CSV file.
        
        Args:
            symbol: Stock ticker
            filepath: Path to CSV file
            timeframe: Timeframe of the data
            
        Returns:
            True if successful
        """
        try:
            data = pd.read_csv(filepath, parse_dates=['date'])
            data = data.rename(columns={'date': 'datetime'})
            data = data.set_index('datetime')
            
            # Ensure required columns
            required = ['open', 'high', 'low', 'close', 'volume']
            for col in required:
                if col not in data.columns:
                    logger.error(f"Missing column: {col}")
                    return False
            
            self._store_in_memory(symbol, timeframe, data)
            self._current_index[f"{symbol}_{timeframe}"] = 0
            
            logger.info(f"Loaded {len(data)} bars for {symbol} from {filepath}")
            return True
            
        except Exception as e:
            logger.error(f"Error loading data: {e}")
            return False
    
    def get_data_until(
        self,
        symbol: str,
        timeframe: str,
        end_index: int
    ) -> pd.DataFrame:
        """
        Get data up to a specific index (for backtesting).
        
        Args:
            symbol: Stock ticker
            timeframe: Timeframe
            end_index: End index (exclusive)
            
        Returns:
            DataFrame with data up to end_index
        """
        if symbol in self._data and timeframe in self._data[symbol]:
            return self._data[symbol][timeframe].iloc[:end_index].copy()
        return pd.DataFrame()
    
    def step(self, symbol: str, timeframe: str) -> Optional[pd.Series]:
        """
        Advance to next bar (for backtesting simulation).
        
        Args:
            symbol: Stock ticker
            timeframe: Timeframe
            
        Returns:
            Next bar data or None if at end
        """
        key = f"{symbol}_{timeframe}"
        
        if symbol not in self._data or timeframe not in self._data[symbol]:
            return None
        
        df = self._data[symbol][timeframe]
        idx = self._current_index.get(key, 0)
        
        if idx >= len(df):
            return None
        
        self._current_index[key] = idx + 1
        return df.iloc[idx]
    
    def reset(self, symbol: Optional[str] = None):
        """Reset index positions for replay."""
        if symbol:
            for key in list(self._current_index.keys()):
                if key.startswith(symbol):
                    self._current_index[key] = 0
        else:
            self._current_index.clear()
