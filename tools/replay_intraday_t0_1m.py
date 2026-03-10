#!/usr/bin/env python3
"""
1-minute replay backtest for intraday core-position T+0 MVP.

Outputs:
- price_signals.png: intraday close + VWAP + buy/sell points
- sizing_timeline.png: working/core/satellite quantity over time
- equity_curve.png: simple equity simulation
- trades.csv / metrics_summary.csv / report.md
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance as yf
import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class ReplayConfig:
    zscore_entry_buy: float
    zscore_entry_sell: float
    zscore_exit_to_mean: float
    rolling_std_window: int
    atr_window: int
    atr_stop_multiplier: float
    satellite_max_pct_of_core: float
    risk_per_trade_pct: float
    max_notional_per_trade_pct: float
    min_trade_shares: int
    timezone: str
    commission_bps: float
    slippage_bps: float
    reduce_only_windows: List[Tuple[str, str]]
    main_windows: List[Tuple[str, str]]
    midday_half_windows: List[Tuple[str, str]]
    min_bars_between_trades: int
    min_hold_bars: int
    max_trades_per_day: int
    min_vwap_distance_bps: float
    min_entry_rvol: float


def _load_replay_config(config_path: str) -> ReplayConfig:
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    strategy = raw.get("strategy", {})
    risk = raw.get("risk", {})
    runtime = raw.get("runtime", {})
    session_windows = raw.get("session_windows", {})
    replay = raw.get("replay", {})

    def _w(name: str) -> List[Tuple[str, str]]:
        vals = session_windows.get(name, []) or []
        return [(str(v["start"]), str(v["end"])) for v in vals if "start" in v and "end" in v]

    return ReplayConfig(
        zscore_entry_buy=float(strategy.get("zscore_entry_buy", -2.0)),
        zscore_entry_sell=float(strategy.get("zscore_entry_sell", 2.0)),
        zscore_exit_to_mean=float(strategy.get("zscore_exit_to_mean", 0.5)),
        rolling_std_window=int(strategy.get("rolling_std_window", 20)),
        atr_window=int(strategy.get("atr_window", 14)),
        atr_stop_multiplier=float(strategy.get("atr_stop_multiplier", 1.2)),
        satellite_max_pct_of_core=float(risk.get("satellite_max_pct_of_core", 0.2)),
        risk_per_trade_pct=float(risk.get("risk_per_trade_pct", 0.01)),
        max_notional_per_trade_pct=float(risk.get("max_notional_per_trade_pct", 0.08)),
        min_trade_shares=int(risk.get("min_trade_shares", 1)),
        timezone=str(runtime.get("timezone", "US/Eastern")),
        commission_bps=float(raw.get("execution", {}).get("commission_bps", 1.0)),
        slippage_bps=float(raw.get("execution", {}).get("slippage_bps", 1.5)),
        reduce_only_windows=_w("reduce_only"),
        main_windows=_w("main"),
        midday_half_windows=_w("midday_half_size"),
        min_bars_between_trades=int(replay.get("min_bars_between_trades", 4)),
        min_hold_bars=int(replay.get("min_hold_bars", 3)),
        max_trades_per_day=int(replay.get("max_trades_per_day", 16)),
        min_vwap_distance_bps=float(replay.get("min_vwap_distance_bps", 35.0)),
        min_entry_rvol=float(replay.get("min_entry_rvol", 0.9)),
    )


def _download_1m(symbol: str, days: int) -> pd.DataFrame:
    # yfinance 1m supports recent period only; cap to 30d.
    days = max(1, min(days, 30))
    period = f"{days}d"
    df = yf.download(
        symbol,
        period=period,
        interval="1m",
        auto_adjust=False,
        progress=False,
    )
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    # yfinance can occasionally return duplicate labels for a field;
    # force a single canonical OHLCV schema.
    def _pick_one(col_name: str) -> pd.Series:
        col = df[col_name]
        if isinstance(col, pd.DataFrame):
            return col.iloc[:, 0]
        return col

    out = pd.DataFrame(index=df.index)
    out["open"] = _pick_one("Open")
    out["high"] = _pick_one("High")
    out["low"] = _pick_one("Low")
    out["close"] = _pick_one("Close")
    out["volume"] = _pick_one("Volume")
    df = out.dropna()
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_convert("US/Eastern")
    return df


def _keep_rth_only(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    mask = [(idx.weekday() <= 4) and (idx.time() >= pd.Timestamp("09:30").time()) and (idx.time() <= pd.Timestamp("16:00").time()) for idx in df.index]
    out = df.loc[mask].copy()
    return out


def _build_intraday_features(df: pd.DataFrame, cfg: ReplayConfig) -> pd.DataFrame:
    out = df.copy()
    session_key = out.index.date
    typical = (out["high"] + out["low"] + out["close"]) / 3.0
    pv = typical * out["volume"]
    out["session_vwap"] = pv.groupby(session_key).cumsum() / out["volume"].groupby(session_key).cumsum().replace(0, np.nan)

    spread = out["close"] - out["session_vwap"]
    out["zscore"] = spread.groupby(session_key).transform(
        lambda s: s / s.rolling(cfg.rolling_std_window, min_periods=cfg.rolling_std_window).std().replace(0, np.nan)
    )

    tr1 = out["high"] - out["low"]
    tr2 = (out["high"] - out["close"].shift(1)).abs()
    tr3 = (out["low"] - out["close"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    out["atr"] = tr.groupby(session_key).transform(
        lambda s: s.rolling(cfg.atr_window, min_periods=cfg.atr_window).mean()
    )
    out["vol_ma20"] = out["volume"].groupby(session_key).transform(
        lambda s: s.rolling(20, min_periods=20).mean()
    )
    out["rvol"] = out["volume"] / out["vol_ma20"].replace(0, np.nan)
    out["vwap_dist_bps"] = ((out["close"] - out["session_vwap"]) / out["session_vwap"].replace(0, np.nan)).abs() * 10000.0
    return out


def _is_rth(ts: pd.Timestamp) -> bool:
    if ts.weekday() > 4:
        return False
    t = ts.time()
    return (t >= pd.Timestamp("09:30").time()) and (t <= pd.Timestamp("16:00").time())


def _force_flatten_time(ts: pd.Timestamp) -> bool:
    return ts.time() >= pd.Timestamp("15:55").time()


def _in_window(ts: pd.Timestamp, win: Tuple[str, str]) -> bool:
    t = ts.time()
    start_t = pd.Timestamp(win[0]).time()
    end_t = pd.Timestamp(win[1]).time()
    return start_t <= t < end_t


def _phase(ts: pd.Timestamp, cfg: ReplayConfig) -> str:
    for w in cfg.reduce_only_windows:
        if _in_window(ts, w):
            return "reduce_only"
    for w in cfg.main_windows:
        if _in_window(ts, w):
            return "main"
    for w in cfg.midday_half_windows:
        if _in_window(ts, w):
            return "midday_half"
    if _is_rth(ts):
        return "rth_other"
    return "closed"


def _calc_qty(
    price: float,
    atr: float,
    equity: float,
    satellite_qty: int,
    core_qty: int,
    action: str,
    cfg: ReplayConfig,
    size_mult: float = 1.0,
) -> int:
    max_sat_qty = int(max(0, core_qty) * cfg.satellite_max_pct_of_core)
    risk_budget = equity * cfg.risk_per_trade_pct
    stop_dist = max(0.01, atr * cfg.atr_stop_multiplier, price * 0.001)
    qty_risk = int(risk_budget / stop_dist)
    qty_notional = int((equity * cfg.max_notional_per_trade_pct) / max(price, 0.01))
    qty = max(cfg.min_trade_shares, int(min(qty_risk, qty_notional) * size_mult))

    if action == "BUY":
        room = max_sat_qty - satellite_qty
        qty = min(qty, max(0, room))
    else:
        room = max_sat_qty + satellite_qty
        qty = min(qty, max(0, room))
    return max(0, int(qty))


def _simulate(
    df: pd.DataFrame,
    cfg: ReplayConfig,
    core_qty: int,
    initial_equity: float,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    # working_qty = core + satellite
    working_qty = core_qty
    satellite_qty = 0
    avg_cost = np.nan
    cash = initial_equity - core_qty * float(df["close"].iloc[0])

    trades: List[Dict] = []
    curve: List[Dict] = []
    flattened_dates = set()
    last_trade_idx = -10_000
    last_side_idx = {"BUY": -10_000, "SELL": -10_000}
    daily_trade_count: Dict = {}
    sat_open_bar_idx: Optional[int] = None
    sat_avg_cost: Optional[float] = None
    sat_qty_for_pnl = 0

    for i, (ts, row) in enumerate(df.iterrows()):
        price = float(row["close"])
        z = row.get("zscore")
        atr = row.get("atr")
        rvol = row.get("rvol")
        vwap_dist_bps = row.get("vwap_dist_bps")
        cur_date = ts.date()
        slip = cfg.slippage_bps / 10000.0
        fee_rate = cfg.commission_bps / 10000.0

        ph = _phase(ts, cfg)
        if not _is_rth(ts) or pd.isna(z) or pd.isna(atr):
            equity = cash + working_qty * price
            curve.append(
                {
                    "timestamp": ts,
                    "price": price,
                    "session_vwap": row.get("session_vwap"),
                    "zscore": z,
                    "equity": equity,
                    "working_qty": working_qty,
                    "core_qty": core_qty,
                    "satellite_qty": satellite_qty,
                }
            )
            continue

        daily_trade_count.setdefault(cur_date, 0)
        action = None
        reason = ""

        # force flatten near close: do it once/day, then block further entries.
        if _force_flatten_time(ts):
            if cur_date not in flattened_dates and satellite_qty != 0:
                action = "SELL" if satellite_qty > 0 else "BUY"
                reason = "eod_force_flatten"
                flattened_dates.add(cur_date)
            else:
                action = None
                reason = ""
        else:
            # Frequency controls for NEW entries (not for exits/covers).
            allow_new_entry = True
            if daily_trade_count[cur_date] >= cfg.max_trades_per_day:
                allow_new_entry = False
            if i - last_trade_idx < cfg.min_bars_between_trades:
                allow_new_entry = False

            # close-to-mean
            if action is None and satellite_qty > 0 and z >= cfg.zscore_exit_to_mean:
                action = "SELL"
                reason = "mean_reversion_exit_long_satellite"
            elif action is None and satellite_qty < 0 and z <= -cfg.zscore_exit_to_mean:
                action = "BUY"
                reason = "mean_reversion_buyback_short_satellite"
            # extreme entries
            elif action is None and allow_new_entry and z <= cfg.zscore_entry_buy:
                action = "BUY"
                reason = "zscore_downside_entry"
            elif action is None and allow_new_entry and z >= cfg.zscore_entry_sell:
                action = "SELL"
                reason = "zscore_upside_sell"

            # Entry quality filters: avoid knife-catching / blow-off chasing.
            prev_close = float(df["close"].iloc[i - 1]) if i > 0 else price
            prev_z = float(df["zscore"].iloc[i - 1]) if i > 0 and not pd.isna(df["zscore"].iloc[i - 1]) else z
            if action == "BUY" and reason == "zscore_downside_entry":
                # Require downside exhaustion improving (zscore rising or price reclaim).
                if not (z > prev_z or price >= prev_close):
                    action = None
                    reason = ""
            if action == "SELL" and reason == "zscore_upside_sell":
                # Require upside exhaustion fading (zscore falling or price rolling).
                if not (z < prev_z or price <= prev_close):
                    action = None
                    reason = ""

            # Entry-quality gates: VWAP distance and relative volume.
            if reason in ("zscore_downside_entry", "zscore_upside_sell"):
                if pd.isna(vwap_dist_bps) or vwap_dist_bps < cfg.min_vwap_distance_bps:
                    action = None
                    reason = ""
                if pd.isna(rvol) or rvol < cfg.min_entry_rvol:
                    action = None
                    reason = ""

            # Time-window policy from plan.
            if action == "BUY" and reason == "zscore_downside_entry" and ph in ("reduce_only", "rth_other"):
                action = None
                reason = ""
            if action in ("BUY", "SELL") and ph == "closed":
                action = None
                reason = ""

            # Minimum hold before mean-reversion exit to reduce churn.
            if reason.startswith("mean_reversion_exit") and sat_open_bar_idx is not None:
                if i - sat_open_bar_idx < cfg.min_hold_bars:
                    action = None
                    reason = ""

        size_mult = 0.5 if ph == "midday_half" else 1.0
        if action is not None:
            qty = _calc_qty(
                price=price,
                atr=float(atr),
                equity=cash + working_qty * price,
                satellite_qty=satellite_qty,
                core_qty=core_qty,
                action=action,
                cfg=cfg,
                size_mult=size_mult,
            )

            if reason == "eod_force_flatten":
                qty = abs(satellite_qty)

            if qty > 0:
                sat_before = satellite_qty
                exec_price = price * (1 + slip if action == "BUY" else 1 - slip)
                gross_notional = qty * exec_price
                commission = gross_notional * fee_rate
                if action == "BUY":
                    cost = gross_notional + commission
                    cash -= cost
                    working_qty += qty
                    satellite_qty += qty
                    if np.isnan(avg_cost):
                        avg_cost = exec_price
                    else:
                        # Keep avg cost on gross fill price basis.
                        prev_qty = max(0, working_qty - qty)
                        avg_cost = ((avg_cost * prev_qty) + gross_notional) / max(1, working_qty)
                else:
                    proceeds = gross_notional - commission
                    cash += proceeds
                    working_qty -= qty
                    satellite_qty -= qty
                    if working_qty <= 0:
                        avg_cost = np.nan

                # Round-trip satellite realized PnL approximation (separate from cash equity).
                realized_pnl = 0.0
                if sat_qty_for_pnl == 0:
                    sat_qty_for_pnl = qty if action == "BUY" else -qty
                    sat_avg_cost = exec_price
                    sat_open_bar_idx = i
                elif sat_qty_for_pnl > 0:
                    if action == "BUY":
                        sat_avg_cost = ((sat_avg_cost * sat_qty_for_pnl) + (exec_price * qty)) / (sat_qty_for_pnl + qty)
                        sat_qty_for_pnl += qty
                    else:
                        close_qty = min(qty, sat_qty_for_pnl)
                        realized_pnl = (exec_price - sat_avg_cost) * close_qty
                        sat_qty_for_pnl -= close_qty
                        if qty > close_qty:
                            sat_qty_for_pnl = -(qty - close_qty)
                            sat_avg_cost = exec_price
                            sat_open_bar_idx = i
                else:
                    # sat_qty_for_pnl < 0
                    if action == "SELL":
                        cur_abs = abs(sat_qty_for_pnl)
                        sat_avg_cost = ((sat_avg_cost * cur_abs) + (exec_price * qty)) / (cur_abs + qty)
                        sat_qty_for_pnl -= qty
                    else:
                        close_qty = min(qty, abs(sat_qty_for_pnl))
                        realized_pnl = (sat_avg_cost - exec_price) * close_qty
                        sat_qty_for_pnl += close_qty
                        if qty > close_qty:
                            sat_qty_for_pnl = qty - close_qty
                            sat_avg_cost = exec_price
                            sat_open_bar_idx = i

                trades.append(
                    {
                        "timestamp": ts,
                        "action": action,
                        "price": price,
                        "exec_price": exec_price,
                        "qty": qty,
                        "gross_notional": gross_notional,
                        "commission": commission,
                        "realized_pnl_approx": realized_pnl - commission,
                        "zscore": float(z),
                        "rvol": float(rvol) if not pd.isna(rvol) else np.nan,
                        "vwap_dist_bps": float(vwap_dist_bps) if not pd.isna(vwap_dist_bps) else np.nan,
                        "reason": reason,
                        "satellite_before": sat_before,
                        "satellite_after": satellite_qty,
                        "working_after": working_qty,
                    }
                )
                last_trade_idx = i
                last_side_idx[action] = i
                daily_trade_count[cur_date] += 1

        equity = cash + working_qty * price
        curve.append(
            {
                "timestamp": ts,
                "price": price,
                "session_vwap": row.get("session_vwap"),
                "zscore": z,
                "equity": equity,
                "working_qty": working_qty,
                "core_qty": core_qty,
                "satellite_qty": satellite_qty,
            }
        )

    trades_df = pd.DataFrame(trades)
    curve_df = pd.DataFrame(curve).set_index("timestamp")
    return trades_df, curve_df


def _plot_price_signals(symbol: str, curve_df: pd.DataFrame, trades_df: pd.DataFrame, out_file: Path) -> None:
    x = np.arange(len(curve_df))
    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(x, curve_df["price"].values, linewidth=1.0, color="#E0E0E0", label=f"{symbol} close (1m)")
    ax.plot(x, curve_df["session_vwap"].values, linewidth=1.0, color="#58A6FF", alpha=0.8, label="session VWAP")

    if not trades_df.empty:
        idx_map = {ts: i for i, ts in enumerate(curve_df.index)}
        buys = trades_df[trades_df["action"] == "BUY"]
        sells = trades_df[trades_df["action"] == "SELL"]
        buy_x = [idx_map[t] for t in buys["timestamp"] if t in idx_map]
        sell_x = [idx_map[t] for t in sells["timestamp"] if t in idx_map]
        ax.scatter(buy_x, buys["price"].values[: len(buy_x)], marker="^", color="#2ECC71", s=36, label="BUY")
        ax.scatter(sell_x, sells["price"].values[: len(sell_x)], marker="v", color="#E74C3C", s=36, label="SELL")

    ax.set_title(f"{symbol} 1m Replay: Price + VWAP + Buy/Sell Points")
    ax.set_ylabel("Price")
    tick_step = max(1, len(curve_df) // 8)
    ticks = np.arange(0, len(curve_df), tick_step)
    labels = [curve_df.index[i].strftime("%m-%d %H:%M") for i in ticks]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.grid(alpha=0.25)
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(out_file, dpi=140)
    plt.close(fig)


def _plot_combined_overlay(symbol: str, curve_df: pd.DataFrame, trades_df: pd.DataFrame, out_file: Path) -> None:
    """
    Single-figure overlay:
    - left axis: price + VWAP + buy/sell points
    - right axis: core / working / satellite shares
    """
    x = np.arange(len(curve_df))
    fig, (ax_price, ax_eq) = plt.subplots(
        2, 1, figsize=(16, 9), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
    )
    ax_pos = ax_price.twinx()

    ax_price.plot(x, curve_df["price"].values, linewidth=1.0, color="#E0E0E0", label=f"{symbol} close (1m)")
    ax_price.plot(x, curve_df["session_vwap"].values, linewidth=1.0, color="#58A6FF", alpha=0.85, label="session VWAP")

    if not trades_df.empty:
        idx_map = {ts: i for i, ts in enumerate(curve_df.index)}
        buys = trades_df[trades_df["action"] == "BUY"]
        sells = trades_df[trades_df["action"] == "SELL"]
        buy_x = [idx_map[t] for t in buys["timestamp"] if t in idx_map]
        sell_x = [idx_map[t] for t in sells["timestamp"] if t in idx_map]
        ax_price.scatter(buy_x, buys["price"].values[: len(buy_x)], marker="^", color="#2ECC71", s=30, label="BUY")
        ax_price.scatter(sell_x, sells["price"].values[: len(sell_x)], marker="v", color="#E74C3C", s=30, label="SELL")

    ax_pos.plot(x, curve_df["working_qty"].values, color="#F39C12", linewidth=1.2, alpha=0.9, label="working_qty")
    ax_pos.plot(x, curve_df["core_qty"].values, color="#3498DB", linewidth=1.0, alpha=0.9, label="core_qty")
    ax_pos.plot(x, curve_df["satellite_qty"].values, color="#27AE60", linewidth=1.0, alpha=0.9, label="satellite_qty")

    ax_price.set_title(f"{symbol} 1m Replay: Price + Buy/Sell + Position Sizing")
    ax_price.set_ylabel("Price")
    ax_pos.set_ylabel("Shares")
    tick_step = max(1, len(curve_df) // 8)
    ticks = np.arange(0, len(curve_df), tick_step)
    labels = [curve_df.index[i].strftime("%m-%d %H:%M") for i in ticks]
    # Daily PnL bar chart (T0 alpha vs buy & hold)
    init_eq = float(curve_df["equity"].iloc[0])
    first_px = float(curve_df["price"].iloc[0])
    core_qty = int(curve_df["core_qty"].iloc[0])
    buy_hold_eq = pd.Series(init_eq + core_qty * (curve_df["price"].values - first_px), index=curve_df.index)
    t0_eq = curve_df["equity"]
    # Group by date, take last value per day to compute daily PnL
    dates = pd.Series(curve_df.index.date, index=curve_df.index)
    unique_dates = sorted(dates.unique())
    daily_t0_pnl, daily_bh_pnl, daily_labels, bar_x_positions = [], [], [], []
    for d in unique_dates:
        mask = (dates == d).values
        day_x = x[mask]
        if len(day_x) == 0:
            continue
        t0_start = float(t0_eq.iloc[np.where(mask)[0][0]])
        t0_end = float(t0_eq.iloc[np.where(mask)[0][-1]])
        bh_start = float(buy_hold_eq.iloc[np.where(mask)[0][0]])
        bh_end = float(buy_hold_eq.iloc[np.where(mask)[0][-1]])
        daily_t0_pnl.append(t0_end - t0_start)
        daily_bh_pnl.append(bh_end - bh_start)
        daily_labels.append(pd.Timestamp(d).strftime("%m-%d"))
        bar_x_positions.append(int(day_x[len(day_x) // 2]))
    bar_w = max(1, (x[-1] - x[0]) / (len(unique_dates) * 4)) if len(unique_dates) > 0 else 10
    if daily_t0_pnl:
        ax_eq.bar([p - bar_w * 0.55 for p in bar_x_positions], daily_t0_pnl, width=bar_w, color="#9B59B6", alpha=0.8, label="T0 daily PnL")
        ax_eq.bar([p + bar_w * 0.55 for p in bar_x_positions], daily_bh_pnl, width=bar_w, color="#95A5A6", alpha=0.7, label="B&H daily PnL")
    ax_eq.axhline(0, color="#555", linewidth=0.5, linestyle="-")
    ax_eq.set_ylabel("Daily PnL ($)")
    ax_eq.grid(alpha=0.25)
    ax_eq.legend(loc="best", fontsize=9)

    ax_eq.set_xticks(ticks)
    ax_eq.set_xticklabels(labels, rotation=20, ha="right")
    ax_price.grid(alpha=0.25)

    price_handles, price_labels = ax_price.get_legend_handles_labels()
    pos_handles, pos_labels = ax_pos.get_legend_handles_labels()
    ax_price.legend(price_handles + pos_handles, price_labels + pos_labels, loc="best", fontsize=9)

    fig.tight_layout()
    fig.savefig(out_file, dpi=140)
    plt.close(fig)


def _plot_sizing(curve_df: pd.DataFrame, out_file: Path) -> None:
    x = np.arange(len(curve_df))
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(x, curve_df["core_qty"].values, label="core_qty", linewidth=1.3)
    ax.plot(x, curve_df["working_qty"].values, label="working_qty", linewidth=1.3)
    ax.plot(x, curve_df["satellite_qty"].values, label="satellite_qty", linewidth=1.2)
    ax.set_title("Position Sizing Timeline (Core / Working / Satellite)")
    ax.set_ylabel("Shares")
    tick_step = max(1, len(curve_df) // 8)
    ticks = np.arange(0, len(curve_df), tick_step)
    labels = [curve_df.index[i].strftime("%m-%d %H:%M") for i in ticks]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.grid(alpha=0.25)
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(out_file, dpi=140)
    plt.close(fig)


def _plot_equity(curve_df: pd.DataFrame, out_file: Path) -> None:
    x = np.arange(len(curve_df))
    init_eq = float(curve_df["equity"].iloc[0])
    first_px = float(curve_df["price"].iloc[0])
    core_qty = int(curve_df["core_qty"].iloc[0])
    buy_hold_eq = pd.Series(init_eq + core_qty * (curve_df["price"].values - first_px), index=curve_df.index)
    t0_eq = curve_df["equity"]
    dates = pd.Series(curve_df.index.date, index=curve_df.index)
    unique_dates = sorted(dates.unique())

    fig, (ax_cum, ax_bar) = plt.subplots(2, 1, figsize=(14, 7), gridspec_kw={"height_ratios": [1, 1]})

    # Top: cumulative PnL lines
    t0_pnl = t0_eq.values - init_eq
    bh_pnl = buy_hold_eq.values - init_eq
    ax_cum.plot(x, t0_pnl, label="T0 cumul. PnL", linewidth=1.2, color="#9B59B6")
    ax_cum.plot(x, bh_pnl, label="B&H cumul. PnL", linewidth=1.0, alpha=0.8, color="#95A5A6", linestyle="--")
    ax_cum.axhline(0, color="#555", linewidth=0.5)
    ax_cum.set_title("Cumulative PnL: T0 vs Buy & Hold")
    ax_cum.set_ylabel("PnL ($)")
    ax_cum.grid(alpha=0.25)
    ax_cum.legend(loc="best", fontsize=9)

    # Bottom: daily PnL bars
    daily_t0, daily_bh, bar_labels, bar_positions = [], [], [], []
    for d in unique_dates:
        mask = dates == d
        idxs = np.where(mask)[0]
        if len(idxs) == 0:
            continue
        daily_t0.append(float(t0_eq.iloc[idxs[-1]]) - float(t0_eq.iloc[idxs[0]]))
        daily_bh.append(float(buy_hold_eq.iloc[idxs[-1]]) - float(buy_hold_eq.iloc[idxs[0]]))
        bar_labels.append(pd.Timestamp(d).strftime("%m-%d"))
        bar_positions.append(int(x[idxs[len(idxs) // 2]]))

    if daily_t0:
        bar_w = max(1, (x[-1] - x[0]) / (len(unique_dates) * 4))
        ax_bar.bar([p - bar_w * 0.55 for p in bar_positions], daily_t0, width=bar_w, color="#9B59B6", alpha=0.8, label="T0 daily PnL")
        ax_bar.bar([p + bar_w * 0.55 for p in bar_positions], daily_bh, width=bar_w, color="#95A5A6", alpha=0.7, label="B&H daily PnL")
    ax_bar.axhline(0, color="#555", linewidth=0.5)
    ax_bar.set_title("Daily PnL Comparison")
    ax_bar.set_ylabel("PnL ($)")
    tick_step = max(1, len(curve_df) // 8)
    ticks = np.arange(0, len(curve_df), tick_step)
    tick_labels = [curve_df.index[i].strftime("%m-%d %H:%M") for i in ticks]
    ax_bar.set_xticks(ticks)
    ax_bar.set_xticklabels(tick_labels, rotation=20, ha="right")
    ax_bar.grid(alpha=0.25)
    ax_bar.legend(loc="best", fontsize=9)

    fig.tight_layout()
    fig.savefig(out_file, dpi=140)
    plt.close(fig)


def _write_outputs(
    symbol: str,
    trades_df: pd.DataFrame,
    curve_df: pd.DataFrame,
    out_dir: Path,
    cfg: ReplayConfig,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    trades_path = out_dir / "trades.csv"
    metrics_path = out_dir / "metrics_summary.csv"
    report_path = out_dir / "report.md"

    trades_df.to_csv(trades_path, index=False)

    # Daily PnL CSV
    if not curve_df.empty:
        init_eq = float(curve_df["equity"].iloc[0])
        first_px = float(curve_df["price"].iloc[0])
        core_qty = int(curve_df["core_qty"].iloc[0])
        buy_hold_eq = init_eq + core_qty * (curve_df["price"].values - first_px)
        t0_eq = curve_df["equity"].values
        dates = pd.Series(curve_df.index.date, index=curve_df.index)
        unique_dates = sorted(dates.unique())
        daily_rows = []
        for d in unique_dates:
            mask = dates == d
            idxs = np.where(mask)[0]
            if len(idxs) < 2:
                continue
            t0_pnl = float(t0_eq[idxs[-1]]) - float(t0_eq[idxs[0]])
            bh_pnl = float(buy_hold_eq[idxs[-1]]) - float(buy_hold_eq[idxs[0]])
            alpha_pnl = t0_pnl - bh_pnl
            daily_rows.append({"date": pd.Timestamp(d).strftime("%Y-%m-%d"), "t0_pnl": t0_pnl, "bh_pnl": bh_pnl, "alpha_pnl": alpha_pnl})
        if daily_rows:
            pd.DataFrame(daily_rows).to_csv(out_dir / "daily_pnl.csv", index=False)

    if curve_df.empty:
        metrics = pd.DataFrame(
            [
                {
                    "level": "overall",
                    "key": symbol,
                    "side": "long",
                    "trade_count": 0,
                    "win_rate": 0.0,
                    "profit_factor": 0.0,
                    "total_return": 0.0,
                    "buy_hold_return": 0.0,
                    "alpha_pct": 0.0,
                    "cagr": 0.0,
                    "sharpe": 0.0,
                    "max_dd": 0.0,
                }
            ]
        )
    else:
        rets = curve_df["equity"].pct_change().dropna()
        peak = curve_df["equity"].cummax()
        dd = ((curve_df["equity"] - peak) / peak).min() * 100.0
        total_return = (curve_df["equity"].iloc[-1] / curve_df["equity"].iloc[0] - 1.0) * 100.0

        # Buy & hold baseline: core position unchanged, price return only
        buy_hold_return = (curve_df["price"].iloc[-1] / curve_df["price"].iloc[0] - 1.0) * 100.0
        alpha_pct = float(total_return - buy_hold_return)

        pf = 0.0
        win_rate = 0.0
        if not trades_df.empty and "realized_pnl_approx" in trades_df.columns:
            pnls = trades_df["realized_pnl_approx"].fillna(0.0).values
            win_rate = float((pnls > 0).mean() * 100.0)
            gp = float(np.sum(np.maximum(pnls, 0)))
            gl = float(-np.sum(np.minimum(pnls, 0)))
            pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)

        sharpe = 0.0
        if rets.std() > 0:
            sharpe = float(np.sqrt(252 * 390) * rets.mean() / rets.std())

        metrics = pd.DataFrame(
            [
                {
                    "level": "overall",
                    "key": symbol,
                    "side": "long",
                    "trade_count": int(len(trades_df)),
                    "win_rate": win_rate,
                    "profit_factor": pf,
                    "total_return": float(total_return),
                    "buy_hold_return": float(buy_hold_return),
                    "alpha_pct": alpha_pct,
                    "cagr": 0.0,
                    "sharpe": sharpe,
                    "max_dd": float(abs(dd)),
                }
            ]
        )

    metrics.to_csv(metrics_path, index=False)

    report_lines = [
        f"# Intraday 1m Replay Report - {symbol}",
        "",
        "## Output Files",
        "- `price_position_overlay.png`: 单图展示价格/买卖点/仓位",
        "- `price_signals.png`: 日内价格 + VWAP + 买卖点",
        "- `sizing_timeline.png`: 核心仓/总仓/卫星仓数量变化",
        "- `equity_curve.png`: 回放权益曲线",
        "- `trades.csv`: 逐笔交易动作",
        "- `metrics_summary.csv`: 指标汇总",
        "",
        "## Quick Stats",
        f"- Trade actions: {len(trades_df)}",
        f"- Final equity: {curve_df['equity'].iloc[-1]:.2f}" if not curve_df.empty else "- Final equity: N/A",
    ]
    if not curve_df.empty and "buy_hold_return" in metrics.columns:
        buy_hold = float(metrics["buy_hold_return"].iloc[0])
        alpha = float(metrics["alpha_pct"].iloc[0])
        report_lines.extend([
            "",
            "## vs Buy & Hold",
            f"- Buy & hold return: {buy_hold:.3f}%",
            f"- T0 strategy total return: {float(metrics['total_return'].iloc[0]):.3f}%",
            f"- Alpha (T0 gain over B&H): {alpha:+.3f}%",
        ])
    report_lines.extend([
        f"- Costs model: commission={cfg.commission_bps:.2f}bps, slippage={cfg.slippage_bps:.2f}bps",
        f"- Replay controls: cooldown={cfg.min_bars_between_trades} bars, min_hold={cfg.min_hold_bars} bars, max_trades_day={cfg.max_trades_per_day}",
    ])
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="1m replay backtest for intraday T+0 with charts")
    parser.add_argument("--symbol", default="AAPL", help="Ticker symbol, e.g. AAPL")
    parser.add_argument("--days", type=int, default=5, help="Replay days (1-30 for yfinance 1m)")
    parser.add_argument("--core-qty", type=int, default=1000, help="Core holding shares")
    parser.add_argument("--initial-equity", type=float, default=100000.0, help="Initial account equity")
    parser.add_argument(
        "--config",
        default="config/intraday_t0_config.yaml",
        help="Path to intraday T+0 config file",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Default: outputs/intraday_t0_replay/{symbol}",
    )
    args = parser.parse_args()

    cfg = _load_replay_config(args.config)
    data = _download_1m(args.symbol, args.days)
    if data.empty:
        raise RuntimeError("No 1m data downloaded. Try another symbol or smaller day window.")
    data = _keep_rth_only(data)
    if data.empty:
        raise RuntimeError("No RTH 1m data after session filter.")

    feat = _build_intraday_features(data, cfg)
    first_px = float(feat["close"].iloc[0])
    core_notional = max(0, args.core_qty) * first_px
    effective_equity = float(args.initial_equity)
    if core_notional > effective_equity:
        effective_equity = core_notional * 1.2
        print(
            f"[auto-adjust] initial_equity too low for core position: "
            f"{args.initial_equity:.2f} -> {effective_equity:.2f}"
        )

    trades_df, curve_df = _simulate(
        feat,
        cfg=cfg,
        core_qty=max(0, args.core_qty),
        initial_equity=effective_equity,
    )

    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        out_dir = Path("outputs") / "intraday_t0_replay" / args.symbol.upper()
    out_dir.mkdir(parents=True, exist_ok=True)

    _plot_combined_overlay(args.symbol.upper(), curve_df, trades_df, out_dir / "price_position_overlay.png")
    _plot_price_signals(args.symbol.upper(), curve_df, trades_df, out_dir / "price_signals.png")
    _plot_sizing(curve_df, out_dir / "sizing_timeline.png")
    _plot_equity(curve_df, out_dir / "equity_curve.png")
    _write_outputs(args.symbol.upper(), trades_df, curve_df, out_dir, cfg)

    print("Done.")
    print(f"- Output dir: {out_dir}")
    print(f"- Trades: {out_dir / 'trades.csv'}")
    print(f"- Overlay chart: {out_dir / 'price_position_overlay.png'}")
    print(f"- Price chart: {out_dir / 'price_signals.png'}")
    print(f"- Sizing chart: {out_dir / 'sizing_timeline.png'}")
    print(f"- Equity chart: {out_dir / 'equity_curve.png'}")


if __name__ == "__main__":
    main()

