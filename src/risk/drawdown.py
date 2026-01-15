"""
Drawdown Monitor
================

Track and respond to drawdowns to protect capital.
"""

from datetime import datetime, timedelta
from typing import Dict, Optional, List
from dataclasses import dataclass, field


@dataclass
class DrawdownConfig:
    """Drawdown monitoring configuration."""
    max_daily_loss: float = 0.03  # 3% max daily loss
    max_weekly_loss: float = 0.06  # 6% max weekly loss
    max_total_drawdown: float = 0.15  # 15% max total drawdown
    
    # Recovery settings
    recovery_threshold: float = 0.05  # 5% drawdown triggers recovery mode
    recovery_scale: float = 0.50  # Scale positions to 50% in recovery


class DrawdownMonitor:
    """
    Monitor and manage drawdowns.
    
    Features:
    - Track daily, weekly, total drawdown
    - Automatic position scaling in recovery
    - Hard stop at max drawdown
    
    Example:
        monitor = DrawdownMonitor(max_daily_loss=0.03)
        
        # Update with daily P&L
        monitor.update(daily_pnl=-500, capital=100000)
        
        # Check status
        status = monitor.get_status()
        if status['halted']:
            close_all_positions()
    """
    
    def __init__(
        self,
        max_daily_loss: float = 0.03,
        max_weekly_loss: float = 0.06,
        max_total_drawdown: float = 0.15
    ):
        self.max_daily_loss = max_daily_loss
        self.max_weekly_loss = max_weekly_loss
        self.max_total_drawdown = max_total_drawdown
        
        # Tracking
        self._high_water_mark: float = 0.0
        self._current_value: float = 0.0
        self._daily_pnl: float = 0.0
        self._weekly_pnl: float = 0.0
        self._total_drawdown: float = 0.0
        
        # State
        self._halted: bool = False
        self._halt_reason: str = ""
        self._in_recovery: bool = False
        
        # History
        self._daily_history: List[float] = []
    
    def update(self, pnl: float, capital: float):
        """
        Update drawdown tracking with new P&L.
        
        Args:
            pnl: Today's P&L
            capital: Current capital value
        """
        self._daily_pnl = pnl
        self._weekly_pnl += pnl
        self._current_value = capital
        
        # Update high water mark
        if capital > self._high_water_mark:
            self._high_water_mark = capital
            self._in_recovery = False
        
        # Calculate total drawdown
        if self._high_water_mark > 0:
            self._total_drawdown = (self._high_water_mark - capital) / self._high_water_mark
        
        # Check limits
        self._check_limits(capital)
        
        # Track history
        self._daily_history.append(pnl)
        if len(self._daily_history) > 252:  # 1 year
            self._daily_history.pop(0)
    
    def _check_limits(self, capital: float):
        """Check if any limits are breached."""
        # Daily limit
        daily_loss_pct = -self._daily_pnl / capital if self._daily_pnl < 0 and capital > 0 else 0
        if daily_loss_pct >= self.max_daily_loss:
            self._halted = True
            self._halt_reason = f"Daily loss limit ({daily_loss_pct:.1%} >= {self.max_daily_loss:.1%})"
            return
        
        # Weekly limit
        weekly_loss_pct = -self._weekly_pnl / capital if self._weekly_pnl < 0 and capital > 0 else 0
        if weekly_loss_pct >= self.max_weekly_loss:
            self._halted = True
            self._halt_reason = f"Weekly loss limit ({weekly_loss_pct:.1%} >= {self.max_weekly_loss:.1%})"
            return
        
        # Total drawdown limit
        if self._total_drawdown >= self.max_total_drawdown:
            self._halted = True
            self._halt_reason = f"Max drawdown ({self._total_drawdown:.1%} >= {self.max_total_drawdown:.1%})"
            return
        
        # Recovery mode check
        if self._total_drawdown >= 0.05:  # 5% drawdown
            self._in_recovery = True
    
    def get_status(self) -> Dict:
        """Get current drawdown status."""
        return {
            'halted': self._halted,
            'reason': self._halt_reason,
            'in_recovery': self._in_recovery,
            'daily_pnl': self._daily_pnl,
            'weekly_pnl': self._weekly_pnl,
            'total_drawdown': self._total_drawdown,
            'total_drawdown_pct': f"{self._total_drawdown:.1%}",
            'high_water_mark': self._high_water_mark,
            'current_value': self._current_value,
        }
    
    def in_recovery(self) -> bool:
        """Check if in recovery mode."""
        return self._in_recovery
    
    def is_halted(self) -> bool:
        """Check if trading is halted."""
        return self._halted
    
    def reset_daily(self):
        """Reset daily tracking (call at market open)."""
        self._daily_pnl = 0.0
        if self._halted and "Daily" in self._halt_reason:
            self._halted = False
            self._halt_reason = ""
    
    def reset_weekly(self):
        """Reset weekly tracking."""
        self._weekly_pnl = 0.0
        if self._halted and "Weekly" in self._halt_reason:
            self._halted = False
            self._halt_reason = ""
    
    def reset_all(self, new_capital: float):
        """Full reset with new capital."""
        self._high_water_mark = new_capital
        self._current_value = new_capital
        self._daily_pnl = 0.0
        self._weekly_pnl = 0.0
        self._total_drawdown = 0.0
        self._halted = False
        self._halt_reason = ""
        self._in_recovery = False
    
    def get_max_drawdown_history(self) -> float:
        """Calculate maximum drawdown from history."""
        if not self._daily_history:
            return 0.0
        
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        
        for pnl in self._daily_history:
            cumulative += pnl
            peak = max(peak, cumulative)
            dd = (peak - cumulative) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        return max_dd
