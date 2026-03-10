"""
Core module - Foundation types, events, and configuration.
"""

from .types import (
    # Enums
    Regime,
    VolatilityLevel,
    SignalType,
    OrderSide,
    OrderType,
    OrderStatus,
    PositionStatus,
    
    # Data classes
    Price,
    OHLCV,
    Signal,
    Order,
    Fill,
    Position,
    Trade,
    PickScore,
    RiskLimits,
    
    # Type aliases
    Symbol,
    Timestamp,
)

from .events import EventBus, Event, EventType
from .config import SystemConfig, load_config

__all__ = [
    # Enums
    'Regime',
    'VolatilityLevel', 
    'SignalType',
    'OrderSide',
    'OrderType',
    'OrderStatus',
    'PositionStatus',
    
    # Data classes
    'Price',
    'OHLCV',
    'Signal',
    'Order',
    'Fill',
    'Position',
    'Trade',
    'PickScore',
    'RiskLimits',
    
    # Type aliases
    'Symbol',
    'Timestamp',
    
    # Events
    'EventBus',
    'Event',
    'EventType',
    
    # Config
    'SystemConfig',
    'load_config',
]
