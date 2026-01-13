"""
Position Manager
Tracks positions, manages sizing, and coordinates with risk rules.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import json
from pathlib import Path
import pandas as pd
from loguru import logger

from ..classifier.stock_classifier import StockType, ClassificationResult


class PositionStatus(Enum):
    """Position status types."""
    OPEN = "open"
    PARTIAL = "partial"        # Partially closed
    CLOSED = "closed"


@dataclass
class Position:
    """Individual position details."""
    symbol: str
    status: PositionStatus
    
    # Entry details
    entry_price: float
    entry_date: datetime
    initial_quantity: int
    current_quantity: int
    
    # Classification at entry
    stock_type: StockType
    
    # Risk management
    stop_price: float
    target_price: float
    trailing_stop: Optional[float] = None
    highest_price: float = 0.0
    
    # Tracking
    entry_signal_score: float = 0.0
    add_count: int = 0                    # Number of additions
    avg_entry_price: float = 0.0          # Volume-weighted avg
    
    # P&L
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    
    # Metadata
    entry_reason: str = ""
    notes: List[str] = field(default_factory=list)
    
    def update_unrealized_pnl(self, current_price: float):
        """Update unrealized P&L."""
        self.unrealized_pnl = (current_price - self.avg_entry_price) * self.current_quantity
        
        # Update highest price for trailing stop
        if current_price > self.highest_price:
            self.highest_price = current_price
    
    def get_pnl_pct(self) -> float:
        """Get P&L as percentage."""
        if self.avg_entry_price <= 0:
            return 0.0
        return (self.unrealized_pnl / (self.avg_entry_price * self.current_quantity)) * 100
    
    def get_position_value(self, current_price: float) -> float:
        """Get current position value."""
        return current_price * self.current_quantity
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            'symbol': self.symbol,
            'status': self.status.value,
            'entry_price': self.entry_price,
            'entry_date': self.entry_date.isoformat() if isinstance(self.entry_date, datetime) else str(self.entry_date),
            'initial_quantity': self.initial_quantity,
            'current_quantity': self.current_quantity,
            'stock_type': self.stock_type.value,
            'stop_price': self.stop_price,
            'target_price': self.target_price,
            'trailing_stop': self.trailing_stop,
            'highest_price': self.highest_price,
            'entry_signal_score': self.entry_signal_score,
            'add_count': self.add_count,
            'avg_entry_price': self.avg_entry_price,
            'realized_pnl': self.realized_pnl,
            'unrealized_pnl': self.unrealized_pnl,
            'entry_reason': self.entry_reason,
            'notes': self.notes
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Position':
        """Create from dictionary."""
        return cls(
            symbol=data['symbol'],
            status=PositionStatus(data['status']),
            entry_price=data['entry_price'],
            entry_date=datetime.fromisoformat(data['entry_date']) if isinstance(data['entry_date'], str) else data['entry_date'],
            initial_quantity=data['initial_quantity'],
            current_quantity=data['current_quantity'],
            stock_type=StockType(data['stock_type']),
            stop_price=data['stop_price'],
            target_price=data['target_price'],
            trailing_stop=data.get('trailing_stop'),
            highest_price=data.get('highest_price', 0.0),
            entry_signal_score=data.get('entry_signal_score', 0.0),
            add_count=data.get('add_count', 0),
            avg_entry_price=data.get('avg_entry_price', data['entry_price']),
            realized_pnl=data.get('realized_pnl', 0.0),
            unrealized_pnl=data.get('unrealized_pnl', 0.0),
            entry_reason=data.get('entry_reason', ''),
            notes=data.get('notes', [])
        )


class PositionManager:
    """
    Manages all trading positions.
    Enforces sizing rules and tracks portfolio state.
    """
    
    def __init__(
        self,
        initial_capital: float = 100000,
        initial_position_pct: float = 0.05,
        add_position_pct: float = 0.05,
        max_position_pct: float = 0.25,
        max_positions: int = 10,
        state_file: Optional[str] = None
    ):
        """
        Initialize position manager.
        
        Args:
            initial_capital: Starting capital
            initial_position_pct: Initial position size %
            add_position_pct: Add position size %
            max_position_pct: Maximum position size %
            max_positions: Maximum number of positions
            state_file: File for persisting state
        """
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        
        self.initial_pct = initial_position_pct
        self.add_pct = add_position_pct
        self.max_pct = max_position_pct
        self.max_positions = max_positions
        
        self.state_file = Path(state_file) if state_file else None
        
        # Active positions: {symbol: Position}
        self.positions: Dict[str, Position] = {}
        
        # Historical positions
        self.closed_positions: List[Position] = []
        
        # Load state if exists
        if self.state_file and self.state_file.exists():
            self._load_state()
    
    @property
    def portfolio_value(self) -> float:
        """Get total portfolio value."""
        positions_value = sum(
            p.current_quantity * p.avg_entry_price 
            for p in self.positions.values()
        )
        return self.current_capital + positions_value
    
    @property
    def cash_available(self) -> float:
        """Get available cash."""
        return self.current_capital
    
    @property
    def positions_value(self) -> float:
        """Get total positions value."""
        return sum(
            p.current_quantity * p.avg_entry_price 
            for p in self.positions.values()
        )
    
    @property
    def num_positions(self) -> int:
        """Get number of open positions."""
        return len(self.positions)
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for symbol."""
        return self.positions.get(symbol)
    
    def has_position(self, symbol: str) -> bool:
        """Check if position exists."""
        return symbol in self.positions
    
    def get_position_pct(self, symbol: str) -> float:
        """Get position size as % of portfolio."""
        if symbol not in self.positions:
            return 0.0
        
        pos = self.positions[symbol]
        pos_value = pos.current_quantity * pos.avg_entry_price
        
        return pos_value / self.portfolio_value
    
    def calculate_position_size(
        self,
        symbol: str,
        current_price: float,
        classification: ClassificationResult,
        is_add: bool = False
    ) -> Tuple[int, float]:
        """
        Calculate position size based on rules.
        
        Args:
            symbol: Stock ticker
            current_price: Current price
            classification: Stock classification
            is_add: True if adding to existing position
            
        Returns:
            (quantity, value)
        """
        if current_price <= 0:
            return 0, 0.0
        
        # Check position limits
        if not is_add and len(self.positions) >= self.max_positions:
            logger.warning(f"Max positions ({self.max_positions}) reached")
            return 0, 0.0
        
        # Get size percentage
        if is_add:
            size_pct = self.add_pct
        else:
            size_pct = self.initial_pct
        
        # Adjust for classification
        max_allowed = min(classification.max_position_pct, self.max_pct)
        
        # Check if would exceed max
        current_pct = self.get_position_pct(symbol)
        
        if current_pct + size_pct > max_allowed:
            size_pct = max_allowed - current_pct
        
        if size_pct <= 0:
            logger.warning(f"Position {symbol} at max size")
            return 0, 0.0
        
        # Calculate value and quantity
        position_value = self.portfolio_value * size_pct
        
        # Check cash available
        if position_value > self.cash_available:
            position_value = self.cash_available * 0.95  # Keep 5% buffer
        
        quantity = int(position_value / current_price)
        actual_value = quantity * current_price
        
        return quantity, actual_value
    
    def open_position(
        self,
        symbol: str,
        quantity: int,
        entry_price: float,
        stop_price: float,
        target_price: float,
        classification: ClassificationResult,
        signal_score: float = 0.0,
        reason: str = ""
    ) -> Optional[Position]:
        """
        Open a new position.
        
        Args:
            symbol: Stock ticker
            quantity: Number of shares
            entry_price: Entry price
            stop_price: Stop loss price
            target_price: Target price
            classification: Stock classification
            signal_score: Entry signal score
            reason: Entry reason
            
        Returns:
            Position if successful
        """
        if symbol in self.positions:
            logger.warning(f"Position already exists for {symbol}")
            return None
        
        if quantity <= 0:
            logger.warning(f"Invalid quantity: {quantity}")
            return None
        
        # Check cash
        cost = quantity * entry_price
        if cost > self.cash_available:
            logger.warning(f"Insufficient cash: need ${cost:.2f}, have ${self.cash_available:.2f}")
            return None
        
        # Create position
        position = Position(
            symbol=symbol,
            status=PositionStatus.OPEN,
            entry_price=entry_price,
            entry_date=datetime.now(),
            initial_quantity=quantity,
            current_quantity=quantity,
            stock_type=classification.stock_type,
            stop_price=stop_price,
            target_price=target_price,
            highest_price=entry_price,
            entry_signal_score=signal_score,
            avg_entry_price=entry_price,
            entry_reason=reason
        )
        
        # Update cash
        self.current_capital -= cost
        
        # Store position
        self.positions[symbol] = position
        
        logger.info(f"Opened position: {symbol} x {quantity} @ ${entry_price:.2f}")
        
        self._save_state()
        
        return position
    
    def add_to_position(
        self,
        symbol: str,
        quantity: int,
        price: float,
        reason: str = ""
    ) -> bool:
        """
        Add to existing position.
        
        Args:
            symbol: Stock ticker
            quantity: Additional shares
            price: Add price
            reason: Reason for adding
            
        Returns:
            True if successful
        """
        if symbol not in self.positions:
            logger.warning(f"No position exists for {symbol}")
            return False
        
        position = self.positions[symbol]
        
        if quantity <= 0:
            return False
        
        cost = quantity * price
        
        if cost > self.cash_available:
            logger.warning(f"Insufficient cash for add")
            return False
        
        # Update average price
        total_cost = (position.avg_entry_price * position.current_quantity) + cost
        new_quantity = position.current_quantity + quantity
        position.avg_entry_price = total_cost / new_quantity
        
        # Update position
        position.current_quantity = new_quantity
        position.add_count += 1
        position.notes.append(f"Added {quantity} @ ${price:.2f}: {reason}")
        
        # Update cash
        self.current_capital -= cost
        
        logger.info(f"Added to position: {symbol} +{quantity} @ ${price:.2f}")
        
        self._save_state()
        
        return True
    
    def reduce_position(
        self,
        symbol: str,
        quantity: int,
        price: float,
        reason: str = ""
    ) -> float:
        """
        Reduce position (partial exit).
        
        Args:
            symbol: Stock ticker
            quantity: Shares to sell
            price: Exit price
            reason: Exit reason
            
        Returns:
            Realized P&L
        """
        if symbol not in self.positions:
            logger.warning(f"No position exists for {symbol}")
            return 0.0
        
        position = self.positions[symbol]
        
        if quantity > position.current_quantity:
            quantity = position.current_quantity
        
        # Calculate P&L
        cost_basis = position.avg_entry_price * quantity
        proceeds = price * quantity
        pnl = proceeds - cost_basis
        
        # Update position
        position.current_quantity -= quantity
        position.realized_pnl += pnl
        position.status = PositionStatus.PARTIAL if position.current_quantity > 0 else PositionStatus.CLOSED
        position.notes.append(f"Reduced by {quantity} @ ${price:.2f}: {reason}")
        
        # Update cash
        self.current_capital += proceeds
        
        logger.info(f"Reduced position: {symbol} -{quantity} @ ${price:.2f}, P&L: ${pnl:.2f}")
        
        # If fully closed, move to history
        if position.current_quantity <= 0:
            self.closed_positions.append(position)
            del self.positions[symbol]
        
        self._save_state()
        
        return pnl
    
    def close_position(
        self,
        symbol: str,
        price: float,
        reason: str = ""
    ) -> float:
        """
        Close entire position.
        
        Args:
            symbol: Stock ticker
            price: Exit price
            reason: Exit reason
            
        Returns:
            Total realized P&L
        """
        if symbol not in self.positions:
            return 0.0
        
        position = self.positions[symbol]
        return self.reduce_position(symbol, position.current_quantity, price, reason)
    
    def update_stops(
        self,
        symbol: str,
        stop_price: Optional[float] = None,
        target_price: Optional[float] = None,
        trailing_stop: Optional[float] = None
    ):
        """Update position stop/target prices."""
        if symbol not in self.positions:
            return
        
        position = self.positions[symbol]
        
        if stop_price is not None:
            position.stop_price = stop_price
        
        if target_price is not None:
            position.target_price = target_price
        
        if trailing_stop is not None:
            position.trailing_stop = trailing_stop
        
        self._save_state()
    
    def update_prices(self, prices: Dict[str, float]):
        """
        Update positions with current prices.
        
        Args:
            prices: {symbol: current_price}
        """
        for symbol, price in prices.items():
            if symbol in self.positions:
                self.positions[symbol].update_unrealized_pnl(price)
    
    def get_portfolio_summary(self) -> Dict:
        """Get portfolio summary."""
        total_pnl = sum(p.unrealized_pnl for p in self.positions.values())
        total_realized = sum(p.realized_pnl for p in self.positions.values())
        total_realized += sum(p.realized_pnl for p in self.closed_positions)
        
        return {
            'portfolio_value': self.portfolio_value,
            'cash': self.cash_available,
            'positions_value': self.positions_value,
            'num_positions': self.num_positions,
            'unrealized_pnl': total_pnl,
            'realized_pnl': total_realized,
            'total_return_pct': (self.portfolio_value - self.initial_capital) / self.initial_capital * 100
        }
    
    def get_positions_list(self) -> List[Dict]:
        """Get list of all positions."""
        return [p.to_dict() for p in self.positions.values()]
    
    def _save_state(self):
        """Save state to file."""
        if not self.state_file:
            return
        
        try:
            state = {
                'current_capital': self.current_capital,
                'positions': {s: p.to_dict() for s, p in self.positions.items()},
                'closed_positions': [p.to_dict() for p in self.closed_positions[-100:]]  # Keep last 100
            }
            
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
                
        except Exception as e:
            logger.error(f"Error saving state: {e}")
    
    def _load_state(self):
        """Load state from file."""
        if not self.state_file or not self.state_file.exists():
            return
        
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
            
            self.current_capital = state.get('current_capital', self.initial_capital)
            
            self.positions = {
                s: Position.from_dict(p) 
                for s, p in state.get('positions', {}).items()
            }
            
            self.closed_positions = [
                Position.from_dict(p) 
                for p in state.get('closed_positions', [])
            ]
            
            logger.info(f"Loaded state: {len(self.positions)} positions")
            
        except Exception as e:
            logger.error(f"Error loading state: {e}")
