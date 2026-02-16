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
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
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
from src.data.providers.resilient import fallback_price_fetch
from src.data.providers.yfinance_provider import YFinanceProvider, batch_download_daily_ohlcv
from src.picker.fundamentals_service import FundamentalSnapshotService
from src.picker.incremental_refresh import run as run_incremental_earnings_refresh
from src.picker.lookback import compare_across_dates, run_picker_at_date
from src.picker.post_processor import deduplicate_share_classes, diversify_by_industry
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

def _parse_ibkr_endpoint(endpoint: str):
    try:
        host, port = endpoint.split(":")
        return host.strip(), int(port.strip())
    except Exception:
        return "127.0.0.1", 4002


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

def stage_load_prices(dm, symbols, cfg) -> Dict[str, pd.DataFrame]:
    """Stage A-C: load cached prices, Yahoo bulk, fallback chain."""
    data_cfg = cfg.get("data", {})
    ibkr_cfg = cfg.get("ibkr", {})
    period = data_cfg.get("period", "2y")

    # A: cache
    print("\n[A] Load cached prices")
    t0 = time.time()
    prices = dm.load_cached_prices(
        symbols, period=period, min_bars=60,
        progress_hook=lambda i, total, loaded: (
            print(f"  [cache] {i}/{total} loaded={loaded}") if (i % 500 == 0 or i == total) else None
        ),
    )
    missing = [s for s in symbols if s not in prices]
    print(f"  cache={len(prices)} missing={len(missing)} ({time.time()-t0:.1f}s)")

    # B: Yahoo bulk
    if missing:
        print("\n[B] Yahoo bulk fetch")
        t1 = time.time()
        yahoo = batch_download_daily_ohlcv(
            symbols=missing, period=period,
            batch_size=data_cfg.get("batch_size", 200),
            threads=data_cfg.get("yf_threads", False),
            progress_hook=lambda done, total: print(f"  [yahoo] {done}/{total}"),
        )
        prices.update(yahoo)
        dm.persist_prices(yahoo, progress_hook=lambda i, t: (
            print(f"  [persist] {i}/{t}") if (i % 500 == 0 or i == t) else None
        ))
        missing = [s for s in symbols if s not in prices]
        print(f"  yahoo={len(yahoo)} missing={len(missing)} ({time.time()-t1:.1f}s)")

    # C: Fallback (IBKR GCloud -> local -> Stooq)
    if missing:
        print(f"\n[C] Fallback for {len(missing)} remaining")
        t2 = time.time()
        ib_host, ib_port = _parse_ibkr_endpoint(ibkr_cfg.get("local_gateway_url", "127.0.0.1:4002"))
        fallback = {}
        for i, sym in enumerate(missing, 1):
            data, _ = fallback_price_fetch(
                symbol=sym, period=period,
                gcloud_base_url=ibkr_cfg.get("gcloud_trade_api_url", "").rstrip("/"),
                gcloud_api_key=ibkr_cfg.get("gcloud_trade_api_key", ""),
                local_ibkr_host=ib_host, local_ibkr_port=ib_port,
                local_ibkr_client_id=ibkr_cfg.get("local_client_id", 99),
            )
            if data is not None and not data.empty:
                prices[sym] = data
                fallback[sym] = data
            if i % 100 == 0 or i == len(missing):
                print(f"  [fallback] {i}/{len(missing)}")
        if fallback:
            dm.persist_prices(fallback)
        print(f"  resolved={len(fallback)} ({time.time()-t2:.1f}s)")

    return prices


def stage_technical(prices, spy_df, cfg) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Stage D: technical base + filter."""
    tech_cfg = cfg.get("technical", {})
    spy_close = spy_df["Close"]
    spy_ret = float(spy_close.iloc[-1] / spy_close.iloc[-252] - 1)

    base = build_technical_base_from_prices(
        prices, benchmark_return_252=spy_ret,
        near_52w_ratio=tech_cfg.get("near_52w_ratio", 0.85),
        min_bars=tech_cfg.get("min_bars", 260),
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
    symbols = load_all_listed_us_symbols()
    max_sym = data_cfg.get("max_symbols", 0)
    if max_sym > 0:
        symbols = symbols[:max_sym]
    blacklist = blacklist_store.load()
    symbols = [s for s in symbols if s not in blacklist]
    print(f"Universe: {len(symbols)} symbols ({len(blacklist)} blacklisted)")

    # Embedded DQ gate: run automatically so users do not need manual pre-checks.
    _run_cache_preflight(cfg, symbols, out_dir)

    # A-C: Price ingestion
    prices = stage_load_prices(dm, symbols, cfg)

    # SPY benchmark
    spy = prices.get("SPY")
    if spy is None or spy.empty:
        spy = dm.get_daily_data("SPY", period=data_cfg.get("period", "2y"))
    if spy is None or spy.empty:
        raise RuntimeError("SPY data unavailable")
    if "Date" in spy.columns:
        spy = spy.set_index(pd.to_datetime(spy["Date"]))
    elif not isinstance(spy.index, pd.DatetimeIndex):
        spy.index = pd.to_datetime(spy.index)

    # D: Technical
    print("\n[D] Technical filter")
    base, tech = stage_technical(prices, spy, cfg)
    print(f"  base={len(base)} technical={len(tech)}")
    tech.to_csv(out_dir / "three_layer_after_technical.csv", index=False)

    # D.5: Auto earnings refresh before fundamentals (no manual command required).
    _run_auto_earnings_refresh(
        cfg=cfg,
        technical_tickers=tech["ticker"].astype(str).tolist() if "ticker" in tech.columns else [],
        out_dir=out_dir,
        config_path=config_path,
    )

    # E: Fundamentals
    print("\n[E] Fundamental enrichment + filter")
    quality = stage_fundamentals(tech, fund_svc, cfg, out_dir)
    print(f"  quality picks={len(quality)}")

    # F: Post-process
    print("\n[F] Post-process (dedup + diversify)")
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

    # G: Lookback (optional)
    if enable_lookback or cfg.get("lookback", {}).get("enabled", False):
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
        config_path=Path(args.config),
    )
