"""
Risk Manager
============

Central risk management system that monitors and controls all risk.

Features:
1. Pre-trade risk checks
2. Real-time position monitoring
3. Portfolio-level risk limits
4. Drawdown protection
5. Circuit breakers
"""

import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from ..core.types import Signal, Position, Order, RiskLimits
from .position_limits import PositionLimits
from .portfolio_limits import PortfolioLimits
from .drawdown import DrawdownMonitor
from .circuit_breaker import CircuitBreaker


class RiskStatus(Enum):
    """Overall risk status."""
    NORMAL = "normal"
    ELEVATED = "elevated"
    HIGH = "high"
    CRITICAL = "critical"
    HALTED = "halted"


@dataclass
class RiskCheckResult:
    """Result from risk check."""
    passed: bool
    status: RiskStatus
    reasons: List[str] = field(default_factory=list)
    adjustments: Dict = field(default_factory=dict)


@dataclass
class RiskConfig:
    """Risk management configuration."""
    # Capital
    total_capital: float = 100000.0
    
    # Position limits
    max_position_pct: float = 0.10  # 10% max per position
    max_position_risk_pct: float = 0.02  # 2% max risk per position
    max_positions: int = 20
    
    # Portfolio limits
    max_portfolio_risk_pct: float = 0.06  # 6% total portfolio risk
    max_sector_exposure_pct: float = 0.30  # 30% max per sector
    max_correlation_exposure: float = 0.50  # Max correlated exposure
    
    # Drawdown limits
    max_daily_loss_pct: float = 0.03  # 3% max daily loss
    max_weekly_loss_pct: float = 0.06  # 6% max weekly loss
    max_total_drawdown_pct: float = 0.15  # 15% max drawdown
    
    # Circuit breakers
    enable_circuit_breakers: bool = True
    halt_on_circuit_break: bool = True
    
    # Recovery
    recovery_position_scale: float = 0.50  # Scale down after losses


class RiskManager:
    """
    Central Risk Management System.
    
    Features:
    - Pre-trade risk validation
    - Real-time position monitoring
    - Drawdown protection
    - Automatic position reduction
    - Circuit breakers
    
    Example:
        risk_mgr = RiskManager(capital=100000)
        
        # Check if trade is allowed
        result = risk_mgr.check_trade(signal, position_size)
        
        if result.passed:
            execute_trade(signal)
        else:
            print(f"Trade rejected: {result.reasons}")
    """
    
    def __init__(self, config: Optional[RiskConfig] = None):
        self.config = config or RiskConfig()
        
        # Initialize sub-components
        self.position_limits = PositionLimits(
            max_position_pct=self.config.max_position_pct,
            max_risk_pct=self.config.max_position_risk_pct,
            max_positions=self.config.max_positions
        )
        
        self.portfolio_limits = PortfolioLimits(
            max_portfolio_risk=self.config.max_portfolio_risk_pct,
            max_sector_exposure=self.config.max_sector_exposure_pct
        )
        
        self.drawdown_monitor = DrawdownMonitor(
            max_daily_loss=self.config.max_daily_loss_pct,
            max_weekly_loss=self.config.max_weekly_loss_pct,
            max_total_drawdown=self.config.max_total_drawdown_pct
        )
        
        self.circuit_breaker = CircuitBreaker()
        
        # State tracking
        self._positions: Dict[str, Position] = {}
        self._current_risk: float = 0.0
        self._daily_pnl: float = 0.0
        self._weekly_pnl: float = 0.0
        self._status: RiskStatus = RiskStatus.NORMAL
    
    def check_trade(
        self,
        signal: Signal,
        proposed_size: float,
        proposed_risk: float
    ) -> RiskCheckResult:
        """
        Check if proposed trade passes all risk checks.
        
        Args:
            signal: Entry signal
            proposed_size: Proposed position value
            proposed_risk: Proposed risk amount
            
        Returns:
            RiskCheckResult with pass/fail and reasons
        """
        reasons = []
        adjustments = {}
        
        # Check circuit breaker first
        if self.config.enable_circuit_breakers:
            if self.circuit_breaker.is_triggered():
                return RiskCheckResult(
                    passed=False,
                    status=RiskStatus.HALTED,
                    reasons=["Circuit breaker active - trading halted"]
                )
        
        # Check drawdown status
        dd_status = self.drawdown_monitor.get_status()
        if dd_status['halted']:
            return RiskCheckResult(
                passed=False,
                status=RiskStatus.HALTED,
                reasons=[f"Drawdown limit hit: {dd_status['reason']}"]
            )
        
        # Check position limits
        pos_check = self.position_limits.check(
            symbol=signal.symbol,
            size=proposed_size,
            risk=proposed_risk,
            capital=self.config.total_capital,
            current_positions=len(self._positions)
        )
        
        if not pos_check['passed']:
            reasons.extend(pos_check['reasons'])
            if 'adjusted_size' in pos_check:
                adjustments['size'] = pos_check['adjusted_size']
                adjustments['risk'] = pos_check['adjusted_risk']
        
        # Check portfolio limits
        port_check = self.portfolio_limits.check(
            new_risk=proposed_risk,
            current_risk=self._current_risk,
            capital=self.config.total_capital
        )
        
        if not port_check['passed']:
            reasons.extend(port_check['reasons'])
        
        # Determine status
        if reasons:
            if 'halted' in str(reasons).lower():
                status = RiskStatus.HALTED
            elif 'critical' in str(reasons).lower():
                status = RiskStatus.CRITICAL
            elif len(reasons) > 2:
                status = RiskStatus.HIGH
            else:
                status = RiskStatus.ELEVATED
        else:
            status = RiskStatus.NORMAL
        
        # Apply recovery scaling if in drawdown
        if self.drawdown_monitor.in_recovery():
            scale = self.config.recovery_position_scale
            adjustments['scale'] = scale
            adjustments['size'] = proposed_size * scale
            adjustments['risk'] = proposed_risk * scale
            reasons.append(f"Recovery mode: scaled to {scale:.0%}")
        
        passed = len([r for r in reasons if 'reject' in r.lower() or 'limit' in r.lower() or 'halt' in r.lower()]) == 0
        
        return RiskCheckResult(
            passed=passed,
            status=status,
            reasons=reasons,
            adjustments=adjustments
        )
    
    def update_position(self, position: Position):
        """Update tracked position."""
        self._positions[position.symbol] = position
        self._recalculate_risk()
    
    def remove_position(self, symbol: str):
        """Remove closed position from tracking."""
        if symbol in self._positions:
            del self._positions[symbol]
            self._recalculate_risk()
    
    def update_pnl(self, daily_pnl: float, weekly_pnl: float = None):
        """Update P&L tracking."""
        self._daily_pnl = daily_pnl
        if weekly_pnl is not None:
            self._weekly_pnl = weekly_pnl
        
        # Update drawdown monitor
        self.drawdown_monitor.update(daily_pnl, self.config.total_capital)
        
        # Check circuit breakers
        if self.config.enable_circuit_breakers:
            daily_loss_pct = -daily_pnl / self.config.total_capital if daily_pnl < 0 else 0
            self.circuit_breaker.check(daily_loss_pct, self.config.max_daily_loss_pct)
    
    def _recalculate_risk(self):
        """Recalculate total portfolio risk."""
        total_risk = 0.0
        
        for position in self._positions.values():
            if position.stop_loss and position.entry_price:
                risk_per_share = abs(position.entry_price - position.stop_loss)
                position_risk = risk_per_share * position.quantity
                total_risk += position_risk
        
        self._current_risk = total_risk
    
    def get_status(self) -> Dict:
        """Get current risk status."""
        dd_status = self.drawdown_monitor.get_status()
        cb_status = self.circuit_breaker.get_status()
        
        return {
            'status': self._status.value,
            'current_risk': self._current_risk,
            'risk_pct': self._current_risk / self.config.total_capital * 100 if self.config.total_capital > 0 else 0,
            'positions': len(self._positions),
            'max_positions': self.config.max_positions,
            'daily_pnl': self._daily_pnl,
            'daily_pnl_pct': self._daily_pnl / self.config.total_capital * 100 if self.config.total_capital > 0 else 0,
            'drawdown': dd_status,
            'circuit_breaker': cb_status,
            'can_trade': not dd_status['halted'] and not cb_status['triggered'],
        }
    
    def get_available_risk(self) -> float:
        """Get available risk budget."""
        max_risk = self.config.total_capital * self.config.max_portfolio_risk_pct
        available = max_risk - self._current_risk
        return max(0, available)
    
    def get_position_limit(self, symbol: str) -> float:
        """Get maximum position size for symbol."""
        return self.config.total_capital * self.config.max_position_pct
    
    def should_reduce_exposure(self) -> Tuple[bool, float]:
        """
        Check if exposure should be reduced.
        
        Returns:
            (should_reduce, target_scale)
        """
        dd_status = self.drawdown_monitor.get_status()
        
        if dd_status['halted']:
            return True, 0.0  # Close all
        
        if dd_status['in_recovery']:
            return True, self.config.recovery_position_scale
        
        # Check daily loss
        daily_loss_pct = -self._daily_pnl / self.config.total_capital if self._daily_pnl < 0 else 0
        if daily_loss_pct > self.config.max_daily_loss_pct * 0.5:
            return True, 0.75  # Scale down 25%
        
        return False, 1.0
    
    def reset_daily(self):
        """Reset daily tracking (call at market open)."""
        self._daily_pnl = 0.0
        self.circuit_breaker.reset()
        self.drawdown_monitor.reset_daily()
    
    def reset_weekly(self):
        """Reset weekly tracking."""
        self._weekly_pnl = 0.0
        self.drawdown_monitor.reset_weekly()
