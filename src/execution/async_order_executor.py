"""
Async Order Executor - Production-grade order execution for IB Gateway.

Features:
- Async order execution with proper fill tracking
- Event-driven order status updates
- Timeout protection for fills
- Order state management
- Automatic cleanup on shutdown

For 24/7 cloud deployment reliability.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from loguru import logger

try:
    from ib_async import Trade
    IB_ASYNC_AVAILABLE = True
except ImportError:
    IB_ASYNC_AVAILABLE = False
    Trade = None

from ..data.ibkr_async_client import IBKRAsyncClient


class OrderStatus(Enum):
    """Order status states."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    ERROR = "error"


class OrderType(Enum):
    """Order types."""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


@dataclass
class OrderResult:
    """Order execution result."""
    success: bool
    order_id: str
    symbol: str
    action: str  # BUY or SELL
    order_type: OrderType
    quantity: int
    requested_price: float
    filled_price: float
    filled_quantity: int
    status: OrderStatus
    timestamp: datetime
    commission: float
    message: str
    trade: Optional[Trade] = None


@dataclass
class ManagedOrder:
    """Internal order tracking."""
    order_id: int
    symbol: str
    action: str
    order_type: OrderType
    quantity: int
    price: float
    trade: Trade
    status: OrderStatus
    fill_event: asyncio.Event
    created_at: datetime = field(default_factory=datetime.now)
    filled_at: Optional[datetime] = None
    filled_price: float = 0.0
    filled_quantity: int = 0
    commission: float = 0.0
    message: str = ""


class AsyncOrderExecutor:
    """
    Async order executor with proper fill tracking.
    
    Handles order execution through IBKRAsyncClient with:
    - Event-driven fill tracking
    - Timeout protection
    - Order state management
    
    Usage:
        client = IBKRAsyncClient(config)
        executor = AsyncOrderExecutor(client)
        
        # Execute market order and wait for fill
        result = await executor.execute_market_order("AAPL", 10, "BUY")
        
        # Check result
        if result.success and result.status == OrderStatus.FILLED:
            print(f"Filled at {result.filled_price}")
    """
    
    def __init__(
        self,
        client: IBKRAsyncClient,
        default_timeout: float = 30.0,
        commission_rate: float = 0.001,
        slippage_pct: float = 0.001
    ):
        """
        Initialize executor.
        
        Args:
            client: IBKRAsyncClient instance
            default_timeout: Default timeout for order fills (seconds)
            commission_rate: Commission rate for simulation
            slippage_pct: Slippage percentage for simulation
        """
        self.client = client
        self.default_timeout = default_timeout
        self.commission_rate = commission_rate
        self.slippage_pct = slippage_pct
        
        # Order tracking
        self._orders: Dict[int, ManagedOrder] = {}
        self._order_counter = 0
        
        # Callbacks
        self.on_fill: List[Callable[[OrderResult], Any]] = []
        self.on_status_change: List[Callable[[ManagedOrder], Any]] = []
        
        # Register for IB events (will be set up when client connects)
        self._events_registered = False
    
    def _ensure_events_registered(self):
        """Register for IB order events if not already done."""
        if self._events_registered or not self.client.ib:
            return
        
        self.client.ib.orderStatusEvent += self._on_order_status
        self.client.ib.execDetailsEvent += self._on_execution
        self._events_registered = True
        logger.debug("Order executor events registered")
    
    def _on_order_status(self, trade: Trade):
        """Handle order status updates from IB."""
        order_id = trade.order.orderId
        
        if order_id not in self._orders:
            return
        
        managed = self._orders[order_id]
        status_str = trade.orderStatus.status
        
        # Map IB status to our status
        status_map = {
            'PendingSubmit': OrderStatus.PENDING,
            'PreSubmitted': OrderStatus.PENDING,
            'Submitted': OrderStatus.SUBMITTED,
            'ApiPending': OrderStatus.PENDING,
            'PendingCancel': OrderStatus.PENDING,
            'Cancelled': OrderStatus.CANCELLED,
            'Filled': OrderStatus.FILLED,
            'Inactive': OrderStatus.REJECTED,
        }
        
        new_status = status_map.get(status_str, managed.status)
        
        # Update managed order
        if new_status != managed.status:
            managed.status = new_status
            logger.debug(f"Order {order_id} status: {status_str} -> {new_status.value}")
            
            # Notify callbacks
            asyncio.create_task(self._notify_status_change(managed))
        
        # Check if filled
        if new_status == OrderStatus.FILLED:
            managed.filled_at = datetime.now()
            managed.filled_quantity = int(trade.orderStatus.filled)
            managed.filled_price = trade.orderStatus.avgFillPrice
            managed.commission = sum(f.commission for f in trade.fills) if trade.fills else 0.0
            managed.fill_event.set()
    
    def _on_execution(self, trade: Trade, fill):
        """Handle execution details from IB."""
        order_id = trade.order.orderId
        
        if order_id not in self._orders:
            return
        
        managed = self._orders[order_id]
        
        # Update fill information
        managed.filled_quantity = int(trade.orderStatus.filled)
        managed.filled_price = trade.orderStatus.avgFillPrice
        managed.commission += fill.commission if hasattr(fill, 'commission') else 0.0
        
        logger.debug(
            f"Order {order_id} execution: "
            f"{managed.filled_quantity}/{managed.quantity} @ {managed.filled_price}"
        )
        
        # Check for partial fill
        if managed.filled_quantity > 0 and managed.filled_quantity < managed.quantity:
            managed.status = OrderStatus.PARTIAL
    
    async def _notify_status_change(self, managed: ManagedOrder):
        """Notify status change callbacks."""
        for callback in self.on_status_change:
            try:
                result = callback(managed)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Status change callback error: {e}")
    
    async def _notify_fill(self, result: OrderResult):
        """Notify fill callbacks."""
        for callback in self.on_fill:
            try:
                cb_result = callback(result)
                if asyncio.iscoroutine(cb_result):
                    await cb_result
            except Exception as e:
                logger.error(f"Fill callback error: {e}")
    
    async def execute_market_order(
        self,
        symbol: str,
        quantity: int,
        action: str,
        timeout: Optional[float] = None
    ) -> OrderResult:
        """
        Execute market order and wait for fill.
        
        Args:
            symbol: Stock symbol
            quantity: Number of shares
            action: BUY or SELL
            timeout: Timeout for fill (defaults to default_timeout)
            
        Returns:
            OrderResult with execution details
        """
        self._ensure_events_registered()
        
        if not self.client.is_connected:
            return self._create_error_result(
                symbol, action, quantity, 0.0, OrderType.MARKET,
                "Not connected to IB Gateway"
            )
        
        timeout = timeout or self.default_timeout
        
        try:
            # Place order
            trade = await self.client.place_market_order(symbol, quantity, action)
            
            if not trade:
                return self._create_error_result(
                    symbol, action, quantity, 0.0, OrderType.MARKET,
                    "Order rejected"
                )
            
            # Create managed order
            managed = ManagedOrder(
                order_id=trade.order.orderId,
                symbol=symbol,
                action=action,
                order_type=OrderType.MARKET,
                quantity=quantity,
                price=0.0,
                trade=trade,
                status=OrderStatus.SUBMITTED,
                fill_event=asyncio.Event()
            )
            self._orders[trade.order.orderId] = managed
            
            # Wait for fill with timeout
            try:
                await asyncio.wait_for(managed.fill_event.wait(), timeout=timeout)
                
                result = OrderResult(
                    success=True,
                    order_id=str(trade.order.orderId),
                    symbol=symbol,
                    action=action,
                    order_type=OrderType.MARKET,
                    quantity=quantity,
                    requested_price=0.0,
                    filled_price=managed.filled_price,
                    filled_quantity=managed.filled_quantity,
                    status=managed.status,
                    timestamp=datetime.now(),
                    commission=managed.commission,
                    message=f"Market order filled at ${managed.filled_price:.2f}",
                    trade=trade
                )
                
                await self._notify_fill(result)
                return result
                
            except asyncio.TimeoutError:
                return OrderResult(
                    success=False,
                    order_id=str(trade.order.orderId),
                    symbol=symbol,
                    action=action,
                    order_type=OrderType.MARKET,
                    quantity=quantity,
                    requested_price=0.0,
                    filled_price=managed.filled_price,
                    filled_quantity=managed.filled_quantity,
                    status=OrderStatus.PENDING,
                    timestamp=datetime.now(),
                    commission=managed.commission,
                    message=f"Fill timeout after {timeout}s",
                    trade=trade
                )
                
        except Exception as e:
            logger.error(f"Market order error: {e}")
            return self._create_error_result(
                symbol, action, quantity, 0.0, OrderType.MARKET, str(e)
            )
    
    async def execute_limit_order(
        self,
        symbol: str,
        quantity: int,
        action: str,
        limit_price: float,
        wait_for_fill: bool = False,
        timeout: Optional[float] = None
    ) -> OrderResult:
        """
        Execute limit order.
        
        Args:
            symbol: Stock symbol
            quantity: Number of shares
            action: BUY or SELL
            limit_price: Limit price
            wait_for_fill: If True, wait for fill (with timeout)
            timeout: Timeout for fill (only if wait_for_fill=True)
            
        Returns:
            OrderResult with execution details
        """
        self._ensure_events_registered()
        
        if not self.client.is_connected:
            return self._create_error_result(
                symbol, action, quantity, limit_price, OrderType.LIMIT,
                "Not connected to IB Gateway"
            )
        
        try:
            # Place order
            trade = await self.client.place_limit_order(symbol, quantity, limit_price, action)
            
            if not trade:
                return self._create_error_result(
                    symbol, action, quantity, limit_price, OrderType.LIMIT,
                    "Order rejected"
                )
            
            # Create managed order
            managed = ManagedOrder(
                order_id=trade.order.orderId,
                symbol=symbol,
                action=action,
                order_type=OrderType.LIMIT,
                quantity=quantity,
                price=limit_price,
                trade=trade,
                status=OrderStatus.SUBMITTED,
                fill_event=asyncio.Event()
            )
            self._orders[trade.order.orderId] = managed
            
            if wait_for_fill:
                timeout = timeout or self.default_timeout
                try:
                    await asyncio.wait_for(managed.fill_event.wait(), timeout=timeout)
                    
                    result = OrderResult(
                        success=True,
                        order_id=str(trade.order.orderId),
                        symbol=symbol,
                        action=action,
                        order_type=OrderType.LIMIT,
                        quantity=quantity,
                        requested_price=limit_price,
                        filled_price=managed.filled_price,
                        filled_quantity=managed.filled_quantity,
                        status=managed.status,
                        timestamp=datetime.now(),
                        commission=managed.commission,
                        message=f"Limit order filled at ${managed.filled_price:.2f}",
                        trade=trade
                    )
                    
                    await self._notify_fill(result)
                    return result
                    
                except asyncio.TimeoutError:
                    pass  # Fall through to return pending status
            
            # Return submitted status (not waiting for fill)
            return OrderResult(
                success=True,
                order_id=str(trade.order.orderId),
                symbol=symbol,
                action=action,
                order_type=OrderType.LIMIT,
                quantity=quantity,
                requested_price=limit_price,
                filled_price=0.0,
                filled_quantity=0,
                status=OrderStatus.SUBMITTED,
                timestamp=datetime.now(),
                commission=0.0,
                message=f"Limit order submitted at ${limit_price:.2f}",
                trade=trade
            )
            
        except Exception as e:
            logger.error(f"Limit order error: {e}")
            return self._create_error_result(
                symbol, action, quantity, limit_price, OrderType.LIMIT, str(e)
            )
    
    async def execute_stop_order(
        self,
        symbol: str,
        quantity: int,
        action: str,
        stop_price: float
    ) -> OrderResult:
        """
        Execute stop order.
        
        Args:
            symbol: Stock symbol
            quantity: Number of shares
            action: BUY or SELL
            stop_price: Stop trigger price
            
        Returns:
            OrderResult with execution details
        """
        self._ensure_events_registered()
        
        if not self.client.is_connected:
            return self._create_error_result(
                symbol, action, quantity, stop_price, OrderType.STOP,
                "Not connected to IB Gateway"
            )
        
        try:
            # Place order
            trade = await self.client.place_stop_order(symbol, quantity, stop_price, action)
            
            if not trade:
                return self._create_error_result(
                    symbol, action, quantity, stop_price, OrderType.STOP,
                    "Order rejected"
                )
            
            # Create managed order
            managed = ManagedOrder(
                order_id=trade.order.orderId,
                symbol=symbol,
                action=action,
                order_type=OrderType.STOP,
                quantity=quantity,
                price=stop_price,
                trade=trade,
                status=OrderStatus.SUBMITTED,
                fill_event=asyncio.Event()
            )
            self._orders[trade.order.orderId] = managed
            
            return OrderResult(
                success=True,
                order_id=str(trade.order.orderId),
                symbol=symbol,
                action=action,
                order_type=OrderType.STOP,
                quantity=quantity,
                requested_price=stop_price,
                filled_price=0.0,
                filled_quantity=0,
                status=OrderStatus.SUBMITTED,
                timestamp=datetime.now(),
                commission=0.0,
                message=f"Stop order submitted at ${stop_price:.2f}",
                trade=trade
            )
            
        except Exception as e:
            logger.error(f"Stop order error: {e}")
            return self._create_error_result(
                symbol, action, quantity, stop_price, OrderType.STOP, str(e)
            )
    
    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an open order.
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            True if cancellation request sent
        """
        try:
            int_order_id = int(order_id)
            if int_order_id in self._orders:
                managed = self._orders[int_order_id]
                success = await self.client.cancel_order(managed.trade)
                if success:
                    managed.status = OrderStatus.CANCELLED
                return success
        except (ValueError, KeyError):
            pass
        
        return False
    
    async def cancel_all_orders(self) -> int:
        """
        Cancel all pending orders.
        
        Returns:
            Number of orders cancelled
        """
        cancelled = 0
        
        for order_id, managed in list(self._orders.items()):
            if managed.status in [OrderStatus.PENDING, OrderStatus.SUBMITTED]:
                try:
                    success = await self.client.cancel_order(managed.trade)
                    if success:
                        managed.status = OrderStatus.CANCELLED
                        cancelled += 1
                except Exception as e:
                    logger.error(f"Failed to cancel order {order_id}: {e}")
        
        return cancelled
    
    def get_order(self, order_id: str) -> Optional[ManagedOrder]:
        """Get order by ID."""
        try:
            return self._orders.get(int(order_id))
        except ValueError:
            return None
    
    def get_pending_orders(self) -> List[ManagedOrder]:
        """Get all pending orders."""
        return [
            o for o in self._orders.values()
            if o.status in [OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL]
        ]
    
    def get_filled_orders(self) -> List[ManagedOrder]:
        """Get all filled orders."""
        return [
            o for o in self._orders.values()
            if o.status == OrderStatus.FILLED
        ]
    
    def _create_error_result(
        self,
        symbol: str,
        action: str,
        quantity: int,
        price: float,
        order_type: OrderType,
        message: str
    ) -> OrderResult:
        """Create error result."""
        return OrderResult(
            success=False,
            order_id="",
            symbol=symbol,
            action=action,
            order_type=order_type,
            quantity=quantity,
            requested_price=price,
            filled_price=0.0,
            filled_quantity=0,
            status=OrderStatus.ERROR,
            timestamp=datetime.now(),
            commission=0.0,
            message=message,
            trade=None
        )
    
    def _generate_order_id(self) -> str:
        """Generate unique order ID for simulation."""
        self._order_counter += 1
        return f"SIM_{datetime.now().strftime('%Y%m%d')}_{self._order_counter:06d}"
    
    async def cleanup(self):
        """Cleanup executor state."""
        # Cancel all pending orders
        await self.cancel_all_orders()
        
        # Unregister events
        if self._events_registered and self.client.ib:
            try:
                self.client.ib.orderStatusEvent -= self._on_order_status
                self.client.ib.execDetailsEvent -= self._on_execution
            except Exception:
                pass
            self._events_registered = False
        
        # Clear order tracking
        self._orders.clear()
        
        logger.info("Order executor cleanup complete")
