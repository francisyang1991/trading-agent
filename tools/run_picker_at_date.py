#!/usr/bin/env python3
"""
Run the three-layer picker at a specific historical date (no lookahead).

Shows all intermediate results at each stage for debugging/analysis.

Usage:
    python tools/run_picker_at_date.py --date 2025-06-01
    python tools/run_picker_at_date.py --date 2025-06-01 --db-only-prices
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

from src.data.blacklist_store import DataBlacklistStore
from src.data_manager import DataManager
from src.picker.fundamentals_service import FundamentalSnapshotService
from src.picker.lookback import run_picker_at_date
from src.picker.post_processor import deduplicate_share_classes, diversify_by_industry
from src.picker.price_loader import stage_load_prices
from src.universe.filters import (
    build_technical_base_from_prices,
    moat_quality_filter,
    technical_filter,
)
from src.universe.listed_symbols import load_all_listed_us_symbols


def _truncate(df: pd.DataFrame, as_of: date):
    d = df.copy()
    if "Date" in d.columns:
        d = d[pd.to_datetime(d["Date"]).dt.date <= as_of]
    elif isinstance(d.index, pd.DatetimeIndex):
        d = d[d.index.date <= as_of]
    return d if not d.empty else None


def _load_prices_from_db(dm, symbols, period):
    out = {}
    total = len(symbols)
    for i, sym in enumerate(symbols, 1):
        try:
            raw = dm._load_daily_from_db(str(sym).upper(), period)
            if raw is None or raw.empty:
                continue
            frame = raw.reset_index().rename(columns={raw.index.name or "index": "Date"})
            frame["Date"] = pd.to_datetime(frame["Date"]).dt.date
            out[str(sym).upper()] = frame
        except Exception:
            continue
        finally:
            if i % 500 == 0 or i == total:
                print(f"  [prices] {i}/{total} loaded={len(out)}")
    return out


def _industry_map(dm, tickers):
    out = {}
    for t in tickers:
        try:
            info = dm.get_fundamentals(t, validate=False, strict=False)
            out[t] = str(info.get("industry") or "Unknown")
        except Exception:
            out[t] = "Unknown"
    return out


def _compute_forward_returns(picks, prices, as_of, end_date, cost_pct, tp_pct, sl_pct):
    rows = []
    for _, r in picks.iterrows():
        t = str(r["ticker"]).upper()
        frame = prices.get(t)
        if frame is None or frame.empty:
            continue
        d = frame.copy()
        if "Date" in d.columns:
            d["Date"] = pd.to_datetime(d["Date"]).dt.date
        elif isinstance(d.index, pd.DatetimeIndex):
            d = d.reset_index().rename(columns={d.index.name or "index": "Date"})
            d["Date"] = pd.to_datetime(d["Date"]).dt.date
        else:
            continue

        start_path = d[d["Date"] <= as_of]
        if start_path.empty:
            continue
        p0 = float(start_path.iloc[-1]["Close"])
        start_dt = start_path.iloc[-1]["Date"]
        if p0 <= 0:
            continue

        forward = d[(d["Date"] > as_of) & (d["Date"] <= end_date)]
        if forward.empty:
            continue

        p1 = float(forward.iloc[-1]["Close"])
        exit_dt = forward.iloc[-1]["Date"]
        reason = "Time"

        if tp_pct > 0 or sl_pct < 0:
            for _, pr in forward.iterrows():
                low = float(pr.get("Low", pr["Close"]))
                high = float(pr.get("High", pr["Close"]))
                if sl_pct < 0 and (low / p0 - 1.0) <= sl_pct:
                    p1 = p0 * (1.0 + sl_pct)
                    exit_dt = pr["Date"]
                    reason = "SL"
                    break
                if tp_pct > 0 and (high / p0 - 1.0) >= tp_pct:
                    p1 = p0 * (1.0 + tp_pct)
                    exit_dt = pr["Date"]
                    reason = "TP"
                    break

        gross = p1 / p0 - 1.0
        net = gross - cost_pct
        rows.append({
            "ticker": t,
            "entry_date": start_dt,
            "exit_date": exit_dt,
            "holding_days": (exit_dt - start_dt).days,
            "entry_price": round(p0, 2),
            "exit_price": round(p1, 2),
            "gross_pct": round(gross * 100, 2),
            "net_pct": round(net * 100, 2),
            "exit_reason": reason,
        })
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description="Run picker at a specific historical date")
    parser.add_argument("--date", required=True, help="As-of date (YYYY-MM-DD)")
    parser.add_argument("--config", default="config/picker_config.yaml")
    parser.add_argument("--price-period", default="3y")
    parser.add_argument("--db-only-prices", action="store_true")
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--take-profit-pct", type=float, default=20.0)
    parser.add_argument("--stop-loss-pct", type=float, default=-8.0)
    parser.add_argument("--commission-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    args = parser.parse_args()

    as_of = datetime.strptime(args.date, "%Y-%m-%d").date()
    today = date.today()

    with open(args.config, "r") as f:
        cfg = yaml.safe_load(f) or {}

    out_dir = Path(f"results/picker/historical_{args.date}")
    out_dir.mkdir(parents=True, exist_ok=True)

    dm = DataManager()
    fund_svc = FundamentalSnapshotService(dm)
    tech_cfg = cfg.get("technical", {})
    fund_cfg = cfg.get("fundamental", {})
    qual_cfg = cfg.get("quality", {})
    score_cfg = cfg.get("scoring", {})
    lb_cfg = cfg.get("lookback", {})

    # 1. Universe
    t0 = time.time()
    print(f"\n{'='*80}")
    print(f"PICKER AT {as_of} (no lookahead)")
    print(f"{'='*80}")
    print("\n[1/6] Loading universe...")
    symbols = load_all_listed_us_symbols()
    blacklist = DataBlacklistStore(dm).load()
    symbols = [s for s in symbols if s not in blacklist]
    if args.max_symbols > 0:
        symbols = symbols[:args.max_symbols]
    print(f"  Universe: {len(symbols)} symbols ({len(blacklist)} blacklisted)")

    # 2. Prices
    t1 = time.time()
    print(f"\n[2/6] Loading prices ({args.price_period})...")
    cfg.setdefault("data", {})["period"] = args.price_period
    if args.db_only_prices:
        prices = _load_prices_from_db(dm, symbols, args.price_period)
    else:
        prices = stage_load_prices(dm, symbols, cfg)
    print(f"  Loaded: {len(prices)} symbols ({time.time()-t1:.1f}s)")

    # SPY
    spy = prices.get("SPY")
    if spy is None or spy.empty:
        spy_raw = dm.get_daily_data("SPY", period=args.price_period, force_refresh=True)
        if spy_raw is None or spy_raw.empty:
            raise RuntimeError("SPY data unavailable")
        spy = spy_raw.reset_index().rename(columns={spy_raw.index.name or "index": "Date"})
        spy["Date"] = pd.to_datetime(spy["Date"]).dt.date
        prices["SPY"] = spy

    spy_trunc = _truncate(spy, as_of)
    if spy_trunc is None or len(spy_trunc) < 130:
        raise RuntimeError(f"SPY has insufficient history before {as_of}")
    spy_close = spy_trunc["Close"]
    spy_ret_252 = float(spy_close.iloc[-1] / spy_close.iloc[-130] - 1)
    print(f"  SPY 6-month return at {as_of}: {spy_ret_252*100:.1f}%")

    # 3. Technical base
    t2 = time.time()
    print(f"\n[3/6] Building technical base (truncated to {as_of})...")
    base = build_technical_base_from_prices(
        prices,
        benchmark_return_252=spy_ret_252,
        near_52w_ratio=tech_cfg.get("near_52w_ratio", 0.85),
        min_bars=tech_cfg.get("min_bars", 260),
        min_bars_recent_ipo=tech_cfg.get("min_bars_recent_ipo", 130),
        as_of_date=as_of,
    )
    base.to_csv(out_dir / "stage_1_base.csv", index=False)
    print(f"  Technical base: {len(base)} stocks ({time.time()-t2:.1f}s)")
    if base.empty:
        print("  NO stocks in technical base. Aborting.")
        return

    # 4. Technical filter (RS rank)
    t3 = time.time()
    tech = technical_filter(base, rs_threshold=tech_cfg.get("rs_threshold", 80.0))
    tech.to_csv(out_dir / "stage_2_technical_filter.csv", index=False)
    print(f"\n[4/6] Technical filter (RS >= {tech_cfg.get('rs_threshold', 80.0)}):")
    print(f"  Passed: {len(tech)} / {len(base)} stocks ({time.time()-t3:.1f}s)")
    if tech.empty:
        print("  NO stocks passed technical filter. Aborting.")
        return

    # Show top 10 by RS rank
    print(f"\n  Top 10 by RS rank:")
    for i, (_, r) in enumerate(tech.head(10).iterrows(), 1):
        print(f"    {i:>2}. {r['ticker']:>6}  RS={r['rs_rank']:.1f}")

    # 5. Fundamental enrichment
    t4 = time.time()
    print(f"\n[5/6] Fundamental enrichment + quality filter...")
    fund_df = fund_svc.load_for_tickers(
        tech["ticker"].tolist(),
        existing_snapshot_path=None,
        progress_every=50,
        as_of_date=as_of,
        use_persistent_cache=True,
        refresh_persistent_cache=False,
    )
    fund_df.to_csv(out_dir / "stage_3_fund_snapshot_raw.csv", index=False)

    merged = tech.merge(fund_df, on="ticker", how="left")
    merged["gm_rank"] = (
        pd.to_numeric(merged["gross_margin"], errors="coerce").rank(pct=True) * 100
    ).round(2)

    skip_yoy = bool(lb_cfg.get("skip_missing_yoy_checks", False))

    # Data quality report
    print(f"\n  Data quality ({len(merged)} stocks after merge):")
    for col in ["eps_yoy", "revenue_growth", "roe", "gm_rank"]:
        nn = pd.to_numeric(merged.get(col, pd.Series(dtype=float)), errors="coerce").notna().sum()
        print(f"    {col}: {nn}/{len(merged)} non-null")

    # Fundamental filter
    eps = pd.to_numeric(merged.get("eps_yoy"), errors="coerce")
    rev = pd.to_numeric(merged.get("revenue_growth"), errors="coerce")
    mask = pd.Series(True, index=merged.index)
    min_eps = fund_cfg.get("min_eps_yoy", 0.25)
    min_rev = fund_cfg.get("min_revenue_growth", 0.10)

    if skip_yoy:
        mask &= eps.isna() | (eps >= min_eps)
        mask &= rev.isna() | (rev >= min_rev)
    else:
        mask &= eps.notna() & (eps >= min_eps)
        mask &= rev.notna() & (rev >= min_rev)
    fund_filtered = merged[mask].copy()

    print(f"\n  Fundamental filter (EPS >= {min_eps*100:.0f}%, Rev >= {min_rev*100:.0f}%):")
    print(f"    Passed: {len(fund_filtered)} / {len(merged)}")

    # Quality filter
    quality = moat_quality_filter(
        fund_filtered,
        min_roe=qual_cfg.get("min_roe", 0.10),
        min_gm_rank=qual_cfg.get("min_gm_rank", 60.0),
        max_debt_to_equity=qual_cfg.get("max_debt_to_equity", 1.2),
    )
    print(f"  Quality filter (ROE >= {qual_cfg.get('min_roe', 0.10)*100:.0f}%, "
          f"GM rank >= {qual_cfg.get('min_gm_rank', 60.0)}, "
          f"D/E <= {qual_cfg.get('max_debt_to_equity', 1.2)}):")
    print(f"    Passed: {len(quality)} / {len(fund_filtered)}")

    # Score
    if not quality.empty:
        quality = quality.copy()
        eps_s = pd.to_numeric(quality["eps_yoy"], errors="coerce").fillna(0.0)
        rev_s = pd.to_numeric(quality["revenue_growth"], errors="coerce").fillna(0.0)
        quality["composite"] = (
            quality["rs_rank"] * score_cfg.get("rs_rank_weight", 0.35)
            + (eps_s * 100).clip(-100, 200) * score_cfg.get("eps_yoy_weight", 0.20)
            + (rev_s * 100).clip(-100, 200) * score_cfg.get("revenue_growth_weight", 0.20)
            + quality["gm_rank"] * score_cfg.get("gm_rank_weight", 0.15)
            + (quality["roe"] * 100).clip(-100, 100) * score_cfg.get("roe_weight", 0.10)
        )
        quality = quality.sort_values("composite", ascending=False)

    quality.to_csv(out_dir / "stage_4_quality_scored.csv", index=False)
    print(f"  ({time.time()-t4:.1f}s)")

    # 6. Post-process
    t5 = time.time()
    print(f"\n[6/6] Post-process (dedup + diversify)...")
    pp_cfg = cfg.get("post_process", {})
    final = quality.copy()

    if pp_cfg.get("deduplicate_share_classes", True) and not final.empty:
        final, removed = deduplicate_share_classes(final)
        if removed:
            print(f"  Dedup removed: {removed}")

    max_per_ind = pp_cfg.get("max_per_industry", 2)
    if max_per_ind and not final.empty:
        ind_map = _industry_map(dm, final["ticker"].tolist())
        final, removed = diversify_by_industry(final, ind_map, max_per_industry=max_per_ind)
        if removed:
            print(f"  Sector cap removed: {len(removed)} stocks")

    top_n = pp_cfg.get("top_n", 25)
    final = final.head(top_n)
    final.to_csv(out_dir / "stage_5_final_picks.csv", index=False)
    print(f"  Final picks: {len(final)} ({time.time()-t5:.1f}s)")

    # Summary
    display_cols = ["ticker", "rs_rank", "eps_yoy", "revenue_growth", "roe", "gm_rank", "composite"]
    available_cols = [c for c in display_cols if c in final.columns]
    print(f"\n{'='*80}")
    print(f"FINAL PICKS AT {as_of} ({len(final)} stocks)")
    print(f"{'='*80}")
    if not final.empty:
        for i, (_, r) in enumerate(final.iterrows(), 1):
            eps_val = r.get("eps_yoy", 0)
            rev_val = r.get("revenue_growth", 0)
            roe_val = r.get("roe", 0)
            eps_str = f"{float(eps_val)*100:+.1f}%" if pd.notna(eps_val) else "N/A"
            rev_str = f"{float(rev_val)*100:+.1f}%" if pd.notna(rev_val) else "N/A"
            roe_str = f"{float(roe_val)*100:.1f}%" if pd.notna(roe_val) else "N/A"
            print(f"  {i:>2}. {r['ticker']:>6}  RS={r['rs_rank']:.1f}  "
                  f"EPS={eps_str}  Rev={rev_str}  ROE={roe_str}  "
                  f"Score={r.get('composite', 0):.1f}")
    else:
        print("  (no picks)")

    # Forward returns
    print(f"\n{'='*80}")
    print(f"FORWARD RETURNS ({as_of} -> {today})")
    print(f"TP={args.take_profit_pct}% / SL={args.stop_loss_pct}%")
    print(f"{'='*80}")

    cost = (args.commission_bps + args.slippage_bps) * 2.0 / 10000.0
    tp = args.take_profit_pct / 100.0
    sl = args.stop_loss_pct / 100.0

    trades = _compute_forward_returns(final, prices, as_of, today, cost, tp, sl)
    trades.to_csv(out_dir / "stage_6_forward_returns.csv", index=False)

    # SPY benchmark
    spy_trades = _compute_forward_returns(
        pd.DataFrame({"ticker": ["SPY"]}), prices, as_of, today, cost, tp, sl
    )
    spy_ret = float(spy_trades["net_pct"].iloc[0]) if not spy_trades.empty else 0.0

    if not trades.empty:
        mean_net = float(trades["net_pct"].mean())
        median_net = float(trades["net_pct"].median())
        win_rate = float((trades["net_pct"] > 0).mean() * 100)
        avg_hold = float(trades["holding_days"].mean())

        print(f"\n  Trades: {len(trades)}")
        print(f"  Mean net return: {mean_net:+.2f}%")
        print(f"  Median net return: {median_net:+.2f}%")
        print(f"  Win rate: {win_rate:.1f}%")
        print(f"  Avg holding days: {avg_hold:.0f}")
        print(f"  SPY net return: {spy_ret:+.2f}%")
        print(f"  Alpha vs SPY: {mean_net - spy_ret:+.2f}%")

        print(f"\n  {'Ticker':>6} {'Entry':>10} {'Exit':>10} {'Hold':>5} "
              f"{'Entry$':>8} {'Exit$':>8} {'Net%':>8} {'Reason':>6}")
        print(f"  {'-'*6:>6} {'-'*10:>10} {'-'*10:>10} {'-'*5:>5} "
              f"{'-'*8:>8} {'-'*8:>8} {'-'*8:>8} {'-'*6:>6}")
        for _, tr in trades.iterrows():
            print(f"  {tr['ticker']:>6} {str(tr['entry_date']):>10} {str(tr['exit_date']):>10} "
                  f"{tr['holding_days']:>5} {tr['entry_price']:>8.2f} {tr['exit_price']:>8.2f} "
                  f"{tr['net_pct']:>+8.2f} {tr['exit_reason']:>6}")

        # Winners vs losers
        winners = trades[trades["net_pct"] > 0]
        losers = trades[trades["net_pct"] <= 0]
        print(f"\n  Winners: {len(winners)} | Losers: {len(losers)}")
        if not winners.empty:
            print(f"  Avg winner: +{winners['net_pct'].mean():.2f}%")
        if not losers.empty:
            print(f"  Avg loser: {losers['net_pct'].mean():.2f}%")
    else:
        print("  No trades generated.")

    print(f"\n  All results saved to: {out_dir}/")
    print(f"  Total runtime: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
