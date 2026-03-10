"""
PDT/day-trade safeguards for intraday T+0 MVP.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional


@dataclass
class DayTradeGuardConfig:
    enforce_pdt_guard: bool = True
    min_equity_for_unlimited_daytrade: float = 25000.0
    max_day_trades_under_25k: int = 3


class DayTradeGuard:
    def __init__(self, config: DayTradeGuardConfig):
        self.config = config
        self._local_day_trades_used = 0
        self._last_reset_date: Optional[str] = None

    def _ensure_daily_reset(self) -> None:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if self._last_reset_date != today:
            self._local_day_trades_used = 0
            self._last_reset_date = today

    def mark_day_trade(self) -> None:
        self._ensure_daily_reset()
        self._local_day_trades_used += 1

    @property
    def local_day_trades_used(self) -> int:
        self._ensure_daily_reset()
        return self._local_day_trades_used

    def can_trade(
        self,
        net_liquidation: Optional[float],
        day_trades_remaining: Optional[int],
    ) -> tuple[bool, str]:
        self._ensure_daily_reset()

        if not self.config.enforce_pdt_guard:
            return True, "PDT guard disabled"

        if net_liquidation is not None and net_liquidation >= self.config.min_equity_for_unlimited_daytrade:
            return True, "Net liquidation above PDT threshold"

        # Prefer broker-reported remaining trades when available.
        if day_trades_remaining is not None:
            if day_trades_remaining <= 0:
                return False, "Blocked by broker PDT day-trades remaining"
            return True, "Broker day-trades remaining available"

        # Fallback to local conservative limit for <25k accounts.
        if self._local_day_trades_used >= self.config.max_day_trades_under_25k:
            return False, "Blocked by local PDT fallback limit"

        return True, "Local PDT fallback allows trading"
