#!/usr/bin/env python3
"""
Analyze sustained multi-month 200%+ bull runs over the last N years.

This investigation is designed for strategy validation:
- find runs that are large and sustained (skip quick spike/fade behavior)
- inspect likely drivers: fundamentals at run start, volume regime,
  earnings catalysts, analyst rating activity, and headline keywords

Usage:
  python3 tools/analyze_two_year_bull_runs.py
  python3 tools/analyze_two_year_bull_runs.py --years 2 --max-symbols 0
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.blacklist_store import DataBlacklistStore
from src.data_manager import DataManager
from src.picker.fundamentals_service import FundamentalSnapshotService
from src.universe.listed_symbols import load_all_listed_us_symbols


@dataclass
class BullRunWindow:
    ticker: str
    start_date: date
    peak_date: date
    start_price: float
    peak_price: float
    return_pct: float
    duration_days: int
    post30_min_return_pct: float


def _is_common_stock_ticker(ticker: str) -> bool:
    """
    Heuristic filter to drop warrants/rights/units and other non-common symbols.
    """
    t = str(ticker).upper().strip()
    if not t:
        return False
    # Keep common share classes like BRK.B / HEI.A.
    if t.endswith(("W", "WW", "WS", "U", "R", "RT")):
        return False
    return True


def _as_price_frame(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"]).dt.date
    elif isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index()
        if "index" in df.columns:
            df = df.rename(columns={"index": "Date"})
        df["Date"] = pd.to_datetime(df["Date"]).dt.date
    else:
        raise ValueError("Price frame has no date axis")
    if "Close" not in df.columns:
        raise ValueError("Price frame missing Close")
    if "Volume" not in df.columns:
        df["Volume"] = np.nan
    return df.sort_values("Date").reset_index(drop=True)


def detect_best_bull_run(
    ticker: str,
    price_df: pd.DataFrame,
    min_return_pct: float = 200.0,
    min_duration_days: int = 30,
    max_duration_days: int = 270,
    sustain_days: int = 30,
    sustain_min_return_pct: float = 50.0,
    years: int = 2,
) -> Optional[BullRunWindow]:
    """
    Detect the strongest sustained run in the lookback window.
    """
    df = _as_price_frame(price_df)
    cutoff = date.today() - timedelta(days=years * 365)
    df = df[df["Date"] >= cutoff].copy()
    if len(df) < min_duration_days + 2:
        return None

    close = pd.to_numeric(df["Close"], errors="coerce").to_numpy()
    dates = pd.to_datetime(df["Date"]).dt.date.to_numpy()
    n = len(df)
    min_mult = 1.0 + min_return_pct / 100.0
    sustain_floor_mult = 1.0 + sustain_min_return_pct / 100.0

    best: Optional[BullRunWindow] = None
    best_ret = -1.0

    for i in range(0, n - min_duration_days):
        p0 = close[i]
        if not np.isfinite(p0) or p0 <= 0:
            continue

        j_start = i + min_duration_days
        j_end = min(i + max_duration_days, n - 1)
        if j_start > j_end:
            continue

        segment = close[j_start : j_end + 1]
        if len(segment) == 0 or not np.isfinite(segment).any():
            continue

        rel_idx = int(np.nanargmax(segment))
        j = j_start + rel_idx
        p_peak = close[j]
        if not np.isfinite(p_peak) or p_peak <= 0:
            continue

        mult = p_peak / p0
        if mult < min_mult:
            continue

        # Anti "quick push and fade": require the run to hold meaningful gains
        # over the next month after peak.
        k_end = min(j + sustain_days, n - 1)
        if k_end > j:
            post_min = np.nanmin(close[j : k_end + 1])
            if not np.isfinite(post_min) or post_min < p0 * sustain_floor_mult:
                continue
            post_min_ret = (post_min / p0 - 1.0) * 100.0
        else:
            post_min_ret = (p_peak / p0 - 1.0) * 100.0

        ret_pct = (mult - 1.0) * 100.0
        if ret_pct > best_ret:
            best_ret = ret_pct
            best = BullRunWindow(
                ticker=ticker,
                start_date=dates[i],
                peak_date=dates[j],
                start_price=float(p0),
                peak_price=float(p_peak),
                return_pct=float(ret_pct),
                duration_days=(dates[j] - dates[i]).days,
                post30_min_return_pct=float(post_min_ret),
            )

    return best


def _load_earnings_catalysts(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if "earnings_date" in df.columns:
        df["earnings_date"] = pd.to_datetime(df["earnings_date"], errors="coerce").dt.date
    return df


def _is_rate_limited_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "too many requests" in text or "rate limit" in text


def _upgrade_scores(ticker: str, start_dt: date, end_dt: date) -> Tuple[int, int, str]:
    """
    Count upgrades/downgrades from yfinance analyst action history.
    """
    try:
        ud = yf.Ticker(ticker).upgrades_downgrades
    except Exception as exc:
        return 0, 0, "rate_limited" if _is_rate_limited_error(exc) else "provider_error"
    if ud is None or ud.empty:
        return 0, 0, "empty"
    df = ud.copy()
    df = df.reset_index().rename(columns={"GradeDate": "grade_date"})
    if "grade_date" not in df.columns:
        return 0, 0, "missing_grade_date"
    df["grade_date"] = pd.to_datetime(df["grade_date"], errors="coerce").dt.date
    df = df[(df["grade_date"] >= start_dt) & (df["grade_date"] <= end_dt)]
    if df.empty:
        return 0, 0, "no_events_in_window"

    def _is_upgrade(row) -> bool:
        action = str(row.get("Action", "")).lower()
        to_grade = str(row.get("ToGrade", "")).lower()
        if action in {"up", "init"}:
            return True
        positive_tokens = ("buy", "outperform", "overweight", "strong buy")
        return any(tok in to_grade for tok in positive_tokens)

    def _is_downgrade(row) -> bool:
        action = str(row.get("Action", "")).lower()
        to_grade = str(row.get("ToGrade", "")).lower()
        if action == "down":
            return True
        negative_tokens = ("sell", "underperform", "underweight")
        return any(tok in to_grade for tok in negative_tokens)

    upgrades = int(df.apply(_is_upgrade, axis=1).sum())
    downgrades = int(df.apply(_is_downgrade, axis=1).sum())
    return upgrades, downgrades, "ok"


def _news_keyword_signals(ticker: str, start_dt: date, end_dt: date) -> Tuple[int, int, int, str]:
    """
    Extract keyword counts from Yahoo news metadata (best-effort).
    """
    try:
        items = yf.Ticker(ticker).news or []
    except Exception as exc:
        return 0, 0, 0, "rate_limited" if _is_rate_limited_error(exc) else "provider_error"
    if not items:
        return 0, 0, 0, "empty"

    in_window = []
    for it in items:
        ts = it.get("providerPublishTime")
        if not ts:
            continue
        d = pd.to_datetime(ts, unit="s", errors="coerce")
        if pd.isna(d):
            continue
        d = d.date()
        if start_dt <= d <= end_dt:
            in_window.append(it)
    if not in_window:
        return 0, 0, 0, "no_events_in_window"

    opt_kw = ("option", "call volume", "put volume", "unusual options")
    up_kw = ("upgrade", "raised target", "price target", "outperform")
    cat_kw = ("earnings", "guidance", "contract", "approval", "acquisition", "launch")

    opt_hits = 0
    up_hits = 0
    cat_hits = 0
    for it in in_window:
        title = str(it.get("title", "")).lower()
        if any(k in title for k in opt_kw):
            opt_hits += 1
        if any(k in title for k in up_kw):
            up_hits += 1
        if any(k in title for k in cat_kw):
            cat_hits += 1
    return len(in_window), opt_hits, up_hits + cat_hits, "ok"


def _earnings_signals(
    ticker: str,
    start_dt: date,
    peak_dt: date,
    earnings_df: pd.DataFrame,
) -> Tuple[int, float, float, str]:
    """
    Build earnings event signals with source tagging.
    Priority:
      1) precomputed earnings_catalysts csv (includes reaction/surprise)
      2) yfinance earnings dates as event-count fallback
    """
    ev = pd.DataFrame()
    if not earnings_df.empty:
        ev = earnings_df[
            (earnings_df["ticker"].astype(str).str.upper() == ticker.upper())
            & (earnings_df["earnings_date"] >= (start_dt - timedelta(days=30)))
            & (earnings_df["earnings_date"] <= (peak_dt + timedelta(days=15)))
        ].copy()
    if not ev.empty:
        earnings_events = int(len(ev))
        max_earnings_5d = float(pd.to_numeric(ev.get("reaction_5d_pct"), errors="coerce").max())
        max_surprise = float(pd.to_numeric(ev.get("surprise_pct"), errors="coerce").max())
        return earnings_events, max_earnings_5d, max_surprise, "catalyst_csv"

    try:
        ed = yf.Ticker(ticker).get_earnings_dates(limit=24)
    except Exception as exc:
        return 0, 0.0, 0.0, "rate_limited" if _is_rate_limited_error(exc) else "provider_error"
    if ed is None or ed.empty:
        return 0, 0.0, 0.0, "empty"

    fallback = ed.reset_index()
    date_col = fallback.columns[0]
    fallback["earnings_date"] = pd.to_datetime(fallback[date_col], errors="coerce").dt.date
    fallback = fallback[
        (fallback["earnings_date"] >= (start_dt - timedelta(days=30)))
        & (fallback["earnings_date"] <= (peak_dt + timedelta(days=15)))
    ]
    if fallback.empty:
        return 0, 0.0, 0.0, "no_events_in_window"
    return int(len(fallback)), 0.0, 0.0, "yf_earnings_dates"


def _start_fundamental_gap_reason(dm: DataManager, ticker: str, start_dt: date, frow: Dict) -> Tuple[int, str]:
    fields = ["eps_yoy", "revenue_growth", "roe", "gross_margin", "debt_to_equity"]
    non_null = int(sum(pd.notna(frow.get(col)) for col in fields))
    if non_null == len(fields):
        return non_null, "complete"

    try:
        q = dm.get_quarterly_fundamentals(ticker, force_refresh=False)
    except Exception:
        return non_null, "quarterly_provider_error"
    if q is None or q.empty:
        return non_null, "no_quarterly_cache"

    vis = q.copy()
    if "disclosure_date" in vis.columns:
        vis["disclosure_date"] = pd.to_datetime(vis["disclosure_date"], errors="coerce").dt.date
        vis = vis[vis["disclosure_date"] <= start_dt]
    elif "report_date" in vis.columns:
        vis["report_date"] = pd.to_datetime(vis["report_date"], errors="coerce").dt.date
        vis = vis[vis["report_date"] <= start_dt]
    else:
        return non_null, "quarterly_missing_date_columns"

    if vis.empty:
        return non_null, "no_reports_visible_as_of_start"

    reasons: List[str] = []
    eps_hist = pd.to_numeric(vis.get("eps"), errors="coerce") if "eps" in vis.columns else pd.Series(dtype=float)
    rev_hist = pd.to_numeric(vis.get("revenue"), errors="coerce") if "revenue" in vis.columns else pd.Series(dtype=float)
    roe_hist = pd.to_numeric(vis.get("roe"), errors="coerce") if "roe" in vis.columns else pd.Series(dtype=float)
    gm_hist = pd.to_numeric(vis.get("gross_margin"), errors="coerce") if "gross_margin" in vis.columns else pd.Series(dtype=float)

    if pd.isna(frow.get("eps_yoy")):
        reasons.append("eps_history_too_sparse" if eps_hist.notna().sum() < 2 else "eps_growth_not_computable")
    if pd.isna(frow.get("revenue_growth")):
        reasons.append("revenue_history_too_sparse" if rev_hist.notna().sum() < 2 else "revenue_growth_not_computable")
    if pd.isna(frow.get("roe")) and roe_hist.notna().sum() == 0:
        reasons.append("missing_roe_quarterly")
    if pd.isna(frow.get("gross_margin")) and gm_hist.notna().sum() == 0:
        reasons.append("missing_gross_margin_quarterly")
    if pd.isna(frow.get("debt_to_equity")):
        reasons.append("missing_debt_to_equity_profile")

    if not reasons:
        reasons.append("provider_field_gap")
    return non_null, ";".join(reasons)


def _build_driver_summary(row: Dict) -> str:
    drivers: List[str] = []
    if row.get("max_earnings_5d_pct", 0) >= 15:
        drivers.append("strong earnings reaction")
    if row.get("max_surprise_pct", 0) >= 20:
        drivers.append("large earnings surprise")
    if row.get("upgrades_count", 0) >= 2 and row.get("upgrades_count", 0) > row.get("downgrades_count", 0):
        drivers.append("analyst upgrade trend")
    if row.get("max_volume_ratio_20d", 0) >= 2.0:
        drivers.append("abnormal volume accumulation")
    if row.get("eps_yoy_start") is not None and pd.notna(row.get("eps_yoy_start")) and float(row.get("eps_yoy_start")) >= 0.25:
        drivers.append("high EPS growth at run start")
    if row.get("revenue_growth_start") is not None and pd.notna(row.get("revenue_growth_start")) and float(row.get("revenue_growth_start")) >= 0.10:
        drivers.append("strong revenue growth at run start")
    if row.get("roe_start") is not None and pd.notna(row.get("roe_start")) and float(row.get("roe_start")) >= 0.10:
        drivers.append("quality ROE backdrop")
    if row.get("news_option_mentions", 0) > 0:
        drivers.append("options-related headline activity")
    if not drivers:
        drivers.append("sector/macro momentum with limited public catalyst signal")
    return "; ".join(drivers)


def _volume_signal(price_df: pd.DataFrame, start_dt: date, end_dt: date) -> float:
    df = _as_price_frame(price_df)
    df = df[(df["Date"] >= start_dt) & (df["Date"] <= end_dt)].copy()
    if df.empty:
        return 0.0
    vol = pd.to_numeric(df["Volume"], errors="coerce")
    roll = vol.rolling(20, min_periods=10).mean()
    ratio = vol / roll
    ratio = ratio.replace([np.inf, -np.inf], np.nan).dropna()
    if ratio.empty:
        return 0.0
    return float(ratio.max())


def analyze_bull_runs(args) -> pd.DataFrame:
    dm = DataManager()
    blacklist = DataBlacklistStore(dm).load()
    symbols = [s for s in load_all_listed_us_symbols() if s not in blacklist]
    if args.max_symbols > 0:
        symbols = symbols[: args.max_symbols]

    print(f"Universe: {len(symbols)} symbols (blacklist filtered)")
    period = f"{args.years}y"
    min_bars = max(120, args.min_duration_days + 20)
    prices: Dict[str, pd.DataFrame] = {}
    total = len(symbols)
    for i, sym in enumerate(symbols, 1):
        try:
            raw = dm._load_daily_from_db(str(sym).upper(), period)  # DB-only, no freshness gate.
            if raw is None or raw.empty or len(raw) < min_bars:
                continue
            frame = raw.reset_index().rename(columns={raw.index.name or "index": "Date"})
            frame["Date"] = pd.to_datetime(frame["Date"]).dt.date
            prices[str(sym).upper()] = frame
        except Exception:
            continue
        finally:
            if i % 500 == 0 or i == total:
                print(f"  [db-cache] {i}/{total} loaded={len(prices)}")

    print(f"Loaded price frames: {len(prices)}")
    runs: List[BullRunWindow] = []
    for i, (ticker, frame) in enumerate(prices.items(), 1):
        try:
            run = detect_best_bull_run(
                ticker=ticker,
                price_df=frame,
                min_return_pct=args.min_run_return_pct,
                min_duration_days=args.min_duration_days,
                max_duration_days=args.max_duration_days,
                sustain_days=args.sustain_days,
                sustain_min_return_pct=args.sustain_min_return_pct,
                years=args.years,
            )
            if run is not None:
                runs.append(run)
        except Exception:
            pass
        if i % 1000 == 0:
            print(f"  [scan] {i}/{len(prices)} found={len(runs)}")

    if not runs:
        return pd.DataFrame()

    runs_df = pd.DataFrame([r.__dict__ for r in runs]).sort_values("return_pct", ascending=False)
    # Tradability and sanity filters.
    runs_df = runs_df[runs_df["ticker"].apply(_is_common_stock_ticker)].copy()
    runs_df = runs_df[runs_df["start_price"] >= float(args.min_start_price)].copy()
    if runs_df.empty:
        return pd.DataFrame()

    # Add liquidity signal from run window and filter illiquid spikes.
    liq_rows = []
    for _, rr in runs_df.iterrows():
        t = str(rr["ticker"])
        start_dt = pd.to_datetime(rr["start_date"]).date()
        peak_dt = pd.to_datetime(rr["peak_date"]).date()
        frame = _as_price_frame(prices[t])
        w = frame[(frame["Date"] >= start_dt) & (frame["Date"] <= peak_dt)].copy()
        if w.empty:
            avg_dv = 0.0
        else:
            close = pd.to_numeric(w["Close"], errors="coerce")
            vol = pd.to_numeric(w["Volume"], errors="coerce")
            avg_dv = float((close * vol).dropna().mean() or 0.0)
        liq_rows.append((t, avg_dv))
    liq_df = pd.DataFrame(liq_rows, columns=["ticker", "avg_dollar_volume_run"])
    runs_df = runs_df.merge(liq_df, on="ticker", how="left")
    runs_df = runs_df[runs_df["avg_dollar_volume_run"] >= float(args.min_avg_dollar_volume)].copy()
    if runs_df.empty:
        return pd.DataFrame()

    # Market-cap filter (current cap as a tradability proxy).
    cap_map: Dict[str, float] = {}
    for t in runs_df["ticker"].unique():
        prof = dm.get_fundamentals(str(t), validate=False, strict=False)
        cap = pd.to_numeric(pd.Series([prof.get("market_cap")]), errors="coerce").iloc[0]
        cap_map[str(t)] = float(cap) if pd.notna(cap) else np.nan
    runs_df["market_cap_now"] = runs_df["ticker"].map(cap_map)
    runs_df = runs_df[runs_df["market_cap_now"].fillna(0.0) >= float(args.min_market_cap)].copy()
    if runs_df.empty:
        return pd.DataFrame()

    if args.top_n > 0:
        runs_df = runs_df.head(args.top_n).copy()

    fund_svc = FundamentalSnapshotService(dm)
    earnings_df = _load_earnings_catalysts(Path(args.earnings_catalysts_csv))

    enriched_rows: List[Dict] = []
    for _, rr in runs_df.iterrows():
        ticker = str(rr["ticker"])
        start_dt = pd.to_datetime(rr["start_date"]).date()
        peak_dt = pd.to_datetime(rr["peak_date"]).date()

        # Fundamental context at run start.
        f = fund_svc.load_for_tickers(
            [ticker],
            as_of_date=start_dt,
            use_persistent_cache=True,
            refresh_persistent_cache=False,
            progress_every=9999,
        )
        frow = f.iloc[0].to_dict() if not f.empty else {}
        current_profile = dm.get_fundamentals(ticker, validate=False, strict=False)

        earnings_events, max_earnings_5d, max_surprise, earnings_source = _earnings_signals(
            ticker=ticker,
            start_dt=start_dt,
            peak_dt=peak_dt,
            earnings_df=earnings_df,
        )
        upgrades_count, downgrades_count, upgrades_status = _upgrade_scores(ticker, start_dt, peak_dt)
        news_count, opt_hits, catalyst_hits, news_status = _news_keyword_signals(ticker, start_dt, peak_dt)
        start_non_null, start_gap_reason = _start_fundamental_gap_reason(dm, ticker, start_dt, frow)

        row = {
            "ticker": ticker,
            "start_date": start_dt,
            "peak_date": peak_dt,
            "duration_days": int(rr["duration_days"]),
            "run_return_pct": float(rr["return_pct"]),
            "start_price": float(rr["start_price"]),
            "peak_price": float(rr["peak_price"]),
            "post30_min_return_pct": float(rr["post30_min_return_pct"]),
            "market_cap_now": float(rr.get("market_cap_now", np.nan)),
            "avg_dollar_volume_run": float(rr.get("avg_dollar_volume_run", 0.0)),
            "eps_yoy_start": frow.get("eps_yoy"),
            "revenue_growth_start": frow.get("revenue_growth"),
            "roe_start": frow.get("roe"),
            "gross_margin_start": frow.get("gross_margin"),
            "debt_to_equity_start": frow.get("debt_to_equity"),
            "start_fundamentals_non_null": start_non_null,
            "start_fundamentals_gap_reason": start_gap_reason,
            "eps_yoy_current": current_profile.get("earnings_growth"),
            "revenue_growth_current": current_profile.get("revenue_growth"),
            "roe_current": current_profile.get("roe"),
            "gross_margin_current": current_profile.get("gross_margin"),
            "debt_to_equity_current": current_profile.get("debt_to_equity"),
            "max_volume_ratio_20d": _volume_signal(prices[ticker], start_dt, peak_dt),
            "earnings_events": earnings_events,
            "max_earnings_5d_pct": max_earnings_5d,
            "max_surprise_pct": max_surprise,
            "earnings_signal_source": earnings_source,
            "upgrades_count": upgrades_count,
            "downgrades_count": downgrades_count,
            "upgrades_signal_status": upgrades_status,
            "news_events": news_count,
            "news_option_mentions": opt_hits,
            "news_catalyst_mentions": catalyst_hits,
            "news_signal_status": news_status,
        }
        row["driver_summary"] = _build_driver_summary(row)
        enriched_rows.append(row)

    out = pd.DataFrame(enriched_rows).sort_values("run_return_pct", ascending=False)
    return out


def write_markdown_report(df: pd.DataFrame, out_path: Path, args) -> None:
    lines = []
    lines.append("# 2Y Sustained Bull Runs (200%+)")
    lines.append("")
    lines.append(
        f"- Universe years: `{args.years}` | Min run: `{args.min_run_return_pct:.0f}%` | "
        f"Min duration: `{args.min_duration_days}` days | Sustain window: `{args.sustain_days}` days"
    )
    lines.append(f"- Candidates found: `{len(df)}`")
    lines.append("")
    lines.append("## Top Runs")
    lines.append("")
    show = df.head(min(20, len(df)))
    cols = ["ticker", "start_date", "peak_date", "duration_days", "run_return_pct", "driver_summary"]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("|" + "|".join(["---"] * len(cols)) + "|")
    for _, r in show.iterrows():
        vals = [str(r[c]) for c in cols]
        lines.append("| " + " | ".join(vals) + " |")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- `news_option_mentions` uses headline keyword matching and is best-effort, not a full OPRA history.")
    lines.append("- For full historical options-volume attribution, integrate a dedicated options history data provider.")
    lines.append("")
    lines.append("## Data Coverage")
    lines.append("")
    cov_fields = [
        "eps_yoy_start",
        "revenue_growth_start",
        "roe_start",
        "gross_margin_start",
        "debt_to_equity_start",
        "earnings_events",
        "upgrades_count",
        "news_events",
    ]
    for c in cov_fields:
        if c not in df.columns:
            continue
        series = pd.to_numeric(df[c], errors="coerce")
        non_null = int(series.notna().sum())
        zero = int((series.fillna(0.0) == 0.0).sum())
        lines.append(f"- `{c}`: non-null `{non_null}/{len(df)}`, zero-value `{zero}/{len(df)}`")
    if "start_fundamentals_gap_reason" in df.columns:
        top_gaps = (
            df["start_fundamentals_gap_reason"]
            .fillna("unknown")
            .astype(str)
            .value_counts()
            .head(5)
        )
        lines.append("")
        lines.append("Top start-fundamental gap reasons:")
        for k, v in top_gaps.items():
            lines.append(f"- `{k}`: `{int(v)}`")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze sustained 200%+ bull runs in the last N years.")
    parser.add_argument("--years", type=int, default=2, help="Lookback years")
    parser.add_argument("--min-run-return-pct", type=float, default=200.0, help="Minimum run return percent")
    parser.add_argument("--min-duration-days", type=int, default=30, help="Minimum run duration (calendar days)")
    parser.add_argument("--max-duration-days", type=int, default=270, help="Maximum run duration (calendar days)")
    parser.add_argument("--sustain-days", type=int, default=30, help="Post-peak sustain window days")
    parser.add_argument(
        "--sustain-min-return-pct",
        type=float,
        default=50.0,
        help="Minimum retained return after peak over sustain window",
    )
    parser.add_argument("--max-symbols", type=int, default=0, help="Cap symbols scanned (0 = all)")
    parser.add_argument("--top-n", type=int, default=30, help="Top runs to keep in output")
    parser.add_argument("--min-start-price", type=float, default=2.0, help="Minimum run start price")
    parser.add_argument(
        "--min-avg-dollar-volume",
        type=float,
        default=2_000_000.0,
        help="Minimum average dollar volume during run",
    )
    parser.add_argument(
        "--min-market-cap",
        type=float,
        default=100_000_000.0,
        help="Minimum current market cap filter",
    )
    parser.add_argument(
        "--earnings-catalysts-csv",
        type=str,
        default="results/picker/earnings_catalysts.csv",
        help="Path to earnings catalyst csv",
    )
    parser.add_argument("--output-csv", type=str, default="results/picker/bull_runs_2y_analysis.csv")
    parser.add_argument("--output-md", type=str, default="results/picker/bull_runs_2y_report.md")
    args = parser.parse_args()

    out_df = analyze_bull_runs(args)
    if out_df.empty:
        print("No qualifying sustained 200%+ runs found.")
        return

    out_csv = Path(args.output_csv)
    out_md = Path(args.output_md)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    out_df.to_csv(out_csv, index=False)
    write_markdown_report(out_df, out_md, args)
    print(f"Saved CSV: {out_csv}")
    print(f"Saved report: {out_md}")
    print(out_df.head(15).to_string(index=False))


if __name__ == "__main__":
    main()
