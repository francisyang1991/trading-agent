"""
Data Layer Module
=================

This module provides data access for the trading agent.

DataManager implementations:
1. CachedDataManager (src/data_manager.py) - SQLite cache + environment-aware provider chain
2. IBKRDataManager (src/data/ibkr_data_manager.py) - IBKR multi-timeframe, for live trading

IBKR Clients (choose based on deployment):
1. IBKRAsyncClient - Pure async, production-grade for 24/7 operation (RECOMMENDED)
2. IBKRClient - Legacy sync/async wrapper for TWS/Gateway
3. IBKRWebClient - REST client for IBeam Gateway

Connection Management:
- IBConnectionManager - Auto-reconnect, heartbeat, state management

Use get_data_manager() factory to get the appropriate manager based on your use case.
"""

from .ibkr_client import IBKRClient
from .ibkr_web_client import IBKRWebClient, IBeamManager
from .ibkr_data_manager import DataManager as IBKRDataManager, BacktestDataManager

# New async client and connection manager for 24/7 operation
from .connection_manager import IBConnectionManager, ConnectionConfig, ConnectionState, TradingMode, AccountStatus
from .ibkr_async_client import IBKRAsyncClient

# Rate limiting for API calls
from .rate_limiter import RateLimiter, Priority, ThrottledAPIClient, get_rate_limiter

# Import the SQLite-cached data manager from src root level
# This is the canonical data manager for scanning and backtesting
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
try:
    from src.data_manager import DataManager as CachedDataManager
except ImportError:
    CachedDataManager = None


def get_data_manager(mode: str = "cached", **kwargs):
    """
    Factory function to get the appropriate DataManager.
    
    Args:
        mode: One of "cached", "ibkr", or "backtest"
            - "cached": SQLite cache + routed providers (for scanning, research, local dev)
            - "ibkr": IBKR multi-timeframe (for live trading)
            - "backtest": Backtest-specialized manager with step/replay
        **kwargs: Additional arguments passed to the manager constructor
        
    Returns:
        DataManager instance
        
    Example:
        # For scanning/backtesting (recommended default)
        dm = get_data_manager("cached")
        data = dm.get_daily_data("NVDA", period="1y")
        
        # For live trading with IBKR
        dm = get_data_manager("ibkr", ibkr_client=my_client)
        data = dm.get_data("NVDA", "5 mins", lookback_bars=200)
        
        # For backtest simulation
        dm = get_data_manager("backtest", data_dir="data/historical")
        dm.load_historical_data("NVDA", "data/nvda.csv")
    """
    if mode == "cached":
        if CachedDataManager is None:
            raise ImportError(
                "CachedDataManager not available. "
                "Ensure src/data_manager.py exists."
            )
        return CachedDataManager(**kwargs)
    
    elif mode == "ibkr":
        return IBKRDataManager(**kwargs)
    
    elif mode == "backtest":
        return BacktestDataManager(**kwargs)
    
    else:
        raise ValueError(f"Unknown mode: {mode}. Use 'cached', 'ibkr', or 'backtest'.")


__all__ = [
    # Connection management (for 24/7 operation)
    "IBConnectionManager",
    "ConnectionConfig",
    "ConnectionState",
    "TradingMode",
    "AccountStatus",
    # Rate limiting
    "RateLimiter",
    "Priority",
    "ThrottledAPIClient",
    "get_rate_limiter",
    # IBKR clients
    "IBKRAsyncClient",  # Pure async - RECOMMENDED for production
    "IBKRClient",        # Legacy sync/async wrapper
    "IBKRWebClient",     # REST client for IBeam
    "IBeamManager",
    # Data managers
    "IBKRDataManager",
    "BacktestDataManager",
    "CachedDataManager",
    # Factory
    "get_data_manager",
]
