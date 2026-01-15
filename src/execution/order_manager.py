"""
Order Manager
=============

Manage order lifecycle from creation to fill.
"""

import uuid
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from ..core.types import Order, OrderSide, OrderType, OrderStatus, Fill, Signal


class OrderState(Enum):
    """Order state machine."""
    CREATED = "created"
    VALIDATING = "validating"
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class ManagedOrder:
    """Order with management metadata."""
    order: Order
    state: OrderState = OrderState.CREATED
    created_at: datetime = field(default_factory=datetime.now)
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    fills: List[Fill] = field(default_factory=list)
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    rejection_reason: str = ""


class OrderManager:
    """
    Order Manager - Track and manage orders.
    
    Features:
    - Order creation from signals
    - Order state management
    - Fill tracking
    - Order modification/cancellation
    
    Example:
        mgr = OrderManager()
        
        # Create order from signal
        order = mgr.create_order(signal, shares=100)
        
        # Submit order
        mgr.submit_order(order.order_id)
        
        # Record fill
        mgr.record_fill(order.order_id, fill)
    """
    
    def __init__(self):
        self._orders: Dict[str, ManagedOrder] = {}
        self._pending_orders: List[str] = []
        self._filled_orders: List[str] = []
    
    def create_order(
        self,
        signal: Signal,
        shares: int,
        order_type: OrderType = OrderType.LIMIT,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: str = "DAY"
    ) -> ManagedOrder:
        """
        Create order from signal.
        
        Args:
            signal: Entry/exit signal
            shares: Number of shares
            order_type: Order type (MARKET, LIMIT, etc.)
            limit_price: Limit price (if applicable)
            stop_price: Stop price (if applicable)
            time_in_force: Order duration
            
        Returns:
            ManagedOrder with generated order
        """
        order_id = str(uuid.uuid4())[:8]
        
        # Determine side from signal
        if signal.direction == 1:
            side = OrderSide.BUY
        else:
            side = OrderSide.SELL
        
        # Use signal price for limit if not specified
        if order_type == OrderType.LIMIT and limit_price is None:
            limit_price = signal.price
        
        order = Order(
            order_id=order_id,
            symbol=signal.symbol,
            side=side,
            quantity=shares,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            status=OrderStatus.PENDING,
            created_at=datetime.now(),
            metadata={
                'signal_confidence': signal.confidence,
                'strategy': signal.strategy.value if signal.strategy else None,
                'time_in_force': time_in_force,
            }
        )
        
        managed = ManagedOrder(order=order, state=OrderState.CREATED)
        self._orders[order_id] = managed
        
        return managed
    
    def submit_order(self, order_id: str) -> bool:
        """Submit order for execution."""
        if order_id not in self._orders:
            return False
        
        managed = self._orders[order_id]
        
        if managed.state != OrderState.CREATED:
            return False
        
        managed.state = OrderState.SUBMITTED
        managed.submitted_at = datetime.now()
        managed.order.status = OrderStatus.SUBMITTED
        
        self._pending_orders.append(order_id)
        
        return True
    
    def record_fill(self, order_id: str, fill: Fill):
        """Record a fill for an order."""
        if order_id not in self._orders:
            return
        
        managed = self._orders[order_id]
        managed.fills.append(fill)
        managed.filled_qty += fill.quantity
        
        # Update average fill price
        total_value = sum(f.price * f.quantity for f in managed.fills)
        total_qty = sum(f.quantity for f in managed.fills)
        managed.avg_fill_price = total_value / total_qty if total_qty > 0 else 0
        
        # Update state
        if managed.filled_qty >= managed.order.quantity:
            managed.state = OrderState.FILLED
            managed.filled_at = datetime.now()
            managed.order.status = OrderStatus.FILLED
            managed.order.filled_quantity = managed.filled_qty
            managed.order.filled_price = managed.avg_fill_price
            
            if order_id in self._pending_orders:
                self._pending_orders.remove(order_id)
            self._filled_orders.append(order_id)
        else:
            managed.state = OrderState.PARTIAL
            managed.order.status = OrderStatus.PARTIAL
    
    def cancel_order(self, order_id: str, reason: str = "") -> bool:
        """Cancel an order."""
        if order_id not in self._orders:
            return False
        
        managed = self._orders[order_id]
        
        if managed.state in [OrderState.FILLED, OrderState.CANCELLED]:
            return False
        
        managed.state = OrderState.CANCELLED
        managed.order.status = OrderStatus.CANCELLED
        managed.rejection_reason = reason
        
        if order_id in self._pending_orders:
            self._pending_orders.remove(order_id)
        
        return True
    
    def reject_order(self, order_id: str, reason: str):
        """Reject an order."""
        if order_id not in self._orders:
            return
        
        managed = self._orders[order_id]
        managed.state = OrderState.REJECTED
        managed.order.status = OrderStatus.REJECTED
        managed.rejection_reason = reason
        
        if order_id in self._pending_orders:
            self._pending_orders.remove(order_id)
    
    def get_order(self, order_id: str) -> Optional[ManagedOrder]:
        """Get order by ID."""
        return self._orders.get(order_id)
    
    def get_pending_orders(self) -> List[ManagedOrder]:
        """Get all pending orders."""
        return [self._orders[oid] for oid in self._pending_orders if oid in self._orders]
    
    def get_orders_by_symbol(self, symbol: str) -> List[ManagedOrder]:
        """Get all orders for a symbol."""
        return [m for m in self._orders.values() if m.order.symbol == symbol]
    
    def get_open_orders(self) -> List[ManagedOrder]:
        """Get all open (non-terminal) orders."""
        return [
            m for m in self._orders.values()
            if m.state in [OrderState.PENDING, OrderState.SUBMITTED, OrderState.PARTIAL]
        ]
