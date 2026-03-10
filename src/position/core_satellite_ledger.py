"""
Core/satellite position ledger for intraday T+0 operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict


@dataclass
class SymbolLedger:
    symbol: str
    core_qty: int
    working_qty: int
    satellite_max_pct_of_core: float
    realized_pnl: float = 0.0
    avg_cost_working: float = 0.0
    updated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def max_satellite_qty(self) -> int:
        base = max(0, self.core_qty)
        return int(base * self.satellite_max_pct_of_core)

    @property
    def satellite_qty(self) -> int:
        return self.working_qty - self.core_qty

    def can_buy_satellite(self) -> bool:
        return self.satellite_qty < self.max_satellite_qty

    def can_sell_satellite(self) -> bool:
        # Allow reducing from current working down to core - max_satellite.
        return self.satellite_qty > -self.max_satellite_qty and self.working_qty > 0

    def apply_fill(self, action: str, qty: int, price: float) -> None:
        qty = int(max(0, qty))
        if qty == 0:
            return
        action = action.upper()
        self.updated_at = datetime.utcnow()

        if action == "BUY":
            new_qty = self.working_qty + qty
            if self.working_qty <= 0:
                self.avg_cost_working = price
            else:
                total_cost = self.avg_cost_working * self.working_qty + price * qty
                self.avg_cost_working = total_cost / max(1, new_qty)
            self.working_qty = new_qty
            return

        if action == "SELL":
            if self.working_qty <= 0:
                return
            close_qty = min(qty, self.working_qty)
            pnl = (price - self.avg_cost_working) * close_qty
            self.realized_pnl += pnl
            self.working_qty -= close_qty
            if self.working_qty == 0:
                self.avg_cost_working = 0.0


class CoreSatelliteLedger:
    def __init__(self, satellite_max_pct_of_core: float = 0.20):
        self.satellite_max_pct_of_core = satellite_max_pct_of_core
        self._symbols: Dict[str, SymbolLedger] = {}

    def initialize_symbol(self, symbol: str, core_qty: int, avg_cost: float = 0.0) -> None:
        core_qty = int(max(0, core_qty))
        self._symbols[symbol] = SymbolLedger(
            symbol=symbol,
            core_qty=core_qty,
            working_qty=core_qty,
            satellite_max_pct_of_core=self.satellite_max_pct_of_core,
            avg_cost_working=float(max(0.0, avg_cost)),
        )

    def has_symbol(self, symbol: str) -> bool:
        return symbol in self._symbols

    def get(self, symbol: str) -> SymbolLedger:
        return self._symbols[symbol]

    def all_symbols(self) -> Dict[str, SymbolLedger]:
        return self._symbols

    def update_from_broker_qty(self, symbol: str, qty: int) -> None:
        if symbol not in self._symbols:
            return
        self._symbols[symbol].working_qty = int(qty)
        self._symbols[symbol].updated_at = datetime.utcnow()

    def apply_fill(self, symbol: str, action: str, qty: int, price: float) -> None:
        if symbol not in self._symbols:
            return
        self._symbols[symbol].apply_fill(action=action, qty=qty, price=price)

    def required_rebalance_qty(self, symbol: str) -> int:
        """
        Positive -> need BUY to restore core.
        Negative -> need SELL to restore core.
        """
        info = self._symbols[symbol]
        return info.core_qty - info.working_qty

    def total_realized_pnl(self) -> float:
        return sum(s.realized_pnl for s in self._symbols.values())
