#!/usr/bin/env python3
"""
Walk-forward validation report card for the three-layer picker with Take-Profit/Stop-Loss.

This validator runs point-in-time picks at historical anchors (default: 3/6/9/12
months ago), then measures realized forward returns vs SPY with transaction costs.
It simulates exit at take-profit or stop-loss.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.blacklist_store import DataBlacklistStore
from src.data_manager import DataManager
from src.picker.fundamentals_service import FundamentalSnapshotService
from src.picker.lookback import run_picker_at_date
from src.picker.post_processor import deduplicate_share_classes, diversify_by_industry
from src.picker.price_loader import stage_load_prices
from src.universe.listed_symbols import load_all_listed_us_symbols


@dataclass
class WindowResult:
    label: str
    as_of_date: date
    status: str
    n_picks: int
    mean_gross_return_pct: float
    mean_net_return_pct: float
    median_net_return_pct: float
    hit_rate_pct: float
    spy_return_pct: float
    alpha_vs_spy_pct: float
    avg_holding_days: float


def _load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_prices_from_db(dm: DataManager, symbols: List[str], period: str) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
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


def _local_industry_map(dm: DataManager, tickers: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for t in tickers:
        try:
            info = dm.get_fundamentals(t, validate=False, strict=False)
            out[t] = str(info.get("industry") or "Unknown")
        except Exception:
            out[t] = "Unknown"
    return out


def _post_process(picks_df: pd.DataFrame, cfg: dict, dm: DataManager) -> pd.DataFrame:
    if picks_df is None or picks_df.empty:
        return pd.DataFrame()

    pp_cfg = cfg.get("post_process", {})
    out = picks_df.copy()

    if pp_cfg.get("deduplicate_share_classes", True):
        out, _ = deduplicate_share_classes(out)

    max_per_ind = int(pp_cfg.get("max_per_industry", 2))
    if max_per_ind > 0 and not out.empty:
        ind_map = _local_industry_map(dm, out["ticker"].astype(str).tolist())
        out, _ = diversify_by_industry(out, ind_map, max_per_industry=max_per_ind)

    top_n = int(pp_cfg.get("top_n", 25))
    if top_n > 0:
        out = out.head(top_n).copy()
    return out


def _price_on_or_before(frame: pd.DataFrame, target: date) -> Optional[Tuple[date, float]]:
    d = frame.copy()
    d["Date"] = pd.to_datetime(d["Date"]).dt.date
    d = d[d["Date"] <= target]
    if d.empty:
        return None
    row = d.iloc[-1]
    px = pd.to_numeric(pd.Series([row["Close"]]), errors="coerce").iloc[0]
    if not np.isfinite(px) or px <= 0:
        return None
    return row["Date"], float(px)


def _rsi_at_entry(frame: pd.DataFrame, as_of: date, period: int = 14) -> Optional[float]:
    """Compute RSI at the last bar on or before as_of."""
    d = frame.copy()
    if "Date" in d.columns:
        d["Date"] = pd.to_datetime(d["Date"]).dt.date
        d = d[d["Date"] <= as_of]
    elif isinstance(d.index, pd.DatetimeIndex):
        d = d[d.index.date <= as_of]
    else:
        return None
    if len(d) < period + 1:
        return None
    close = d["Close"] if "Close" in d.columns else d["close"]
    delta = pd.to_numeric(close, errors="coerce").diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    val = float(rsi.iloc[-1])
    return val if np.isfinite(val) else None


def _compute_window_returns(
    picks: pd.DataFrame,
    prices: Dict[str, pd.DataFrame],
    as_of: date,
    end_date: date,
    round_trip_cost_pct: float,
    take_profit_pct: float,
    stop_loss_pct: float,
    rsi_max_overbought: Optional[float] = None,
) -> pd.DataFrame:
    rows: List[Dict] = []
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
        start_row = start_path.iloc[-1]
        start_dt = start_row["Date"]
        p0 = float(start_row["Close"])
        if p0 <= 0:
            continue

        # Skip overbought entries (RSI > threshold)
        if rsi_max_overbought is not None:
            rsi_val = _rsi_at_entry(frame, as_of)
            if rsi_val is not None and rsi_val > rsi_max_overbought:
                continue
            
        forward_path = d[(d["Date"] > as_of) & (d["Date"] <= end_date)]
        if forward_path.empty:
            continue
            
        exit_dt = forward_path.iloc[-1]["Date"]
        p1 = float(forward_path.iloc[-1]["Close"])
        hit_tp_sl = False
        
        if take_profit_pct > 0 or stop_loss_pct < 0:
            for _, path_row in forward_path.iterrows():
                curr_p = float(path_row["Close"])
                curr_low = float(path_row["Low"]) if "Low" in path_row else curr_p
                curr_high = float(path_row["High"]) if "High" in path_row else curr_p
                
                # Check stop loss
                if stop_loss_pct < 0 and (curr_low / p0 - 1.0) <= stop_loss_pct:
                    p1 = p0 * (1.0 + stop_loss_pct)
                    exit_dt = path_row["Date"]
                    hit_tp_sl = True
                    break
                    
                # Check take profit
                if take_profit_pct > 0 and (curr_high / p0 - 1.0) >= take_profit_pct:
                    p1 = p0 * (1.0 + take_profit_pct)
                    exit_dt = path_row["Date"]
                    hit_tp_sl = True
                    break

        gross = p1 / p0 - 1.0
        net = gross - round_trip_cost_pct
        rows.append(
            {
                "ticker": t,
                "entry_date": start_dt,
                "exit_date": exit_dt,
                "holding_days": int((exit_dt - start_dt).days),
                "entry_price": p0,
                "exit_price": p1,
                "gross_return_pct": gross * 100.0,
                "net_return_pct": net * 100.0,
                "exit_reason": "TP/SL" if hit_tp_sl else "Time",
            }
        )
    return pd.DataFrame(rows)


def _count_symbols_with_min_bars(
    prices: Dict[str, pd.DataFrame],
    as_of: date,
    min_bars: int,
) -> int:
    count = 0
    for t, df in prices.items():
        if t == "SPY":
            continue
        try:
            d = df.copy()
            if "Date" in d.columns:
                d["Date"] = pd.to_datetime(d["Date"]).dt.date
            elif isinstance(d.index, pd.DatetimeIndex):
                d = d.reset_index().rename(columns={d.index.name or "index": "Date"})
                d["Date"] = pd.to_datetime(d["Date"]).dt.date
            d = d[d["Date"] <= as_of]
            if len(d) >= min_bars:
                count += 1
        except Exception:
            continue
    return count


def _as_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    if "Date" in d.columns:
        d = d.set_index(pd.to_datetime(d["Date"]))
    elif not isinstance(d.index, pd.DatetimeIndex):
        d.index = pd.to_datetime(d.index)
    return d


def _perf_from_period_returns(r: pd.Series, periods_per_year: float) -> Dict[str, float]:
    r = pd.to_numeric(r, errors="coerce").dropna()
    if r.empty:
        return {
            "total_return_pct": 0.0,
            "cagr_pct": 0.0,
            "volatility_pct": 0.0,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
        }
    equity = (1.0 + r).cumprod()
    total_ret = float(equity.iloc[-1] - 1.0)
    years = max(len(r) / periods_per_year, 1e-9)
    cagr = float((equity.iloc[-1] ** (1.0 / years)) - 1.0)
    vol = float(r.std(ddof=1) * np.sqrt(periods_per_year)) if len(r) > 1 else 0.0
    sharpe = float((r.mean() * periods_per_year) / vol) if vol > 0 else 0.0
    peak = equity.cummax()
    dd = (equity / peak - 1.0).min()
    return {
        "total_return_pct": total_ret * 100.0,
        "cagr_pct": cagr * 100.0,
        "volatility_pct": vol * 100.0,
        "sharpe": sharpe,
        "max_drawdown_pct": float(abs(dd) * 100.0),
    }


def run_validation(args) -> Tuple[pd.DataFrame, pd.DataFrame]:
    cfg = _load_config(Path(args.config))
    dm = DataManager()
    fund_svc = FundamentalSnapshotService(dm)

    t0 = time.time()
    print("[Universe] Loading listed symbols...")
    symbols = load_all_listed_us_symbols()
    blacklist = DataBlacklistStore(dm).load()
    symbols = [s for s in symbols if s not in blacklist]
    if args.max_symbols > 0:
        symbols = symbols[: args.max_symbols]
    print(f"  {len(symbols)} symbols ({time.time()-t0:.1f}s)")

    cfg_data = cfg.setdefault("data", {})
    cfg_data["period"] = str(args.price_period)
    t1 = time.time()
    if bool(args.db_only_prices):
        print("[Prices] Loading from DB only...")
        prices = _load_prices_from_db(dm, symbols, period=args.price_period)
    else:
        prices = stage_load_prices(dm, symbols, cfg)
    print(f"  [Prices] Loaded {len(prices)} symbols ({time.time()-t1:.1f}s)")
    spy = prices.get("SPY")
    if spy is None or spy.empty:
        spy_raw = dm.get_daily_data("SPY", period=args.price_period, force_refresh=True)
        if spy_raw is None or spy_raw.empty:
            raise RuntimeError("SPY data unavailable")
        spy = spy_raw.reset_index().rename(columns={spy_raw.index.name or "index": "Date"})
        spy["Date"] = pd.to_datetime(spy["Date"]).dt.date
        prices["SPY"] = spy
    spy_idx = _as_datetime_index(spy)

    months = [int(m) for m in str(args.months_back).split(",") if str(m).strip()]
    months = sorted(set(months))
    today = date.today()
    round_trip_cost_pct = (float(args.commission_bps) + float(args.slippage_bps)) * 2.0 / 10000.0
    tp_pct = args.take_profit_pct / 100.0 if args.take_profit_pct else 0.0
    sl_pct = args.stop_loss_pct / 100.0 if args.stop_loss_pct else 0.0
    rsi_max = getattr(args, "rsi_max_overbought", None)

    window_rows: List[WindowResult] = []
    trade_rows: List[Dict] = []

    for m in months:
        as_of = today - timedelta(days=int(30 * m))
        label = f"{m}m_ago"
        print(f"\n[window] {label} as_of={as_of}")
        tw = time.time()
        min_bars = int(cfg.get("technical", {}).get("min_bars", 260))
        raw = run_picker_at_date(
            prices=prices,
            spy_df=spy_idx,
            fundamentals_service=fund_svc,
            cfg=cfg,
            as_of_date=as_of,
            refresh_historical_cache=bool(args.rebuild_historical_cache),
        )
        picks = _post_process(raw, cfg, dm)
        print(f"  picker+post_process: {time.time()-tw:.1f}s")
        if picks.empty:
            print("  no picks")
            spy_path = _compute_window_returns(
                picks=pd.DataFrame({"ticker": ["SPY"]}),
                prices=prices,
                as_of=as_of,
                end_date=today,
                round_trip_cost_pct=round_trip_cost_pct,
                take_profit_pct=tp_pct,
                stop_loss_pct=sl_pct,
            )
            spy_ret = float(spy_path["net_return_pct"].iloc[0]) if not spy_path.empty else 0.0
            window_rows.append(
                WindowResult(
                    label=label,
                    as_of_date=as_of,
                    status=f"no_picks;eligible_universe={_count_symbols_with_min_bars(prices, as_of, min_bars)}",
                    n_picks=0,
                    mean_gross_return_pct=0.0,
                    mean_net_return_pct=0.0,
                    median_net_return_pct=0.0,
                    hit_rate_pct=0.0,
                    spy_return_pct=spy_ret,
                    alpha_vs_spy_pct=-spy_ret,
                    avg_holding_days=0.0,
                )
            )
            continue

        pick_rets = _compute_window_returns(
            picks=picks,
            prices=prices,
            as_of=as_of,
            end_date=today,
            round_trip_cost_pct=round_trip_cost_pct,
            take_profit_pct=tp_pct,
            stop_loss_pct=sl_pct,
            rsi_max_overbought=rsi_max,
        )
        if pick_rets.empty:
            print("  picks exist but missing return path")
            spy_path = _compute_window_returns(
                picks=pd.DataFrame({"ticker": ["SPY"]}),
                prices=prices,
                as_of=as_of,
                end_date=today,
                round_trip_cost_pct=round_trip_cost_pct,
                take_profit_pct=tp_pct,
                stop_loss_pct=sl_pct,
            )
            spy_ret = float(spy_path["net_return_pct"].iloc[0]) if not spy_path.empty else 0.0
            window_rows.append(
                WindowResult(
                    label=label,
                    as_of_date=as_of,
                    status="missing_return_paths",
                    n_picks=0,
                    mean_gross_return_pct=0.0,
                    mean_net_return_pct=0.0,
                    median_net_return_pct=0.0,
                    hit_rate_pct=0.0,
                    spy_return_pct=spy_ret,
                    alpha_vs_spy_pct=-spy_ret,
                    avg_holding_days=0.0,
                )
            )
            continue

        spy_path = _compute_window_returns(
            picks=pd.DataFrame({"ticker": ["SPY"]}),
            prices=prices,
            as_of=as_of,
            end_date=today,
            round_trip_cost_pct=round_trip_cost_pct,
            take_profit_pct=tp_pct,
            stop_loss_pct=sl_pct,
        )
        spy_ret = float(spy_path["net_return_pct"].iloc[0]) if not spy_path.empty else 0.0

        mean_gross = float(pick_rets["gross_return_pct"].mean())
        mean_net = float(pick_rets["net_return_pct"].mean())
        median_net = float(pick_rets["net_return_pct"].median())
        hit_rate = float((pick_rets["net_return_pct"] > 0).mean() * 100.0)
        avg_hold = float(pick_rets["holding_days"].mean())

        for _, tr in pick_rets.iterrows():
            trade_rows.append(
                {
                    "window": label,
                    "as_of_date": as_of,
                    "ticker": tr["ticker"],
                    "entry_date": tr["entry_date"],
                    "exit_date": tr["exit_date"],
                    "holding_days": tr["holding_days"],
                    "entry_price": tr["entry_price"],
                    "exit_price": tr["exit_price"],
                    "gross_return_pct": tr["gross_return_pct"],
                    "net_return_pct": tr["net_return_pct"],
                    "exit_reason": tr.get("exit_reason", "Time"),
                }
            )

        window_rows.append(
            WindowResult(
                label=label,
                as_of_date=as_of,
                status="ok",
                n_picks=int(len(pick_rets)),
                mean_gross_return_pct=mean_gross,
                mean_net_return_pct=mean_net,
                median_net_return_pct=median_net,
                hit_rate_pct=hit_rate,
                spy_return_pct=spy_ret,
                alpha_vs_spy_pct=mean_net - spy_ret,
                avg_holding_days=avg_hold,
            )
        )
        print(
            f"  picks={len(pick_rets)} mean_net={mean_net:+.2f}% "
            f"spy={spy_ret:+.2f}% alpha={mean_net - spy_ret:+.2f}%"
        )

    windows_df = pd.DataFrame([w.__dict__ for w in window_rows]).sort_values("as_of_date")
    trades_df = pd.DataFrame(trade_rows)
    return windows_df, trades_df


def _write_report(
    windows_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    out_md: Path,
    args,
) -> None:
    lines: List[str] = []
    lines.append("# Three-Layer Walk-Forward Validation (TP/SL)")
    lines.append("")
    lines.append(f"- Anchors (months): `{args.months_back}`")
    lines.append(f"- TP: `{args.take_profit_pct}%` | SL: `{args.stop_loss_pct}%`")
    lines.append(
        f"- Costs: commission `{args.commission_bps:.1f}` bps/side + "
        f"slippage `{args.slippage_bps:.1f}` bps/side"
    )
    lines.append(f"- Rows: `{len(windows_df)}` windows")
    lines.append("")

    if windows_df.empty:
        lines.append("No windows produced.")
        out_md.write_text("\n".join(lines), encoding="utf-8")
        return

    active = windows_df[(windows_df["status"] == "ok") & (windows_df["n_picks"] > 0)].copy()
    period_months = active["label"].astype(str).str.replace("m_ago", "", regex=False).astype(float)
    avg_months = float(period_months.mean()) if not period_months.empty else 6.0
    periods_per_year = max(12.0 / max(avg_months, 1.0), 1.0)

    strat_r = pd.to_numeric(active["mean_net_return_pct"], errors="coerce") / 100.0
    spy_r = pd.to_numeric(active["spy_return_pct"], errors="coerce") / 100.0
    strat_perf = _perf_from_period_returns(strat_r, periods_per_year=periods_per_year)
    spy_perf = _perf_from_period_returns(spy_r, periods_per_year=periods_per_year)

    lines.append("## Report Card")
    lines.append("")
    lines.append(f"- Active windows (with picks): `{len(active)}/{len(windows_df)}`")
    lines.append(f"- Total realized picks: `{int(pd.to_numeric(active['n_picks'], errors='coerce').sum())}`")
    
    # Calculate overall win rate
    overall_win_rate = "N/A"
    if not active.empty:
        # Assuming each window hit rate is equally weighted by its picks
        weighted_hr = sum(active["hit_rate_pct"] * active["n_picks"]) / sum(active["n_picks"])
        overall_win_rate = f"{weighted_hr:.2f}%"

    lines.append(f"- Strategy Overall Win Rate (Hit Rate): `{overall_win_rate}`")
    lines.append(f"- Strategy total return: `{strat_perf['total_return_pct']:.2f}%`")
    lines.append(f"- Strategy CAGR (window-compounded): `{strat_perf['cagr_pct']:.2f}%`")
    lines.append(f"- Strategy Sharpe: `{strat_perf['sharpe']:.2f}`")
    lines.append(f"- Strategy max drawdown: `{strat_perf['max_drawdown_pct']:.2f}%`")
    lines.append(f"- SPY total return: `{spy_perf['total_return_pct']:.2f}%`")
    lines.append(f"- SPY CAGR (window-compounded): `{spy_perf['cagr_pct']:.2f}%`")
    if len(active) < 4 or int(pd.to_numeric(active["n_picks"], errors="coerce").sum()) < 20:
        lines.append("- Sample-size warning: very limited active windows/picks, so CAGR/Sharpe are unstable.")
    lines.append("")

    lines.append("## Window Results")
    lines.append("")
    cols = [
        "label",
        "as_of_date",
        "status",
        "n_picks",
        "mean_net_return_pct",
        "median_net_return_pct",
        "hit_rate_pct",
        "spy_return_pct",
        "alpha_vs_spy_pct",
        "avg_holding_days",
    ]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("|" + "|".join(["---"] * len(cols)) + "|")
    for _, r in windows_df.iterrows():
        vals = [str(r[c]) for c in cols]
        lines.append("| " + " | ".join(vals) + " |")

    # Trade list
    if not trades_df.empty:
        lines.append("## Trades")
        lines.append("")
        lines.append("| window | ticker | entry_date | exit_date | holding_days | entry | exit | net_return_pct | exit_reason |")
        lines.append("|--------|--------|------------|-----------|--------------|-------|------|----------------|-------------|")
        for _, tr in trades_df.iterrows():
            win = str(tr.get("window", ""))
            ticker = str(tr.get("ticker", ""))
            entry_d = str(tr.get("entry_date", ""))
            exit_d = str(tr.get("exit_date", ""))
            hold = int(tr.get("holding_days", 0))
            entry_p = f"{float(tr.get('entry_price', 0)):.2f}"
            exit_p = f"{float(tr.get('exit_price', 0)):.2f}"
            net_pct = f"{float(tr.get('net_return_pct', 0)):+.2f}"
            reason = str(tr.get("exit_reason", "Time"))
            lines.append(f"| {win} | {ticker} | {entry_d} | {exit_d} | {hold} | {entry_p} | {exit_p} | {net_pct} | {reason} |")
        lines.append("")

    lines.append("## Caveats")
    lines.append("")
    lines.append("- Uses current listed-universe membership; this still has survivorship bias.")
    lines.append("- Anchored-window design is robust for sanity checks, not a full daily portfolio simulation.")
    out_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward validator for three-layer picker with TP/SL.")
    parser.add_argument("--config", type=str, default="config/picker_config.yaml", help="Picker config path")
    parser.add_argument(
        "--months-back",
        type=str,
        default="3,6,9,12",
        help="Comma-separated lookback anchors in months",
    )
    parser.add_argument("--price-period", type=str, default="3y", help="Price history period for DB load")
    parser.add_argument(
        "--db-only-prices",
        action="store_true",
        help="Use DB-only price loads (faster, but may fail coverage for older anchors)",
    )
    parser.add_argument("--max-symbols", type=int, default=0, help="Optional cap on universe symbols")
    parser.add_argument("--commission-bps", type=float, default=5.0, help="Commission bps per side")
    parser.add_argument("--slippage-bps", type=float, default=5.0, help="Slippage bps per side")
    parser.add_argument("--take-profit-pct", type=float, default=20.0, help="Take profit pct (e.g. 20.0)")
    parser.add_argument("--stop-loss-pct", type=float, default=-8.0, help="Stop loss pct (e.g. -8.0)")
    parser.add_argument(
        "--rsi-max-overbought",
        type=float,
        default=None,
        help="Skip entries where RSI(14) > this (e.g. 70 to avoid overbought)",
    )
    parser.add_argument(
        "--rebuild-historical-cache",
        action="store_true",
        help="Recompute historical fundamental snapshots for each anchor date",
    )
    parser.add_argument(
        "--output-windows-csv",
        type=str,
        default="results/picker/walkforward_windows_report_tpsl.csv",
        help="Per-window summary CSV",
    )
    parser.add_argument(
        "--output-trades-csv",
        type=str,
        default="results/picker/walkforward_trades_report_tpsl.csv",
        help="Per-pick realized return CSV",
    )
    parser.add_argument(
        "--output-md",
        type=str,
        default="results/picker/walkforward_report_tpsl.md",
        help="Markdown report path",
    )
    args = parser.parse_args()

    windows_df, trades_df = run_validation(args)
    out_windows = Path(args.output_windows_csv)
    out_trades = Path(args.output_trades_csv)
    out_md = Path(args.output_md)
    out_windows.parent.mkdir(parents=True, exist_ok=True)

    windows_df.to_csv(out_windows, index=False)
    trades_df.to_csv(out_trades, index=False)
    _write_report(windows_df, trades_df, out_md, args)

    print(f"Saved: {out_windows}")
    print(f"Saved: {out_trades}")
    print(f"Saved: {out_md}")
    if not windows_df.empty:
        print(windows_df.to_string(index=False))
    if not trades_df.empty:
        print("\n--- Trades ---")
        print(trades_df[["window", "ticker", "entry_date", "exit_date", "net_return_pct", "exit_reason"]].to_string(index=False))


if __name__ == "__main__":
    main()
