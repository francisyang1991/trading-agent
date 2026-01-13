"""
Order Executor
Handles order execution through IBKR or simulation.
"""

from typing import Dict, List, Optional, Callable
from dataclasses import dataclass
from enum import Enum
from datetime import datetime
import time
from loguru import logger

from ..data.ibkr_client import IBKRClient


class OrderType(Enum):
    """Order types."""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(Enum):
    """Order status."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class OrderResult:
    """Order execution result."""
    success: bool
    order_id: str
    symbol: str
    action: str              # BUY, SELL
    order_type: OrderType
    quantity: int
    requested_price: float
    filled_price: float
    filled_quantity: int
    status: OrderStatus
    timestamp: datetime
    commission: float
    message: str


class OrderExecutor:
    """
    Executes orders through IBKR or simulation mode.
    """
    
    def __init__(
        self,
        ibkr_client: Optional[IBKRClient] = None,
        simulation_mode: bool = True,
        commission_rate: float = 0.001,
        slippage_pct: float = 0.001
    ):
        """
        Initialize order executor.
        
        Args:
            ibkr_client: IBKR client for live trading
            simulation_mode: If True, simulate orders
            commission_rate: Commission rate for simulation
            slippage_pct: Slippage for simulation
        """
        self.ibkr = ibkr_client
        self.simulation_mode = simulation_mode
        self.commission_rate = commission_rate
        self.slippage_pct = slippage_pct
        
        # Order tracking
        self._order_counter = 0
        self._pending_orders: Dict[str, OrderResult] = {}
        self._order_history: List[OrderResult] = []
        
        # Callbacks
        self._on_fill: Optional[Callable] = None
    
    def set_fill_callback(self, callback: Callable):
        """Set callback for order fills."""
        self._on_fill = callback
    
    def execute_market_order(
        self,
        symbol: str,
        quantity: int,
        action: str,
        current_price: float
    ) -> OrderResult:
        """
        Execute a market order.
        
        Args:
            symbol: Stock ticker
            quantity: Number of shares
            action: BUY or SELL
            current_price: Current market price
            
        Returns:
            OrderResult with execution details
        """
        if self.simulation_mode:
            return self._simulate_market_order(symbol, quantity, action, current_price)
        else:
            return self._execute_ibkr_market_order(symbol, quantity, action)
    
    def execute_limit_order(
        self,
        symbol: str,
        quantity: int,
        action: str,
        limit_price: float
    ) -> OrderResult:
        """
        Execute a limit order.
        
        Args:
            symbol: Stock ticker
            quantity: Number of shares
            action: BUY or SELL
            limit_price: Limit price
            
        Returns:
            OrderResult with execution details
        """
        if self.simulation_mode:
            return self._simulate_limit_order(symbol, quantity, action, limit_price)
        else:
            return self._execute_ibkr_limit_order(symbol, quantity, action, limit_price)
    
    def execute_stop_order(
        self,
        symbol: str,
        quantity: int,
        action: str,
        stop_price: float
    ) -> OrderResult:
        """
        Execute a stop order.
        
        Args:
            symbol: Stock ticker
            quantity: Number of shares
            action: BUY or SELL
            stop_price: Stop trigger price
            
        Returns:
            OrderResult with execution details
        """
        if self.simulation_mode:
            # In simulation, stop orders are tracked but not immediately filled
            order_id = self._generate_order_id()
            
            result = OrderResult(
                success=True,
                order_id=order_id,
                symbol=symbol,
                action=action,
                order_type=OrderType.STOP,
                quantity=quantity,
                requested_price=stop_price,
                filled_price=0.0,
                filled_quantity=0,
                status=OrderStatus.PENDING,
                timestamp=datetime.now(),
                commission=0.0,
                message=f"Stop order submitted at ${stop_price:.2f}"
            )
            
            self._pending_orders[order_id] = result
            return result
        else:
            return self._execute_ibkr_stop_order(symbol, quantity, action, stop_price)
    
    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel a pending order.
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            True if cancelled successfully
        """
        if order_id in self._pending_orders:
            order = self._pending_orders[order_id]
            order.status = OrderStatus.CANCELLED
            order.message = "Cancelled by user"
            del self._pending_orders[order_id]
            self._order_history.append(order)
            return True
        
        if not self.simulation_mode and self.ibkr:
            # Cancel through IBKR
            # Note: Would need to track IBKR trade objects
            pass
        
        return False
    
    def check_stop_orders(self, prices: Dict[str, float]):
        """
        Check if any stop orders should be triggered.
        
        Args:
            prices: Current prices {symbol: price}
        """
        triggered = []
        
        for order_id, order in list(self._pending_orders.items()):
            if order.order_type != OrderType.STOP:
                continue
            
            if order.symbol not in prices:
                continue
            
            current_price = prices[order.symbol]
            
            # Check trigger condition
            if order.action == "SELL" and current_price <= order.requested_price:
                # Sell stop triggered
                triggered.append(order_id)
            elif order.action == "BUY" and current_price >= order.requested_price:
                # Buy stop triggered
                triggered.append(order_id)
        
        # Execute triggered orders
        for order_id in triggered:
            order = self._pending_orders[order_id]
            current_price = prices[order.symbol]
            
            # Execute as market order with slippage
            result = self._simulate_market_order(
                order.symbol,
                order.quantity,
                order.action,
                current_price
            )
            
            # Update original order
            order.filled_price = result.filled_price
            order.filled_quantity = result.filled_quantity
            order.status = OrderStatus.FILLED
            order.commission = result.commission
            order.message = f"Stop triggered at ${current_price:.2f}, filled at ${result.filled_price:.2f}"
            
            del self._pending_orders[order_id]
            self._order_history.append(order)
            
            # Callback
            if self._on_fill:
                self._on_fill(order)
            
            logger.info(f"Stop order triggered: {order.symbol} {order.action} @ ${result.filled_price:.2f}")
    
    def _simulate_market_order(
        self,
        symbol: str,
        quantity: int,
        action: str,
        current_price: float
    ) -> OrderResult:
        """Simulate market order execution."""
        order_id = self._generate_order_id()
        
        # Apply slippage
        if action == "BUY":
            filled_price = current_price * (1 + self.slippage_pct)
        else:
            filled_price = current_price * (1 - self.slippage_pct)
        
        # Calculate commission
        commission = abs(filled_price * quantity * self.commission_rate)
        
        result = OrderResult(
            success=True,
            order_id=order_id,
            symbol=symbol,
            action=action,
            order_type=OrderType.MARKET,
            quantity=quantity,
            requested_price=current_price,
            filled_price=filled_price,
            filled_quantity=quantity,
            status=OrderStatus.FILLED,
            timestamp=datetime.now(),
            commission=commission,
            message=f"Market order filled at ${filled_price:.2f}"
        )
        
        self._order_history.append(result)
        
        if self._on_fill:
            self._on_fill(result)
        
        logger.info(f"Simulated market {action}: {symbol} x {quantity} @ ${filled_price:.2f}")
        
        return result
    
    def _simulate_limit_order(
        self,
        symbol: str,
        quantity: int,
        action: str,
        limit_price: float
    ) -> OrderResult:
        """Simulate limit order execution."""
        order_id = self._generate_order_id()
        
        # For simulation, assume immediate fill at limit price
        commission = abs(limit_price * quantity * self.commission_rate)
        
        result = OrderResult(
            success=True,
            order_id=order_id,
            symbol=symbol,
            action=action,
            order_type=OrderType.LIMIT,
            quantity=quantity,
            requested_price=limit_price,
            filled_price=limit_price,
            filled_quantity=quantity,
            status=OrderStatus.FILLED,
            timestamp=datetime.now(),
            commission=commission,
            message=f"Limit order filled at ${limit_price:.2f}"
        )
        
        self._order_history.append(result)
        
        if self._on_fill:
            self._on_fill(result)
        
        logger.info(f"Simulated limit {action}: {symbol} x {quantity} @ ${limit_price:.2f}")
        
        return result
    
    def _execute_ibkr_market_order(
        self,
        symbol: str,
        quantity: int,
        action: str
    ) -> OrderResult:
        """Execute market order through IBKR."""
        if not self.ibkr or not self.ibkr.is_connected:
            return self._failed_order(symbol, action, quantity, "IBKR not connected")
        
        try:
            trade = self.ibkr.place_market_order(symbol, quantity, action)
            
            if trade is None:
                return self._failed_order(symbol, action, quantity, "Order rejected")
            
            # Wait for fill (with timeout)
            for _ in range(30):  # 30 second timeout
                if trade.isDone():
                    break
                time.sleep(1)
            
            # Get fill details
            if trade.orderStatus.status == 'Filled':
                result = OrderResult(
                    success=True,
                    order_id=str(trade.order.orderId),
                    symbol=symbol,
                    action=action,
                    order_type=OrderType.MARKET,
                    quantity=quantity,
                    requested_price=0.0,
                    filled_price=trade.orderStatus.avgFillPrice,
                    filled_quantity=int(trade.orderStatus.filled),
                    status=OrderStatus.FILLED,
                    timestamp=datetime.now(),
                    commission=sum(f.commission for f in trade.fills),
                    message="Order filled"
                )
            else:
                result = self._failed_order(
                    symbol, action, quantity,
                    f"Order status: {trade.orderStatus.status}"
                )
            
            self._order_history.append(result)
            return result
            
        except Exception as e:
            logger.error(f"IBKR order error: {e}")
            return self._failed_order(symbol, action, quantity, str(e))
    
    def _execute_ibkr_limit_order(
        self,
        symbol: str,
        quantity: int,
        action: str,
        limit_price: float
    ) -> OrderResult:
        """Execute limit order through IBKR."""
        if not self.ibkr or not self.ibkr.is_connected:
            return self._failed_order(symbol, action, quantity, "IBKR not connected")
        
        try:
            trade = self.ibkr.place_limit_order(symbol, quantity, limit_price, action)
            
            if trade is None:
                return self._failed_order(symbol, action, quantity, "Order rejected")
            
            result = OrderResult(
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
                message="Limit order submitted"
            )
            
            self._pending_orders[result.order_id] = result
            return result
            
        except Exception as e:
            logger.error(f"IBKR order error: {e}")
            return self._failed_order(symbol, action, quantity, str(e))
    
    def _execute_ibkr_stop_order(
        self,
        symbol: str,
        quantity: int,
        action: str,
        stop_price: float
    ) -> OrderResult:
        """Execute stop order through IBKR."""
        if not self.ibkr or not self.ibkr.is_connected:
            return self._failed_order(symbol, action, quantity, "IBKR not connected")
        
        try:
            trade = self.ibkr.place_stop_order(symbol, quantity, stop_price, action)
            
            if trade is None:
                return self._failed_order(symbol, action, quantity, "Order rejected")
            
            result = OrderResult(
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
                message="Stop order submitted"
            )
            
            self._pending_orders[result.order_id] = result
            return result
            
        except Exception as e:
            logger.error(f"IBKR order error: {e}")
            return self._failed_order(symbol, action, quantity, str(e))
    
    def _generate_order_id(self) -> str:
        """Generate unique order ID."""
        self._order_counter += 1
        return f"SIM_{datetime.now().strftime('%Y%m%d')}_{self._order_counter:06d}"
    
    def _failed_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        message: str
    ) -> OrderResult:
        """Create failed order result."""
        return OrderResult(
            success=False,
            order_id="",
            symbol=symbol,
            action=action,
            order_type=OrderType.MARKET,
            quantity=quantity,
            requested_price=0.0,
            filled_price=0.0,
            filled_quantity=0,
            status=OrderStatus.REJECTED,
            timestamp=datetime.now(),
            commission=0.0,
            message=message
        )
    
    def get_pending_orders(self) -> List[OrderResult]:
        """Get all pending orders."""
        return list(self._pending_orders.values())
    
    def get_order_history(self, limit: int = 100) -> List[OrderResult]:
        """Get order history."""
        return self._order_history[-limit:]
