"""
Event system for component communication.

Allows loose coupling between components through publish/subscribe pattern.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


class EventType(Enum):
    """Event types for system communication."""
    
    # Market Data Events
    MARKET_DATA_UPDATE = auto()
    PRICE_UPDATE = auto()
    VOLUME_SPIKE = auto()
    
    # Signal Events
    SIGNAL_GENERATED = auto()
    SIGNAL_CONFIRMED = auto()
    SIGNAL_EXPIRED = auto()
    
    # Order Events
    ORDER_SUBMITTED = auto()
    ORDER_FILLED = auto()
    ORDER_PARTIAL_FILL = auto()
    ORDER_CANCELLED = auto()
    ORDER_REJECTED = auto()
    
    # Position Events
    POSITION_OPENED = auto()
    POSITION_ADDED = auto()
    POSITION_REDUCED = auto()
    POSITION_CLOSED = auto()
    POSITION_STOP_HIT = auto()
    POSITION_TARGET_HIT = auto()
    
    # Risk Events
    RISK_LIMIT_WARNING = auto()
    RISK_LIMIT_BREACH = auto()
    DRAWDOWN_WARNING = auto()
    CIRCUIT_BREAKER_TRIGGERED = auto()
    
    # Portfolio Events
    PORTFOLIO_UPDATE = auto()
    REBALANCE_REQUIRED = auto()
    
    # Regime Events
    REGIME_CHANGE = auto()
    VOLATILITY_SPIKE = auto()
    
    # System Events
    SYSTEM_START = auto()
    SYSTEM_STOP = auto()
    HEARTBEAT = auto()
    ERROR = auto()
    
    # Pick Events
    PICK_GENERATED = auto()
    PICK_EXPIRED = auto()


@dataclass
class Event:
    """Event with payload and metadata."""
    event_type: EventType
    timestamp: datetime
    source: str                     # Component that generated event
    data: Dict[str, Any] = field(default_factory=dict)
    
    # Optional fields
    symbol: Optional[str] = None
    priority: int = 0               # Higher = more important
    
    def __post_init__(self):
        if self.data is None:
            self.data = {}
    
    def __str__(self):
        return f"Event({self.event_type.name}, {self.source}, {self.symbol or 'N/A'})"


class EventBus:
    """
    Central event bus for publish/subscribe communication.
    
    Components can:
    - Subscribe to specific event types
    - Publish events
    - Filter events by symbol or criteria
    
    Example:
        bus = EventBus()
        
        def on_signal(event):
            print(f"Got signal: {event.data}")
        
        bus.subscribe(EventType.SIGNAL_GENERATED, on_signal)
        bus.publish(Event(
            event_type=EventType.SIGNAL_GENERATED,
            timestamp=datetime.now(),
            source="SignalEngine",
            symbol="NVDA",
            data={"signal_type": "ENTRY_LONG", "score": 85}
        ))
    """
    
    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable]] = defaultdict(list)
        self._symbol_subscribers: Dict[str, Dict[EventType, List[Callable]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._all_subscribers: List[Callable] = []
        self._event_history: List[Event] = []
        self._max_history: int = 1000
        self._paused: bool = False
    
    def subscribe(
        self,
        event_type: EventType,
        callback: Callable[[Event], None],
        symbol: Optional[str] = None
    ):
        """
        Subscribe to events of a specific type.
        
        Args:
            event_type: Type of events to subscribe to
            callback: Function to call when event occurs
            symbol: Optional - only receive events for this symbol
        """
        if symbol:
            self._symbol_subscribers[symbol][event_type].append(callback)
        else:
            self._subscribers[event_type].append(callback)
    
    def subscribe_all(self, callback: Callable[[Event], None]):
        """Subscribe to all events."""
        self._all_subscribers.append(callback)
    
    def unsubscribe(
        self,
        event_type: EventType,
        callback: Callable[[Event], None],
        symbol: Optional[str] = None
    ):
        """Unsubscribe from events."""
        if symbol:
            if event_type in self._symbol_subscribers[symbol]:
                if callback in self._symbol_subscribers[symbol][event_type]:
                    self._symbol_subscribers[symbol][event_type].remove(callback)
        else:
            if callback in self._subscribers[event_type]:
                self._subscribers[event_type].remove(callback)
    
    def publish(self, event: Event):
        """
        Publish an event to all subscribers.
        
        Args:
            event: Event to publish
        """
        if self._paused:
            return
        
        # Store in history
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history.pop(0)
        
        # Notify all-event subscribers
        for callback in self._all_subscribers:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Error in event callback: {e}")
        
        # Notify type-specific subscribers
        for callback in self._subscribers[event.event_type]:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Error in event callback: {e}")
        
        # Notify symbol-specific subscribers
        if event.symbol:
            for callback in self._symbol_subscribers[event.symbol][event.event_type]:
                try:
                    callback(event)
                except Exception as e:
                    logger.error(f"Error in event callback: {e}")
    
    def pause(self):
        """Pause event delivery."""
        self._paused = True
    
    def resume(self):
        """Resume event delivery."""
        self._paused = False
    
    def get_history(
        self,
        event_type: Optional[EventType] = None,
        symbol: Optional[str] = None,
        limit: int = 100
    ) -> List[Event]:
        """
        Get recent event history.
        
        Args:
            event_type: Filter by event type
            symbol: Filter by symbol
            limit: Maximum events to return
            
        Returns:
            List of matching events (most recent first)
        """
        events = self._event_history.copy()
        events.reverse()
        
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        
        if symbol:
            events = [e for e in events if e.symbol == symbol]
        
        return events[:limit]
    
    def clear_history(self):
        """Clear event history."""
        self._event_history.clear()
    
    def get_subscriber_count(self, event_type: Optional[EventType] = None) -> int:
        """Get count of subscribers."""
        if event_type:
            return len(self._subscribers[event_type])
        return sum(len(callbacks) for callbacks in self._subscribers.values())


# Global event bus instance (optional singleton pattern)
_global_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get or create global event bus."""
    global _global_bus
    if _global_bus is None:
        _global_bus = EventBus()
    return _global_bus


def reset_event_bus():
    """Reset global event bus (for testing)."""
    global _global_bus
    _global_bus = None


# =============================================================================
# HELPER FUNCTIONS FOR CREATING EVENTS
# =============================================================================

def create_signal_event(
    symbol: str,
    signal_type: str,
    score: float,
    source: str = "SignalEngine",
    **kwargs
) -> Event:
    """Create a signal event."""
    return Event(
        event_type=EventType.SIGNAL_GENERATED,
        timestamp=datetime.now(),
        source=source,
        symbol=symbol,
        data={
            "signal_type": signal_type,
            "score": score,
            **kwargs
        }
    )


def create_order_event(
    event_type: EventType,
    order_id: str,
    symbol: str,
    side: str,
    quantity: int,
    source: str = "OrderManager",
    **kwargs
) -> Event:
    """Create an order event."""
    return Event(
        event_type=event_type,
        timestamp=datetime.now(),
        source=source,
        symbol=symbol,
        data={
            "order_id": order_id,
            "side": side,
            "quantity": quantity,
            **kwargs
        }
    )


def create_position_event(
    event_type: EventType,
    symbol: str,
    quantity: int,
    price: float,
    source: str = "PositionManager",
    **kwargs
) -> Event:
    """Create a position event."""
    return Event(
        event_type=event_type,
        timestamp=datetime.now(),
        source=source,
        symbol=symbol,
        data={
            "quantity": quantity,
            "price": price,
            **kwargs
        }
    )


def create_risk_event(
    event_type: EventType,
    level: str,
    message: str,
    value: float,
    threshold: float,
    source: str = "RiskManager",
    **kwargs
) -> Event:
    """Create a risk event."""
    return Event(
        event_type=event_type,
        timestamp=datetime.now(),
        source=source,
        priority=1 if event_type == EventType.RISK_LIMIT_BREACH else 0,
        data={
            "level": level,
            "message": message,
            "value": value,
            "threshold": threshold,
            **kwargs
        }
    )
