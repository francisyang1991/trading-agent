"""
Core types and data structures for the trading system.

This module defines the fundamental types used throughout the system,
ensuring consistency and type safety.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum, auto
from typing import Dict, List, Optional, Any, Union
import pandas as pd


# =============================================================================
# TYPE ALIASES
# =============================================================================

Symbol = str
Timestamp = datetime
Price = float
Quantity = int
Percentage = float  # 0.0 to 1.0 or 0 to 100 based on context


# =============================================================================
# ENUMS
# =============================================================================

class Regime(Enum):
    """Market/stock regime classification."""
    PARABOLIC = "PARABOLIC"        # >100% 6M momentum, ride the wave
    STRONG_UP = "STRONG_UP"        # 50-100% 6M, trend following
    MODERATE_UP = "MODERATE_UP"    # 20-50% 6M, trend following
    WEAK_UP = "WEAK_UP"            # 0-20% 6M, swing trading
    SIDEWAYS = "SIDEWAYS"          # -20% to 0%, mean reversion
    DOWNTREND = "DOWNTREND"        # <-20%, stay cash
    UNKNOWN = "UNKNOWN"            # Insufficient data


class VolatilityLevel(Enum):
    """Volatility classification for position sizing."""
    EXTREME = "EXTREME"           # >80% annualized
    HIGH = "HIGH"                 # 50-80%
    MEDIUM = "MEDIUM"             # 30-50%
    LOW = "LOW"                   # <30%
    # Backward-compatible aliases used by older modules/tests.
    MODERATE = "MEDIUM"
    ULTRA_HIGH = "EXTREME"


class SignalType(Enum):
    """Trading signal types."""
    # Entry signals
    ENTRY = auto()  # Generic entry
    ENTRY_LONG = auto()
    ENTRY_SHORT = auto()
    
    # Exit signals
    EXIT = auto()  # Generic exit
    EXIT_LONG = auto()
    EXIT_SHORT = auto()
    
    # Position management
    ADD_LONG = auto()
    ADD_SHORT = auto()
    PARTIAL_EXIT = auto()
    
    # Neutral
    NO_ACTION = auto()
    WAIT = auto()


class SignalStrength(Enum):
    """Signal conviction level."""
    STRONG = "STRONG"              # High conviction, full size
    MODERATE = "MODERATE"          # Medium conviction, reduced size
    WEAK = "WEAK"                  # Low conviction, minimum size
    NONE = "NONE"                  # No signal


class OrderSide(Enum):
    """Order direction."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    """Order type for execution."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"
    TRAILING_STOP = "TRAILING_STOP"


class OrderStatus(Enum):
    """Order lifecycle status."""
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class PositionStatus(Enum):
    """Position lifecycle status."""
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"            # Partially closed
    CLOSED = "CLOSED"


class Strategy(Enum):
    """Trading strategy type."""
    BUY_HOLD = "Buy & Hold"
    TRAILING_STOP = "Trailing Stop"
    TREND_FOLLOWING = "Trend Following"
    SWING_TRADE = "Swing Trade"
    MEAN_REVERSION = "Mean Reversion"
    STAY_CASH = "Stay Cash"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class OHLCV:
    """Single OHLCV bar."""
    timestamp: Timestamp
    open: Price
    high: Price
    low: Price
    close: Price
    volume: int
    
    @property
    def typical_price(self) -> Price:
        """Calculate typical price (HLC/3)."""
        return (self.high + self.low + self.close) / 3
    
    @property
    def range(self) -> Price:
        """Calculate high-low range."""
        return self.high - self.low
    
    @property
    def body(self) -> Price:
        """Calculate candle body (close - open)."""
        return self.close - self.open
    
    @property
    def is_bullish(self) -> bool:
        """Check if bullish candle."""
        return self.close > self.open


@dataclass
class Signal:
    """Trading signal with metadata."""
    symbol: Symbol
    signal_type: SignalType
    strength: SignalStrength = SignalStrength.MODERATE
    timestamp: Timestamp = None
    
    # Direction: 1 = long, -1 = short
    direction: int = 1
    
    # Scoring
    score: float = 0.0              # 0-100 score
    confidence: float = 0.0         # 0-1 confidence
    
    # Price levels (alternative names for compatibility)
    price: Optional[Price] = None           # Current/entry price
    entry_price: Optional[Price] = None
    stop_loss: Optional[Price] = None       # Alias for stop_price
    stop_price: Optional[Price] = None
    take_profit: Optional[Price] = None     # Alias for target_price
    target_price: Optional[Price] = None
    
    # Entry zone
    entry_zone_low: Optional[Price] = None
    entry_zone_high: Optional[Price] = None
    
    # Context
    regime: Optional[Regime] = None
    volatility: Optional[VolatilityLevel] = None
    strategy: Optional[Strategy] = None
    
    # Reasoning
    reasoning: str = ""
    factors: Dict[str, float] = field(default_factory=dict)
    metadata: Dict = field(default_factory=dict)
    
    def __post_init__(self):
        if self.factors is None:
            self.factors = {}
        if self.metadata is None:
            self.metadata = {}
        if self.timestamp is None:
            self.timestamp = datetime.now()
        # Sync aliases
        if self.price and not self.entry_price:
            self.entry_price = self.price
        if self.stop_loss and not self.stop_price:
            self.stop_price = self.stop_loss
        if self.take_profit and not self.target_price:
            self.target_price = self.take_profit
    
    @property
    def risk_reward(self) -> float:
        """Calculate risk/reward ratio."""
        if not all([self.entry_price, self.stop_price, self.target_price]):
            return 0.0
        risk = abs(self.entry_price - self.stop_price)
        reward = abs(self.target_price - self.entry_price)
        return reward / risk if risk > 0 else 0.0


@dataclass
class Order:
    """Order representation."""
    order_id: str
    symbol: Symbol
    side: OrderSide
    quantity: Quantity
    order_type: OrderType
    
    # Prices
    limit_price: Optional[Price] = None
    stop_price: Optional[Price] = None
    
    # Status
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: Quantity = 0
    avg_fill_price: Optional[Price] = None
    
    # Timestamps
    created_at: Optional[Timestamp] = None
    submitted_at: Optional[Timestamp] = None
    filled_at: Optional[Timestamp] = None
    
    # Metadata
    reason: str = ""
    signal_id: Optional[str] = None
    
    @property
    def remaining_quantity(self) -> Quantity:
        """Calculate unfilled quantity."""
        return self.quantity - self.filled_quantity
    
    @property
    def is_complete(self) -> bool:
        """Check if order is complete (filled, cancelled, rejected)."""
        return self.status in [
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED
        ]


@dataclass
class Fill:
    """Order fill/execution."""
    fill_id: str
    order_id: str
    symbol: Symbol
    side: OrderSide
    quantity: Quantity
    price: Price
    timestamp: Timestamp
    
    # Costs
    commission: float = 0.0
    slippage: float = 0.0
    
    @property
    def value(self) -> float:
        """Calculate fill value."""
        return self.quantity * self.price
    
    @property
    def total_cost(self) -> float:
        """Calculate total cost including fees."""
        return self.value + self.commission


@dataclass
class Position:
    """Open position in portfolio."""
    symbol: Symbol
    quantity: Quantity
    avg_entry_price: Price
    
    # Status
    status: PositionStatus = PositionStatus.OPEN
    
    # Risk management
    stop_price: Optional[Price] = None
    target_price: Optional[Price] = None
    trailing_stop_pct: Optional[float] = None
    
    # Classification
    regime: Optional[Regime] = None
    strategy: Optional[Strategy] = None
    
    # Timestamps
    entry_date: Optional[Timestamp] = None
    last_update: Optional[Timestamp] = None
    
    # Entry details
    initial_quantity: Quantity = 0
    entry_reason: str = ""
    signal_score: float = 0.0
    
    # Current state (updated on price change)
    current_price: Optional[Price] = None
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    
    # History
    add_history: List[Dict] = field(default_factory=list)
    partial_exits: List[Dict] = field(default_factory=list)
    
    def __post_init__(self):
        if self.initial_quantity == 0:
            self.initial_quantity = self.quantity
        if self.add_history is None:
            self.add_history = []
        if self.partial_exits is None:
            self.partial_exits = []
    
    @property
    def market_value(self) -> float:
        """Calculate current market value."""
        if self.current_price:
            return self.quantity * self.current_price
        return self.quantity * self.avg_entry_price
    
    @property
    def cost_basis(self) -> float:
        """Calculate cost basis."""
        return self.quantity * self.avg_entry_price
    
    def update_price(self, price: Price):
        """Update position with current price."""
        self.current_price = price
        self.unrealized_pnl = (price - self.avg_entry_price) * self.quantity
        self.unrealized_pnl_pct = (price / self.avg_entry_price - 1) * 100


@dataclass
class Trade:
    """Completed trade (closed position or partial exit)."""
    trade_id: str
    symbol: Symbol
    side: str                       # "LONG" or "SHORT"
    
    # Entry
    entry_date: Timestamp
    entry_price: Price
    entry_quantity: Quantity
    entry_reason: str
    
    # Exit
    exit_date: Timestamp
    exit_price: Price
    exit_quantity: Quantity
    exit_reason: str
    
    # Performance
    pnl: float
    pnl_pct: float
    holding_days: int
    
    # Context
    regime: Optional[Regime] = None
    strategy: Optional[Strategy] = None
    signal_score: float = 0.0
    
    # Costs
    commission: float = 0.0
    slippage: float = 0.0
    
    @property
    def gross_pnl(self) -> float:
        """P&L before costs."""
        return (self.exit_price - self.entry_price) * self.exit_quantity
    
    @property
    def net_pnl(self) -> float:
        """P&L after costs."""
        return self.pnl - self.commission - self.slippage
    
    @property
    def is_winner(self) -> bool:
        """Check if trade was profitable."""
        return self.pnl > 0


@dataclass
class PickScore:
    """Stock pick with scoring breakdown."""
    symbol: Symbol
    timestamp: Timestamp
    
    # Overall
    total_score: float              # 0-100
    grade: str                      # A, B, C, D, F
    rank: int                       # Rank in universe
    
    # Factor scores (0-100 each)
    momentum_score: float = 0.0
    relative_strength_score: float = 0.0
    quality_score: float = 0.0
    value_score: float = 0.0
    technical_score: float = 0.0
    volume_score: float = 0.0
    
    # Metrics
    momentum_6m: float = 0.0
    momentum_3m: float = 0.0
    rs_vs_spy: float = 0.0
    volatility: float = 0.0
    rsi: float = 0.0
    dist_from_high: float = 0.0
    
    # Classification
    regime: Optional[Regime] = None
    volatility_level: Optional[VolatilityLevel] = None
    strategy: Optional[Strategy] = None
    
    # Entry plan
    entry_zone_low: Optional[Price] = None
    entry_zone_high: Optional[Price] = None
    stop_loss: Optional[Price] = None
    target_1: Optional[Price] = None
    target_2: Optional[Price] = None
    
    # Expected value
    win_rate: float = 0.5
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expected_value: float = 0.0
    risk_reward: float = 0.0
    
    # Position sizing
    position_size_pct: float = 0.0
    kelly_pct: float = 0.0
    
    # Metadata
    pattern: str = ""
    catalysts: List[str] = field(default_factory=list)
    reasoning: str = ""
    
    def __post_init__(self):
        if self.catalysts is None:
            self.catalysts = []


@dataclass
class RiskLimits:
    """Risk management limits."""
    # Position limits
    max_position_pct: float = 0.15           # Max single position
    max_position_value: float = 50000        # Max $ per position
    
    # Portfolio limits
    max_positions: int = 20                  # Max concurrent positions
    max_portfolio_exposure: float = 1.0      # Max total exposure
    max_sector_exposure: float = 0.30        # Max per sector
    max_correlated_exposure: float = 0.50    # Max correlated positions
    
    # Loss limits
    max_daily_loss: float = 0.03             # Daily loss limit
    max_weekly_loss: float = 0.06            # Weekly loss limit
    max_drawdown: float = 0.15               # Max drawdown before halt
    single_trade_max_loss: float = 0.02      # Max loss per trade
    
    # Circuit breakers
    consecutive_loss_halt: int = 5           # Halt after N losses
    intraday_loss_halt: float = 0.02         # Intraday loss halt
    
    def validate_position_size(
        self,
        size_pct: float,
        size_value: float,
        portfolio_value: float
    ) -> tuple[bool, str]:
        """Validate proposed position size."""
        if size_pct > self.max_position_pct:
            return False, f"Size {size_pct:.1%} exceeds max {self.max_position_pct:.1%}"
        if size_value > self.max_position_value:
            return False, f"Value ${size_value:,.0f} exceeds max ${self.max_position_value:,.0f}"
        return True, "OK"


@dataclass
class PortfolioSnapshot:
    """Point-in-time portfolio state."""
    timestamp: Timestamp
    
    # Values
    total_value: float
    cash: float
    positions_value: float
    
    # Positions
    num_positions: int
    positions: List[Position]
    
    # Performance
    daily_pnl: float = 0.0
    daily_return: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    
    # Risk metrics
    current_drawdown: float = 0.0
    max_drawdown: float = 0.0
    exposure: float = 0.0
    
    # Sector exposure
    sector_exposure: Dict[str, float] = field(default_factory=dict)
    
    def __post_init__(self):
        if self.sector_exposure is None:
            self.sector_exposure = {}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def dataframe_to_ohlcv_list(df: pd.DataFrame) -> List[OHLCV]:
    """Convert DataFrame to list of OHLCV objects."""
    ohlcv_list = []
    
    # Normalize column names
    col_map = {}
    for col in df.columns:
        col_lower = col.lower()
        if col_lower in ['open', 'o']:
            col_map['open'] = col
        elif col_lower in ['high', 'h']:
            col_map['high'] = col
        elif col_lower in ['low', 'l']:
            col_map['low'] = col
        elif col_lower in ['close', 'c', 'adj close']:
            col_map['close'] = col
        elif col_lower in ['volume', 'v', 'vol']:
            col_map['volume'] = col
    
    for idx, row in df.iterrows():
        ohlcv_list.append(OHLCV(
            timestamp=idx if isinstance(idx, datetime) else datetime.now(),
            open=row[col_map.get('open', 'open')],
            high=row[col_map.get('high', 'high')],
            low=row[col_map.get('low', 'low')],
            close=row[col_map.get('close', 'close')],
            volume=int(row[col_map.get('volume', 'volume')])
        ))
    
    return ohlcv_list


def get_regime_from_momentum(momentum_6m: float) -> Regime:
    """Classify regime based on 6-month momentum."""
    if momentum_6m > 100:
        return Regime.PARABOLIC
    elif momentum_6m > 50:
        return Regime.STRONG_UP
    elif momentum_6m > 20:
        return Regime.MODERATE_UP
    elif momentum_6m > 0:
        return Regime.WEAK_UP
    elif momentum_6m > -20:
        return Regime.SIDEWAYS
    else:
        return Regime.DOWNTREND


def get_volatility_level(annualized_vol: float) -> VolatilityLevel:
    """Classify volatility level."""
    if annualized_vol > 80:
        return VolatilityLevel.ULTRA_HIGH
    elif annualized_vol > 50:
        return VolatilityLevel.HIGH
    elif annualized_vol > 30:
        return VolatilityLevel.MODERATE
    else:
        return VolatilityLevel.LOW


def calculate_grade(score: float) -> str:
    """Convert score to letter grade."""
    if score >= 80:
        return "A"
    elif score >= 70:
        return "B"
    elif score >= 60:
        return "C"
    elif score >= 50:
        return "D"
    else:
        return "F"
