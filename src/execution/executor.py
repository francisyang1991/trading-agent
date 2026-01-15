"""
Trade Executor
==============

Main execution engine that coordinates order flow.
"""

from datetime import datetime
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass

from ..core.types import Signal, Order, Fill, Position
from ..risk.risk_manager import RiskManager, RiskCheckResult
from ..sizing.position_sizer import PositionSizer, SizingResult
from .order_manager import OrderManager, ManagedOrder


@dataclass
class ExecutionConfig:
    """Execution configuration."""
    # Slippage assumptions
    default_slippage_pct: float = 0.001  # 0.1%
    
    # Order types
    use_limit_orders: bool = True
    limit_offset_pct: float = 0.002  # 0.2% above/below market
    
    # Timing
    wait_for_fill_seconds: int = 60
    
    # Safety
    require_risk_approval: bool = True


class TradeExecutor:
    """
    Trade Execution Engine.
    
    Coordinates:
    - Signal processing
    - Risk checks
    - Position sizing
    - Order creation
    - Fill handling
    
    Example:
        executor = TradeExecutor(capital=100000)
        
        # Execute entry signal
        result = executor.execute_entry(signal, price_data)
        
        if result['success']:
            print(f"Bought {result['shares']} shares")
    """
    
    def __init__(
        self,
        capital: float = 100000.0,
        config: Optional[ExecutionConfig] = None
    ):
        self.capital = capital
        self.config = config or ExecutionConfig()
        
        # Components
        self.order_manager = OrderManager()
        self.risk_manager = RiskManager()
        self.position_sizer = PositionSizer()
        
        # Update capital
        self.risk_manager.config.total_capital = capital
        self.position_sizer.config.total_capital = capital
        
        # Positions
        self._positions: Dict[str, Position] = {}
        
        # Callbacks
        self._on_fill: Optional[Callable] = None
        self._on_order: Optional[Callable] = None
    
    def execute_entry(
        self,
        signal: Signal,
        current_price: float,
        volatility: float = 0.20
    ) -> Dict:
        """
        Execute entry signal.
        
        Args:
            signal: Entry signal
            current_price: Current market price
            volatility: Stock volatility
            
        Returns:
            Execution result dict
        """
        symbol = signal.symbol
        
        # Calculate position size
        sizing = self.position_sizer.calculate_size(
            signal=signal,
            current_price=current_price,
            stop_loss=signal.stop_loss,
            volatility=volatility
        )
        
        if sizing.shares <= 0:
            return {
                'success': False,
                'reason': 'Position size too small',
                'sizing': sizing
            }
        
        # Risk check
        if self.config.require_risk_approval:
            risk_check = self.risk_manager.check_trade(
                signal=signal,
                proposed_size=sizing.position_value,
                proposed_risk=sizing.risk_amount
            )
            
            if not risk_check.passed:
                return {
                    'success': False,
                    'reason': f"Risk check failed: {', '.join(risk_check.reasons)}",
                    'risk_check': risk_check
                }
            
            # Apply adjustments if any
            if risk_check.adjustments:
                if 'size' in risk_check.adjustments:
                    adjusted_size = risk_check.adjustments['size']
                    sizing.shares = int(adjusted_size / current_price)
                    sizing.position_value = sizing.shares * current_price
        
        # Create order
        managed_order = self.order_manager.create_order(
            signal=signal,
            shares=sizing.shares,
            limit_price=current_price * (1 + self.config.limit_offset_pct)
        )
        
        # Submit order
        self.order_manager.submit_order(managed_order.order.order_id)
        
        if self._on_order:
            self._on_order(managed_order)
        
        return {
            'success': True,
            'order': managed_order,
            'sizing': sizing,
            'shares': sizing.shares,
            'position_value': sizing.position_value,
            'risk_amount': sizing.risk_amount
        }
    
    def execute_exit(
        self,
        signal: Signal,
        position: Position,
        current_price: float
    ) -> Dict:
        """
        Execute exit signal.
        
        Args:
            signal: Exit signal
            position: Position to exit
            current_price: Current market price
            
        Returns:
            Execution result dict
        """
        # Get exit percentage from signal
        exit_pct = signal.metadata.get('exit_pct', 1.0) if signal.metadata else 1.0
        
        shares_to_sell = int(position.quantity * exit_pct)
        
        if shares_to_sell <= 0:
            return {
                'success': False,
                'reason': 'No shares to sell'
            }
        
        # Create exit order
        managed_order = self.order_manager.create_order(
            signal=signal,
            shares=shares_to_sell,
            limit_price=current_price * (1 - self.config.limit_offset_pct)
        )
        
        # Submit
        self.order_manager.submit_order(managed_order.order.order_id)
        
        return {
            'success': True,
            'order': managed_order,
            'shares': shares_to_sell,
            'exit_pct': exit_pct
        }
    
    def simulate_fill(
        self,
        order_id: str,
        fill_price: Optional[float] = None,
        slippage: Optional[float] = None
    ) -> Optional[Fill]:
        """
        Simulate fill for paper trading.
        
        Args:
            order_id: Order ID to fill
            fill_price: Fill price (uses limit price if None)
            slippage: Slippage to apply
            
        Returns:
            Fill object if successful
        """
        managed = self.order_manager.get_order(order_id)
        if not managed:
            return None
        
        order = managed.order
        
        # Calculate fill price
        if fill_price is None:
            fill_price = order.limit_price or order.stop_price or 0
        
        # Apply slippage
        if slippage is None:
            slippage = self.config.default_slippage_pct
        
        if order.side.value == 'BUY':
            fill_price *= (1 + slippage)
        else:
            fill_price *= (1 - slippage)
        
        # Create fill
        fill = Fill(
            fill_id=f"fill_{order_id}",
            order_id=order_id,
            symbol=order.symbol,
            quantity=order.quantity - managed.filled_qty,
            price=fill_price,
            timestamp=datetime.now(),
            commission=0.0
        )
        
        # Record fill
        self.order_manager.record_fill(order_id, fill)
        
        # Update position
        self._update_position_from_fill(order, fill)
        
        if self._on_fill:
            self._on_fill(fill)
        
        return fill
    
    def _update_position_from_fill(self, order: Order, fill: Fill):
        """Update position tracking from fill."""
        symbol = order.symbol
        
        if order.side.value == 'BUY':
            # Opening or adding to position
            if symbol in self._positions:
                pos = self._positions[symbol]
                # Average price
                total_qty = pos.quantity + fill.quantity
                total_value = (pos.entry_price * pos.quantity) + (fill.price * fill.quantity)
                pos.entry_price = total_value / total_qty
                pos.quantity = total_qty
                pos.current_price = fill.price
            else:
                self._positions[symbol] = Position(
                    symbol=symbol,
                    quantity=fill.quantity,
                    entry_price=fill.price,
                    current_price=fill.price,
                    entry_time=datetime.now(),
                    direction=1
                )
            
            # Update risk manager
            self.risk_manager.update_position(self._positions[symbol])
        
        else:  # SELL
            if symbol in self._positions:
                pos = self._positions[symbol]
                pos.quantity -= fill.quantity
                
                if pos.quantity <= 0:
                    self.risk_manager.remove_position(symbol)
                    del self._positions[symbol]
                else:
                    pos.current_price = fill.price
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """Get current position."""
        return self._positions.get(symbol)
    
    def get_all_positions(self) -> Dict[str, Position]:
        """Get all positions."""
        return self._positions.copy()
    
    def update_capital(self, new_capital: float):
        """Update capital for all components."""
        self.capital = new_capital
        self.risk_manager.config.total_capital = new_capital
        self.position_sizer.config.total_capital = new_capital
    
    def on_fill(self, callback: Callable):
        """Register fill callback."""
        self._on_fill = callback
    
    def on_order(self, callback: Callable):
        """Register order callback."""
        self._on_order = callback
