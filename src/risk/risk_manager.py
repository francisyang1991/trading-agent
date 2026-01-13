"""
Risk Manager
Enforces risk limits and circuit breakers.
NON-NEGOTIABLE rules that override all trading signals.
"""

from typing import Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, date
import pandas as pd
from loguru import logger

from ..position.position_manager import PositionManager, Position


class RiskLevel(Enum):
    """Risk alert levels."""
    NORMAL = "normal"
    ELEVATED = "elevated"
    HIGH = "high"
    CRITICAL = "critical"
    HALT = "halt"


@dataclass
class RiskAlert:
    """Risk alert record."""
    timestamp: datetime
    level: RiskLevel
    category: str
    message: str
    value: float
    threshold: float
    action_taken: str


class RiskManager:
    """
    Portfolio risk manager.
    Enforces hard limits and triggers circuit breakers.
    
    NON-NEGOTIABLE RULES:
    - Single position max limit
    - Portfolio max drawdown limit
    - Daily loss circuit breaker
    - Sector concentration limit
    """
    
    def __init__(
        self,
        # Position limits
        max_position_pct: float = 0.25,
        max_positions: int = 10,
        
        # Sector limits
        max_sector_exposure: float = 0.30,
        
        # Loss limits
        max_portfolio_drawdown: float = 0.15,
        daily_loss_limit: float = 0.03,
        single_trade_loss_limit: float = 0.02,
        
        # Profit taking
        partial_profit_threshold: float = 0.15,
        trailing_stop_pct: float = 0.08,
        
        # Market conditions
        vix_threshold: float = 30,
        market_crash_threshold: float = 0.05,
        
        # Alert callback
        alert_callback: Optional[Callable[[RiskAlert], None]] = None
    ):
        """
        Initialize risk manager.
        
        Args:
            max_position_pct: Max single position % of portfolio
            max_positions: Max concurrent positions
            max_sector_exposure: Max sector exposure %
            max_portfolio_drawdown: Portfolio drawdown halt level
            daily_loss_limit: Daily loss circuit breaker %
            single_trade_loss_limit: Max loss per trade %
            partial_profit_threshold: Take partial profit above this gain
            trailing_stop_pct: Trailing stop percentage
            vix_threshold: VIX level for reduced exposure
            market_crash_threshold: Market drop for emergency mode
            alert_callback: Function to call on alerts
        """
        # Limits
        self.max_position_pct = max_position_pct
        self.max_positions = max_positions
        self.max_sector_exposure = max_sector_exposure
        
        self.max_portfolio_drawdown = max_portfolio_drawdown
        self.daily_loss_limit = daily_loss_limit
        self.single_trade_loss = single_trade_loss_limit
        
        self.partial_profit_threshold = partial_profit_threshold
        self.trailing_stop_pct = trailing_stop_pct
        
        self.vix_threshold = vix_threshold
        self.market_crash_threshold = market_crash_threshold
        
        self.alert_callback = alert_callback
        
        # State tracking
        self._peak_equity: float = 0
        self._daily_start_equity: float = 0
        self._current_date: Optional[date] = None
        self._trading_halted: bool = False
        self._halt_reason: str = ""
        
        # Alert history
        self._alerts: List[RiskAlert] = []
        
        # Sector tracking
        self._sector_map: Dict[str, str] = {}  # symbol -> sector
    
    def set_sector_map(self, sector_map: Dict[str, str]):
        """Set symbol to sector mapping."""
        self._sector_map = sector_map
    
    def update_equity(self, equity: float, current_date: Optional[date] = None):
        """
        Update equity tracking.
        
        Args:
            equity: Current portfolio equity
            current_date: Current date
        """
        # Update peak
        if equity > self._peak_equity:
            self._peak_equity = equity
        
        # Reset daily tracking on new day
        today = current_date or date.today()
        if self._current_date != today:
            self._current_date = today
            self._daily_start_equity = equity
            self._trading_halted = False  # Reset daily halt
    
    def check_all_risks(
        self,
        position_manager: PositionManager,
        current_prices: Dict[str, float],
        vix_level: float = 0,
        market_return: float = 0
    ) -> Tuple[RiskLevel, List[RiskAlert]]:
        """
        Comprehensive risk check.
        
        Args:
            position_manager: Position manager instance
            current_prices: Current prices {symbol: price}
            vix_level: Current VIX level
            market_return: Today's market return
            
        Returns:
            (overall_risk_level, list_of_alerts)
        """
        alerts = []
        
        # Update equity
        portfolio_value = position_manager.portfolio_value
        self.update_equity(portfolio_value)
        
        # Check portfolio drawdown
        dd_alert = self._check_portfolio_drawdown(portfolio_value)
        if dd_alert:
            alerts.append(dd_alert)
        
        # Check daily loss
        daily_alert = self._check_daily_loss(portfolio_value)
        if daily_alert:
            alerts.append(daily_alert)
        
        # Check position limits
        pos_alerts = self._check_position_limits(position_manager, current_prices)
        alerts.extend(pos_alerts)
        
        # Check sector concentration
        sector_alerts = self._check_sector_concentration(position_manager, current_prices)
        alerts.extend(sector_alerts)
        
        # Check market conditions
        market_alerts = self._check_market_conditions(vix_level, market_return)
        alerts.extend(market_alerts)
        
        # Store alerts
        self._alerts.extend(alerts)
        
        # Determine overall level
        overall_level = RiskLevel.NORMAL
        
        for alert in alerts:
            if alert.level == RiskLevel.HALT:
                overall_level = RiskLevel.HALT
                break
            elif alert.level == RiskLevel.CRITICAL and overall_level != RiskLevel.HALT:
                overall_level = RiskLevel.CRITICAL
            elif alert.level == RiskLevel.HIGH and overall_level not in [RiskLevel.HALT, RiskLevel.CRITICAL]:
                overall_level = RiskLevel.HIGH
            elif alert.level == RiskLevel.ELEVATED and overall_level == RiskLevel.NORMAL:
                overall_level = RiskLevel.ELEVATED
        
        # Trigger callbacks
        for alert in alerts:
            if self.alert_callback:
                self.alert_callback(alert)
            logger.warning(f"Risk Alert [{alert.level.value}]: {alert.message}")
        
        return overall_level, alerts
    
    def _check_portfolio_drawdown(self, portfolio_value: float) -> Optional[RiskAlert]:
        """Check portfolio drawdown limit."""
        if self._peak_equity <= 0:
            return None
        
        drawdown = (self._peak_equity - portfolio_value) / self._peak_equity
        
        if drawdown >= self.max_portfolio_drawdown:
            self._trading_halted = True
            self._halt_reason = "Max portfolio drawdown reached"
            
            return RiskAlert(
                timestamp=datetime.now(),
                level=RiskLevel.HALT,
                category="portfolio_drawdown",
                message=f"Portfolio drawdown {drawdown*100:.1f}% exceeds limit {self.max_portfolio_drawdown*100:.1f}%",
                value=drawdown,
                threshold=self.max_portfolio_drawdown,
                action_taken="Trading halted"
            )
        
        elif drawdown >= self.max_portfolio_drawdown * 0.8:
            return RiskAlert(
                timestamp=datetime.now(),
                level=RiskLevel.CRITICAL,
                category="portfolio_drawdown",
                message=f"Portfolio drawdown {drawdown*100:.1f}% approaching limit",
                value=drawdown,
                threshold=self.max_portfolio_drawdown,
                action_taken="Reduce exposure recommended"
            )
        
        elif drawdown >= self.max_portfolio_drawdown * 0.5:
            return RiskAlert(
                timestamp=datetime.now(),
                level=RiskLevel.ELEVATED,
                category="portfolio_drawdown",
                message=f"Portfolio drawdown {drawdown*100:.1f}%",
                value=drawdown,
                threshold=self.max_portfolio_drawdown,
                action_taken="Monitor closely"
            )
        
        return None
    
    def _check_daily_loss(self, portfolio_value: float) -> Optional[RiskAlert]:
        """Check daily loss circuit breaker."""
        if self._daily_start_equity <= 0:
            return None
        
        daily_loss = (self._daily_start_equity - portfolio_value) / self._daily_start_equity
        
        if daily_loss >= self.daily_loss_limit:
            self._trading_halted = True
            self._halt_reason = "Daily loss limit reached"
            
            return RiskAlert(
                timestamp=datetime.now(),
                level=RiskLevel.HALT,
                category="daily_loss",
                message=f"Daily loss {daily_loss*100:.1f}% exceeds limit {self.daily_loss_limit*100:.1f}%",
                value=daily_loss,
                threshold=self.daily_loss_limit,
                action_taken="Trading halted for today"
            )
        
        elif daily_loss >= self.daily_loss_limit * 0.7:
            return RiskAlert(
                timestamp=datetime.now(),
                level=RiskLevel.HIGH,
                category="daily_loss",
                message=f"Daily loss {daily_loss*100:.1f}% approaching limit",
                value=daily_loss,
                threshold=self.daily_loss_limit,
                action_taken="No new positions"
            )
        
        return None
    
    def _check_position_limits(
        self,
        position_manager: PositionManager,
        current_prices: Dict[str, float]
    ) -> List[RiskAlert]:
        """Check individual position limits."""
        alerts = []
        
        portfolio_value = position_manager.portfolio_value
        
        for symbol, position in position_manager.positions.items():
            price = current_prices.get(symbol, position.avg_entry_price)
            pos_value = position.current_quantity * price
            pos_pct = pos_value / portfolio_value
            
            # Check position size
            if pos_pct > self.max_position_pct:
                alerts.append(RiskAlert(
                    timestamp=datetime.now(),
                    level=RiskLevel.HIGH,
                    category="position_size",
                    message=f"{symbol} position {pos_pct*100:.1f}% exceeds limit {self.max_position_pct*100:.1f}%",
                    value=pos_pct,
                    threshold=self.max_position_pct,
                    action_taken="Reduce position"
                ))
            
            # Check individual position loss
            pnl_pct = (price - position.avg_entry_price) / position.avg_entry_price
            
            if pnl_pct <= -self.single_trade_loss:
                alerts.append(RiskAlert(
                    timestamp=datetime.now(),
                    level=RiskLevel.HIGH,
                    category="trade_loss",
                    message=f"{symbol} loss {abs(pnl_pct)*100:.1f}% exceeds limit",
                    value=abs(pnl_pct),
                    threshold=self.single_trade_loss,
                    action_taken="Exit position"
                ))
        
        # Check total positions
        if len(position_manager.positions) > self.max_positions:
            alerts.append(RiskAlert(
                timestamp=datetime.now(),
                level=RiskLevel.ELEVATED,
                category="position_count",
                message=f"Position count {len(position_manager.positions)} exceeds limit {self.max_positions}",
                value=len(position_manager.positions),
                threshold=self.max_positions,
                action_taken="No new positions"
            ))
        
        return alerts
    
    def _check_sector_concentration(
        self,
        position_manager: PositionManager,
        current_prices: Dict[str, float]
    ) -> List[RiskAlert]:
        """Check sector concentration limits."""
        alerts = []
        
        if not self._sector_map:
            return alerts
        
        portfolio_value = position_manager.portfolio_value
        
        # Calculate sector exposures
        sector_values: Dict[str, float] = {}
        
        for symbol, position in position_manager.positions.items():
            sector = self._sector_map.get(symbol, "Unknown")
            price = current_prices.get(symbol, position.avg_entry_price)
            pos_value = position.current_quantity * price
            
            sector_values[sector] = sector_values.get(sector, 0) + pos_value
        
        # Check each sector
        for sector, value in sector_values.items():
            exposure = value / portfolio_value
            
            if exposure > self.max_sector_exposure:
                alerts.append(RiskAlert(
                    timestamp=datetime.now(),
                    level=RiskLevel.ELEVATED,
                    category="sector_concentration",
                    message=f"{sector} exposure {exposure*100:.1f}% exceeds limit {self.max_sector_exposure*100:.1f}%",
                    value=exposure,
                    threshold=self.max_sector_exposure,
                    action_taken="Reduce sector exposure"
                ))
        
        return alerts
    
    def _check_market_conditions(
        self,
        vix_level: float,
        market_return: float
    ) -> List[RiskAlert]:
        """Check market-wide conditions."""
        alerts = []
        
        # VIX check
        if vix_level >= self.vix_threshold:
            alerts.append(RiskAlert(
                timestamp=datetime.now(),
                level=RiskLevel.HIGH,
                category="market_volatility",
                message=f"VIX at {vix_level:.1f} exceeds threshold {self.vix_threshold}",
                value=vix_level,
                threshold=self.vix_threshold,
                action_taken="Reduce overall exposure"
            ))
        
        # Market crash check
        if market_return <= -self.market_crash_threshold:
            alerts.append(RiskAlert(
                timestamp=datetime.now(),
                level=RiskLevel.CRITICAL,
                category="market_crash",
                message=f"Market down {abs(market_return)*100:.1f}% - emergency mode",
                value=abs(market_return),
                threshold=self.market_crash_threshold,
                action_taken="Emergency position reduction"
            ))
        
        return alerts
    
    def can_open_position(
        self,
        symbol: str,
        position_manager: PositionManager
    ) -> Tuple[bool, str]:
        """
        Check if new position is allowed.
        
        Args:
            symbol: Symbol to trade
            position_manager: Position manager
            
        Returns:
            (allowed, reason)
        """
        if self._trading_halted:
            return False, f"Trading halted: {self._halt_reason}"
        
        if len(position_manager.positions) >= self.max_positions:
            return False, f"Max positions ({self.max_positions}) reached"
        
        if symbol in position_manager.positions:
            return False, f"Position already exists for {symbol}"
        
        return True, "OK"
    
    def can_add_to_position(
        self,
        symbol: str,
        position_manager: PositionManager,
        current_price: float
    ) -> Tuple[bool, str]:
        """
        Check if adding to position is allowed.
        
        Args:
            symbol: Symbol
            position_manager: Position manager
            current_price: Current price
            
        Returns:
            (allowed, reason)
        """
        if self._trading_halted:
            return False, f"Trading halted: {self._halt_reason}"
        
        if symbol not in position_manager.positions:
            return False, "No position to add to"
        
        # Check current position size
        pos_pct = position_manager.get_position_pct(symbol)
        
        if pos_pct >= self.max_position_pct:
            return False, f"Position at max size ({self.max_position_pct*100:.0f}%)"
        
        return True, "OK"
    
    def get_position_size_limit(
        self,
        symbol: str,
        position_manager: PositionManager
    ) -> float:
        """
        Get maximum allowed position size percentage.
        
        Args:
            symbol: Symbol
            position_manager: Position manager
            
        Returns:
            Max position percentage allowed
        """
        current_pct = position_manager.get_position_pct(symbol)
        remaining = self.max_position_pct - current_pct
        
        return max(0, remaining)
    
    def should_take_profit(
        self,
        position: Position,
        current_price: float
    ) -> Tuple[bool, float]:
        """
        Check if should take partial profit.
        
        Args:
            position: Position
            current_price: Current price
            
        Returns:
            (should_take_profit, percentage_to_exit)
        """
        pnl_pct = (current_price - position.avg_entry_price) / position.avg_entry_price
        
        if pnl_pct >= self.partial_profit_threshold:
            return True, 0.5  # Take 50% profit
        
        # Check trailing stop
        if position.highest_price > 0:
            from_high = (position.highest_price - current_price) / position.highest_price
            
            if from_high >= self.trailing_stop_pct and pnl_pct > 0:
                return True, 0.5
        
        return False, 0.0
    
    @property
    def is_trading_halted(self) -> bool:
        """Check if trading is halted."""
        return self._trading_halted
    
    @property
    def halt_reason(self) -> str:
        """Get halt reason."""
        return self._halt_reason
    
    def get_recent_alerts(self, limit: int = 20) -> List[RiskAlert]:
        """Get recent alerts."""
        return self._alerts[-limit:]
    
    def reset_daily_state(self, equity: float):
        """Reset daily tracking state."""
        self._daily_start_equity = equity
        self._trading_halted = False
        self._halt_reason = ""
