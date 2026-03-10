"""
Paper Trading Adapter
=====================

Simulated trading for testing strategies without real money.
"""

import uuid
from datetime import datetime
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field

from ..core.types import (
    Signal, Order, Fill, Position, Trade,
    OrderSide, OrderType, OrderStatus
)


@dataclass
class PaperAccount:
    """Paper trading account state."""
    initial_capital: float
    cash: float
    positions: Dict[str, Position] = field(default_factory=dict)
    trades: List[Trade] = field(default_factory=list)
    orders: List[Order] = field(default_factory=list)
    fills: List[Fill] = field(default_factory=list)
    current_prices: Dict[str, float] = field(default_factory=dict)  # Track current prices
    
    @property
    def equity(self) -> float:
        """Total account value."""
        position_value = sum(
            p.quantity * self.current_prices.get(p.symbol, p.avg_entry_price)
            for p in self.positions.values()
        )
        return self.cash + position_value
    
    @property
    def pnl(self) -> float:
        """Total P&L."""
        return self.equity - self.initial_capital
    
    @property
    def pnl_pct(self) -> float:
        """P&L percentage."""
        return self.pnl / self.initial_capital * 100 if self.initial_capital > 0 else 0


@dataclass
class PaperConfig:
    """Paper trading configuration."""
    # Slippage
    slippage_pct: float = 0.001  # 0.1%
    
    # Commission
    commission_per_share: float = 0.0
    min_commission: float = 0.0
    
    # Fill behavior
    fill_all_at_once: bool = True
    partial_fill_pct: float = 1.0


class PaperTradingAdapter:
    """
    Paper Trading Engine - Simulate real trading.
    
    Features:
    - Order submission and fills
    - Position tracking
    - P&L calculation
    - Trade history
    
    Example:
        paper = PaperTradingAdapter(capital=100000)
        
        # Buy stock
        order = paper.submit_order(
            symbol='NVDA',
            side='BUY',
            quantity=50,
            price=150.0
        )
        
        # Simulate fill
        fill = paper.fill_order(order.order_id, 150.05)
        
        # Check account
        print(f"Equity: ${paper.account.equity:,.2f}")
    """
    
    def __init__(
        self,
        capital: float = 100000.0,
        config: Optional[PaperConfig] = None
    ):
        self.config = config or PaperConfig()
        self.account = PaperAccount(
            initial_capital=capital,
            cash=capital
        )
        
        # Callbacks
        self._on_fill: Optional[Callable] = None
        self._on_order: Optional[Callable] = None
    
    def submit_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        order_type: str = "LIMIT",
        stop_price: Optional[float] = None
    ) -> Order:
        """
        Submit order.
        
        Args:
            symbol: Stock symbol
            side: 'BUY' or 'SELL'
            quantity: Number of shares
            price: Limit price
            order_type: 'MARKET' or 'LIMIT'
            stop_price: Stop price for stop orders
            
        Returns:
            Created Order
        """
        order_id = str(uuid.uuid4())[:8]
        
        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=OrderSide.BUY if side.upper() == 'BUY' else OrderSide.SELL,
            quantity=quantity,
            order_type=OrderType[order_type.upper()],
            limit_price=price,
            stop_price=stop_price,
            status=OrderStatus.PENDING,
            created_at=datetime.now()
        )
        
        self.account.orders.append(order)
        
        if self._on_order:
            self._on_order(order)
        
        return order
    
    def submit_signal(self, signal: Signal, quantity: int) -> Order:
        """Submit order from signal."""
        side = 'BUY' if signal.direction == 1 else 'SELL'
        return self.submit_order(
            symbol=signal.symbol,
            side=side,
            quantity=quantity,
            price=signal.price
        )
    
    def fill_order(
        self,
        order_id: str,
        fill_price: Optional[float] = None
    ) -> Optional[Fill]:
        """
        Fill an order.
        
        Args:
            order_id: Order ID to fill
            fill_price: Price to fill at (applies slippage if None)
            
        Returns:
            Fill object
        """
        # Find order
        order = None
        for o in self.account.orders:
            if o.order_id == order_id:
                order = o
                break
        
        if not order or order.status in [OrderStatus.FILLED, OrderStatus.CANCELLED]:
            return None
        
        # Calculate fill price with slippage
        if fill_price is None:
            fill_price = order.limit_price or 0
        
        if order.side == OrderSide.BUY:
            fill_price *= (1 + self.config.slippage_pct)
        else:
            fill_price *= (1 - self.config.slippage_pct)
        
        # Calculate commission
        commission = max(
            self.config.min_commission,
            order.quantity * self.config.commission_per_share
        )
        
        # Check if we have enough cash for buy
        if order.side == OrderSide.BUY:
            cost = fill_price * order.quantity + commission
            if cost > self.account.cash:
                order.status = OrderStatus.REJECTED
                return None
        
        # Create fill
        fill = Fill(
            fill_id=f"fill_{order_id}",
            order_id=order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            timestamp=datetime.now(),
            commission=commission
        )
        
        self.account.fills.append(fill)
        
        # Update order
        order.status = OrderStatus.FILLED
        order.filled_quantity = order.quantity
        order.filled_price = fill_price
        order.filled_at = datetime.now()
        
        # Update position and cash
        self._process_fill(order, fill)
        
        if self._on_fill:
            self._on_fill(fill)
        
        return fill
    
    def _process_fill(self, order: Order, fill: Fill):
        """Process fill - update positions and cash."""
        symbol = order.symbol
        
        if order.side == OrderSide.BUY:
            # Deduct cash
            cost = fill.price * fill.quantity + fill.commission
            self.account.cash -= cost
            
            # Update position
            if symbol in self.account.positions:
                pos = self.account.positions[symbol]
                # Average price
                total_qty = pos.quantity + fill.quantity
                total_value = (pos.avg_entry_price * pos.quantity) + (fill.price * fill.quantity)
                pos.avg_entry_price = total_value / total_qty
                pos.quantity = total_qty
            else:
                self.account.positions[symbol] = Position(
                    symbol=symbol,
                    quantity=fill.quantity,
                    avg_entry_price=fill.price,
                    entry_date=datetime.now()
                )
            
            # Update current price
            self.account.current_prices[symbol] = fill.price
        
        else:  # SELL
            # Add cash
            proceeds = fill.price * fill.quantity - fill.commission
            self.account.cash += proceeds
            
            # Update position
            if symbol in self.account.positions:
                pos = self.account.positions[symbol]
                
                # Record trade
                holding_days = (datetime.now() - pos.entry_date).days if pos.entry_date else 0
                trade = Trade(
                    trade_id=f"trade_{fill.fill_id}",
                    symbol=symbol,
                    side="LONG",
                    entry_date=pos.entry_date or datetime.now(),
                    entry_price=pos.avg_entry_price,
                    entry_quantity=fill.quantity,
                    entry_reason="",
                    exit_date=datetime.now(),
                    exit_price=fill.price,
                    exit_quantity=fill.quantity,
                    exit_reason="",
                    pnl=(fill.price - pos.avg_entry_price) * fill.quantity,
                    pnl_pct=(fill.price - pos.avg_entry_price) / pos.avg_entry_price * 100,
                    holding_days=holding_days
                )
                self.account.trades.append(trade)
                
                # Update position
                pos.quantity -= fill.quantity
                if pos.quantity <= 0:
                    del self.account.positions[symbol]
    
    def update_prices(self, prices: Dict[str, float]):
        """Update current prices for positions."""
        self.account.current_prices.update(prices)
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for symbol."""
        return self.account.positions.get(symbol)
    
    def close_position(self, symbol: str, price: float) -> Optional[Fill]:
        """Close entire position."""
        pos = self.account.positions.get(symbol)
        if not pos:
            return None
        
        order = self.submit_order(
            symbol=symbol,
            side='SELL',
            quantity=pos.quantity,
            price=price
        )
        
        return self.fill_order(order.order_id, price)
    
    def get_summary(self) -> Dict:
        """Get account summary."""
        return {
            'initial_capital': self.account.initial_capital,
            'cash': self.account.cash,
            'equity': self.account.equity,
            'pnl': self.account.pnl,
            'pnl_pct': self.account.pnl_pct,
            'positions': len(self.account.positions),
            'trades': len(self.account.trades),
            'win_rate': self._calculate_win_rate(),
        }
    
    def _calculate_win_rate(self) -> float:
        """Calculate win rate from closed trades."""
        if not self.account.trades:
            return 0.0
        
        wins = sum(1 for t in self.account.trades if t.pnl > 0)
        return wins / len(self.account.trades) * 100
    
    def reset(self, capital: float = None):
        """Reset account."""
        if capital is None:
            capital = self.account.initial_capital
        
        self.account = PaperAccount(
            initial_capital=capital,
            cash=capital
        )
    
    def on_fill(self, callback: Callable):
        """Register fill callback."""
        self._on_fill = callback
    
    def on_order(self, callback: Callable):
        """Register order callback."""
        self._on_order = callback
