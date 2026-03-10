#!/usr/bin/env python3
"""
VWAP trend + 60m pullback backtest with long/short mirror logic.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
import argparse
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.indicators.trend import calculate_atr_series
from src.indicators.volume import rolling_volume_profile_levels
from src.indicators.vwap import calculate_rolling_vwap, calculate_vwap_slope


SECTOR_UNIVERSE: Dict[str, List[str]] = {
    "Technology": ["AAPL", "MSFT", "NVDA"],
    "Financials": ["JPM", "BAC", "GS"],
    "Healthcare": ["LLY", "JNJ", "UNH"],
    "Energy": ["XOM", "CVX", "SLB"],
}


def get_output_paths(profile: str) -> Dict[str, str]:
    out_base = os.path.join("outputs", f"vwap_pullback_{profile}")
    out_entries = os.path.join(out_base, "entries_exits")
    out_perf = os.path.join(out_base, "performance")
    return {
        "base": out_base,
        "entries": out_entries,
        "perf": out_perf,
        "metrics": os.path.join(out_base, "metrics_summary.csv"),
        "trades": os.path.join(out_base, "trades.csv"),
        "report": os.path.join(out_base, "report.md"),
    }


@dataclass
class Trade:
    symbol: str
    sector: str
    side: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    trigger_line: str
    trigger_level: float
    tp1_price: Optional[float]
    return_pct: float
    stop_reason: str


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    return df


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    df = _flatten_columns(df.copy())
    rename_map = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }
    df = df.rename(columns=rename_map)
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    out = df[keep].copy()
    out = out.dropna()
    if out.empty:
        return out
    if isinstance(out.index, pd.DatetimeIndex) and out.index.tz is not None:
        out.index = out.index.tz_convert(None)
    return out


def fetch_data(symbol: str, years: int = 2) -> Tuple[pd.DataFrame, pd.DataFrame]:
    period = f"{years}y"
    daily_raw = yf.download(symbol, period=period, interval="1d", auto_adjust=False, progress=False)
    intraday_raw = yf.download(symbol, period=period, interval="60m", auto_adjust=False, progress=False)
    daily = _normalize_ohlcv(daily_raw)
    intraday = _normalize_ohlcv(intraday_raw)
    return daily, intraday


def build_daily_features(daily: pd.DataFrame) -> pd.DataFrame:
    df = daily.copy()
    df["atr14"] = calculate_atr_series(df["high"], df["low"], df["close"], period=14)
    df["vwap20d"] = calculate_rolling_vwap(df["high"], df["low"], df["close"], df["volume"], window=20)
    df["vwap20d_slope"] = calculate_vwap_slope(df["vwap20d"], slope_lookback=5)
    df["vwap20d_slope_10"] = calculate_vwap_slope(df["vwap20d"], slope_lookback=10)
    vp = rolling_volume_profile_levels(df[["low", "high", "close", "volume"]], window=5, bins=24)
    df["poc_5d"] = vp["poc"]
    df["vah_5d"] = vp["vah"]
    df["val_5d"] = vp["val"]

    df["close_gt_vwap"] = df["close"] > df["vwap20d"]
    df["close_lt_vwap"] = df["close"] < df["vwap20d"]
    df["above_count_20"] = df["close_gt_vwap"].rolling(20, min_periods=20).sum()
    df["below_count_20"] = df["close_lt_vwap"].rolling(20, min_periods=20).sum()

    df["poc_up"] = df["poc_5d"] > df["poc_5d"].shift(5)
    df["poc_down"] = df["poc_5d"] < df["poc_5d"].shift(5)
    df["vah_up"] = df["vah_5d"] > df["vah_5d"].shift(5)
    df["val_up"] = df["val_5d"] > df["val_5d"].shift(5)
    df["vah_down"] = df["vah_5d"] < df["vah_5d"].shift(5)
    df["val_down"] = df["val_5d"] < df["val_5d"].shift(5)
    df["value_area_up"] = df["vah_up"] & df["val_up"]
    df["value_area_down"] = df["vah_down"] & df["val_down"]
    df["poc_up_2w"] = df["poc_up"] & (df["poc_5d"].shift(5) > df["poc_5d"].shift(10))
    df["poc_down_2w"] = df["poc_down"] & (df["poc_5d"].shift(5) < df["poc_5d"].shift(10))
    df["ret_20d"] = df["close"].pct_change(20)
    df["ret_60d"] = df["close"].pct_change(60)
    df["vol_rvol20"] = df["volume"] / df["volume"].rolling(20, min_periods=20).mean()

    df["overheat_long_ok"] = ((df["close"] - df["vwap20d"]) / df["atr14"]) < 1.0
    df["overheat_short_ok"] = ((df["vwap20d"] - df["close"]) / df["atr14"]) < 1.0

    df["long_watch"] = (
        df["close_gt_vwap"]
        & (df["above_count_20"] >= 13)
        & df["poc_up"]
        & (df["vwap20d_slope"] > 0)
        & df["overheat_long_ok"]
    )
    df["short_watch"] = (
        df["close_lt_vwap"]
        & (df["below_count_20"] >= 13)
        & df["poc_down"]
        & (df["vwap20d_slope"] < 0)
        & df["overheat_short_ok"]
    )

    # Enhanced watchlist: stricter quality filters for higher win-rate targeting.
    df["long_watch_enhanced"] = (
        df["long_watch"]
        & df["poc_up_2w"]
        & df["value_area_up"]
        & (df["vwap20d_slope_10"] > 0)
        & (df["ret_60d"] > 0)
        & (df["vol_rvol20"] >= 0.8)
    )
    df["short_watch_enhanced"] = (
        df["short_watch"]
        & df["poc_down_2w"]
        & df["value_area_down"]
        & (df["vwap20d_slope_10"] < 0)
        & (df["ret_60d"] < 0)
        & (df["vol_rvol20"] >= 0.8)
    )
    return df


def _pick_line_for_long(price: float, vwap20d: float, vah_5d: float) -> Tuple[str, float]:
    candidates = [("vwap20d", vwap20d), ("vah_5d", vah_5d)]
    candidates = [(n, v) for n, v in candidates if np.isfinite(v)]
    if not candidates:
        return "none", np.nan
    return min(candidates, key=lambda x: abs(price - x[1]))


def _pick_line_for_short(price: float, vwap20d: float, val_5d: float) -> Tuple[str, float]:
    candidates = [("vwap20d", vwap20d), ("val_5d", val_5d)]
    candidates = [(n, v) for n, v in candidates if np.isfinite(v)]
    if not candidates:
        return "none", np.nan
    return min(candidates, key=lambda x: abs(price - x[1]))


def _calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period, min_periods=period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period, min_periods=period).mean()
    rs = gain / loss
    return 100.0 - (100.0 / (1.0 + rs))


def build_intraday_features(intraday: pd.DataFrame) -> pd.DataFrame:
    df = intraday.copy()
    if df.empty:
        return df
    grp = df.groupby(df.index.date, sort=False)
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = typical * df["volume"]
    df["session_vwap_1h"] = pv.groupby(df.index.date).cumsum() / df["volume"].groupby(df.index.date).cumsum()
    df["rsi14_1h"] = _calculate_rsi(df["close"], period=14)
    df["vol_ma20_1h"] = df["volume"].rolling(20, min_periods=20).mean()
    body = (df["close"] - df["open"]).abs()
    rng = (df["high"] - df["low"]).replace(0.0, np.nan)
    lower_wick = np.minimum(df["open"], df["close"]) - df["low"]
    upper_wick = df["high"] - np.maximum(df["open"], df["close"])
    df["body_ratio"] = (body / rng).fillna(0.0)
    df["lower_wick_ratio"] = (lower_wick / rng).fillna(0.0)
    df["upper_wick_ratio"] = (upper_wick / rng).fillna(0.0)
    return df


def _make_equity_metrics(equity: pd.Series) -> Dict[str, float]:
    if equity.empty or len(equity) < 2:
        return {"total_return": 0.0, "cagr": 0.0, "sharpe": 0.0, "max_dd": 0.0}
    rets = equity.pct_change().dropna()
    mean = rets.mean()
    vol = rets.std()
    sharpe = 0.0 if vol == 0 else np.sqrt(252 * 6.5) * mean / vol
    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = float(abs(dd.min()) * 100.0)
    total_ret = float((equity.iloc[-1] / equity.iloc[0] - 1) * 100.0)
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
    cagr = float(((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100.0)
    return {"total_return": total_ret, "cagr": cagr, "sharpe": float(sharpe), "max_dd": max_dd}


def run_symbol_backtest(
    symbol: str,
    sector: str,
    daily_features: pd.DataFrame,
    intraday: pd.DataFrame,
    profile: str = "baseline",
    fee_bps: float = 3.0,
    slippage_bps: float = 2.0,
) -> Tuple[pd.DataFrame, pd.Series]:
    if intraday.empty or daily_features.empty:
        return pd.DataFrame(), pd.Series(dtype=float)

    daily_shifted = daily_features.shift(1).copy()
    daily_shifted["date"] = daily_shifted.index.date
    levels = daily_shifted.set_index("date")

    bars = build_intraday_features(intraday)
    bars["date"] = bars.index.date
    merged = bars.join(levels, on="date", how="left", rsuffix="_d")
    merged = merged.dropna(subset=["vwap20d", "atr14", "poc_5d", "vah_5d", "val_5d"])
    if merged.empty:
        return pd.DataFrame(), pd.Series(dtype=float)

    trade_cost = (fee_bps + slippage_bps) / 10000.0
    equity = 100000.0
    equity_curve = [equity]
    equity_times = [merged.index[0]]

    trades: List[Trade] = []

    pos_side = 0  # 1 long, -1 short
    pos_line_name = "none"
    pos_line_level = np.nan
    entry_price = np.nan
    entry_time: Optional[pd.Timestamp] = None
    tp1_done = False
    tp1_price: Optional[float] = None
    qty_left = 0.0
    stop_reason = ""

    pending_long_line = np.nan
    pending_long_name = "none"
    pending_short_line = np.nan
    pending_short_name = "none"
    pending_long_reject = False
    pending_short_reject = False

    for i in range(1, len(merged)):
        prev = merged.iloc[i - 1]
        row = merged.iloc[i]
        prev_close = float(prev["close"])
        cur_close = float(row["close"])
        bar_ret = (cur_close / prev_close - 1.0) if prev_close > 0 else 0.0

        if pos_side != 0 and qty_left > 0:
            equity *= 1.0 + (pos_side * qty_left * bar_ret)

        if pos_side == 0:
            pending_long_reject = False
            pending_short_reject = False

            tol = 0.25 * float(row["atr14"])
            if not np.isfinite(tol) or tol <= 0:
                equity_curve.append(equity)
                equity_times.append(merged.index[i])
                continue

            if profile == "enhanced":
                long_watch = bool(row.get("long_watch_enhanced", False))
                short_watch = bool(row.get("short_watch_enhanced", False))
            else:
                long_watch = bool(row["long_watch"])
                short_watch = bool(row["short_watch"])

            if long_watch:
                lname, llevel = _pick_line_for_long(cur_close, float(row["vwap20d"]), float(row["vah_5d"]))
                if np.isfinite(llevel):
                    touch = float(row["low"]) <= (llevel + tol) and float(row["high"]) >= (llevel - tol)
                    reject = touch and (cur_close > llevel)
                    if profile == "enhanced":
                        strong_reject = (
                            (row.get("lower_wick_ratio", 0.0) >= 0.35 and cur_close > float(row["open"]))
                            or (cur_close > llevel + 0.05 * float(row["atr14"]))
                        )
                        vol_ok = float(row.get("volume", 0.0)) >= float(row.get("vol_ma20_1h", np.inf)) * 0.9
                        vwap_ok = cur_close >= float(row.get("session_vwap_1h", cur_close))
                        rsi_ok = float(row.get("rsi14_1h", 50.0)) >= 50.0
                        reject = reject and strong_reject and vol_ok and vwap_ok and rsi_ok
                    if reject:
                        pending_long_reject = True
                        pending_long_line = llevel
                        pending_long_name = lname

            if short_watch:
                sname, slevel = _pick_line_for_short(cur_close, float(row["vwap20d"]), float(row["val_5d"]))
                if np.isfinite(slevel):
                    touch = float(row["low"]) <= (slevel + tol) and float(row["high"]) >= (slevel - tol)
                    reject = touch and (cur_close < slevel)
                    if profile == "enhanced":
                        strong_reject = (
                            (row.get("upper_wick_ratio", 0.0) >= 0.35 and cur_close < float(row["open"]))
                            or (cur_close < slevel - 0.05 * float(row["atr14"]))
                        )
                        vol_ok = float(row.get("volume", 0.0)) >= float(row.get("vol_ma20_1h", np.inf)) * 0.9
                        vwap_ok = cur_close <= float(row.get("session_vwap_1h", cur_close))
                        rsi_ok = float(row.get("rsi14_1h", 50.0)) <= 50.0
                        reject = reject and strong_reject and vol_ok and vwap_ok and rsi_ok
                    if reject:
                        pending_short_reject = True
                        pending_short_line = slevel
                        pending_short_name = sname

            # 60m micro-structure confirmation on current bar vs previous bar
            long_micro = float(row["high"]) > float(prev["high"]) and cur_close > pending_long_line
            short_micro = float(row["low"]) < float(prev["low"]) and cur_close < pending_short_line
            if profile == "enhanced":
                prev3_high = float(merged["high"].iloc[max(0, i - 3) : i].max())
                prev3_low = float(merged["low"].iloc[max(0, i - 3) : i].min())
                long_micro = long_micro and float(row["high"]) > prev3_high
                short_micro = short_micro and float(row["low"]) < prev3_low

            if pending_long_reject and long_micro:
                pos_side = 1
                entry_price = cur_close * (1 + trade_cost)
                entry_time = merged.index[i]
                pos_line_level = pending_long_line
                pos_line_name = pending_long_name
                qty_left = 1.0
                tp1_done = False
                tp1_price = None
                equity *= (1 - trade_cost)

            elif pending_short_reject and short_micro:
                pos_side = -1
                entry_price = cur_close * (1 - trade_cost)
                entry_time = merged.index[i]
                pos_line_level = pending_short_line
                pos_line_name = pending_short_name
                qty_left = 1.0
                tp1_done = False
                tp1_price = None
                equity *= (1 - trade_cost)

        else:
            # TP1 at daily POC_5D
            if not tp1_done:
                target = float(row["poc_5d"])
                if pos_side == 1 and float(row["high"]) >= target:
                    tp1_done = True
                    tp1_price = target
                    qty_left = 0.5
                    equity *= (1 - trade_cost * 0.5)
                elif pos_side == -1 and float(row["low"]) <= target:
                    tp1_done = True
                    tp1_price = target
                    qty_left = 0.5
                    equity *= (1 - trade_cost * 0.5)

            # Stop-loss on 60m close back through trigger line
            stop_hit = False
            if pos_side == 1 and cur_close < pos_line_level:
                stop_hit = True
                stop_reason = "close_below_trigger_line"
            elif pos_side == -1 and cur_close > pos_line_level:
                stop_hit = True
                stop_reason = "close_above_trigger_line"

            # Trend exit on daily close back through VWAP20D
            trend_exit = False
            if pos_side == 1 and cur_close < float(row["vwap20d"]):
                trend_exit = True
                stop_reason = "daily_vwap_trend_exit"
            elif pos_side == -1 and cur_close > float(row["vwap20d"]):
                trend_exit = True
                stop_reason = "daily_vwap_trend_exit"

            # Time stop for failed follow-through
            time_stop = False
            if profile == "enhanced" and entry_time is not None:
                bars_in_trade = int((merged.index[i] - entry_time) / pd.Timedelta(hours=1))
                if bars_in_trade >= 18:  # ~3 trading days
                    if pos_side == 1 and cur_close <= entry_price:
                        time_stop = True
                        stop_reason = "time_stop_no_follow_through"
                    elif pos_side == -1 and cur_close >= entry_price:
                        time_stop = True
                        stop_reason = "time_stop_no_follow_through"

            if stop_hit or trend_exit or time_stop:
                exit_price = cur_close * (1 - trade_cost if pos_side == 1 else 1 + trade_cost)
                equity *= (1 - trade_cost * qty_left)
                if pos_side == 1:
                    ret = (exit_price / entry_price - 1.0) if not tp1_done else (
                        0.5 * (tp1_price / entry_price - 1.0) + 0.5 * (exit_price / entry_price - 1.0)
                    )
                else:
                    ret = (entry_price / exit_price - 1.0) if not tp1_done else (
                        0.5 * (entry_price / tp1_price - 1.0) + 0.5 * (entry_price / exit_price - 1.0)
                    )

                trades.append(
                    Trade(
                        symbol=symbol,
                        sector=sector,
                        side="long" if pos_side == 1 else "short",
                        entry_time=entry_time if entry_time is not None else merged.index[i],
                        exit_time=merged.index[i],
                        entry_price=float(entry_price),
                        exit_price=float(exit_price),
                        trigger_line=pos_line_name,
                        trigger_level=float(pos_line_level),
                        tp1_price=float(tp1_price) if tp1_price is not None else None,
                        return_pct=float(ret * 100.0),
                        stop_reason=stop_reason,
                    )
                )
                pos_side = 0
                pos_line_name = "none"
                pos_line_level = np.nan
                entry_price = np.nan
                entry_time = None
                tp1_done = False
                tp1_price = None
                qty_left = 0.0

        equity_curve.append(equity)
        equity_times.append(merged.index[i])

    if pos_side != 0 and entry_time is not None:
        last_close = float(merged["close"].iloc[-1])
        exit_price = last_close
        if pos_side == 1:
            ret = (exit_price / entry_price - 1.0) if not tp1_done else (
                0.5 * (tp1_price / entry_price - 1.0) + 0.5 * (exit_price / entry_price - 1.0)
            )
        else:
            ret = (entry_price / exit_price - 1.0) if not tp1_done else (
                0.5 * (entry_price / tp1_price - 1.0) + 0.5 * (entry_price / exit_price - 1.0)
            )
        trades.append(
            Trade(
                symbol=symbol,
                sector=sector,
                side="long" if pos_side == 1 else "short",
                entry_time=entry_time,
                exit_time=merged.index[-1],
                entry_price=float(entry_price),
                exit_price=float(exit_price),
                trigger_line=pos_line_name,
                trigger_level=float(pos_line_level),
                tp1_price=float(tp1_price) if tp1_price is not None else None,
                return_pct=float(ret * 100.0),
                stop_reason="forced_end_of_test",
            )
        )

    trades_df = pd.DataFrame([t.__dict__ for t in trades])
    eq = pd.Series(equity_curve, index=pd.DatetimeIndex(equity_times))
    return trades_df, eq


def _profit_factor(returns_pct: pd.Series) -> float:
    if returns_pct.empty:
        return 0.0
    gross_profit = returns_pct[returns_pct > 0].sum()
    gross_loss = -returns_pct[returns_pct < 0].sum()
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return float(gross_profit / gross_loss)


def summarize_metrics(trades_df: pd.DataFrame, equity: pd.Series, level: str, key: str) -> Dict[str, object]:
    if trades_df.empty:
        base = _make_equity_metrics(equity)
        return {
            "level": level,
            "key": key,
            "side": "all",
            "trade_count": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            **base,
        }

    base = _make_equity_metrics(equity)
    rows = []
    for side in ["long", "short", "all"]:
        subset = trades_df if side == "all" else trades_df[trades_df["side"] == side]
        n = len(subset)
        win_rate = float((subset["return_pct"] > 0).mean() * 100.0) if n else 0.0
        rows.append(
            {
                "level": level,
                "key": key,
                "side": side,
                "trade_count": int(n),
                "win_rate": win_rate,
                "profit_factor": _profit_factor(subset["return_pct"]) if n else 0.0,
                **base,
            }
        )
    return rows


def _plot_entries_exits(symbol: str, intraday: pd.DataFrame, trades: pd.DataFrame, out_path: str) -> None:
    if intraday.empty:
        return
    fig, ax = plt.subplots(figsize=(13, 6))
    ax.plot(intraday.index, intraday["close"], color="#e8e8e8", linewidth=1.2, label=f"{symbol} close (60m)")
    ax.set_title(f"{symbol} Entry/Exit Map (60m)")
    ax.set_ylabel("Price")
    ax.grid(alpha=0.25)

    if not trades.empty:
        long_t = trades[trades["side"] == "long"]
        short_t = trades[trades["side"] == "short"]
        ax.scatter(long_t["entry_time"], long_t["entry_price"], marker="^", color="#1f77b4", s=40, label="Long entry")
        ax.scatter(long_t["exit_time"], long_t["exit_price"], marker="v", color="#17becf", s=40, label="Long exit")
        ax.scatter(short_t["entry_time"], short_t["entry_price"], marker="v", color="#d62728", s=40, label="Short entry")
        ax.scatter(short_t["exit_time"], short_t["exit_price"], marker="^", color="#ff9896", s=40, label="Short exit")

    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def _plot_equity_curves(curves: Dict[str, pd.Series], out_path: str, title: str) -> None:
    fig, ax = plt.subplots(figsize=(12, 6))
    for name, series in curves.items():
        if series.empty:
            continue
        norm = series / series.iloc[0]
        ax.plot(norm.index, norm.values, linewidth=1.6, label=name)
    ax.set_title(title)
    ax.set_ylabel("Normalized Equity")
    ax.grid(alpha=0.25)
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def _plot_sector_side_bars(metrics_df: pd.DataFrame, out_path: str) -> None:
    plot_df = metrics_df[(metrics_df["level"] == "sector") & (metrics_df["side"].isin(["long", "short"]))].copy()
    if plot_df.empty:
        return
    pivot = plot_df.pivot(index="key", columns="side", values="total_return").fillna(0.0)
    ax = pivot.plot(kind="bar", figsize=(10, 6), rot=0)
    ax.set_title("Sector Returns: Long vs Short")
    ax.set_ylabel("Total Return (%)")
    ax.grid(axis="y", alpha=0.25)
    fig = ax.get_figure()
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def _write_report(path: str, metrics_df: pd.DataFrame, profile: str) -> None:
    overall = metrics_df[(metrics_df["level"] == "overall") & (metrics_df["key"] == "portfolio")].copy()
    if overall.empty:
        text = f"# VWAP Pullback {profile} report\n\nNo metrics generated.\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return

    all_row = overall[overall["side"] == "all"].iloc[0]
    long_row = overall[overall["side"] == "long"].iloc[0] if not overall[overall["side"] == "long"].empty else None
    short_row = overall[overall["side"] == "short"].iloc[0] if not overall[overall["side"] == "short"].empty else None
    lines = [
        f"# VWAP Pullback {profile} report",
        "",
        "## Overall",
        f"- total_return: {all_row['total_return']:.2f}%",
        f"- cagr: {all_row['cagr']:.2f}%",
        f"- sharpe: {all_row['sharpe']:.2f}",
        f"- max_dd: {all_row['max_dd']:.2f}%",
        f"- trade_count: {int(all_row['trade_count'])}",
        f"- win_rate_all: {all_row['win_rate']:.2f}%",
    ]
    if long_row is not None:
        lines.append(f"- win_rate_long: {long_row['win_rate']:.2f}%")
    if short_row is not None:
        lines.append(f"- win_rate_short: {short_row['win_rate']:.2f}%")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _compute_spy_baseline(years: int = 2) -> pd.Series:
    raw = yf.download("SPY", period=f"{years}y", interval="60m", auto_adjust=False, progress=False)
    spy = _normalize_ohlcv(raw)
    if spy.empty:
        return pd.Series(dtype=float)
    r = spy["close"].pct_change().fillna(0.0)
    eq = (1 + r).cumprod() * 100000.0
    return eq


def main() -> None:
    parser = argparse.ArgumentParser(description="VWAP pullback multi-sector backtest")
    parser.add_argument("--profile", choices=["baseline", "enhanced"], default="baseline")
    args = parser.parse_args()

    paths = get_output_paths(args.profile)
    os.makedirs(paths["entries"], exist_ok=True)
    os.makedirs(paths["perf"], exist_ok=True)

    all_trades: List[pd.DataFrame] = []
    all_metrics: List[Dict[str, object]] = []
    symbol_curves: Dict[str, pd.Series] = {}

    for sector, symbols in SECTOR_UNIVERSE.items():
        print(f"[{sector}]")
        sector_curves: List[pd.Series] = []
        sector_trades = []
        for symbol in symbols:
            daily, intraday = fetch_data(symbol, years=2)
            if daily.empty or intraday.empty:
                print(f"  - {symbol}: skipped (no data)")
                continue

            daily_features = build_daily_features(daily)
            trades_df, eq = run_symbol_backtest(
                symbol,
                sector,
                daily_features,
                intraday,
                profile=args.profile,
            )
            if eq.empty:
                print(f"  - {symbol}: skipped (insufficient signal)")
                continue

            symbol_curves[symbol] = eq
            sector_curves.append(eq)
            if not trades_df.empty:
                all_trades.append(trades_df)
                sector_trades.append(trades_df)

            symbol_metrics = summarize_metrics(trades_df, eq, "symbol", symbol)
            if isinstance(symbol_metrics, list):
                all_metrics.extend(symbol_metrics)

            chart_path = os.path.join(paths["entries"], f"{symbol}_entries_exits.png")
            _plot_entries_exits(symbol, intraday, trades_df, chart_path)
            print(f"  - {symbol}: trades={len(trades_df)}")

        if sector_curves:
            aligned = pd.concat(sector_curves, axis=1).ffill().dropna(how="all")
            sector_eq = aligned.mean(axis=1)
            sector_trade_df = pd.concat(sector_trades, ignore_index=True) if sector_trades else pd.DataFrame()
            sector_metrics = summarize_metrics(sector_trade_df, sector_eq, "sector", sector)
            if isinstance(sector_metrics, list):
                all_metrics.extend(sector_metrics)

    all_trade_df = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    all_eq = pd.concat(symbol_curves.values(), axis=1).mean(axis=1) if symbol_curves else pd.Series(dtype=float)
    all_metrics.extend(summarize_metrics(all_trade_df, all_eq, "overall", "portfolio"))
    metrics_df = pd.DataFrame(all_metrics)

    if not all_trade_df.empty:
        all_trade_df.to_csv(paths["trades"], index=False)
    metrics_df.to_csv(paths["metrics"], index=False)

    _plot_equity_curves(
        symbol_curves,
        os.path.join(paths["perf"], "symbol_equity_curves.png"),
        "Per-Symbol Strategy Equity Curves",
    )
    _plot_sector_side_bars(metrics_df, os.path.join(paths["perf"], "sector_long_short_returns.png"))

    portfolio_curves = {"Strategy": all_eq}
    spy_eq = _compute_spy_baseline(years=2)
    if not spy_eq.empty:
        portfolio_curves["SPY_BuyHold"] = spy_eq.reindex(all_eq.index).ffill().dropna() if not all_eq.empty else spy_eq
    _plot_equity_curves(
        portfolio_curves,
        os.path.join(paths["perf"], "portfolio_vs_spy.png"),
        "Portfolio Strategy vs SPY (2Y, 60m)",
    )
    _write_report(paths["report"], metrics_df, args.profile)

    print("\nDone.")
    print(f"- Profile: {args.profile}")
    print(f"- Metrics: {paths['metrics']}")
    print(f"- Trades: {paths['trades']}")
    print(f"- Entry/Exit charts: {paths['entries']}")
    print(f"- Performance charts: {paths['perf']}")


if __name__ == "__main__":
    main()
