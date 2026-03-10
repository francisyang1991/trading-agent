"""
Circuit Breaker
===============

Emergency stop mechanism to halt trading during extreme conditions.
"""

from datetime import datetime, timedelta
from typing import Dict, Optional, List
from dataclasses import dataclass
from enum import Enum


class CircuitBreakerLevel(Enum):
    """Circuit breaker levels."""
    NONE = 0
    LEVEL_1 = 1  # Warning
    LEVEL_2 = 2  # Reduce exposure
    LEVEL_3 = 3  # Halt new trades
    LEVEL_4 = 4  # Close all positions


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration."""
    # Trigger thresholds
    level_1_loss: float = 0.015  # 1.5% loss
    level_2_loss: float = 0.025  # 2.5% loss
    level_3_loss: float = 0.035  # 3.5% loss - halt
    level_4_loss: float = 0.050  # 5% loss - close all
    
    # Cool-down periods
    level_1_cooldown_minutes: int = 15
    level_2_cooldown_minutes: int = 30
    level_3_cooldown_minutes: int = 60
    level_4_cooldown_minutes: int = 1440  # 24 hours
    
    # Auto-reset
    auto_reset_next_day: bool = True


class CircuitBreaker:
    """
    Circuit Breaker System - Emergency trading halt.
    
    Levels:
    - Level 1: Warning (1.5% loss) - Alert only
    - Level 2: Reduce (2.5% loss) - Cut position sizes by 50%
    - Level 3: Halt (3.5% loss) - No new trades
    - Level 4: Close All (5% loss) - Liquidate portfolio
    
    Example:
        breaker = CircuitBreaker()
        
        # Check after loss
        breaker.check(loss_pct=0.03, threshold=0.035)
        
        if breaker.is_triggered():
            if breaker.level >= CircuitBreakerLevel.LEVEL_3:
                halt_trading()
            if breaker.level == CircuitBreakerLevel.LEVEL_4:
                close_all_positions()
    """
    
    def __init__(self, config: Optional[CircuitBreakerConfig] = None):
        self.config = config or CircuitBreakerConfig()
        
        # State
        self._level: CircuitBreakerLevel = CircuitBreakerLevel.NONE
        self._triggered_at: Optional[datetime] = None
        self._trigger_reason: str = ""
        
        # History
        self._trigger_history: List[Dict] = []
    
    def check(self, loss_pct: float, threshold: float = None) -> CircuitBreakerLevel:
        """
        Check if circuit breaker should trigger.
        
        Args:
            loss_pct: Current loss as decimal (0.03 = 3%)
            threshold: Optional override threshold
            
        Returns:
            Current circuit breaker level
        """
        # Use config thresholds
        if loss_pct >= self.config.level_4_loss:
            self._trigger(CircuitBreakerLevel.LEVEL_4, f"Critical loss: {loss_pct:.1%}")
        elif loss_pct >= self.config.level_3_loss:
            self._trigger(CircuitBreakerLevel.LEVEL_3, f"Severe loss: {loss_pct:.1%}")
        elif loss_pct >= self.config.level_2_loss:
            self._trigger(CircuitBreakerLevel.LEVEL_2, f"Significant loss: {loss_pct:.1%}")
        elif loss_pct >= self.config.level_1_loss:
            self._trigger(CircuitBreakerLevel.LEVEL_1, f"Warning: {loss_pct:.1%}")
        
        return self._level
    
    def _trigger(self, level: CircuitBreakerLevel, reason: str):
        """Trigger circuit breaker at level."""
        if level.value > self._level.value:
            self._level = level
            self._triggered_at = datetime.now()
            self._trigger_reason = reason
            
            self._trigger_history.append({
                'level': level,
                'reason': reason,
                'time': self._triggered_at
            })
    
    def is_triggered(self) -> bool:
        """Check if any circuit breaker is active."""
        if self._level == CircuitBreakerLevel.NONE:
            return False
        
        # Check if cool-down has passed
        if self._triggered_at:
            elapsed = datetime.now() - self._triggered_at
            cooldown = self._get_cooldown(self._level)
            
            if elapsed > cooldown:
                self._level = CircuitBreakerLevel.NONE
                return False
        
        return True
    
    def _get_cooldown(self, level: CircuitBreakerLevel) -> timedelta:
        """Get cool-down period for level."""
        cooldowns = {
            CircuitBreakerLevel.LEVEL_1: timedelta(minutes=self.config.level_1_cooldown_minutes),
            CircuitBreakerLevel.LEVEL_2: timedelta(minutes=self.config.level_2_cooldown_minutes),
            CircuitBreakerLevel.LEVEL_3: timedelta(minutes=self.config.level_3_cooldown_minutes),
            CircuitBreakerLevel.LEVEL_4: timedelta(minutes=self.config.level_4_cooldown_minutes),
        }
        return cooldowns.get(level, timedelta(minutes=60))
    
    def get_status(self) -> Dict:
        """Get current circuit breaker status."""
        remaining = None
        if self._triggered_at and self._level != CircuitBreakerLevel.NONE:
            cooldown = self._get_cooldown(self._level)
            elapsed = datetime.now() - self._triggered_at
            remaining = max(0, (cooldown - elapsed).total_seconds() / 60)
        
        return {
            'triggered': self.is_triggered(),
            'level': self._level.value,
            'level_name': self._level.name,
            'reason': self._trigger_reason,
            'triggered_at': self._triggered_at,
            'cooldown_remaining_minutes': remaining,
        }
    
    @property
    def level(self) -> CircuitBreakerLevel:
        """Get current level."""
        return self._level
    
    def should_halt_trading(self) -> bool:
        """Check if trading should be halted."""
        return self._level.value >= CircuitBreakerLevel.LEVEL_3.value
    
    def should_close_all(self) -> bool:
        """Check if all positions should be closed."""
        return self._level == CircuitBreakerLevel.LEVEL_4
    
    def get_position_scale(self) -> float:
        """Get position scaling factor based on level."""
        scales = {
            CircuitBreakerLevel.NONE: 1.0,
            CircuitBreakerLevel.LEVEL_1: 0.75,
            CircuitBreakerLevel.LEVEL_2: 0.50,
            CircuitBreakerLevel.LEVEL_3: 0.0,
            CircuitBreakerLevel.LEVEL_4: 0.0,
        }
        return scales.get(self._level, 1.0)
    
    def reset(self):
        """Reset circuit breaker."""
        self._level = CircuitBreakerLevel.NONE
        self._triggered_at = None
        self._trigger_reason = ""
    
    def get_history(self) -> List[Dict]:
        """Get trigger history."""
        return self._trigger_history
