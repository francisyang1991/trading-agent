"""
T0 Intraday Mean Reversion Suitability Scorer (v1.1)
=====================================================
Scores stocks for T0 intraday mean reversion strategy suitability.

A good T0 stock OSCILLATES around VWAP (range-bound).
A bad T0 stock GAPS and TRENDS intraday (momentum/directional).

Key metrics scored (100 points total):
  1. VWAP Crossing Frequency   (20 pts) — more crossings = more oscillation
  2. VWAP Deviation Quality    (15 pts) — sweet spot: bars 20-100 bps from VWAP
  3. Mean Reversion Strength   (20 pts) — negative return autocorrelation
  4. Intraday Range Fitness    (20 pts) — sweet spot 2.5-5% daily range
  5. Trend-to-Range Ratio      (15 pts) — low directional move / high range
  6. Liquidity                 (10 pts) — ADV and volume consistency

Changes from v1.0:
  - Recalibrated VWAP crossing threshold for 1m data (20/day max, not 6)
  - Replaced broken z-score reversion speed with return autocorrelation
  - Replaced VWAP anchoring with deviation quality (penalizes too-close AND too-far)
  - Tightened range fitness to favor 3-4% sweet spot, harsh penalty < 2%
  - Increased trend-to-range weight (15 pts, was 10)

Usage:
    from src.picker.t0_suitability import T0SuitabilityScorer
    scorer = T0SuitabilityScorer()
    results = scorer.score_batch(["TSLA", "PLTR", "AMD", "COIN"], days=5)
    for r in results:
        print(f"{r['symbol']:6s}  score={r['total_score']:.0f}  grade={r['grade']}")
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Data helpers — uses intraday cache (SQLite + IBKR + yfinance fallback)
# ---------------------------------------------------------------------------

def _get_cache_module():
    """Import intraday_cache avoiding src/picker/__init__.py issues."""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cache_path = os.path.join(project_root, "src", "data", "intraday_cache.py")
    if os.path.exists(cache_path):
        import importlib.util
        spec = importlib.util.spec_from_file_location("intraday_cache", cache_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    return None


def download_1m_rth(symbol: str, days: int = 5) -> pd.DataFrame:
    """Get 1m RTH-only data for a symbol (cached)."""
    cache = _get_cache_module()
    if cache is not None:
        return cache.get_intraday_1m(symbol, days=days, rth_only=True)

    # Fallback: direct yfinance download (no cache available)
    import yfinance as yf
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

    mask = [
        (idx.weekday() <= 4)
        and (idx.time() >= pd.Timestamp("09:30").time())
        and (idx.time() <= pd.Timestamp("16:00").time())
        for idx in out.index
    ]
    return out.loc[mask].copy()


def compute_session_vwap(df: pd.DataFrame) -> pd.Series:
    """Compute cumulative session VWAP grouped by date."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = typical * df["volume"]
    session_key = df.index.date
    cum_pv = pv.groupby(session_key).cumsum()
    cum_vol = df["volume"].groupby(session_key).cumsum().replace(0, np.nan)
    return cum_pv / cum_vol


# ---------------------------------------------------------------------------
# Individual metric scorers (v1.1 — recalibrated for 1m data)
# ---------------------------------------------------------------------------

def _score_vwap_crossings(df: pd.DataFrame, vwap: pd.Series) -> float:
    """
    Score: How often does price cross VWAP per session?
    More crossings = more oscillation = better for mean reversion.
    With 1m bars, typical stocks cross 10-25 times/day.
    Scoring: 20+ crossings/day = 20 pts, <5 = 0 pts.
    """
    spread = df["close"] - vwap
    sign_changes = (spread.shift(1) * spread < 0).astype(int)
    session_key = df.index.date
    crossings_per_day = sign_changes.groupby(session_key).sum()
    avg_crossings = float(crossings_per_day.mean()) if len(crossings_per_day) > 0 else 0

    # 20+ crossings/day = max, <5 = 0
    score = max(0, (avg_crossings - 5.0)) * (20.0 / 15.0)
    return min(20.0, score)


def _score_vwap_deviation_quality(df: pd.DataFrame, vwap: pd.Series) -> float:
    """
    Score: What fraction of bars are in the "tradeable zone" (20-100 bps from VWAP)?
    Too close (<20 bps) = no edge, spread eats profit.
    Too far (>100 bps) = trending, mean reversion risky.
    Sweet spot = 20-100 bps distance from VWAP.
    Scoring: 40%+ bars in sweet spot = 15 pts, <10% = 0 pts.
    """
    dist_bps = ((df["close"] - vwap) / vwap.replace(0, np.nan)).abs() * 10000.0
    in_zone = ((dist_bps >= 20.0) & (dist_bps <= 100.0)).mean() * 100.0

    # 40%+ in zone = max, <10% = 0
    score = max(0, (in_zone - 10.0)) * (15.0 / 30.0)
    return min(15.0, score)


def _score_mean_reversion_strength(df: pd.DataFrame, vwap: pd.Series) -> float:
    """
    Score: VWAP reversion rate — when price deviates >=30 bps from VWAP,
    how often does it return to within 15 bps within the next 10 bars?
    Higher rate = more mean-reverting = better for T0.
    Scoring: 25%+ reversion rate = 20 pts, <8% = 0 pts.
    """
    session_key = df.index.date
    reversions = 0
    extremes = 0
    for date, group in df.groupby(session_key):
        if len(group) < 60:
            continue
        v = vwap.loc[group.index]
        dist_bps = ((group["close"] - v) / v * 10000).values
        for i in range(len(dist_bps) - 10):
            if abs(dist_bps[i]) >= 30:  # at least 30 bps from VWAP
                extremes += 1
                future = dist_bps[i + 1:i + 11]
                if any(abs(f) < 15 for f in future):
                    reversions += 1

    if extremes < 10:
        return 10.0  # insufficient data = neutral

    reversion_rate = reversions / extremes * 100.0
    # 25%+ = max (20 pts), <8% = 0
    score = max(0, (reversion_rate - 8.0)) * (20.0 / 17.0)
    return min(20.0, score)


def _score_intraday_range_fitness(df: pd.DataFrame) -> float:
    """
    Score: Average daily intraday range as % of price.
    Sweet spot: 2.5-5% daily range. Too low = no edge. Too high = trending.
    Scoring: 3.5% = 20 pts (optimal), <1.5% or >7% = 0 pts.
    """
    session_key = df.index.date
    daily_high = df["high"].groupby(session_key).max()
    daily_low = df["low"].groupby(session_key).min()
    daily_close = df["close"].groupby(session_key).last()
    range_pct = float(((daily_high - daily_low) / daily_close).mean() * 100.0)

    # Asymmetric scoring: penalize low range more harshly
    optimal = 3.5
    if range_pct < 1.5:
        return 0.0
    if range_pct > 7.0:
        return 0.0
    if range_pct < optimal:
        # Steeper penalty below optimal — low range = no edge
        distance = optimal - range_pct
        return max(0.0, 20.0 * (1.0 - distance / 2.0))
    else:
        # Gentler penalty above optimal — high range still has some edge
        distance = range_pct - optimal
        return max(0.0, 20.0 * (1.0 - distance / 3.5))


def _score_trend_to_range_ratio(df: pd.DataFrame) -> float:
    """
    Score: Low multi-day directional move relative to daily range = good.
    Formula: abs(period_return) / avg_daily_range. Lower = better.
    Scoring: ratio < 0.3 = 15 pts, ratio > 2.5 = 0 pts.
    """
    period_return = abs(float(df["close"].iloc[-1] / df["close"].iloc[0] - 1.0)) * 100.0
    session_key = df.index.date
    daily_high = df["high"].groupby(session_key).max()
    daily_low = df["low"].groupby(session_key).min()
    daily_close = df["close"].groupby(session_key).last()
    avg_range = float(((daily_high - daily_low) / daily_close).mean() * 100.0)

    if avg_range <= 0:
        return 0.0

    ratio = period_return / avg_range
    # ratio < 0.3 = full score, > 2.5 = 0
    score = max(0, (2.5 - ratio)) * (15.0 / 2.2)
    return min(15.0, score)


def _score_liquidity(df: pd.DataFrame, min_adv_m: float = 100.0) -> float:
    """
    Score: Average daily dollar volume and volume consistency.
    Scoring: ADV > $500M = 10 pts, < $100M = 0 pts. Consistency bonus.
    """
    session_key = df.index.date
    daily_dv = (df["close"] * df["volume"]).groupby(session_key).sum()
    adv = float(daily_dv.mean() / 1e6)

    if adv < min_adv_m:
        return 0.0

    # ADV scoring: $500M+ = 8 pts
    adv_score = min(8.0, (adv - min_adv_m) * 8.0 / 400.0)

    # Volume consistency: low CV of daily volume = bonus 2 pts
    cv = float(daily_dv.std() / daily_dv.mean()) if daily_dv.mean() > 0 else 1.0
    consistency_score = max(0, 2.0 * (1.0 - cv))

    return min(10.0, adv_score + consistency_score)


# ---------------------------------------------------------------------------
# Main scorer
# ---------------------------------------------------------------------------

def _grade(score: float) -> str:
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    if score >= 45:
        return "C"
    if score >= 30:
        return "D"
    return "F"


class T0SuitabilityScorer:
    """Score stocks for T0 intraday mean reversion suitability."""

    VERSION = "1.1"

    def __init__(self, min_adv_m: float = 100.0):
        self.min_adv_m = min_adv_m

    def score_dataframe(self, symbol: str, df: pd.DataFrame) -> Dict:
        """Score a symbol from pre-loaded 1m RTH DataFrame."""
        if df.empty or len(df) < 100:
            return {"symbol": symbol, "total_score": 0, "grade": "F",
                    "skip_reason": "insufficient data"}

        vwap = compute_session_vwap(df)
        last_price = float(df["close"].iloc[-1])

        s1 = _score_vwap_crossings(df, vwap)
        s2 = _score_vwap_deviation_quality(df, vwap)
        s3 = _score_mean_reversion_strength(df, vwap)
        s4 = _score_intraday_range_fitness(df)
        s5 = _score_trend_to_range_ratio(df)
        s6 = _score_liquidity(df, self.min_adv_m)

        total = s1 + s2 + s3 + s4 + s5 + s6

        # Compute descriptive stats
        session_key = df.index.date
        daily_high = df["high"].groupby(session_key).max()
        daily_low = df["low"].groupby(session_key).min()
        daily_close = df["close"].groupby(session_key).last()
        avg_range_pct = float(((daily_high - daily_low) / daily_close).mean() * 100.0)
        period_return = float((df["close"].iloc[-1] / df["close"].iloc[0] - 1.0) * 100.0)
        daily_dv = (df["close"] * df["volume"]).groupby(session_key).sum()
        adv_m = float(daily_dv.mean() / 1e6)

        # Reversion rate detail
        reversions = 0
        extremes = 0
        for date, group in df.groupby(session_key):
            if len(group) < 60:
                continue
            v = vwap.loc[group.index]
            dist_bps = ((group["close"] - v) / v * 10000).values
            for i in range(len(dist_bps) - 10):
                if abs(dist_bps[i]) >= 30:
                    extremes += 1
                    future = dist_bps[i + 1:i + 11]
                    if any(abs(f) < 15 for f in future):
                        reversions += 1
        reversion_rate = reversions / max(1, extremes) * 100.0

        return {
            "symbol": symbol,
            "price": last_price,
            "total_score": round(total, 1),
            "grade": _grade(total),
            "vwap_crossing_score": round(s1, 1),
            "vwap_deviation_score": round(s2, 1),
            "mean_reversion_score": round(s3, 1),
            "range_fitness_score": round(s4, 1),
            "trend_range_ratio_score": round(s5, 1),
            "liquidity_score": round(s6, 1),
            "avg_range_pct": round(avg_range_pct, 2),
            "period_return_pct": round(period_return, 2),
            "adv_millions": round(adv_m, 0),
            "reversion_rate_pct": round(reversion_rate, 1),
            "n_days": len(set(session_key)),
        }

    def score_symbol(self, symbol: str, days: int = 5) -> Dict:
        """Download 1m data and score a single symbol."""
        df = download_1m_rth(symbol, days)
        return self.score_dataframe(symbol, df)

    def score_batch(self, symbols: List[str], days: int = 5,
                    top_n: Optional[int] = None) -> List[Dict]:
        """Score multiple symbols, return sorted by total_score descending."""
        results = []
        for sym in symbols:
            try:
                result = self.score_symbol(sym, days)
                if result.get("skip_reason"):
                    continue
                results.append(result)
            except Exception as e:
                continue

        results.sort(key=lambda x: x["total_score"], reverse=True)
        if top_n:
            results = results[:top_n]
        return results

    def print_report(self, results: List[Dict]) -> None:
        """Print a formatted suitability report."""
        print(f"\nT0 Suitability Report (scorer v{self.VERSION})")
        print("=" * 105)
        print(f"{'Symbol':<7} {'Price':>7} {'Score':>6} {'Grd':>4} "
              f"{'Cross':>6} {'DevQ':>6} {'MRev':>6} {'Range':>6} "
              f"{'T/R':>5} {'Liq':>5} {'Range%':>7} {'RevR%':>6} {'ADV$M':>7}")
        print("-" * 105)
        for r in results:
            print(f"{r['symbol']:<7} ${r['price']:>6.1f} {r['total_score']:>5.0f} "
                  f"{r['grade']:>4} {r['vwap_crossing_score']:>5.1f} "
                  f"{r['vwap_deviation_score']:>5.1f} {r['mean_reversion_score']:>5.1f} "
                  f"{r['range_fitness_score']:>5.1f} {r['trend_range_ratio_score']:>4.1f} "
                  f"{r['liquidity_score']:>4.1f} {r['avg_range_pct']:>6.2f}% "
                  f"{r['reversion_rate_pct']:>5.1f} {r['adv_millions']:>6.0f}")
        print()
