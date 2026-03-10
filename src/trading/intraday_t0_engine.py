"""
Intraday T+0 MVP engine for core-position trading on IBKR paper account.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import yaml
from loguru import logger

from src.data.connection_manager import ConnectionConfig, TradingMode
from src.data.ibkr_async_client import IBKRAsyncClient
from src.execution.async_order_executor import AsyncOrderExecutor, OrderResult
from src.position.core_satellite_ledger import CoreSatelliteLedger
from src.signals.intraday_signals import (
    IntradayAction,
    IntradaySignalConfig,
    IntradaySignalEngine,
)
from src.trading.day_trade_guard import DayTradeGuard, DayTradeGuardConfig


@dataclass
class RuntimeConfig:
    timezone: str = "US/Eastern"
    loop_interval_seconds: int = 5
    signal_cooldown_seconds: int = 60
    force_flatten_time: str = "15:55"
    output_base_dir: str = "outputs"


@dataclass
class ExecutionConfig:
    order_type: str = "limit"
    limit_offset_bps: float = 3.0
    order_timeout_seconds: int = 20


@dataclass
class ExecutionCommissionConfig:
    commission_per_share: float = 0.005
    commission_min_per_trade: float = 1.00
    commission_max_pct: float = 0.01  # 1% of trade value cap


@dataclass
class ReplayConfig:
    """One-shot model controls (from V3 tuning)."""
    min_bars_between_trades: int = 6
    min_hold_bars: int = 5
    max_trades_per_day: int = 6
    min_vwap_distance_bps: float = 50.0
    max_entry_legs: int = 1  # one-shot: single entry per cycle


@dataclass
class RiskConfig:
    core_target_pct: float = 0.80
    satellite_max_pct_of_core: float = 0.75  # V3 tuned (was 0.20)
    risk_per_trade_pct: float = 0.03          # V3 tuned (was 0.01)
    daily_loss_limit_pct: float = 0.02
    max_notional_per_trade_pct: float = 0.25  # V3 tuned (was 0.08)
    min_trade_shares: int = 1


@dataclass
class ReportingConfig:
    write_signals_csv: bool = True
    write_trades_csv: bool = True
    write_metrics_csv: bool = True
    write_daily_report: bool = True


@dataclass
class SessionWindow:
    start: str
    end: str

    def contains(self, now_t: time) -> bool:
        start_t = datetime.strptime(self.start, "%H:%M").time()
        end_t = datetime.strptime(self.end, "%H:%M").time()
        return start_t <= now_t < end_t


@dataclass
class IntradayT0Config:
    ib_gateway: Dict[str, Any] = field(default_factory=dict)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    watchlist_symbols: List[str] = field(default_factory=list)
    reduce_only_windows: List[SessionWindow] = field(default_factory=list)
    main_windows: List[SessionWindow] = field(default_factory=list)
    midday_half_windows: List[SessionWindow] = field(default_factory=list)
    signal: IntradaySignalConfig = field(default_factory=IntradaySignalConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    commission: ExecutionCommissionConfig = field(default_factory=ExecutionCommissionConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    pdt: DayTradeGuardConfig = field(default_factory=DayTradeGuardConfig)
    reporting: ReportingConfig = field(default_factory=ReportingConfig)
    replay: ReplayConfig = field(default_factory=ReplayConfig)


class IntradayT0Engine:
    def __init__(self, config: IntradayT0Config):
        self.config = config
        self.tz = ZoneInfo(config.runtime.timezone)

        self.client: Optional[IBKRAsyncClient] = None
        self.executor: Optional[AsyncOrderExecutor] = None
        self.signal_engine = IntradaySignalEngine(config.signal)
        self.ledger = CoreSatelliteLedger(config.risk.satellite_max_pct_of_core)
        self.day_trade_guard = DayTradeGuard(config.pdt)

        self.running = False
        self.halted = False
        self.flattened = False
        self.account_status: Dict[str, Any] = {}
        self.start_equity: float = 100000.0
        self._last_account_refresh: Optional[datetime] = None

        self.raw_5s: Dict[str, List[Dict[str, Any]]] = {s: [] for s in config.watchlist_symbols}
        self.bars_1m: Dict[str, List[Dict[str, Any]]] = {s: [] for s in config.watchlist_symbols}
        self.last_signal_time: Dict[str, datetime] = {}

        self.signal_rows: List[Dict[str, Any]] = []
        self.trade_rows: List[Dict[str, Any]] = []
        self.risk_rows: List[Dict[str, Any]] = []

        # One-shot cycle tracking: {(date_str, symbol, "long"|"short"): True}
        self._cycles_done: Dict[tuple, bool] = {}
        # Track satellite state before exit for cycle direction detection
        self._sat_before_trade: Dict[str, int] = {}

    @staticmethod
    def load_from_yaml(config_path: str) -> IntradayT0Config:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        runtime = RuntimeConfig(**(raw.get("runtime", {})))
        # Filter execution config to only fields ExecutionConfig accepts
        exec_fields = {k: v for k, v in raw.get("execution", {}).items()
                       if k in ("order_type", "limit_offset_bps", "order_timeout_seconds")}
        execution = ExecutionConfig(**exec_fields)
        risk = RiskConfig(**(raw.get("risk", {})))
        pdt = DayTradeGuardConfig(**(raw.get("pdt", {})))
        reporting = ReportingConfig(**(raw.get("reporting", {})))

        # Commission config (IBKR Fixed rate)
        exec_raw = raw.get("execution", {})
        commission = ExecutionCommissionConfig(
            commission_per_share=float(exec_raw.get("commission_per_share", 0.005)),
            commission_min_per_trade=float(exec_raw.get("commission_min_per_trade", 1.00)),
            commission_max_pct=float(exec_raw.get("commission_max_pct", 0.01)),
        )

        # Replay / one-shot config
        replay_raw = raw.get("replay", {})
        replay = ReplayConfig(
            min_bars_between_trades=int(replay_raw.get("min_bars_between_trades", 6)),
            min_hold_bars=int(replay_raw.get("min_hold_bars", 5)),
            max_trades_per_day=int(replay_raw.get("max_trades_per_day", 6)),
            min_vwap_distance_bps=float(replay_raw.get("min_vwap_distance_bps", 50.0)),
            max_entry_legs=int(replay_raw.get("max_entry_legs", 1)),
        )

        strategy_raw = raw.get("strategy", {})
        signal = IntradaySignalConfig(
            min_bars_for_signal=int(strategy_raw.get("min_bars_for_signal", 30)),
            zscore_entry_buy=float(strategy_raw.get("zscore_entry_buy", -2.0)),
            zscore_entry_sell=float(strategy_raw.get("zscore_entry_sell", 2.0)),
            zscore_exit_to_mean=float(strategy_raw.get("zscore_exit_to_mean", 0.5)),
            rolling_std_window=int(strategy_raw.get("rolling_std_window", 20)),
            atr_window=int(strategy_raw.get("atr_window", 14)),
            atr_stop_multiplier=float(strategy_raw.get("atr_stop_multiplier", 1.2)),
        )

        windows = raw.get("session_windows", {})
        reduce_only = [SessionWindow(**w) for w in windows.get("reduce_only", [])]
        main = [SessionWindow(**w) for w in windows.get("main", [])]
        midday = [SessionWindow(**w) for w in windows.get("midday_half_size", [])]

        watchlist = raw.get("watchlist", {}).get("symbols", []) or []

        return IntradayT0Config(
            ib_gateway=raw.get("ib_gateway", {}),
            runtime=runtime,
            watchlist_symbols=watchlist,
            reduce_only_windows=reduce_only,
            main_windows=main,
            midday_half_windows=midday,
            signal=signal,
            execution=execution,
            commission=commission,
            risk=risk,
            pdt=pdt,
            reporting=reporting,
            replay=replay,
        )

    async def setup(self) -> None:
        ib = self.config.ib_gateway
        conn_cfg = ConnectionConfig(
            host=str(ib.get("host", "127.0.0.1")),
            port=int(ib.get("port", 4002)),
            client_id=int(ib.get("client_id", 21)),
            readonly=bool(ib.get("readonly", False)),
            trading_mode=TradingMode.PAPER,
            reconnect_delay=float(ib.get("reconnect_delay", 5)),
            max_reconnect_attempts=int(ib.get("max_reconnect_attempts", 10)),
            heartbeat_interval=float(ib.get("heartbeat_interval", 30)),
            connection_timeout=float(ib.get("connection_timeout", 30)),
            requests_per_second=int(ib.get("requests_per_second", 45)),
        )

        self.client = IBKRAsyncClient(conn_cfg)
        if not await self.client.connect():
            raise RuntimeError("Failed to connect to IB Gateway")

        self.executor = AsyncOrderExecutor(client=self.client)
        await self._refresh_account_status(force=True)
        await self._bootstrap_core_positions()
        await self._subscribe_realtime()

    async def _bootstrap_core_positions(self) -> None:
        assert self.client is not None
        positions = await self.client.get_positions()
        pos_map = {p["symbol"]: p for p in positions}

        for sym in self.config.watchlist_symbols:
            p = pos_map.get(sym)
            core_qty = int(p["quantity"]) if p else 0
            avg_cost = float(p["avg_cost"]) if p else 0.0
            self.ledger.initialize_symbol(sym, core_qty=core_qty, avg_cost=avg_cost)
            logger.info(f"Init {sym}: core={core_qty}, avg_cost={avg_cost:.2f}")

    async def _subscribe_realtime(self) -> None:
        assert self.client is not None
        for sym in self.config.watchlist_symbols:
            ok = await self.client.subscribe_bars(sym, lambda *args, _sym=sym: self._on_realtime_bar(_sym, *args))
            if not ok:
                logger.warning(f"Failed to subscribe {sym}")

    def _extract_bar(self, *args) -> Optional[Any]:
        if not args:
            return None
        first = args[0]
        if hasattr(first, "close") and hasattr(first, "time"):
            return first
        if hasattr(first, "__iter__"):
            try:
                bars = list(first)
                if bars:
                    return bars[-1]
            except Exception:
                return None
        if len(args) >= 2 and hasattr(args[1], "close"):
            return args[1]
        return None

    def _to_et(self, dt_obj: datetime) -> datetime:
        if dt_obj.tzinfo is None:
            # IB data can come tz-naive depending on runtime; assume UTC if naive.
            dt_obj = dt_obj.replace(tzinfo=ZoneInfo("UTC"))
        return dt_obj.astimezone(self.tz)

    def _on_realtime_bar(self, symbol: str, *args) -> None:
        bar = self._extract_bar(*args)
        if bar is None:
            return
        try:
            ts = self._to_et(getattr(bar, "time"))
            self.raw_5s[symbol].append(
                {
                    "datetime": ts,
                    "open": float(getattr(bar, "open")),
                    "high": float(getattr(bar, "high")),
                    "low": float(getattr(bar, "low")),
                    "close": float(getattr(bar, "close")),
                    "volume": float(getattr(bar, "volume", 0.0)),
                }
            )
        except Exception as exc:
            logger.debug(f"Bar parse failed for {symbol}: {exc}")

    def _flush_completed_minutes(self, now_et: datetime) -> None:
        current_minute = now_et.replace(second=0, microsecond=0)
        for sym, points in self.raw_5s.items():
            if not points:
                continue
            finished = [p for p in points if p["datetime"].replace(second=0, microsecond=0) < current_minute]
            if not finished:
                continue
            self.raw_5s[sym] = [p for p in points if p not in finished]

            df = pd.DataFrame(finished)
            df["bucket"] = df["datetime"].dt.floor("min")
            for bucket, g in df.groupby("bucket"):
                self.bars_1m[sym].append(
                    {
                        "datetime": bucket,
                        "open": float(g.iloc[0]["open"]),
                        "high": float(g["high"].max()),
                        "low": float(g["low"].min()),
                        "close": float(g.iloc[-1]["close"]),
                        "volume": float(g["volume"].sum()),
                    }
                )
            # keep memory bounded for intraday session
            if len(self.bars_1m[sym]) > 1200:
                self.bars_1m[sym] = self.bars_1m[sym][-1200:]

    def _phase(self, now_et: datetime) -> str:
        now_t = now_et.time()
        for w in self.config.reduce_only_windows:
            if w.contains(now_t):
                return "reduce_only"
        for w in self.config.main_windows:
            if w.contains(now_t):
                return "main"
        for w in self.config.midday_half_windows:
            if w.contains(now_t):
                return "midday_half"
        return "closed"

    def _is_force_flatten_time(self, now_et: datetime) -> bool:
        force_t = datetime.strptime(self.config.runtime.force_flatten_time, "%H:%M").time()
        return now_et.time() >= force_t

    async def _refresh_account_status(self, force: bool = False) -> None:
        assert self.client is not None
        now = datetime.now(self.tz)
        if not force and self._last_account_refresh and (now - self._last_account_refresh).total_seconds() < 30:
            return
        self._last_account_refresh = now

        # fetch_account_status is sync in connection manager.
        status = await asyncio.to_thread(self.client.conn_manager.fetch_account_status)
        if status is None:
            return
        self.account_status = status.to_dict()
        net_liq = self.account_status.get("net_liquidation")
        if isinstance(net_liq, (float, int)) and net_liq > 0:
            self.start_equity = float(net_liq)

    def _bars_df(self, symbol: str) -> pd.DataFrame:
        rows = self.bars_1m.get(symbol, [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df = df.sort_values("datetime")
        df = df.drop_duplicates(subset=["datetime"], keep="last")
        df = df.set_index("datetime")
        return df[["open", "high", "low", "close", "volume"]]

    def _phase_size_multiplier(self, phase: str) -> float:
        if phase == "midday_half":
            return 0.5
        return 1.0

    def _compute_order_qty(self, symbol: str, action: IntradayAction, price: float, atr: float, phase: str) -> int:
        info = self.ledger.get(symbol)
        eq = float(self.account_status.get("net_liquidation") or self.start_equity or 100000.0)
        risk_budget = eq * self.config.risk.risk_per_trade_pct * self._phase_size_multiplier(phase)
        stop_dist = max(atr * self.config.signal.atr_stop_multiplier, price * 0.001)
        qty_risk = int(risk_budget / max(0.01, stop_dist))
        qty_notional_cap = int((eq * self.config.risk.max_notional_per_trade_pct) / max(0.01, price))
        qty = max(self.config.risk.min_trade_shares, min(qty_risk, qty_notional_cap))

        if action == IntradayAction.BUY_SATELLITE:
            room = info.max_satellite_qty - info.satellite_qty
            qty = min(qty, max(0, room))
        elif action == IntradayAction.SELL_SATELLITE:
            room = info.max_satellite_qty + info.satellite_qty
            qty = min(qty, max(0, room), info.working_qty)

        return max(0, int(qty))

    async def _execute_action(
        self,
        symbol: str,
        action: IntradayAction,
        qty: int,
        trigger_price: float,
        reason: str,
        zscore: float,
    ) -> Optional[OrderResult]:
        if qty <= 0:
            return None
        assert self.executor is not None
        assert self.client is not None

        side = "BUY" if action == IntradayAction.BUY_SATELLITE else "SELL"
        offset = self.config.execution.limit_offset_bps / 10000.0
        if self.config.execution.order_type.lower() == "limit":
            limit_px = trigger_price * (1 + offset if side == "BUY" else 1 - offset)
            result = await self.executor.execute_limit_order(
                symbol=symbol,
                quantity=qty,
                action=side,
                limit_price=round(limit_px, 2),
                wait_for_fill=True,
                timeout=self.config.execution.order_timeout_seconds,
            )
        else:
            result = await self.executor.execute_market_order(
                symbol=symbol,
                quantity=qty,
                action=side,
                timeout=self.config.execution.order_timeout_seconds,
            )

        self.signal_rows.append(
            {
                "timestamp": datetime.now(self.tz).strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": symbol,
                "signal_type": action.value,
                "score": abs(zscore),
                "side": side,
                "trigger_price": trigger_price,
                "reasoning": reason,
                "status": "executed" if result.success else "rejected_execution",
            }
        )

        if not result.success:
            return result

        fill_qty = int(result.filled_quantity or qty)
        fill_px = float(result.filled_price or trigger_price)
        before = self.ledger.get(symbol).realized_pnl
        self.ledger.apply_fill(symbol=symbol, action=side, qty=fill_qty, price=fill_px)
        after = self.ledger.get(symbol).realized_pnl
        pnl_delta = after - before

        # IBKR Fixed rate commission: max($1.00, qty × $0.005), capped at 1% of trade value
        cc = self.config.commission
        gross_notional = fill_qty * fill_px
        commission = max(
            cc.commission_min_per_trade,
            min(fill_qty * cc.commission_per_share, gross_notional * cc.commission_max_pct),
        )

        ts_now = datetime.now(self.tz).strftime("%Y-%m-%d %H:%M:%S")
        self.trade_rows.append(
            {
                "symbol": symbol,
                "side": "long",
                "action": side,
                "entry_time": ts_now,
                "exit_time": ts_now,
                "entry_price": fill_px,
                "exit_price": fill_px,
                "quantity": fill_qty,
                "trigger_line": "session_vwap",
                "trigger_level": trigger_price,
                "tp1_price": "",
                "return_pct": 0.0,
                "stop_reason": reason,
                "realized_pnl": pnl_delta,
                "commission": round(commission, 2),
                "zscore": zscore,
            }
        )

        self.day_trade_guard.mark_day_trade()
        return result

    def _record_risk(self, level: str, category: str, message: str, value: float, threshold: float) -> None:
        self.risk_rows.append(
            {
                "timestamp": datetime.now(self.tz).isoformat(),
                "level": level,
                "category": category,
                "message": message,
                "value": value,
                "threshold": threshold,
            }
        )
        logger.warning(f"[{level}] {category}: {message}")

    def _check_daily_loss_halt(self) -> None:
        eq = float(self.account_status.get("net_liquidation") or self.start_equity or 100000.0)
        max_loss = eq * self.config.risk.daily_loss_limit_pct
        pnl = self.ledger.total_realized_pnl()
        if pnl <= -max_loss and not self.halted:
            self.halted = True
            self._record_risk(
                level="CRITICAL",
                category="daily_loss_limit",
                message="Trading halted by intraday daily loss limit",
                value=pnl,
                threshold=-max_loss,
            )

    def _is_exit_signal(self, reason: str) -> bool:
        """Check if this is a mean-reversion exit (closing satellite)."""
        exit_keywords = ("mean reversion exit", "buyback mean reversion", "rebalance")
        return any(kw in reason.lower() for kw in exit_keywords)

    def _cycle_key(self, symbol: str, direction: str, now_et: datetime) -> tuple:
        """Key for one-shot cycle tracking: (date, symbol, 'long'|'short')."""
        return (now_et.strftime("%Y-%m-%d"), symbol, direction)

    async def _process_symbol(self, symbol: str, now_et: datetime) -> None:
        if not self.ledger.has_symbol(symbol):
            return
        info = self.ledger.get(symbol)
        if info.core_qty <= 0:
            return

        phase = self._phase(now_et)
        if phase == "closed":
            return

        bars = self._bars_df(symbol)
        if bars.empty:
            return

        # --- One-shot cycle guard: block new entries if cycle already done today ---
        date_str = now_et.strftime("%Y-%m-%d")
        long_done = self._cycles_done.get((date_str, symbol, "long"), False)
        short_done = self._cycles_done.get((date_str, symbol, "short"), False)

        # Determine effective can_add / can_reduce with cycle guard
        can_add = info.can_buy_satellite() and phase in ("main", "midday_half")
        can_reduce = info.can_sell_satellite() and phase in ("reduce_only", "main", "midday_half")

        # Block new long entry if long cycle already completed today
        if long_done and info.satellite_qty <= 0:
            can_add = False
        # Block new short entry if short cycle already completed today
        if short_done and info.satellite_qty >= 0:
            can_reduce = False

        decision = self.signal_engine.generate(
            symbol=symbol,
            bars_1m=bars,
            satellite_qty=info.satellite_qty,
            max_satellite_qty=info.max_satellite_qty,
            can_add=can_add,
            can_reduce=can_reduce,
        )
        if decision is None or decision.action == IntradayAction.NONE:
            return

        last_ts = self.last_signal_time.get(symbol)
        if last_ts and (now_et - last_ts).total_seconds() < self.config.runtime.signal_cooldown_seconds:
            self.signal_rows.append(
                {
                    "timestamp": now_et.strftime("%Y-%m-%d %H:%M:%S"),
                    "symbol": symbol,
                    "signal_type": decision.action.value,
                    "score": decision.score,
                    "side": "BUY" if decision.action == IntradayAction.BUY_SATELLITE else "SELL",
                    "trigger_price": decision.trigger_price,
                    "reasoning": decision.reason,
                    "status": "rejected_cooldown",
                }
            )
            return

        self.last_signal_time[symbol] = now_et

        can_trade, pdt_reason = self.day_trade_guard.can_trade(
            net_liquidation=self.account_status.get("net_liquidation"),
            day_trades_remaining=self.account_status.get("day_trades_remaining"),
        )
        if not can_trade:
            self.signal_rows.append(
                {
                    "timestamp": now_et.strftime("%Y-%m-%d %H:%M:%S"),
                    "symbol": symbol,
                    "signal_type": decision.action.value,
                    "score": decision.score,
                    "side": "BUY" if decision.action == IntradayAction.BUY_SATELLITE else "SELL",
                    "trigger_price": decision.trigger_price,
                    "reasoning": f"{decision.reason}; {pdt_reason}",
                    "status": "rejected_risk",
                }
            )
            self._record_risk("WARNING", "pdt_guard", pdt_reason, 0, 0)
            return

        if self.halted:
            self.signal_rows.append(
                {
                    "timestamp": now_et.strftime("%Y-%m-%d %H:%M:%S"),
                    "symbol": symbol,
                    "signal_type": decision.action.value,
                    "score": decision.score,
                    "side": "BUY" if decision.action == IntradayAction.BUY_SATELLITE else "SELL",
                    "trigger_price": decision.trigger_price,
                    "reasoning": "Trading halted by risk manager",
                    "status": "rejected_halted",
                }
            )
            return

        # --- One-shot exit: close FULL satellite position at once ---
        is_exit = self._is_exit_signal(decision.reason)
        if is_exit and info.satellite_qty != 0:
            qty = abs(info.satellite_qty)
        else:
            qty = self._compute_order_qty(
                symbol=symbol,
                action=decision.action,
                price=decision.trigger_price,
                atr=decision.atr,
                phase=phase,
            )

        if qty <= 0:
            self.signal_rows.append(
                {
                    "timestamp": now_et.strftime("%Y-%m-%d %H:%M:%S"),
                    "symbol": symbol,
                    "signal_type": decision.action.value,
                    "score": decision.score,
                    "side": "BUY" if decision.action == IntradayAction.BUY_SATELLITE else "SELL",
                    "trigger_price": decision.trigger_price,
                    "reasoning": f"{decision.reason}; qty=0 after limits",
                    "status": "rejected_sizing",
                }
            )
            return

        # Track satellite state before trade for cycle detection
        self._sat_before_trade[symbol] = info.satellite_qty

        await self._execute_action(
            symbol=symbol,
            action=decision.action,
            qty=qty,
            trigger_price=decision.trigger_price,
            reason=decision.reason,
            zscore=decision.zscore,
        )

        # --- One-shot cycle tracking: mark cycle done when satellite returns to 0 ---
        info_after = self.ledger.get(symbol)
        sat_before = self._sat_before_trade.get(symbol, 0)
        if info_after.satellite_qty == 0 and sat_before != 0:
            direction = "long" if sat_before > 0 else "short"
            self._cycles_done[(date_str, symbol, direction)] = True
            logger.info(f"Cycle complete: {symbol} {direction} cycle done for {date_str}")

        self._check_daily_loss_halt()

    async def _force_flatten_symbol(self, symbol: str, reason: str) -> None:
        rebalance = self.ledger.required_rebalance_qty(symbol)
        if rebalance == 0:
            return
        action = IntradayAction.BUY_SATELLITE if rebalance > 0 else IntradayAction.SELL_SATELLITE
        qty = abs(rebalance)
        bars = self._bars_df(symbol)
        if bars.empty:
            return
        px = float(bars.iloc[-1]["close"])
        await self._execute_action(
            symbol=symbol,
            action=action,
            qty=qty,
            trigger_price=px,
            reason=reason,
            zscore=0.0,
        )

    async def force_flatten_all(self, reason: str = "eod_square_off") -> None:
        for sym in self.config.watchlist_symbols:
            await self._force_flatten_symbol(sym, reason=reason)
        self.flattened = True

    async def run(self) -> None:
        self.running = True
        logger.info("Intraday T+0 engine started")
        try:
            while self.running:
                now_et = datetime.now(self.tz)
                self._flush_completed_minutes(now_et)
                await self._refresh_account_status(force=False)

                if self._is_force_flatten_time(now_et) and not self.flattened:
                    await self.force_flatten_all(reason="eod_square_off")

                for sym in self.config.watchlist_symbols:
                    await self._process_symbol(sym, now_et)

                # After regular market close and flattened, stop loop.
                if now_et.time() >= time(16, 1) and self.flattened:
                    break

                await asyncio.sleep(self.config.runtime.loop_interval_seconds)
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        if self.client:
            await self.client.unsubscribe_all()
        if self.executor:
            await self.executor.cleanup()
        if self.client:
            await self.client.disconnect()
        self.running = False
        await self.write_outputs()
        logger.info("Intraday T+0 engine shutdown complete")

    def _output_dir(self) -> Path:
        date_tag = datetime.now(self.tz).strftime("%Y%m%d")
        out = Path(self.config.runtime.output_base_dir) / f"intraday_t0_{date_tag}"
        out.mkdir(parents=True, exist_ok=True)
        return out

    @staticmethod
    def _profit_factor(pnls: List[float]) -> float:
        gp = sum(x for x in pnls if x > 0)
        gl = -sum(x for x in pnls if x < 0)
        if gl == 0:
            return 0.0 if gp == 0 else float("inf")
        return gp / gl

    def _build_metrics(self) -> pd.DataFrame:
        trades = pd.DataFrame(self.trade_rows)
        eq = float(self.account_status.get("net_liquidation") or self.start_equity or 100000.0)
        pnl = float(self.ledger.total_realized_pnl())
        total_return = pnl / max(eq, 1.0) * 100.0

        rows: List[Dict[str, Any]] = []
        if trades.empty:
            rows.append(
                {
                    "level": "overall",
                    "key": "portfolio",
                    "side": "all",
                    "trade_count": 0,
                    "win_rate": 0.0,
                    "profit_factor": 0.0,
                    "total_return": total_return,
                    "cagr": 0.0,
                    "sharpe": 0.0,
                    "max_dd": 0.0,
                }
            )
            return pd.DataFrame(rows)

        by_symbol = trades.groupby("symbol")
        for sym, g in by_symbol:
            pnls = g["realized_pnl"].fillna(0.0).tolist()
            wins = [x for x in pnls if x > 0]
            rows.append(
                {
                    "level": "symbol",
                    "key": sym,
                    "side": "long",
                    "trade_count": int(len(g)),
                    "win_rate": float(len(wins) / len(g) * 100.0) if len(g) else 0.0,
                    "profit_factor": self._profit_factor(pnls),
                    "total_return": float(sum(pnls) / max(eq, 1.0) * 100.0),
                    "cagr": 0.0,
                    "sharpe": 0.0,
                    "max_dd": 0.0,
                }
            )

        all_pnls = trades["realized_pnl"].fillna(0.0).tolist()
        rows.append(
            {
                "level": "overall",
                "key": "portfolio",
                "side": "all",
                "trade_count": int(len(trades)),
                "win_rate": float((trades["realized_pnl"] > 0).mean() * 100.0),
                "profit_factor": self._profit_factor(all_pnls),
                "total_return": total_return,
                "cagr": 0.0,
                "sharpe": 0.0,
                "max_dd": 0.0,
            }
        )
        return pd.DataFrame(rows)

    async def write_outputs(self) -> None:
        out = self._output_dir()
        signals_df = pd.DataFrame(self.signal_rows)
        trades_df = pd.DataFrame(self.trade_rows)
        metrics_df = self._build_metrics()
        risk_df = pd.DataFrame(self.risk_rows)

        if self.config.reporting.write_signals_csv:
            signals_df.to_csv(out / "signals.csv", index=False)
        if self.config.reporting.write_trades_csv:
            trades_df.to_csv(out / "trades.csv", index=False)
        if self.config.reporting.write_metrics_csv:
            metrics_df.to_csv(out / "metrics_summary.csv", index=False)
        if not risk_df.empty:
            risk_df.to_json(out / "risk_log.json", orient="records", indent=2)

        if self.config.reporting.write_daily_report:
            total_pnl = self.ledger.total_realized_pnl()
            report_lines = [
                f"# Intraday T+0 Trading Report - {datetime.now(self.tz).strftime('%Y-%m-%d')}",
                "",
                "## Session Summary",
                f"- Total signals: {len(signals_df)}",
                f"- Trades executed: {len(trades_df)}",
                f"- Win rate: {((trades_df['realized_pnl'] > 0).mean() * 100.0) if not trades_df.empty else 0.0:.2f}%",
                f"- Total realized P&L: ${total_pnl:,.2f}",
                f"- Risk events: {len(risk_df)}",
                "",
                "## Performance Metrics",
                "- See metrics_summary.csv",
                "",
                "## Risk Notes",
            ]
            if risk_df.empty:
                report_lines.append("- No risk-limit breach recorded.")
            else:
                for _, row in risk_df.tail(5).iterrows():
                    report_lines.append(f"- [{row['level']}] {row['category']}: {row['message']}")
            report_lines.extend(
                [
                    "",
                    "## Next Session Adjustments",
                    "- Validate z-score thresholds against live slippage profile.",
                    "- Review midday_half sizing impact on fill quality.",
                    "- Recheck PDT guard behavior with broker-reported counters.",
                ]
            )
            (out / "daily_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
