#!/usr/bin/env python3
"""
Rule-based T0 stock picker on different timeframes.
Outputs: daily_picked_5, hourly_picked_5 for replay comparison.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
import yfinance as yf

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _pick_one(df: pd.DataFrame, col: str) -> pd.Series:
    x = df[col]
    return x.iloc[:, 0] if isinstance(x, pd.DataFrame) else x


def _load_ohlcv(symbol: str, interval: str, period: str) -> pd.DataFrame | None:
    df = yf.download(symbol, period=period, interval=interval, auto_adjust=False, progress=False)
    if df is None or df.empty or len(df) < 20:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    out = pd.DataFrame(index=df.index)
    for old, new in [("Open", "open"), ("High", "high"), ("Low", "low"), ("Close", "close"), ("Volume", "volume")]:
        if old in df.columns:
            c = df[old]
            out[new] = c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c
    out = out.dropna()
    return out if len(out) >= 20 else None


def _compute_metrics_daily(df: pd.DataFrame) -> dict | None:
    """Daily bars: trend, dollar vol, intraday range."""
    if len(df) < 8:
        return None
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]
    # 8-bar trend (last 8 days)
    trend_pct = (float(close.iloc[-1]) / float(close.iloc[-8]) - 1.0) * 100.0
    # Dollar volume per bar (daily)
    dv = (close * vol).replace(0, np.nan).dropna()
    adv = float(dv.mean() / 1e6) if len(dv) > 0 else 0.0
    # Intraday range pct: (high-low)/close per bar
    rng = ((high - low) / close.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).dropna()
    avg_range_pct = float(rng.mean() * 100.0) if len(rng) > 0 else 0.0
    score = avg_range_pct / max(abs(trend_pct), 1.0)
    return {
        "symbol": None,  # filled by caller
        "buy_hold_return": trend_pct,
        "avg_intraday_range_pct": avg_range_pct,
        "avg_daily_dollar_vol_m": adv,
        "score": score,
    }


def _compute_metrics_hourly(df: pd.DataFrame) -> dict | None:
    """Hourly bars: trend over ~8 RTH days (52 bars), dollar vol, bar range."""
    lookback = min(52, len(df) - 1)
    if lookback < 20:
        return None
    tail = df.iloc[-lookback:]
    close = tail["close"]
    high = tail["high"]
    low = tail["low"]
    vol = tail["volume"]
    trend_pct = (float(close.iloc[-1]) / float(close.iloc[0]) - 1.0) * 100.0
    # Resample to daily dollar volume
    pv = (close * vol).replace(0, np.nan)
    dv_daily = pv.resample("1D").sum().replace(0, np.nan).dropna()
    adv = float(dv_daily.mean() / 1e6) if len(dv_daily) > 0 else 0.0
    rng = ((high - low) / close.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).dropna()
    avg_range_pct = float(rng.mean() * 100.0) if len(rng) > 0 else 0.0
    score = avg_range_pct / max(abs(trend_pct), 1.0)
    return {
        "symbol": None,
        "buy_hold_return": trend_pct,
        "avg_intraday_range_pct": avg_range_pct,
        "avg_daily_dollar_vol_m": adv,
        "score": score,
    }


def run_picker(
    symbols: list[str],
    interval: str,
    period: str,
    min_adv_m: float = 2000.0,
    trend_min: float = -3.0,
    trend_max: float = 3.0,
    min_range_pct: float = 0.10,
    max_trend_avoid: float = 5.0,
    top_n: int = 5,
) -> list[str]:
    compute = _compute_metrics_daily if interval == "1d" else _compute_metrics_hourly
    rows = []
    for s in symbols:
        df = _load_ohlcv(s, interval, period)
        if df is None:
            continue
        m = compute(df)
        if m is None:
            continue
        m["symbol"] = s
        if m["avg_daily_dollar_vol_m"] < 500:  # min ~$500M equiv for liquidity
            continue
        if not (trend_min <= m["buy_hold_return"] <= trend_max):
            continue
        if m["avg_intraday_range_pct"] < min_range_pct:
            continue
        if m["buy_hold_return"] > max_trend_avoid:
            continue
        rows.append(m)
    if not rows:
        return []
    df = pd.DataFrame(rows).sort_values("score", ascending=False)
    return df["symbol"].head(top_n).tolist()


def main() -> None:
    parser = argparse.ArgumentParser(description="T0 rule picker by timeframe (daily vs hourly)")
    parser.add_argument("--universe", default="quick_test,high_conviction,mega_cap", help="Comma-separated universe keys")
    parser.add_argument("--period", default="1mo", help="Data period (1mo, 2mo, etc.)")
    parser.add_argument("--top", type=int, default=5, help="Top N symbols per timeframe")
    parser.add_argument("--output", default="outputs/intraday_t0_replay/picker_by_timeframe.yaml")
    args = parser.parse_args()

    root = PROJECT_ROOT
    cfg = yaml.safe_load((root / "config/stock_universe.yaml").read_text()) or {}
    symbols = []
    for key in args.universe.strip().split(","):
        key = key.strip()
        if key in cfg and isinstance(cfg[key], list):
            symbols.extend(cfg[key])
        elif key in cfg.get("themes", {}):
            symbols.extend(cfg["themes"][key].get("symbols", []))
    seen = set()
    symbols = [s for s in symbols if isinstance(s, str) and s not in seen and not seen.add(s)]

    daily_picked = run_picker(symbols, "1d", args.period, top_n=args.top)
    hourly_picked = run_picker(symbols, "60m", args.period, top_n=args.top)

    out = {"daily_picked": daily_picked, "hourly_picked": hourly_picked}
    out_path = root / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.safe_dump(out, sort_keys=False))
    print("daily_picked:", daily_picked)
    print("hourly_picked:", hourly_picked)
    print("saved:", out_path)


if __name__ == "__main__":
    main()
