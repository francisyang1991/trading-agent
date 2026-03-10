#!/usr/bin/env python3
"""
Intraday T+0 Mean Reversion Replay — V3 (Iteration 4)
======================================================
Changes in Iteration 4 (over Iteration 3):
  1. Intraday data caching (SQLite → IBKR DB → yfinance fallback)
     - Avoids re-downloading data; 16x faster cache hits
  2. Session directional strength feature (dir_strength)
     - Running |close-open|/(high-low) at each bar
     - Used for monitoring; trend-adaptive trading was tested & rejected
  3. Supports longer backtests when IBKR data is cached (>7 days)
  4. Trend-day vs choppy-day analysis: strategy earns +$178/day alpha
     on choppy days, loses -$68/day on trend days. Net +$99/day.
     Trend-adaptive satellite/entry changes tested but all reduced alpha.

Changes in Iteration 3 (over Iteration 2):
  1. T0 Suitability Scorer integration (src/picker/t0_suitability.py v1.1)
     - Replaces naive range-based scanner with 6-metric scoring system
     - Scores: VWAP crossings, deviation quality, mean reversion rate,
       range fitness, trend-to-range ratio, liquidity
  2. Scanner prints full suitability report with grades (A/B/C/D/F)
  3. --scan-only flag to run scorer without replay

Changes in Iteration 2:
  1. Built-in stock scanner: scans universe for high intraday-vol T0 candidates
  2. Adaptive regime threshold: scales by each symbol's ATR/price ratio
  3. 2-minute bars (default) — compromise between 1m noise and 3m timing loss
  4. $50k capital PER STOCK with 4x intraday margin ($200k buying power)
  5. Per-stock charts: price+VWAP+trades overlay, equity curve (like original tool)
  6. Portfolio-level summary dashboard

Usage:
  # Scan universe for T0 suitability scores (scorer only, no replay)
  python3 tools/replay_intraday_t0_v3.py --scan-only --top 10

  # Scan universe + run replay on top picks
  python3 tools/replay_intraday_t0_v3.py --scan --top 6 --days 7

  # Run specific symbols
  python3 tools/replay_intraday_t0_v3.py --symbols TSLA,COIN,MSTR --days 7

  # With custom settings
  python3 tools/replay_intraday_t0_v3.py --symbols TSLA,NVDA --capital 50000 \
      --bar-minutes 2 --days 7
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance as yf
import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class V3Config:
    bar_minutes: int = 2
    zscore_entry_buy: float = -2.5
    zscore_entry_sell: float = 2.5
    zscore_exit_to_mean: float = 0.5
    rolling_std_window: int = 20
    atr_window: int = 14
    atr_stop_multiplier: float = 1.2

    # Regime detection — adaptive
    regime_lookback_bars: int = 20
    regime_slope_threshold_bps: float = 3.0  # base threshold, scaled per symbol
    regime_adaptive: bool = True  # scale threshold by symbol vol

    # Capital & margin
    capital_per_stock: float = 50000.0
    margin_multiplier: float = 4.0  # intraday margin
    core_pct_of_capital: float = 0.80  # 80% of capital in core

    # Risk / sizing
    satellite_max_pct_of_core: float = 0.75
    risk_per_trade_pct: float = 0.03
    max_notional_per_trade_pct: float = 0.25
    min_trade_shares: int = 1

    # Costs — IBKR Fixed rate model
    # Fixed: $0.005/share, $1.00 minimum, capped at 1% of trade value
    commission_per_share: float = 0.005
    commission_min_per_trade: float = 1.00
    commission_max_pct: float = 0.01      # 1% of trade value cap
    slippage_bps: float = 1.5

    # Anti-churn
    min_bars_between_trades: int = 6
    min_hold_bars: int = 5
    max_trades_per_day: int = 6
    min_vwap_distance_bps: float = 50.0
    max_entry_legs: int = 1            # max entries per cycle (1=one-shot)

    # Session directional strength threshold for monitoring (Iteration 4)
    # Trend-adaptive params (satellite sizing, z-score widening) tested & rejected
    dir_strength_trend_threshold: float = 0.50

    # Session windows
    reduce_only_windows: List[Tuple[str, str]] = field(
        default_factory=lambda: [("09:30", "10:00")]
    )
    main_windows: List[Tuple[str, str]] = field(
        default_factory=lambda: [("10:00", "11:30"), ("13:30", "15:00")]
    )
    midday_half_windows: List[Tuple[str, str]] = field(
        default_factory=lambda: [("11:30", "13:30")]
    )


def load_v3_config(path: Optional[str] = None) -> V3Config:
    if path is None or not os.path.exists(path):
        return V3Config()
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    strat = raw.get("strategy", {})
    risk = raw.get("risk", {})
    exe = raw.get("execution", {})
    replay = raw.get("replay", {})
    regime = raw.get("regime", {})
    sw = raw.get("session_windows", {})
    capital = raw.get("capital", {})

    def _w(name):
        vals = sw.get(name, []) or []
        return [(str(v["start"]), str(v["end"])) for v in vals if "start" in v]

    return V3Config(
        bar_minutes=int(strat.get("bar_minutes", 2)),
        zscore_entry_buy=float(strat.get("zscore_entry_buy", -2.5)),
        zscore_entry_sell=float(strat.get("zscore_entry_sell", 2.5)),
        zscore_exit_to_mean=float(strat.get("zscore_exit_to_mean", 0.5)),
        rolling_std_window=int(strat.get("rolling_std_window", 20)),
        atr_window=int(strat.get("atr_window", 14)),
        atr_stop_multiplier=float(strat.get("atr_stop_multiplier", 1.2)),
        regime_lookback_bars=int(regime.get("lookback_bars", 20)),
        regime_slope_threshold_bps=float(regime.get("slope_threshold_bps", 3.0)),
        regime_adaptive=bool(regime.get("adaptive", True)),
        capital_per_stock=float(capital.get("per_stock", 50000.0)),
        margin_multiplier=float(capital.get("margin_multiplier", 4.0)),
        core_pct_of_capital=float(capital.get("core_pct", 0.80)),
        satellite_max_pct_of_core=float(risk.get("satellite_max_pct_of_core", 0.75)),
        risk_per_trade_pct=float(risk.get("risk_per_trade_pct", 0.03)),
        max_notional_per_trade_pct=float(risk.get("max_notional_per_trade_pct", 0.25)),
        min_trade_shares=int(risk.get("min_trade_shares", 1)),
        commission_per_share=float(exe.get("commission_per_share", 0.005)),
        commission_min_per_trade=float(exe.get("commission_min_per_trade", 1.00)),
        commission_max_pct=float(exe.get("commission_max_pct", 0.01)),
        slippage_bps=float(exe.get("slippage_bps", 1.5)),
        min_bars_between_trades=int(replay.get("min_bars_between_trades", 6)),
        min_hold_bars=int(replay.get("min_hold_bars", 5)),
        max_trades_per_day=int(replay.get("max_trades_per_day", 6)),
        min_vwap_distance_bps=float(replay.get("min_vwap_distance_bps", 50.0)),
        max_entry_legs=int(replay.get("max_entry_legs", 1)),
        dir_strength_trend_threshold=float(strat.get("dir_strength_trend_threshold", 0.50)),
        reduce_only_windows=_w("reduce_only") or [("09:30", "10:00")],
        main_windows=_w("main") or [("10:00", "11:30"), ("13:30", "15:00")],
        midday_half_windows=_w("midday_half_size") or [("11:30", "13:30")],
    )


# ---------------------------------------------------------------------------
# Stock Scanner: find best T0 candidates from universe
# ---------------------------------------------------------------------------

def scan_t0_candidates(
    symbols: List[str],
    top_n: int = 6,
    min_grade: str = "C",
) -> List[Dict]:
    """Scan symbols using T0SuitabilityScorer (v1.1) — 6-metric scoring system.

    Uses src/picker/t0_suitability.py for proper mean-reversion suitability analysis:
    VWAP crossings, deviation quality, reversion rate, range fitness,
    trend-to-range ratio, and liquidity.
    """
    # Import scorer directly to avoid package __init__ issues
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "t0_suitability",
        str(PROJECT_ROOT / "src" / "picker" / "t0_suitability.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    scorer = mod.T0SuitabilityScorer(min_adv_m=100.0)
    print(f"Scanning {len(symbols)} symbols with T0SuitabilityScorer v{scorer.VERSION}...")

    results = scorer.score_batch(symbols, days=7, top_n=None)

    # Filter by minimum grade
    grade_order = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}
    min_grade_idx = grade_order.get(min_grade, 2)
    results = [r for r in results if grade_order.get(r["grade"], 4) <= min_grade_idx]

    # Print full report
    scorer.print_report(results)

    return results[:top_n]


# ---------------------------------------------------------------------------
# Data download & preparation
# ---------------------------------------------------------------------------

def _get_cache_module():
    """Import intraday_cache avoiding src/picker/__init__.py issues."""
    cache_path = PROJECT_ROOT / "src" / "data" / "intraday_cache.py"
    if cache_path.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("intraday_cache", str(cache_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    return None


def download_1m(symbol: str, days: int) -> pd.DataFrame:
    """Download 1m bars with caching (SQLite → IBKR → yfinance)."""
    cache = _get_cache_module()
    if cache is not None:
        return cache.get_intraday_1m(symbol, days=days, rth_only=False)

    # Fallback: direct yfinance (no cache)
    days = max(1, min(days, 7))
    df = yf.download(symbol, period=f"{days}d", interval="1m",
                     auto_adjust=False, progress=False)
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    def _pick(col):
        c = df[col]
        return c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c

    out = pd.DataFrame(index=df.index)
    for col in ("Open", "High", "Low", "Close", "Volume"):
        out[col.lower()] = _pick(col)
    out = out.dropna()
    if isinstance(out.index, pd.DatetimeIndex) and out.index.tz is not None:
        out.index = out.index.tz_convert("US/Eastern")
    return out


def keep_rth(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    mask = [
        (idx.weekday() <= 4)
        and (idx.time() >= pd.Timestamp("09:30").time())
        and (idx.time() <= pd.Timestamp("16:00").time())
        for idx in df.index
    ]
    return df.loc[mask].copy()


def resample_bars(df_1m: pd.DataFrame, bar_minutes: int) -> pd.DataFrame:
    if bar_minutes <= 1:
        return df_1m.copy()
    results = []
    for date, group in df_1m.groupby(df_1m.index.date):
        resampled = group.resample(f"{bar_minutes}min").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).dropna()
        results.append(resampled)
    if not results:
        return pd.DataFrame()
    return pd.concat(results)


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

def build_features(df: pd.DataFrame, cfg: V3Config) -> pd.DataFrame:
    out = df.copy()
    session_key = out.index.date

    typical = (out["high"] + out["low"] + out["close"]) / 3.0
    pv = typical * out["volume"]
    out["session_vwap"] = (
        pv.groupby(session_key).cumsum()
        / out["volume"].groupby(session_key).cumsum().replace(0, np.nan)
    )

    spread = out["close"] - out["session_vwap"]
    out["zscore"] = spread.groupby(session_key).transform(
        lambda s: s / s.rolling(cfg.rolling_std_window,
                                min_periods=max(5, cfg.rolling_std_window // 2)).std().replace(0, np.nan)
    )

    tr1 = out["high"] - out["low"]
    tr2 = (out["high"] - out["close"].shift(1)).abs()
    tr3 = (out["low"] - out["close"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    out["atr"] = tr.groupby(session_key).transform(
        lambda s: s.rolling(cfg.atr_window, min_periods=max(3, cfg.atr_window // 2)).mean()
    )

    out["vwap_dist_bps"] = (
        (out["close"] - out["session_vwap"]) / out["session_vwap"].replace(0, np.nan)
    ).abs() * 10000.0

    # ATR as percentage of price (for adaptive regime)
    out["atr_pct"] = out["atr"] / out["close"].replace(0, np.nan) * 100.0

    # VWAP slope for regime detection
    vwap_pct = out["session_vwap"].pct_change() * 10000.0
    out["vwap_slope_bps"] = vwap_pct.groupby(session_key).transform(
        lambda s: s.rolling(cfg.regime_lookback_bars,
                            min_periods=max(5, cfg.regime_lookback_bars // 2)).mean()
    )

    # Adaptive regime threshold: scale by symbol's volatility
    # High-vol stocks get a higher threshold (more tolerant of VWAP slope)
    if cfg.regime_adaptive:
        median_atr_pct = out["atr_pct"].median()
        if median_atr_pct > 0:
            vol_ratio = out["atr_pct"] / median_atr_pct
            adaptive_threshold = cfg.regime_slope_threshold_bps * vol_ratio.clip(0.5, 3.0)
        else:
            adaptive_threshold = cfg.regime_slope_threshold_bps
    else:
        adaptive_threshold = cfg.regime_slope_threshold_bps

    out["regime"] = "RANGE"
    out.loc[out["vwap_slope_bps"] > adaptive_threshold, "regime"] = "UPTREND"
    out.loc[out["vwap_slope_bps"] < -adaptive_threshold, "regime"] = "DOWNTREND"

    out["vol_ma20"] = out["volume"].groupby(session_key).transform(
        lambda s: s.rolling(20, min_periods=5).mean()
    )
    out["rvol"] = out["volume"] / out["vol_ma20"].replace(0, np.nan)

    # Session-level directional strength (Iteration 4)
    # dir_strength = |close - session_open| / (session_high - session_low)
    # High value (>0.5) = trending day, low (<0.3) = choppy/mean-reverting
    session_open = out["open"].groupby(session_key).transform("first")
    session_high = out["high"].groupby(session_key).transform(
        lambda s: s.expanding().max()
    )
    session_low = out["low"].groupby(session_key).transform(
        lambda s: s.expanding().min()
    )
    session_range = (session_high - session_low).replace(0, np.nan)
    out["dir_strength"] = (out["close"] - session_open).abs() / session_range
    out["dir_strength"] = out["dir_strength"].fillna(0)

    return out


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _is_rth(ts): return ts.weekday() <= 4 and pd.Timestamp("09:30").time() <= ts.time() <= pd.Timestamp("16:00").time()
def _flatten_time(ts): return ts.time() >= pd.Timestamp("15:55").time()
def _in_window(ts, win): return pd.Timestamp(win[0]).time() <= ts.time() < pd.Timestamp(win[1]).time()

def _phase(ts, cfg):
    for w in cfg.reduce_only_windows:
        if _in_window(ts, w): return "reduce_only"
    for w in cfg.main_windows:
        if _in_window(ts, w): return "main"
    for w in cfg.midday_half_windows:
        if _in_window(ts, w): return "midday_half"
    return "rth_other" if _is_rth(ts) else "closed"


# ---------------------------------------------------------------------------
# Simulation engine
# ---------------------------------------------------------------------------

def simulate(
    df: pd.DataFrame,
    cfg: V3Config,
    core_qty: int,
    initial_equity: float,
    use_regime_filter: bool = True,
) -> Tuple[pd.DataFrame, pd.DataFrame]:

    working_qty = core_qty
    satellite_qty = 0
    cash = initial_equity - core_qty * float(df["close"].iloc[0])

    trades, curve = [], []
    flattened_dates = set()
    last_trade_idx = -10_000
    daily_trade_count: Dict = {}
    daily_cycles_done: Dict = {}     # {(date, "long"|"short"): True}
    current_entry_legs = 0           # entries in current cycle
    sat_open_bar_idx: Optional[int] = None
    sat_avg_cost: Optional[float] = None
    sat_qty_for_pnl = 0

    for i, (ts, row) in enumerate(df.iterrows()):
        price = float(row["close"])
        z = row.get("zscore")
        atr = row.get("atr")
        vwap_dist_bps = row.get("vwap_dist_bps")
        regime = row.get("regime", "RANGE")
        cur_date = ts.date()
        slip = cfg.slippage_bps / 10000.0
        ph = _phase(ts, cfg)

        if not _is_rth(ts) or pd.isna(z) or pd.isna(atr):
            equity = cash + working_qty * price
            curve.append({"timestamp": ts, "price": price,
                         "session_vwap": row.get("session_vwap"), "zscore": z,
                         "equity": equity, "working_qty": working_qty,
                         "core_qty": core_qty, "satellite_qty": satellite_qty,
                         "regime": regime})
            continue

        daily_trade_count.setdefault(cur_date, 0)
        action = None
        reason = ""

        if _flatten_time(ts):
            if cur_date not in flattened_dates and satellite_qty != 0:
                action = "SELL" if satellite_qty > 0 else "BUY"
                reason = "eod_force_flatten"
                flattened_dates.add(cur_date)
        else:
            allow_new = (daily_trade_count[cur_date] < cfg.max_trades_per_day
                         and i - last_trade_idx >= cfg.min_bars_between_trades)

            # Track completed trade cycles per direction per day
            # Once a long or short cycle completes, don't re-enter that direction
            daily_long_done = daily_cycles_done.get((cur_date, "long"), False)
            daily_short_done = daily_cycles_done.get((cur_date, "short"), False)

            # Exits first — close FULL satellite position at once
            if satellite_qty > 0 and z >= cfg.zscore_exit_to_mean:
                action, reason = "SELL", "mean_reversion_exit_long"
            elif satellite_qty < 0 and z <= -cfg.zscore_exit_to_mean:
                action, reason = "BUY", "mean_reversion_buyback_short"
            # Entries: allow up to max_entry_legs pyramiding per cycle
            # satellite_qty >= 0 means we can add long, satellite_qty <= 0 means add short
            elif (allow_new and z <= cfg.zscore_entry_buy
                  and satellite_qty >= 0  # not already short
                  and current_entry_legs < cfg.max_entry_legs):
                if use_regime_filter and regime == "DOWNTREND":
                    pass  # no buying into downtrend
                elif daily_long_done:
                    pass  # already completed a long cycle today
                else:
                    action, reason = "BUY", "zscore_downside_entry"
            elif (allow_new and z >= cfg.zscore_entry_sell
                  and satellite_qty <= 0  # not already long
                  and current_entry_legs < cfg.max_entry_legs):
                if use_regime_filter and regime == "UPTREND":
                    pass  # no selling into uptrend
                elif daily_short_done:
                    pass  # already completed a short cycle today
                else:
                    action, reason = "SELL", "zscore_upside_sell"

            # Entry quality: reversal confirmation
            if action and reason in ("zscore_downside_entry", "zscore_upside_sell"):
                prev_z = float(df["zscore"].iloc[i - 1]) if i > 0 and not pd.isna(df["zscore"].iloc[i - 1]) else z
                prev_close = float(df["close"].iloc[i - 1]) if i > 0 else price
                if action == "BUY" and not (z > prev_z or price >= prev_close):
                    action, reason = None, ""
                elif action == "SELL" and not (z < prev_z or price <= prev_close):
                    action, reason = None, ""

            # VWAP distance gate
            if action and reason in ("zscore_downside_entry", "zscore_upside_sell"):
                if pd.isna(vwap_dist_bps) or vwap_dist_bps < cfg.min_vwap_distance_bps:
                    action, reason = None, ""

            # Session window policy
            if action == "BUY" and reason == "zscore_downside_entry" and ph in ("reduce_only", "rth_other"):
                action, reason = None, ""
            if action and ph == "closed":
                action, reason = None, ""

            # Min hold before exit
            if action and reason.startswith("mean_reversion_") and sat_open_bar_idx is not None:
                if i - sat_open_bar_idx < cfg.min_hold_bars:
                    action, reason = None, ""

        # Execute
        size_mult = 0.5 if ph == "midday_half" else 1.0
        if action is not None:
            max_sat_qty = int(max(0, core_qty) * cfg.satellite_max_pct_of_core)
            equity_now = cash + working_qty * price

            # Exits & flatten: close FULL satellite in one trade
            if reason in ("mean_reversion_exit_long", "mean_reversion_buyback_short",
                          "eod_force_flatten"):
                qty = abs(satellite_qty)
            else:
                # Entries: size by risk budget with confidence multiplier
                risk_budget = equity_now * cfg.risk_per_trade_pct
                stop_dist = max(0.01, float(atr) * cfg.atr_stop_multiplier, price * 0.001)
                qty_risk = int(risk_budget / stop_dist)
                qty_notional = int((equity_now * cfg.max_notional_per_trade_pct) / max(price, 0.01))
                qty = max(cfg.min_trade_shares, int(min(qty_risk, qty_notional) * size_mult))

                # Confidence-based sizing: scale up for highest-conviction entries
                confidence = 0
                if abs(z) >= 3.5:
                    confidence += 2        # extreme z-score
                elif abs(z) >= 3.0:
                    confidence += 1        # strong z-score
                if not pd.isna(vwap_dist_bps) and vwap_dist_bps >= 75:
                    confidence += 1        # big distance from VWAP
                rvol_val = row.get("rvol", 1.0)
                if not pd.isna(rvol_val) and rvol_val >= 1.5:
                    confidence += 1        # above-avg volume confirms move
                confidence_mult = 1.0 + 0.15 * confidence  # 1.0x to 1.6x
                qty = int(qty * confidence_mult)

                if action == "BUY":
                    qty = min(qty, max(0, max_sat_qty - satellite_qty))
                else:
                    qty = min(qty, max(0, max_sat_qty + satellite_qty))

            qty = max(0, int(qty))

            if qty > 0:
                sat_before = satellite_qty
                exec_price = price * (1 + slip if action == "BUY" else 1 - slip)
                gross_notional = qty * exec_price
                # IBKR Fixed rate: $0.005/share, $1.00 min, capped at 1% of trade
                commission = max(
                    cfg.commission_min_per_trade,
                    min(qty * cfg.commission_per_share,
                        gross_notional * cfg.commission_max_pct)
                )

                if action == "BUY":
                    cash -= (gross_notional + commission)
                    working_qty += qty
                    satellite_qty += qty
                else:
                    cash += (gross_notional - commission)
                    working_qty -= qty
                    satellite_qty -= qty

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

                trades.append({
                    "timestamp": ts, "action": action, "price": price,
                    "exec_price": exec_price, "qty": qty,
                    "gross_notional": gross_notional, "commission": commission,
                    "realized_pnl_approx": realized_pnl - commission,
                    "zscore": float(z), "regime": regime, "reason": reason,
                    "satellite_before": sat_before, "satellite_after": satellite_qty,
                    "working_after": working_qty,
                })
                last_trade_idx = i
                daily_trade_count[cur_date] += 1

                # Track entry legs for pyramiding control
                if reason in ("zscore_downside_entry", "zscore_upside_sell"):
                    current_entry_legs += 1

                # Track completed cycles: when satellite returns to 0 via exit
                if satellite_qty == 0 and reason in (
                    "mean_reversion_exit_long", "mean_reversion_buyback_short"
                ):
                    if sat_before > 0:
                        daily_cycles_done[(cur_date, "long")] = True
                    elif sat_before < 0:
                        daily_cycles_done[(cur_date, "short")] = True
                    current_entry_legs = 0  # reset for next cycle

                # Also reset on EOD flatten
                if reason == "eod_force_flatten":
                    current_entry_legs = 0

        equity = cash + working_qty * price
        curve.append({"timestamp": ts, "price": price,
                     "session_vwap": row.get("session_vwap"), "zscore": z,
                     "equity": equity, "working_qty": working_qty,
                     "core_qty": core_qty, "satellite_qty": satellite_qty,
                     "regime": regime})

    return pd.DataFrame(trades), pd.DataFrame(curve).set_index("timestamp")


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(symbol, trades_df, curve_df, core_qty):
    if curve_df.empty:
        return {"symbol": symbol, "total_return": 0, "buy_hold_return": 0,
                "alpha_pct": 0, "sharpe": 0, "max_dd": 0, "trade_count": 0,
                "win_rate": 0, "profit_factor": 0, "avg_trades_per_day": 0,
                "final_equity": 0, "n_days": 0, "total_commission": 0}
    init_eq = float(curve_df["equity"].iloc[0])
    final_eq = float(curve_df["equity"].iloc[-1])
    total_return = (final_eq / init_eq - 1.0) * 100.0
    bh_return = (curve_df["price"].iloc[-1] / curve_df["price"].iloc[0] - 1.0) * 100.0
    alpha = total_return - bh_return
    rets = curve_df["equity"].pct_change().dropna()
    peak = curve_df["equity"].cummax()
    dd = ((curve_df["equity"] - peak) / peak).min() * 100.0
    bars_per_day = len(curve_df) / max(1, len(set(curve_df.index.date)))
    sharpe = float(np.sqrt(252 * bars_per_day) * rets.mean() / rets.std()) if rets.std() > 0 else 0.0
    win_rate, pf = 0.0, 0.0
    if not trades_df.empty and "realized_pnl_approx" in trades_df.columns:
        pnls = trades_df["realized_pnl_approx"].fillna(0.0).values
        win_rate = float((pnls > 0).mean() * 100.0)
        gp = float(np.sum(np.maximum(pnls, 0)))
        gl = float(-np.sum(np.minimum(pnls, 0)))
        pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)
    n_days = len(set(curve_df.index.date))
    total_commission = float(trades_df["commission"].sum()) if not trades_df.empty else 0.0
    return {"symbol": symbol, "total_return": float(total_return),
            "buy_hold_return": float(bh_return), "alpha_pct": float(alpha),
            "sharpe": sharpe, "max_dd": float(abs(dd)),
            "trade_count": len(trades_df), "win_rate": win_rate,
            "profit_factor": pf, "avg_trades_per_day": len(trades_df) / max(1, n_days),
            "final_equity": final_eq, "n_days": n_days,
            "total_commission": total_commission}


# ---------------------------------------------------------------------------
# Per-stock charts (like original replay tool)
# ---------------------------------------------------------------------------

def _set_xticks(ax, curve_df, n_ticks=8):
    x = np.arange(len(curve_df))
    step = max(1, len(curve_df) // n_ticks)
    ticks = np.arange(0, len(curve_df), step)
    labels = [curve_df.index[i].strftime("%m-%d %H:%M") for i in ticks]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)


def plot_stock_overlay(symbol, curve_df, trades_df, metrics, out_dir, bar_min):
    """Price + VWAP + trades + position sizing + daily PnL — single figure per stock."""
    x = np.arange(len(curve_df))
    fig, (ax_price, ax_eq) = plt.subplots(
        2, 1, figsize=(18, 10), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )
    fig.patch.set_facecolor("#1A1A1A")
    for ax in (ax_price, ax_eq):
        ax.set_facecolor("#1A1A1A")
        ax.tick_params(colors="white")
        ax.yaxis.label.set_color("white")
        for spine in ax.spines.values():
            spine.set_color("#444")

    ax_pos = ax_price.twinx()
    ax_pos.tick_params(colors="white")
    ax_pos.yaxis.label.set_color("white")

    # Price + VWAP
    ax_price.plot(x, curve_df["price"].values, linewidth=1.0, color="#E0E0E0",
                  label=f"{symbol} close ({bar_min}m)")
    ax_price.plot(x, curve_df["session_vwap"].values, linewidth=1.0,
                  color="#58A6FF", alpha=0.85, label="session VWAP")

    # Regime shading
    if "regime" in curve_df.columns:
        for j in range(len(x)):
            r = curve_df["regime"].iloc[j]
            if r == "UPTREND":
                ax_price.axvspan(j - 0.5, j + 0.5, color="#2ECC71", alpha=0.04)
            elif r == "DOWNTREND":
                ax_price.axvspan(j - 0.5, j + 0.5, color="#E74C3C", alpha=0.04)

    # Buy/sell markers
    if not trades_df.empty:
        idx_map = {ts: i for i, ts in enumerate(curve_df.index)}
        buys = trades_df[trades_df["action"] == "BUY"]
        sells = trades_df[trades_df["action"] == "SELL"]
        bx = [idx_map[t] for t in buys["timestamp"] if t in idx_map]
        sx = [idx_map[t] for t in sells["timestamp"] if t in idx_map]
        ax_price.scatter(bx, buys["price"].values[:len(bx)], marker="^",
                         color="#2ECC71", s=40, zorder=5, label="BUY")
        ax_price.scatter(sx, sells["price"].values[:len(sx)], marker="v",
                         color="#E74C3C", s=40, zorder=5, label="SELL")

    # Position sizing on right axis
    ax_pos.plot(x, curve_df["working_qty"].values, color="#F39C12",
                linewidth=1.0, alpha=0.8, label="working_qty")
    ax_pos.plot(x, curve_df["core_qty"].values, color="#3498DB",
                linewidth=0.8, alpha=0.7, label="core_qty")
    ax_pos.fill_between(x, 0, curve_df["satellite_qty"].values,
                        color="#27AE60", alpha=0.3, label="satellite_qty")
    ax_pos.set_ylabel("Shares")

    # Title with key metrics
    title = (f"{symbol} T0 Replay ({bar_min}m) — "
             f"Return: {metrics['total_return']:.2f}% | "
             f"Alpha: {metrics['alpha_pct']:+.2f}% | "
             f"Sharpe: {metrics['sharpe']:.2f} | "
             f"MaxDD: {metrics['max_dd']:.2f}% | "
             f"Trades: {metrics['trade_count']}")
    ax_price.set_title(title, fontsize=12, fontweight="bold", color="white")
    ax_price.set_ylabel("Price", color="white")

    # Combine legends
    h1, l1 = ax_price.get_legend_handles_labels()
    h2, l2 = ax_pos.get_legend_handles_labels()
    ax_price.legend(h1 + h2, l1 + l2, loc="best", fontsize=8,
                    facecolor="#2C2C2C", edgecolor="#555", labelcolor="white")
    ax_price.grid(alpha=0.15, color="#555")

    # Bottom panel: daily PnL bars
    init_eq = float(curve_df["equity"].iloc[0])
    first_px = float(curve_df["price"].iloc[0])
    core_qty = int(curve_df["core_qty"].iloc[0])
    bh_eq = init_eq + core_qty * (curve_df["price"].values - first_px)
    t0_eq = curve_df["equity"].values
    dates = pd.Series(curve_df.index.date, index=curve_df.index)
    unique_dates = sorted(dates.unique())

    daily_t0, daily_bh, bar_pos = [], [], []
    for d in unique_dates:
        idxs = np.where(dates == d)[0]
        if len(idxs) < 2:
            continue
        daily_t0.append(float(t0_eq[idxs[-1]]) - float(t0_eq[idxs[0]]))
        daily_bh.append(float(bh_eq[idxs[-1]]) - float(bh_eq[idxs[0]]))
        bar_pos.append(int(x[idxs[len(idxs) // 2]]))

    if daily_t0:
        bar_w = max(1, (x[-1] - x[0]) / (len(unique_dates) * 4))
        ax_eq.bar([p - bar_w * 0.55 for p in bar_pos], daily_t0, width=bar_w,
                  color="#9B59B6", alpha=0.85, label="T0 daily PnL")
        ax_eq.bar([p + bar_w * 0.55 for p in bar_pos], daily_bh, width=bar_w,
                  color="#95A5A6", alpha=0.7, label="B&H daily PnL")
    ax_eq.axhline(0, color="#555", linewidth=0.5)
    ax_eq.set_ylabel("Daily PnL ($)", color="white")
    ax_eq.legend(loc="best", fontsize=8, facecolor="#2C2C2C", edgecolor="#555",
                 labelcolor="white")
    ax_eq.grid(alpha=0.15, color="#555")
    _set_xticks(ax_eq, curve_df)

    fig.tight_layout()
    fig.savefig(out_dir / f"{symbol}_overlay.png", dpi=150,
                facecolor="#1A1A1A", edgecolor="none")
    plt.close(fig)


def plot_stock_equity(symbol, curve_df, metrics, out_dir, bar_min):
    """Equity curve: T0 vs Buy & Hold."""
    x = np.arange(len(curve_df))
    init_eq = float(curve_df["equity"].iloc[0])
    first_px = float(curve_df["price"].iloc[0])
    core_qty = int(curve_df["core_qty"].iloc[0])

    fig, ax = plt.subplots(figsize=(16, 6))
    fig.patch.set_facecolor("#1A1A1A")
    ax.set_facecolor("#1A1A1A")
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_color("#444")

    t0_pnl = (curve_df["equity"].values / init_eq - 1) * 100
    bh_pnl = (core_qty * (curve_df["price"].values - first_px) / init_eq) * 100
    alpha_pnl = t0_pnl - bh_pnl

    ax.plot(x, t0_pnl, label=f"T0 Strategy ({metrics['total_return']:.2f}%)",
            linewidth=1.5, color="#9B59B6")
    ax.plot(x, bh_pnl, label=f"Buy & Hold ({metrics['buy_hold_return']:.2f}%)",
            linewidth=1.0, color="#95A5A6", linestyle="--")
    ax.fill_between(x, 0, alpha_pnl, where=alpha_pnl > 0,
                    color="#2ECC71", alpha=0.15, label="Alpha > 0")
    ax.fill_between(x, 0, alpha_pnl, where=alpha_pnl <= 0,
                    color="#E74C3C", alpha=0.15, label="Alpha < 0")
    ax.axhline(0, color="#555", linewidth=0.5)

    ax.set_title(f"{symbol} Equity Curve — Alpha: {metrics['alpha_pct']:+.2f}% | "
                 f"Sharpe: {metrics['sharpe']:.2f} | MaxDD: {metrics['max_dd']:.2f}%",
                 fontsize=12, fontweight="bold", color="white")
    ax.set_ylabel("Return %", color="white")
    ax.legend(loc="best", fontsize=9, facecolor="#2C2C2C", edgecolor="#555",
              labelcolor="white")
    ax.grid(alpha=0.15, color="#555")
    _set_xticks(ax, curve_df)

    fig.tight_layout()
    fig.savefig(out_dir / f"{symbol}_equity.png", dpi=150,
                facecolor="#1A1A1A", edgecolor="none")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Portfolio summary chart
# ---------------------------------------------------------------------------

def plot_portfolio_summary(all_metrics, capital_per_stock, bar_min, out_dir):
    symbols = [m["symbol"] for m in all_metrics]
    n = len(symbols)
    if n == 0:
        return

    fig, axes = plt.subplots(2, 2, figsize=(20, 12))
    fig.suptitle(
        f"Portfolio Summary — ${capital_per_stock:,.0f}/stock, 4x margin, {bar_min}m bars, "
        f"adaptive regime",
        fontsize=14, fontweight="bold", color="white",
    )
    fig.patch.set_facecolor("#1A1A1A")
    for ax in axes.flat:
        ax.set_facecolor("#1A1A1A")
        ax.tick_params(colors="white")
        ax.xaxis.label.set_color("white")
        ax.yaxis.label.set_color("white")
        ax.title.set_color("white")
        for spine in ax.spines.values():
            spine.set_color("#444")

    x_pos = np.arange(n)
    w = 0.6

    # Top-left: Alpha per symbol
    ax = axes[0, 0]
    alphas = [m["alpha_pct"] for m in all_metrics]
    colors = ["#2ECC71" if a > 0 else "#E74C3C" for a in alphas]
    bars = ax.bar(x_pos, alphas, w, color=colors, alpha=0.85)
    ax.set_xticks(x_pos)
    ax.set_xticklabels(symbols, color="white", fontsize=10)
    ax.axhline(0, color="#555", linewidth=0.5)
    ax.set_title("Alpha vs Buy & Hold (%)", color="white")
    ax.set_ylabel("Alpha %", color="white")
    ax.grid(alpha=0.15, color="#555", axis="y")
    for bar, val in zip(bars, alphas):
        ax.text(bar.get_x() + bar.get_width() / 2., val,
                f'{val:+.2f}%', ha='center',
                va='bottom' if val >= 0 else 'top',
                fontsize=9, color="white", fontweight="bold")

    # Top-right: Total return vs B&H
    ax = axes[0, 1]
    tot_ret = [m["total_return"] for m in all_metrics]
    bh_ret = [m["buy_hold_return"] for m in all_metrics]
    w2 = 0.35
    ax.bar(x_pos - w2 / 2, tot_ret, w2, label="T0 Return", color="#9B59B6", alpha=0.85)
    ax.bar(x_pos + w2 / 2, bh_ret, w2, label="B&H Return", color="#95A5A6", alpha=0.7)
    ax.set_xticks(x_pos)
    ax.set_xticklabels(symbols, color="white", fontsize=10)
    ax.axhline(0, color="#555", linewidth=0.5)
    ax.set_title("Total Return: T0 vs Buy & Hold (%)", color="white")
    ax.legend(fontsize=9, facecolor="#2C2C2C", edgecolor="#555", labelcolor="white")
    ax.grid(alpha=0.15, color="#555", axis="y")

    # Bottom-left: Max DD + Sharpe
    ax = axes[1, 0]
    dd = [m["max_dd"] for m in all_metrics]
    sharpe = [m["sharpe"] for m in all_metrics]
    ax.bar(x_pos - w2 / 2, dd, w2, label="Max DD %", color="#E74C3C", alpha=0.7)
    ax2 = ax.twinx()
    ax2.tick_params(colors="white")
    ax2.yaxis.label.set_color("white")
    ax2.bar(x_pos + w2 / 2, sharpe, w2, label="Sharpe", color="#3498DB", alpha=0.7)
    ax.set_xticks(x_pos)
    ax.set_xticklabels(symbols, color="white", fontsize=10)
    ax.set_title("Risk: Max Drawdown & Sharpe", color="white")
    ax.set_ylabel("Max DD %", color="white")
    ax2.set_ylabel("Sharpe", color="white")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=9, facecolor="#2C2C2C", edgecolor="#555",
              labelcolor="white")
    ax.grid(alpha=0.15, color="#555", axis="y")

    # Bottom-right: Summary table
    ax = axes[1, 1]
    ax.axis("off")
    avg_alpha = np.mean(alphas)
    avg_sharpe = np.mean(sharpe)
    worst_dd = max(dd) if dd else 0
    total_trades = sum(m["trade_count"] for m in all_metrics)
    avg_wr = np.mean([m["win_rate"] for m in all_metrics])
    avg_pf = np.mean([m["profit_factor"] for m in all_metrics if m["profit_factor"] < 100])
    total_final = sum(m["final_equity"] for m in all_metrics)
    total_capital = capital_per_stock * n

    table_data = [
        ["Metric", "Value"],
        ["Symbols", f"{n}"],
        ["Capital/Stock", f"${capital_per_stock:,.0f}"],
        ["Total Capital", f"${total_capital:,.0f}"],
        ["Final Portfolio", f"${total_final:,.0f}"],
        ["Avg Alpha vs B&H", f"{avg_alpha:+.2f}%"],
        ["Avg Sharpe", f"{avg_sharpe:.2f}"],
        ["Worst Max DD", f"{worst_dd:.2f}%"],
        ["Total Trades", f"{total_trades}"],
        ["Avg Win Rate", f"{avg_wr:.1f}%"],
        ["Avg Profit Factor", f"{avg_pf:.2f}"],
    ]
    colors_t = [["#3A3A3A"] * 2] + [["#2C2C2C"] * 2] * (len(table_data) - 1)
    table = ax.table(cellText=table_data, cellColours=colors_t,
                     loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1.0, 1.8)
    for key, cell in table.get_celld().items():
        cell.set_edgecolor("#555")
        cell.set_text_props(color="white")
        if key[0] == 0:
            cell.set_text_props(fontweight="bold", color="#58A6FF")
    ax.set_title("Portfolio Aggregate", fontsize=12, pad=20, color="white")

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(out_dir / "portfolio_summary.png", dpi=150,
                facecolor="#1A1A1A", edgecolor="none")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="V3 T0 Replay: Adaptive Regime + Multi-Timeframe + Stock Scanner"
    )
    parser.add_argument("--symbols", default=None,
                        help="Comma-separated symbols (or use --scan)")
    parser.add_argument("--scan", action="store_true",
                        help="Auto-scan universe for best T0 candidates")
    parser.add_argument("--scan-only", action="store_true",
                        help="Run scorer only, no replay (for stock selection)")
    parser.add_argument("--top", type=int, default=6,
                        help="Top N stocks from scanner")
    parser.add_argument("--capital", type=float, default=50000.0,
                        help="Capital per stock (default: $50k)")
    parser.add_argument("--days", type=int, default=7,
                        help="Replay days (7 for yfinance, longer with IBKR cache)")
    parser.add_argument("--bar-minutes", type=int, default=2,
                        help="Bar size in minutes (default: 2)")
    parser.add_argument("--config", default=None)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    cfg = load_v3_config(args.config)
    cfg.bar_minutes = args.bar_minutes
    cfg.capital_per_stock = args.capital

    out_dir = Path(args.output_dir) if args.output_dir else Path("outputs") / "intraday_t0_v3"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Determine symbols
    if args.scan or args.scan_only:
        # Load universe from config
        universe_path = PROJECT_ROOT / "config" / "stock_universe.yaml"
        if universe_path.exists():
            ucfg = yaml.safe_load(universe_path.read_text()) or {}
            scan_pool = set()
            for key in ("quick_test", "high_conviction", "volatile_momentum"):
                if key in ucfg and isinstance(ucfg[key], list):
                    scan_pool.update(ucfg[key])
            for theme_key in ("ultra_volatile", "momentum_leaders", "mag7",
                               "crypto", "ai_chips"):
                theme = ucfg.get("themes", {}).get(theme_key, {})
                if "symbols" in theme:
                    scan_pool.update(theme["symbols"])
            scan_list = sorted(scan_pool)
        else:
            scan_list = ["TSLA", "NVDA", "AMD", "COIN", "MSTR", "MARA",
                         "PLTR", "META", "HOOD", "SMCI", "SOFI", "RKLB"]

        candidates = scan_t0_candidates(scan_list, top_n=args.top)
        if not candidates:
            print("No suitable T0 candidates found!")
            return

        print(f"\nSelected top {len(candidates)} for replay:")
        for c in candidates:
            print(f"  {c['symbol']:6s}  score={c['total_score']:5.1f}  "
                  f"grade={c['grade']}  range={c['avg_range_pct']:.2f}%  "
                  f"reversion={c['reversion_rate_pct']:.1f}%")

        if args.scan_only:
            print("\n(--scan-only mode: no replay)")
            return

        symbols = [c["symbol"] for c in candidates]
    else:
        if args.symbols:
            symbols = [s.strip().upper() for s in args.symbols.split(",")]
        else:
            symbols = ["TSLA", "NVDA", "AMD", "COIN", "MSTR", "META"]

    # Run replay for each symbol
    all_metrics = []

    for symbol in symbols:
        print(f"\n{'='*60}")
        print(f"  {symbol} — ${cfg.capital_per_stock:,.0f} capital, "
              f"4x margin, {cfg.bar_minutes}m bars")
        print(f"{'='*60}")

        raw_1m = download_1m(symbol, args.days)
        if raw_1m.empty:
            print(f"  [SKIP] No 1m data for {symbol}")
            continue
        rth_1m = keep_rth(raw_1m)
        if rth_1m.empty:
            print(f"  [SKIP] No RTH data for {symbol}")
            continue

        first_price = float(rth_1m["close"].iloc[0])
        # Core = 80% of $50k capital
        core_value = cfg.capital_per_stock * cfg.core_pct_of_capital
        core_qty = max(1, int(core_value / first_price))
        # Effective equity = capital (margin lets us go up to 4x for intraday)
        effective_equity = cfg.capital_per_stock

        print(f"  Price: ${first_price:.2f} | Core: {core_qty} shares "
              f"(${core_qty * first_price:,.0f}) | Margin BP: ${cfg.capital_per_stock * cfg.margin_multiplier:,.0f}")

        # Resample to N-minute bars
        rth_nm = resample_bars(rth_1m, cfg.bar_minutes)
        feat = build_features(rth_nm, cfg)

        # Run simulation
        trades_df, curve_df = simulate(feat, cfg, core_qty, effective_equity,
                                       use_regime_filter=True)
        metrics = compute_metrics(symbol, trades_df, curve_df, core_qty)
        all_metrics.append(metrics)

        print(f"  Return: {metrics['total_return']:.2f}% | "
              f"Alpha: {metrics['alpha_pct']:+.2f}% | "
              f"Sharpe: {metrics['sharpe']:.2f} | "
              f"MaxDD: {metrics['max_dd']:.2f}% | "
              f"Trades: {metrics['trade_count']} | "
              f"WinR: {metrics['win_rate']:.1f}% | "
              f"Comm: ${metrics['total_commission']:.0f}")

        # Generate per-stock charts
        sym_dir = out_dir / symbol
        sym_dir.mkdir(parents=True, exist_ok=True)

        plot_stock_overlay(symbol, curve_df, trades_df, metrics, sym_dir,
                          cfg.bar_minutes)
        plot_stock_equity(symbol, curve_df, metrics, sym_dir, cfg.bar_minutes)

        # Save trades CSV
        if not trades_df.empty:
            trades_df.to_csv(sym_dir / "trades.csv", index=False)

        # Save metrics
        pd.DataFrame([metrics]).to_csv(sym_dir / "metrics.csv", index=False)

    # Portfolio summary
    if all_metrics:
        print(f"\n{'='*70}")
        print("PORTFOLIO SUMMARY")
        print(f"{'='*70}")
        print(f"\n{'Symbol':<8} {'Return':>8} {'B&H':>8} {'Alpha':>8} {'Sharpe':>8} "
              f"{'MaxDD':>8} {'Trades':>7} {'WinR':>6} {'PF':>6} {'Comm$':>7}")
        print("-" * 85)
        for m in all_metrics:
            print(f"{m['symbol']:<8} {m['total_return']:>7.2f}% {m['buy_hold_return']:>7.2f}% "
                  f"{m['alpha_pct']:>+7.2f}% {m['sharpe']:>7.2f} {m['max_dd']:>7.2f}% "
                  f"{m['trade_count']:>6d} {m['win_rate']:>5.1f}% {m['profit_factor']:>5.2f} "
                  f"${m['total_commission']:>6.0f}")

        avg_alpha = np.mean([m["alpha_pct"] for m in all_metrics])
        avg_sharpe = np.mean([m["sharpe"] for m in all_metrics])
        total_comm = sum(m["total_commission"] for m in all_metrics)
        print(f"\n  Avg Alpha: {avg_alpha:+.2f}% | Avg Sharpe: {avg_sharpe:.2f} | "
              f"Total Commissions: ${total_comm:,.0f}")

        plot_portfolio_summary(all_metrics, cfg.capital_per_stock,
                              cfg.bar_minutes, out_dir)
        pd.DataFrame(all_metrics).to_csv(out_dir / "all_metrics.csv", index=False)

    print(f"\nOutput directory: {out_dir}")
    for s in symbols:
        print(f"  {s}/ — {s}_overlay.png, {s}_equity.png, trades.csv, metrics.csv")
    print(f"  portfolio_summary.png")
    print(f"  all_metrics.csv")


if __name__ == "__main__":
    main()
