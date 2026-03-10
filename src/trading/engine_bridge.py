"""
Thread-safe bridge between the async IntradayT0Engine and the sync Flask dashboard.

The engine runs in a background thread with its own asyncio event loop.
The Flask dashboard reads state via thread-safe snapshots.
"""

from __future__ import annotations

import asyncio
import copy
import threading
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Deque, Dict, List, Optional

from loguru import logger


@dataclass
class PositionSnapshot:
    symbol: str = ""
    core_qty: int = 0
    satellite_qty: int = 0
    working_qty: int = 0
    avg_cost: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    last_price: float = 0.0


@dataclass
class EngineState:
    """Thread-safe snapshot of engine state for the dashboard."""
    status: str = "stopped"   # stopped | starting | running | halted | flattened | error
    error: Optional[str] = None
    started_at: Optional[str] = None
    watchlist: List[str] = field(default_factory=list)

    # Account
    equity: float = 0.0
    cash: float = 0.0
    buying_power: float = 0.0
    daily_pnl: float = 0.0
    total_commission: float = 0.0

    # Positions
    positions: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Signals (most recent first)
    signals: List[Dict[str, Any]] = field(default_factory=list)

    # Trades (most recent first)
    trades: List[Dict[str, Any]] = field(default_factory=list)

    # Metrics
    trade_count: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_realized_pnl: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at,
            "watchlist": self.watchlist,
            "equity": self.equity,
            "cash": self.cash,
            "buying_power": self.buying_power,
            "daily_pnl": self.daily_pnl,
            "total_commission": self.total_commission,
            "positions": self.positions,
            "signals": self.signals[:50],   # last 50
            "trades": self.trades[:100],    # last 100
            "trade_count": self.trade_count,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "total_realized_pnl": self.total_realized_pnl,
        }


class EngineBridge:
    """
    Runs IntradayT0Engine in a background thread and exposes its state
    to the Flask dashboard via thread-safe reads.
    """

    def __init__(self):
        self._state = EngineState()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._engine = None  # IntradayT0Engine instance
        self._stop_event: Optional[asyncio.Event] = None

    def get_state(self) -> Dict[str, Any]:
        """Thread-safe: return current engine state as dict."""
        with self._lock:
            return copy.deepcopy(self._state.to_dict())

    def _update_state(self, **kwargs) -> None:
        """Thread-safe: update state fields."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._state, key):
                    setattr(self._state, key, value)

    def is_running(self) -> bool:
        with self._lock:
            return self._state.status in ("running", "starting")

    def start(self, config_path: str = "config/intraday_t0_paper.yaml") -> bool:
        """Launch the engine in a background thread. Returns False if already running."""
        if self.is_running():
            return False

        self._update_state(status="starting", error=None)

        self._thread = threading.Thread(
            target=self._run_engine_thread,
            args=(config_path,),
            daemon=True,
            name="t0-engine",
        )
        self._thread.start()
        return True

    def stop(self) -> bool:
        """Signal the engine to stop gracefully (flatten + disconnect)."""
        if not self.is_running():
            return False
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        return True

    def force_flatten(self) -> bool:
        """Emergency flatten all positions."""
        if not self.is_running() or self._engine is None:
            return False
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._engine.force_flatten_all(reason="manual_dashboard_flatten"),
                self._loop,
            )
        return True

    def _run_engine_thread(self, config_path: str) -> None:
        """Background thread: create event loop, run engine, update state."""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._run_engine_async(config_path))
        except Exception as exc:
            logger.exception(f"Engine thread crashed: {exc}")
            self._update_state(status="error", error=str(exc))
        finally:
            if self._loop and not self._loop.is_closed():
                self._loop.close()
            self._loop = None
            self._engine = None

    async def _run_engine_async(self, config_path: str) -> None:
        """Async engine lifecycle: setup → run → shutdown."""
        from src.trading.intraday_t0_engine import IntradayT0Engine

        cfg = IntradayT0Engine.load_from_yaml(config_path)
        self._engine = IntradayT0Engine(cfg)
        self._stop_event = asyncio.Event()

        self._update_state(
            watchlist=cfg.watchlist_symbols,
            started_at=datetime.now().isoformat(),
        )

        # Setup: connect to IBKR, load positions, subscribe bars
        try:
            await self._engine.setup()
            self._update_state(status="running")
            logger.info("T0 Paper Trading engine started successfully")
        except Exception as exc:
            self._update_state(status="error", error=f"Setup failed: {exc}")
            logger.exception(f"Engine setup failed: {exc}")
            return

        # Run loop with periodic state sync
        run_task = asyncio.create_task(self._engine.run())
        stop_task = asyncio.create_task(self._stop_event.wait())
        sync_task = asyncio.create_task(self._state_sync_loop())

        done, pending = await asyncio.wait(
            {run_task, stop_task, sync_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cleanup
        for task in pending:
            task.cancel()

        if stop_task in done:
            logger.info("Stop signal received, flattening...")
            await self._engine.force_flatten_all(reason="dashboard_stop")

        await self._engine.shutdown()
        self._sync_state_from_engine()  # final snapshot
        self._update_state(status="stopped")
        logger.info("T0 Paper Trading engine stopped")

    async def _state_sync_loop(self) -> None:
        """Periodically sync engine internals to the shared state."""
        while True:
            try:
                self._sync_state_from_engine()
            except Exception as exc:
                logger.debug(f"State sync error: {exc}")
            await asyncio.sleep(2)  # sync every 2 seconds

    def _sync_state_from_engine(self) -> None:
        """Read engine internals and update shared state (called from engine thread)."""
        engine = self._engine
        if engine is None:
            return

        # Account
        acct = engine.account_status or {}
        equity = float(acct.get("net_liquidation", engine.start_equity or 0))
        cash = float(acct.get("total_cash_value", 0))
        bp = float(acct.get("buying_power", 0))

        # Positions from ledger
        positions = {}
        for sym in engine.config.watchlist_symbols:
            if engine.ledger.has_symbol(sym):
                info = engine.ledger.get(sym)
                # Get last price from bars
                bars = engine.bars_1m.get(sym, [])
                last_price = float(bars[-1]["close"]) if bars else info.avg_cost
                unr_pnl = (last_price - info.avg_cost) * info.working_qty if info.avg_cost > 0 else 0

                positions[sym] = {
                    "core_qty": info.core_qty,
                    "satellite_qty": info.satellite_qty,
                    "working_qty": info.working_qty,
                    "avg_cost": round(info.avg_cost, 2),
                    "realized_pnl": round(info.realized_pnl, 2),
                    "unrealized_pnl": round(unr_pnl, 2),
                    "last_price": round(last_price, 2),
                }

        # Signals
        signals = list(reversed(engine.signal_rows[-50:])) if engine.signal_rows else []

        # Trades
        trades = list(reversed(engine.trade_rows[-100:])) if engine.trade_rows else []

        # Metrics
        trade_count = len(engine.trade_rows)
        total_rpnl = engine.ledger.total_realized_pnl()
        total_comm = sum(
            float(t.get("commission", 0)) for t in engine.trade_rows
        )

        # Win rate and profit factor
        pnls = [float(t.get("realized_pnl", 0)) for t in engine.trade_rows]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        win_rate = (len(wins) / len(pnls) * 100.0) if pnls else 0.0
        gp = sum(wins)
        gl = -sum(losses)
        profit_factor = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)

        daily_pnl = total_rpnl + sum(
            p.get("unrealized_pnl", 0) for p in positions.values()
        )

        # Halted status
        status = "running"
        if engine.halted:
            status = "halted"
        elif engine.flattened:
            status = "flattened"
        elif not engine.running:
            status = "stopped"

        self._update_state(
            status=status,
            equity=round(equity, 2),
            cash=round(cash, 2),
            buying_power=round(bp, 2),
            daily_pnl=round(daily_pnl, 2),
            total_commission=round(total_comm, 2),
            positions=positions,
            signals=signals,
            trades=trades,
            trade_count=trade_count,
            win_rate=round(win_rate, 1),
            profit_factor=round(profit_factor, 2) if profit_factor != float("inf") else 999.0,
            total_realized_pnl=round(total_rpnl, 2),
        )


# Singleton bridge instance for Flask app
engine_bridge = EngineBridge()
