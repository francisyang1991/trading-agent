"""
Execution Module - Order management and trade execution.

This module provides:
- Order management
- Paper trading simulation
- Slippage modeling
- Execution adapters (IBKR, etc.)

For 24/7 production operation, use AsyncOrderExecutor with IBKRAsyncClient.
"""

from .order_manager import OrderManager, OrderState
from .executor import TradeExecutor
from .paper_trading import PaperTradingAdapter

# Async executor for 24/7 production operation
from .async_order_executor import (
    AsyncOrderExecutor,
    OrderResult,
    OrderStatus,
    OrderType,
    ManagedOrder,
)

__all__ = [
    # Legacy executors
    'OrderManager',
    'OrderState',
    'TradeExecutor',
    'PaperTradingAdapter',
    # Async executor (RECOMMENDED for production)
    'AsyncOrderExecutor',
    'OrderResult',
    'OrderStatus',
    'OrderType',
    'ManagedOrder',
]
