"""
Execution Module - Order management and trade execution.

This module provides:
- Order management
- Paper trading simulation
- Slippage modeling
- Execution adapters (IBKR, etc.)
"""

from .order_manager import OrderManager, OrderState
from .executor import TradeExecutor
from .paper_trading import PaperTradingAdapter

__all__ = [
    'OrderManager',
    'OrderState',
    'TradeExecutor',
    'PaperTradingAdapter',
]
