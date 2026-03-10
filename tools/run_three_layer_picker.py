#!/usr/bin/env python3
"""
Three-layer stock picker — thin runner.

Reads config/picker_config.yaml and orchestrates:
  src.data_manager          — price cache / ingestion
  src.universe.filters      — technical / fundamental / quality filters
  src.picker.fundamentals_service — fundamental enrichment
  src.picker.post_processor — dedup + sector diversification
  src.picker.lookback       — historical comparison (optional)

Usage:
    python tools/run_three_layer_picker.py                  # default config
    python tools/run_three_layer_picker.py --config custom.yaml
    python tools/run_three_layer_picker.py --lookback       # enable lookback
    python tools/run_three_layer_picker.py --fundamental-only   # skip technical layer (fundamentals only)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

# Unbuffered stdout so intermediate progress prints appear immediately when piped or run non-interactively
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.blacklist_store import DataBlacklistStore
from src.data_manager import DataManager
from src.data.providers.yfinance_provider import YFinanceProvider
from src.picker.fundamentals_service import FundamentalSnapshotService
from src.picker.incremental_refresh import run as run_incremental_earnings_refresh
from src.picker.lookback import compare_across_dates, run_picker_at_date
from src.picker.post_processor import deduplicate_share_classes, diversify_by_industry
from src.picker.price_loader import stage_load_prices
from src.universe.filters import (
    build_technical_base_from_prices,
    fundamental_filter,
    moat_quality_filter,
    technical_filter,
)
from src.universe.listed_symbols import load_all_listed_us_symbols


# ── Config ──────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = ROOT / "config" / "picker_config.yaml"


def load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _fetch_industry_map(tickers: list[str]) -> Dict[str, str]:
    """Lightweight: sector/industry from Yahoo .info."""
    import yfinance as yf
    out = {}
    for t in tickers:
        try:
            info = yf.Ticker(t.replace(".", "-")).info
            out[t] = info.get("industry", "Unknown")
        except Exception:
            out[t] = "Unknown"
    return out


def _stable_unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen = set()
    for raw in values:
        v = str(raw).upper().strip()
        if not v or v in seen:
            continue
        out.append(v)
        seen.add(v)
    return out


def _run_cache_preflight(cfg: dict, symbols: list[str], out_dir: Path) -> None:
    """
    Run cache health gate automatically before stock picking.

    This is fail-closed by default: if health checks fail, picker aborts.
    """
    pre = cfg.get("preflight", {})
    if not bool(pre.get("enabled", True)):
        print("[preflight] disabled by config")
        return

    output_path = Path(pre.get("output", str(out_dir / "cache_health_preflight.json")))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    symbols_file = out_dir / ".preflight_symbols.txt"
    symbols_file.write_text("\n".join(str(s).upper() for s in symbols), encoding="utf-8")

    data_cfg = cfg.get("data", {})
    tech_cfg = cfg.get("technical", {})
    period = str(data_cfg.get("period", "2y")).lower().strip()
    period_days_map = {
        "1mo": 30,
        "3mo": 90,
        "6mo": 180,
        "1y": 365,
        "2y": 730,
        "3y": 1095,
        "5y": 1825,
        "10y": 3650,
        "max": 36500,
    }
    period_days = int(pre.get("period_days", period_days_map.get(period, 730)))
    min_bars = int(pre.get("min_bars", tech_cfg.get("min_bars", 260)))

    cmd = [
        sys.executable,
        str(ROOT / "tools" / "cache_health_check.py"),
        "--symbols-file",
        str(symbols_file),
        "--period-days",
        str(period_days),
        "--min-bars",
        str(min_bars),
        "--max-stale-days",
        str(int(pre.get("max_stale_days", 7))),
        "--max-invalid-rows",
        str(int(pre.get("max_invalid_rows", 0))),
        "--max-missing-pct",
        str(float(pre.get("max_missing_pct", 0.02))),
        "--min-fresh-pct",
        str(float(pre.get("min_fresh_pct", 0.98))),
        "--min-minbars-pct",
        str(float(pre.get("min_minbars_pct", 0.80))),
        "--output",
        str(output_path),
    ]

    if bool(pre.get("repair_invalid_first", True)):
        cmd.append("--repair-invalid-first")
    if bool(pre.get("include_quarterly", False)):
        cmd.extend(
            [
                "--include-quarterly",
                "--min-quarterly-coverage-pct",
                str(float(pre.get("min_quarterly_coverage_pct", 0.50))),
            ]
        )
    if bool(pre.get("strict", True)):
        cmd.append("--strict")

    print("\n[preflight] running cache health gate")
    print(f"[preflight] cmd: {' '.join(cmd)}")
    rc = subprocess.run(cmd, cwd=str(ROOT)).returncode

    # Clean up temporary file; report JSON is persisted.
    try:
        symbols_file.unlink(missing_ok=True)
    except Exception:
        pass

    fail_action = str(pre.get("on_fail", "abort")).lower().strip()
    if rc != 0:
        msg = f"[preflight] cache health failed with exit code {rc}; report={output_path}"
        if fail_action == "warn":
            print(f"{msg} (continuing due to on_fail=warn)")
            return
        raise RuntimeError(msg)

    print(f"[preflight] passed; report={output_path}")


def _run_auto_earnings_refresh(
    cfg: dict,
    technical_tickers: list[str],
    out_dir: Path,
    config_path: Path,
) -> None:
    """
    Automatically run incremental earnings refresh before fundamentals stage.

    This keeps point-in-time fundamentals cache current (e.g., yesterday/today
    disclosures) without requiring manual commands.
    """
    er_cfg = cfg.get("earnings_refresh", {})
    if not bool(er_cfg.get("enabled", True)):
        print("[earnings-refresh] disabled by config")
        return

    tickers = _stable_unique(technical_tickers)
    if not tickers:
        print("[earnings-refresh] no technical candidates; skipping")
        return

    symbols_file = out_dir / ".earnings_refresh_symbols.txt"
    symbols_file.write_text("\n".join(tickers), encoding="utf-8")

    months_cfg = er_cfg.get("months_back")
    if months_cfg is None:
        lb_months = cfg.get("lookback", {}).get("months_back", [3, 6, 9, 12])
        months_cfg = ",".join(str(m) for m in lb_months)
    months_str = str(months_cfg).strip() or "3,6,9,12"

    args = argparse.Namespace(
        config=str(config_path),
        symbols=[],
        symbols_file=str(symbols_file),
        technical_universe_csv=str(out_dir / "three_layer_after_technical.csv"),
        max_symbols=0,
        force_refresh_all_candidates=bool(er_cfg.get("force_refresh_all_candidates", False)),
        months_back=months_str,
        skip_validation=bool(er_cfg.get("skip_validation", True)),
        skip_scanner=bool(er_cfg.get("skip_scanner", True)),
        validation_windows_csv=str(er_cfg.get("validation_windows_csv", "results/picker/walkforward_windows_report.csv")),
        validation_trades_csv=str(er_cfg.get("validation_trades_csv", "results/picker/walkforward_trades_report.csv")),
        validation_md=str(er_cfg.get("validation_md", "results/picker/walkforward_report.md")),
        scanner_max_tickers=int(er_cfg.get("scanner_max_tickers", 200)),
        scanner_output_dir=str(er_cfg.get("scanner_output_dir", "results")),
        scanner_output=str(er_cfg.get("scanner_output", "")),
        audit_dir=str(er_cfg.get("audit_dir", "results/picker")),
        progress_every=max(1, int(er_cfg.get("progress_every", 50))),
        dry_run=bool(er_cfg.get("dry_run", False)),
        calendar_recent_days=int(er_cfg.get("calendar_recent_days", 2)),
    )

    print("\n[earnings-refresh] running incremental earnings refresh")
    print(
        "[earnings-refresh] "
        f"tickers={len(tickers)} months_back={months_str} "
        f"calendar_recent_days={args.calendar_recent_days} "
        f"skip_validation={args.skip_validation} skip_scanner={args.skip_scanner}"
    )
    rc = run_incremental_earnings_refresh(args)

    try:
        symbols_file.unlink(missing_ok=True)
    except Exception:
        pass

    fail_action = str(er_cfg.get("on_fail", "abort")).lower().strip()
    if rc != 0:
        msg = f"[earnings-refresh] incremental refresh failed with exit code {rc}"
        if fail_action == "warn":
            print(f"{msg} (continuing due to on_fail=warn)")
            return
        raise RuntimeError(msg)

    print("[earnings-refresh] completed")


# ── Pipeline stages ─────────────────────────────────────────────────────────
# stage_load_prices imported from src.picker.price_loader


def stage_technical(prices, spy_df, cfg) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Stage D: technical base + filter."""
    tech_cfg = cfg.get("technical", {})
    spy_close = spy_df["Close"]
    spy_ret = float(spy_close.iloc[-1] / spy_close.iloc[-252] - 1)

    base = build_technical_base_from_prices(
        prices, benchmark_return_252=spy_ret,
        near_52w_ratio=tech_cfg.get("near_52w_ratio", 0.85),
        min_bars=tech_cfg.get("min_bars", 260),
        min_bars_recent_ipo=tech_cfg.get("min_bars_recent_ipo", 130),
    )
    tech = technical_filter(base, rs_threshold=tech_cfg.get("rs_threshold", 80.0))
    return base, tech


def stage_fundamentals(tech_df, fund_svc, cfg, out_dir) -> pd.DataFrame:
    """Stage E: fundamental enrichment + filter + quality filter + scoring."""
    fund_cfg = cfg.get("fundamental", {})
    qual_cfg = cfg.get("quality", {})
    score_cfg = cfg.get("scoring", {})

    raw_path = out_dir / "three_layer_fund_snapshot_raw.csv"
    fund_df = fund_svc.load_for_tickers(
        tech_df["ticker"].tolist(),
        existing_snapshot_path=raw_path,
        progress_every=50,
    )
    fund_df.to_csv(raw_path, index=False)

    merged = tech_df.merge(fund_df, on="ticker", how="left")
    merged["gm_rank"] = (pd.to_numeric(merged["gross_margin"], errors="coerce").rank(pct=True) * 100).round(2)

    # Data quality report
    for col in ["eps_yoy", "revenue_growth", "roe", "gm_rank"]:
        nn = pd.to_numeric(merged[col], errors="coerce").notna().sum()
        print(f"    {col}: {nn}/{len(merged)} non-null")

    before = len(merged)
    merged = merged.dropna(subset=["eps_yoy", "revenue_growth", "roe", "gm_rank"])
    if before - len(merged):
        print(f"    dropna removed {before - len(merged)} rows")

    fund = fundamental_filter(
        merged,
        min_eps_yoy=fund_cfg.get("min_eps_yoy", 0.25),
        min_revenue_growth=fund_cfg.get("min_revenue_growth", 0.10),
        min_revenue_acceleration=fund_cfg.get("min_revenue_acceleration"),
        require_surprise_non_negative=fund_cfg.get("require_surprise_non_negative", False),
    )
    quality = moat_quality_filter(
        fund,
        min_roe=qual_cfg.get("min_roe", 0.10),
        min_gm_rank=qual_cfg.get("min_gm_rank", 60.0),
        max_debt_to_equity=qual_cfg.get("max_debt_to_equity", 1.2),
    )

    if not quality.empty:
        quality = quality.copy()
        quality["composite"] = (
            quality["rs_rank"] * score_cfg.get("rs_rank_weight", 0.35)
            + (quality["eps_yoy"] * 100).clip(-100, 200) * score_cfg.get("eps_yoy_weight", 0.20)
            + (quality["revenue_growth"] * 100).clip(-100, 200) * score_cfg.get("revenue_growth_weight", 0.20)
            + quality["gm_rank"] * score_cfg.get("gm_rank_weight", 0.15)
            + (quality["roe"] * 100).clip(-100, 100) * score_cfg.get("roe_weight", 0.10)
        )
        quality = quality.sort_values("composite", ascending=False)

    return quality


def stage_post_process(picks_df, cfg) -> pd.DataFrame:
    """Stage F: dedup + sector diversification."""
    pp_cfg = cfg.get("post_process", {})
    result = picks_df

    if pp_cfg.get("deduplicate_share_classes", True) and not result.empty:
        result, removed = deduplicate_share_classes(result)
        if removed:
            print(f"  dedup removed: {removed}")

    max_per_ind = pp_cfg.get("max_per_industry", 2)
    if max_per_ind and not result.empty:
        ind_map = _fetch_industry_map(result["ticker"].tolist())
        result, removed = diversify_by_industry(result, ind_map, max_per_industry=max_per_ind)
        if removed:
            print(f"  sector-cap removed {len(removed)} stocks")
            for ticker, ind in removed[:10]:
                print(f"    {ticker} ({ind})")

    return result.head(pp_cfg.get("top_n", 25))


def stage_lookback(
    prices,
    spy_df,
    fund_svc,
    cfg,
    current_picks,
    out_dir: Path,
    refresh_historical_cache: bool = False,
) -> pd.DataFrame:
    """Stage G: run picker at historical dates and compare."""
    lb_cfg = cfg.get("lookback", {})
    months_list = lb_cfg.get("months_back", [3, 6, 9])
    today = date.today()

    results = {"now": current_picks}
    current_picks.to_csv(out_dir / "lookback_now.csv", index=False)
    for m in months_list:
        as_of = today - timedelta(days=m * 30)
        label = f"{m}m_ago"
        print(f"\n  Running lookback at {as_of} ({label})")
        picks = run_picker_at_date(
            prices,
            spy_df,
            fund_svc,
            cfg,
            as_of_date=as_of,
            refresh_historical_cache=refresh_historical_cache,
        )
        picks = stage_post_process(picks, cfg)
        results[label] = picks
        picks.to_csv(out_dir / f"lookback_{label}.csv", index=False)

    comparison = compare_across_dates(results, current_prices=prices)
    return comparison


# ── Main ────────────────────────────────────────────────────────────────────

def run(
    cfg: dict,
    enable_lookback: bool = False,
    rebuild_historical_cache: bool = False,
    fundamental_only: bool = False,
    config_path: Path = DEFAULT_CONFIG,
) -> None:
    t_global = time.time()
    data_cfg = cfg.get("data", {})
    out_cfg = cfg.get("output", {})
    out_dir = Path(out_cfg.get("dir", "results/picker"))
    out_dir.mkdir(parents=True, exist_ok=True)

    dm = DataManager()
    blacklist_store = DataBlacklistStore(dm)
    fund_svc = FundamentalSnapshotService(dm)

    # Load universe
    t0 = time.time()
    print("\n[Universe] Loading listed symbols...")
    symbols = load_all_listed_us_symbols()
    max_sym = data_cfg.get("max_symbols", 0)
    if max_sym > 0:
        symbols = symbols[:max_sym]
    blacklist = blacklist_store.load()
    symbols = [s for s in symbols if s not in blacklist]
    print(f"  [Universe] {len(symbols)} symbols ({len(blacklist)} blacklisted) ({time.time()-t0:.1f}s)")

    # Embedded DQ gate: run automatically so users do not need manual pre-checks.
    # Skip preflight in fundamental-only mode (no technical filter; universe may be large).
    if not fundamental_only:
        _run_cache_preflight(cfg, symbols, out_dir)

    # A-C: Price ingestion (uses cache first, then Yahoo for missing)
    t1 = time.time()
    print("\n[Stage 1/3] Price load (cache -> Yahoo -> fallback)")
    prices = stage_load_prices(dm, symbols, cfg)
    print(f"  [A-C] Price load: {len(prices)}/{len(symbols)} symbols ({time.time()-t1:.1f}s)")

    # D: Technical (skipped in fundamental-only mode)
    if fundamental_only:
        # Pure fundamental mode: no RS, near 52w, or other technical checks.
        # Use all symbols with price data; rs_rank=50 (neutral) for composite scoring.
        tech = pd.DataFrame({"ticker": list(prices.keys())})
        tech["rs_rank"] = 50.0
        base = tech.copy()
        print("\n[D] Fundamental-only mode: skipping technical filter")
        print(f"  symbols with price data={len(tech)}")
    else:
        # SPY benchmark (needed for technical filter)
        spy = prices.get("SPY")
        if spy is None or spy.empty:
            spy = dm.get_daily_data("SPY", period=data_cfg.get("period", "2y"))
        if spy is None or spy.empty:
            raise RuntimeError("SPY data unavailable")
        if "Date" in spy.columns:
            spy = spy.set_index(pd.to_datetime(spy["Date"]))
        elif not isinstance(spy.index, pd.DatetimeIndex):
            spy.index = pd.to_datetime(spy.index)

        print("\n[D] Technical filter")
        base, tech = stage_technical(prices, spy, cfg)
        print(f"  base={len(base)} technical={len(tech)}")

    tech.to_csv(out_dir / "three_layer_after_technical.csv", index=False)

    # D.5: Auto earnings refresh before fundamentals (no manual command required).
    t2 = time.time()
    _run_auto_earnings_refresh(
        cfg=cfg,
        technical_tickers=tech["ticker"].astype(str).tolist() if "ticker" in tech.columns else [],
        out_dir=out_dir,
        config_path=config_path,
    )
    print(f"  [D.5] Earnings refresh: {time.time()-t2:.1f}s")

    # E: Fundamentals
    t3 = time.time()
    print("\n[Stage 2/3] Fundamental enrichment + filter")
    quality = stage_fundamentals(tech, fund_svc, cfg, out_dir)
    print(f"  quality picks={len(quality)} ({time.time()-t3:.1f}s)")

    # F: Post-process
    print("\n[Stage 3/3] Post-process (dedup + diversify)")
    final = stage_post_process(quality, cfg)
    print(f"  final diversified={len(final)}")

    # Save all stages
    base.to_csv(out_dir / "three_layer_base.csv", index=False)
    quality.to_csv(out_dir / "three_layer_picks_raw.csv", index=False)
    final.to_csv(out_dir / "three_layer_picks.csv", index=False)

    # Summary
    print(f"\n{'='*80}")
    print(f"PICKS ({len(final)} stocks, {time.time()-t_global:.0f}s)")
    print(f"{'='*80}")
    for i, (_, r) in enumerate(final.iterrows(), 1):
        print(
            f"  {i:>2}. {r['ticker']:>6}  RS {r['rs_rank']:5.1f}  "
            f"EPS {r['eps_yoy']*100:7.1f}%  Rev {r['revenue_growth']*100:6.1f}%  "
            f"ROE {r['roe']*100:5.1f}%  Score {r.get('composite', 0):6.1f}"
        )

    # G: Lookback (optional; not supported in fundamental-only mode)
    if (enable_lookback or cfg.get("lookback", {}).get("enabled", False)) and not fundamental_only:
        print("\n[G] Historical lookback comparison")
        refresh_historical = rebuild_historical_cache or bool(cfg.get("lookback", {}).get("refresh_snapshot_cache", False))
        comparison = stage_lookback(
            prices,
            spy,
            fund_svc,
            cfg,
            final,
            out_dir,
            refresh_historical_cache=refresh_historical,
        )
        comparison.to_csv(out_dir / "lookback_comparison.csv", index=False)

        persistent = comparison[comparison["appearances"] >= 2]
        print(f"\n  Stocks appearing in 2+ time windows: {len(persistent)}")
        print(f"  {'Ticker':>6} {'#':>2} {'Dates':<40} {'Gain':>7} {'Score':>6}")
        for _, r in persistent.head(20).iterrows():
            gain = f"{r['gain_pct']:+.1f}%" if pd.notna(r.get("gain_pct")) else "N/A"
            print(f"  {r['ticker']:>6} {r['appearances']:>2} {r['dates']:<40} {gain:>7} {r['best_score']:6.1f}")

    print(f"\nResults saved to {out_dir}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Three-layer stock picker")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Config YAML path")
    parser.add_argument("--lookback", action="store_true", help="Enable historical lookback")
    parser.add_argument(
        "--fundamental-only",
        action="store_true",
        help="Skip technical layer (RS, near 52w); output symbols meeting fundamental criteria only",
    )
    parser.add_argument(
        "--rebuild-historical-cache",
        action="store_true",
        help="Recompute and overwrite historical fundamental snapshot cache files",
    )
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    run(
        cfg,
        enable_lookback=args.lookback,
        rebuild_historical_cache=args.rebuild_historical_cache,
        fundamental_only=args.fundamental_only,
        config_path=Path(args.config),
    )
