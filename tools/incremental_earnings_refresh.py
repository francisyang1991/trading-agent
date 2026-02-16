#!/usr/bin/env python3
"""
Incremental earnings-refresh workflow for the three-layer picker.

What this command does:
1. Detect newly published/revised earnings rows (ticker + disclosure_date).
2. Refresh quarterly fundamentals only for likely-impacted tickers.
3. Invalidate/recompute point-in-time snapshot cache rows only for impacted
   tickers on affected as-of dates.
4. Re-run only impacted walk-forward validation windows.
5. Re-run scanner output only for impacted tickers.
6. Persist audit tables with "what changed" details.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data_manager import DataManager
from src.picker.fundamentals_service import FundamentalSnapshotService
from src.universe.listed_symbols import load_all_listed_us_symbols


QUARTERLY_FIELDS = [
    "ticker",
    "report_date",
    "disclosure_date",
    "eps",
    "revenue",
    "roe",
    "gross_margin",
]

SNAPSHOT_FIELDS = [
    "eps_yoy",
    "revenue_growth",
    "revenue_acceleration",
    "earnings_surprise",
    "roe",
    "gross_margin",
    "debt_to_equity",
]


def _now_utc_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _as_date(value) -> Optional[date]:
    try:
        if value is None or pd.isna(value):
            return None
        return pd.to_datetime(value, errors="coerce").date()
    except Exception:
        return None


def _as_iso_date(value) -> str:
    d = _as_date(value)
    return d.isoformat() if d else ""


def _as_float(value) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        out = float(value)
        if pd.isna(out):
            return None
        return out
    except Exception:
        return None


def _value_changed(a, b, tol: float = 1e-10) -> bool:
    if (a is None or pd.isna(a)) and (b is None or pd.isna(b)):
        return False
    if (a is None or pd.isna(a)) != (b is None or pd.isna(b)):
        return True

    da = _as_date(a)
    db = _as_date(b)
    if da and db:
        return da != db

    fa = _as_float(a)
    fb = _as_float(b)
    if fa is not None and fb is not None:
        return abs(fa - fb) > tol

    return str(a) != str(b)


def _parse_months(months_back: str) -> List[int]:
    months: List[int] = []
    for chunk in str(months_back).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            val = int(chunk)
            if val > 0:
                months.append(val)
        except Exception:
            continue
    return sorted(set(months))


def _stable_unique(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in values:
        v = str(raw).upper().strip()
        if not v or v in seen:
            continue
        out.append(v)
        seen.add(v)
    return out


def _load_symbols_file(path: Path) -> List[str]:
    if not path.exists():
        return []
    rows: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        token = s.split(",")[0].strip()
        if token.upper() in {"TICKER", "SYMBOL"}:
            continue
        rows.append(token)
    return _stable_unique(rows)


def _load_candidate_symbols(args) -> tuple[List[str], str]:
    if args.symbols:
        return _stable_unique(args.symbols), "cli_symbols"

    if args.symbols_file:
        syms = _load_symbols_file(Path(args.symbols_file))
        return syms, "symbols_file"

    technical_path = Path(args.technical_universe_csv)
    if technical_path.exists():
        try:
            df = pd.read_csv(technical_path)
            if "ticker" in df.columns:
                return _stable_unique(df["ticker"].astype(str).tolist()), "technical_universe_csv"
        except Exception:
            pass

    return _stable_unique(load_all_listed_us_symbols()), "listed_universe"


def _normalize_quarterly(df: Optional[pd.DataFrame], symbol: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=QUARTERLY_FIELDS)

    out = df.copy()
    if "ticker" not in out.columns:
        out["ticker"] = str(symbol).upper()

    for col in QUARTERLY_FIELDS:
        if col not in out.columns:
            out[col] = pd.NA

    out = out[QUARTERLY_FIELDS].copy()
    out["ticker"] = out["ticker"].astype(str).str.upper().str.strip()
    out["report_date"] = pd.to_datetime(out["report_date"], errors="coerce").dt.date
    out["disclosure_date"] = pd.to_datetime(out["disclosure_date"], errors="coerce").dt.date
    for col in ["eps", "revenue", "roe", "gross_margin"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out.dropna(subset=["ticker", "report_date", "disclosure_date"])
    out = out.drop_duplicates(subset=["ticker", "disclosure_date", "report_date"], keep="last")
    out = out.sort_values(["disclosure_date", "report_date"])
    return out.reset_index(drop=True)


def _normalize_calendar_dates(df: Optional[pd.DataFrame]) -> List[date]:
    if df is None or df.empty:
        return []

    use_col = "disclosure_date" if "disclosure_date" in df.columns else "report_date"
    if use_col not in df.columns:
        return []

    vals = pd.to_datetime(df[use_col], errors="coerce").dt.date.dropna().tolist()
    return sorted(set(vals))


def _latest_disclosure(df: pd.DataFrame) -> Optional[date]:
    if df is None or df.empty or "disclosure_date" not in df.columns:
        return None
    d = pd.to_datetime(df["disclosure_date"], errors="coerce").dt.date.dropna()
    if d.empty:
        return None
    return max(d)


def _compare_quarterly(previous: pd.DataFrame, fresh: pd.DataFrame, symbol: str) -> List[Dict]:
    changes: List[Dict] = []
    if fresh is None or fresh.empty:
        return changes

    prev_last = previous.sort_values("report_date").drop_duplicates("disclosure_date", keep="last")
    fresh_last = fresh.sort_values("report_date").drop_duplicates("disclosure_date", keep="last")

    prev_map = {
        _as_iso_date(r["disclosure_date"]): r.to_dict()
        for _, r in prev_last.iterrows()
    }
    fresh_map = {
        _as_iso_date(r["disclosure_date"]): r.to_dict()
        for _, r in fresh_last.iterrows()
    }

    for disclosure_date, row in fresh_map.items():
        if not disclosure_date:
            continue
        prev_row = prev_map.get(disclosure_date)
        if prev_row is None:
            changes.append(
                {
                    "ticker": symbol,
                    "disclosure_date": disclosure_date,
                    "change_type": "new_disclosure",
                    "changed_fields": "new_row",
                    "prev_report_date": "",
                    "new_report_date": _as_iso_date(row.get("report_date")),
                    "prev_eps": "",
                    "new_eps": _as_float(row.get("eps")),
                    "prev_revenue": "",
                    "new_revenue": _as_float(row.get("revenue")),
                    "prev_roe": "",
                    "new_roe": _as_float(row.get("roe")),
                    "prev_gross_margin": "",
                    "new_gross_margin": _as_float(row.get("gross_margin")),
                }
            )
            continue

        changed_fields: List[str] = []
        for col in ["report_date", "eps", "revenue", "roe", "gross_margin"]:
            if _value_changed(prev_row.get(col), row.get(col)):
                changed_fields.append(col)

        if changed_fields:
            changes.append(
                {
                    "ticker": symbol,
                    "disclosure_date": disclosure_date,
                    "change_type": "revised_disclosure",
                    "changed_fields": ",".join(changed_fields),
                    "prev_report_date": _as_iso_date(prev_row.get("report_date")),
                    "new_report_date": _as_iso_date(row.get("report_date")),
                    "prev_eps": _as_float(prev_row.get("eps")),
                    "new_eps": _as_float(row.get("eps")),
                    "prev_revenue": _as_float(prev_row.get("revenue")),
                    "new_revenue": _as_float(row.get("revenue")),
                    "prev_roe": _as_float(prev_row.get("roe")),
                    "new_roe": _as_float(row.get("roe")),
                    "prev_gross_margin": _as_float(prev_row.get("gross_margin")),
                    "new_gross_margin": _as_float(row.get("gross_margin")),
                }
            )

    return sorted(changes, key=lambda x: (x["ticker"], x["disclosure_date"], x["change_type"]))


def _months_to_window_map(months: List[int]) -> Dict[int, date]:
    today = date.today()
    return {m: today - timedelta(days=int(30 * m)) for m in months}


def _snapshot_changed_fields(before: Optional[Dict], after: Optional[Dict]) -> List[str]:
    changed: List[str] = []
    for field in SNAPSHOT_FIELDS:
        b = None if before is None else before.get(field)
        a = None if after is None else after.get(field)
        if _value_changed(b, a):
            changed.append(field)
    return changed


def _append_rows_csv(path: Path, rows: List[Dict]) -> None:
    if not rows:
        return
    new_df = pd.DataFrame(rows)
    if path.exists():
        old_df = pd.read_csv(path)
        cols = list(dict.fromkeys(list(old_df.columns) + list(new_df.columns)))
        out_df = pd.concat(
            [old_df.reindex(columns=cols), new_df.reindex(columns=cols)],
            ignore_index=True,
        )
    else:
        out_df = new_df
    path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(path, index=False)


def _read_csv_if_exists(path: Path) -> pd.DataFrame:
    if path.exists():
        try:
            return pd.read_csv(path)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def _compute_validation_deltas(
    before_df: pd.DataFrame,
    after_df: pd.DataFrame,
    impacted_labels: List[str],
    run_id: str,
    run_ts_utc: str,
) -> List[Dict]:
    if before_df.empty and after_df.empty:
        return []

    out: List[Dict] = []
    idx_before = {str(r.get("label")): r for _, r in before_df.iterrows()} if not before_df.empty else {}
    idx_after = {str(r.get("label")): r for _, r in after_df.iterrows()} if not after_df.empty else {}

    for label in impacted_labels:
        b = idx_before.get(label)
        a = idx_after.get(label)
        if b is None and a is None:
            continue

        row = {
            "run_id": run_id,
            "run_ts_utc": run_ts_utc,
            "label": label,
            "before_status": "" if b is None else str(b.get("status", "")),
            "after_status": "" if a is None else str(a.get("status", "")),
            "before_n_picks": None if b is None else b.get("n_picks"),
            "after_n_picks": None if a is None else a.get("n_picks"),
            "before_mean_net_return_pct": None if b is None else b.get("mean_net_return_pct"),
            "after_mean_net_return_pct": None if a is None else a.get("mean_net_return_pct"),
            "before_alpha_vs_spy_pct": None if b is None else b.get("alpha_vs_spy_pct"),
            "after_alpha_vs_spy_pct": None if a is None else a.get("alpha_vs_spy_pct"),
        }

        try:
            b_np = float(b.get("n_picks")) if b is not None and pd.notna(b.get("n_picks")) else None
            a_np = float(a.get("n_picks")) if a is not None and pd.notna(a.get("n_picks")) else None
            row["delta_n_picks"] = None if (b_np is None or a_np is None) else (a_np - b_np)
        except Exception:
            row["delta_n_picks"] = None

        try:
            b_mn = float(b.get("mean_net_return_pct")) if b is not None and pd.notna(b.get("mean_net_return_pct")) else None
            a_mn = float(a.get("mean_net_return_pct")) if a is not None and pd.notna(a.get("mean_net_return_pct")) else None
            row["delta_mean_net_return_pct"] = None if (b_mn is None or a_mn is None) else (a_mn - b_mn)
        except Exception:
            row["delta_mean_net_return_pct"] = None

        try:
            b_al = float(b.get("alpha_vs_spy_pct")) if b is not None and pd.notna(b.get("alpha_vs_spy_pct")) else None
            a_al = float(a.get("alpha_vs_spy_pct")) if a is not None and pd.notna(a.get("alpha_vs_spy_pct")) else None
            row["delta_alpha_vs_spy_pct"] = None if (b_al is None or a_al is None) else (a_al - b_al)
        except Exception:
            row["delta_alpha_vs_spy_pct"] = None

        out.append(row)

    return out


def run(args) -> int:
    run_id = uuid.uuid4().hex[:12]
    run_ts_utc = _now_utc_iso()

    out_dir = Path(args.audit_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    dm = DataManager()
    fund_svc = FundamentalSnapshotService(dm)

    candidates, source = _load_candidate_symbols(args)
    if args.max_symbols and args.max_symbols > 0:
        candidates = candidates[: int(args.max_symbols)]

    if not candidates:
        print("No candidate symbols found. Exiting.")
        return 1

    print(f"Run ID: {run_id}")
    print(f"Candidates: {len(candidates)} (source={source})")

    # Stage 1: detect likely-impacted tickers via earnings calendar delta.
    cached_quarterly_by_symbol: Dict[str, pd.DataFrame] = {}
    refresh_candidates: List[str] = []

    total = len(candidates)
    for idx, symbol in enumerate(candidates, 1):
        prev = _normalize_quarterly(dm.parquet_store.load_quarterly_fundamentals(symbol), symbol)
        cached_quarterly_by_symbol[symbol] = prev

        should_refresh = bool(args.force_refresh_all_candidates)
        latest_cached = _latest_disclosure(prev)
        latest_calendar = None

        if not should_refresh:
            cal = _normalize_calendar_dates(dm.get_earnings_calendar(symbol))
            latest_calendar = max(cal) if cal else None
            if latest_calendar and (latest_cached is None or latest_calendar > latest_cached):
                should_refresh = True

        if should_refresh:
            refresh_candidates.append(symbol)

        if idx % args.progress_every == 0 or idx == total:
            print(
                f"  [detect] {idx}/{total} refresh_candidates={len(refresh_candidates)}"
            )

    print(f"Likely impacted (calendar delta): {len(refresh_candidates)}")

    # Stage 2: refresh quarterlies only for likely-impacted tickers and detect true deltas.
    release_changes: List[Dict] = []
    impacted_tickers = set()
    disclosure_dates_by_ticker: Dict[str, set[date]] = defaultdict(set)

    for idx, symbol in enumerate(refresh_candidates, 1):
        previous = cached_quarterly_by_symbol.get(symbol, pd.DataFrame(columns=QUARTERLY_FIELDS))
        fresh = _normalize_quarterly(dm.get_quarterly_fundamentals(symbol, force_refresh=True), symbol)
        changes = _compare_quarterly(previous, fresh, symbol)

        for change in changes:
            change["run_id"] = run_id
            change["run_ts_utc"] = run_ts_utc
            release_changes.append(change)
            impacted_tickers.add(symbol)
            d = _as_date(change.get("disclosure_date"))
            if d:
                disclosure_dates_by_ticker[symbol].add(d)

        if idx % args.progress_every == 0 or idx == len(refresh_candidates):
            print(
                f"  [refresh] {idx}/{len(refresh_candidates)} impacted_tickers={len(impacted_tickers)} "
                f"release_changes={len(release_changes)}"
            )

    impacted_tickers = set(sorted(impacted_tickers))

    months = _parse_months(args.months_back)
    window_map = _months_to_window_map(months)
    impacted_months = [
        m
        for m, as_of in window_map.items()
        if any(as_of >= d for dates in disclosure_dates_by_ticker.values() for d in dates)
    ]
    impacted_months = sorted(set(impacted_months))
    impacted_labels = [f"{m}m_ago" for m in impacted_months]

    # Stage 3: ticker-scoped point-in-time snapshot invalidation/recompute.
    cache_dates = fund_svc.list_persistent_cache_dates()
    all_as_of_dates = sorted(set(cache_dates + list(window_map.values()) + [date.today()]))

    affected_dates_by_ticker: Dict[str, set[date]] = defaultdict(set)
    for ticker, disclosures in disclosure_dates_by_ticker.items():
        for as_of in all_as_of_dates:
            if any(as_of >= d for d in disclosures):
                affected_dates_by_ticker[ticker].add(as_of)

    before_snapshots: Dict[tuple[str, str], Optional[Dict]] = {}
    for ticker, as_of_dates in affected_dates_by_ticker.items():
        for as_of in sorted(as_of_dates):
            key = (ticker, as_of.isoformat())
            before_snapshots[key] = fund_svc.get_persistent_snapshot_row(as_of, ticker)

    invalidated_rows = 0
    recomputed_rows = 0
    affected_dates_sorted = sorted({d for ds in affected_dates_by_ticker.values() for d in ds})

    for idx, as_of in enumerate(affected_dates_sorted, 1):
        tickers_for_date = sorted([t for t, ds in affected_dates_by_ticker.items() if as_of in ds])
        if not tickers_for_date:
            continue

        if args.dry_run:
            removed = 0
        else:
            removed = fund_svc.invalidate_persistent_tickers(as_of, tickers_for_date)
            invalidated_rows += int(removed)
            refreshed = fund_svc.load_for_tickers(
                tickers_for_date,
                existing_snapshot_path=None,
                progress_every=max(1, args.progress_every),
                as_of_date=as_of,
                use_persistent_cache=True,
                refresh_persistent_cache=False,
            )
            recomputed_rows += int(len(refreshed))

        if idx % max(1, min(args.progress_every, 20)) == 0 or idx == len(affected_dates_sorted):
            print(
                f"  [snapshot] {idx}/{len(affected_dates_sorted)} dates "
                f"invalidated_rows={invalidated_rows} recomputed_rows={recomputed_rows}"
            )

    after_snapshots: Dict[tuple[str, str], Optional[Dict]] = {}
    for ticker, as_of_dates in affected_dates_by_ticker.items():
        for as_of in sorted(as_of_dates):
            key = (ticker, as_of.isoformat())
            after_snapshots[key] = fund_svc.get_persistent_snapshot_row(as_of, ticker)

    snapshot_change_rows: List[Dict] = []
    for key in sorted(set(before_snapshots.keys()) | set(after_snapshots.keys())):
        ticker, as_of_iso = key
        before = before_snapshots.get(key)
        after = after_snapshots.get(key)
        changed_fields = _snapshot_changed_fields(before, after)

        row = {
            "run_id": run_id,
            "run_ts_utc": run_ts_utc,
            "ticker": ticker,
            "as_of_date": as_of_iso,
            "changed": bool(changed_fields),
            "changed_fields": ",".join(changed_fields),
        }

        for field in SNAPSHOT_FIELDS:
            row[f"before_{field}"] = None if before is None else before.get(field)
            row[f"after_{field}"] = None if after is None else after.get(field)

        snapshot_change_rows.append(row)

    # Stage 4: targeted validation rerun.
    validation_rc: Optional[int] = None
    validation_cmd = ""
    validation_rows: List[Dict] = []

    if impacted_months and not args.skip_validation:
        windows_csv = Path(args.validation_windows_csv)
        trades_csv = Path(args.validation_trades_csv)
        md_path = Path(args.validation_md)

        before_windows = _read_csv_if_exists(windows_csv)

        cmd = [
            sys.executable,
            str(ROOT / "tools" / "validate_three_layer_walkforward.py"),
            "--config",
            str(args.config),
            "--months-back",
            ",".join(str(m) for m in impacted_months),
            "--output-windows-csv",
            str(windows_csv),
            "--output-trades-csv",
            str(trades_csv),
            "--output-md",
            str(md_path),
        ]

        validation_cmd = " ".join(cmd)
        print(f"Running validation: {validation_cmd}")
        if args.dry_run:
            validation_rc = 0
        else:
            validation_rc = subprocess.run(cmd, cwd=str(ROOT)).returncode

        after_windows = _read_csv_if_exists(windows_csv)
        validation_rows = _compute_validation_deltas(
            before_df=before_windows,
            after_df=after_windows,
            impacted_labels=impacted_labels,
            run_id=run_id,
            run_ts_utc=run_ts_utc,
        )

    # Stage 5: targeted scanner rerun.
    scanner_rc: Optional[int] = None
    scanner_cmd = ""
    scanner_output_path = ""
    scanned_tickers: List[str] = []

    if impacted_tickers and not args.skip_scanner:
        scanned_tickers = sorted(impacted_tickers)
        if args.scanner_max_tickers > 0:
            scanned_tickers = scanned_tickers[: args.scanner_max_tickers]

        if args.scanner_output:
            scanner_output_path = str(args.scanner_output)
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            scanner_output_path = str(Path(args.scanner_output_dir) / f"scan_earnings_refresh_{ts}.txt")

        Path(scanner_output_path).parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            str(ROOT / "tools" / "scanner.py"),
            *scanned_tickers,
            "--output",
            scanner_output_path,
        ]
        scanner_cmd = " ".join(cmd)
        print(f"Running scanner: {scanner_cmd}")
        if args.dry_run:
            scanner_rc = 0
        else:
            scanner_rc = subprocess.run(cmd, cwd=str(ROOT)).returncode

    # Stage 6: audit persistence.
    runs_path = out_dir / "earnings_refresh_runs.csv"
    releases_path = out_dir / "earnings_refresh_release_changes.csv"
    snapshots_path = out_dir / "earnings_refresh_snapshot_changes.csv"
    validation_path = out_dir / "earnings_refresh_validation_changes.csv"

    for row in release_changes:
        # Ensure date columns are serialized consistently.
        row["disclosure_date"] = _as_iso_date(row.get("disclosure_date"))
        row["prev_report_date"] = _as_iso_date(row.get("prev_report_date"))
        row["new_report_date"] = _as_iso_date(row.get("new_report_date"))

    _append_rows_csv(releases_path, release_changes)
    _append_rows_csv(snapshots_path, snapshot_change_rows)
    _append_rows_csv(validation_path, validation_rows)

    run_summary = {
        "run_id": run_id,
        "run_ts_utc": run_ts_utc,
        "candidate_source": source,
        "candidates_total": len(candidates),
        "refresh_candidates": len(refresh_candidates),
        "impacted_tickers": len(impacted_tickers),
        "release_changes": len(release_changes),
        "new_disclosures": sum(1 for r in release_changes if r.get("change_type") == "new_disclosure"),
        "revised_disclosures": sum(1 for r in release_changes if r.get("change_type") == "revised_disclosure"),
        "affected_asof_dates": len(affected_dates_sorted),
        "invalidated_snapshot_rows": invalidated_rows,
        "recomputed_snapshot_rows": recomputed_rows,
        "snapshot_change_rows": len(snapshot_change_rows),
        "snapshot_rows_with_changes": sum(1 for r in snapshot_change_rows if bool(r.get("changed"))),
        "impacted_validation_months": ",".join(str(m) for m in impacted_months),
        "validation_rc": validation_rc,
        "scanner_rc": scanner_rc,
        "scanned_tickers": len(scanned_tickers),
        "scanner_output": scanner_output_path,
        "validation_cmd": validation_cmd,
        "scanner_cmd": scanner_cmd,
        "dry_run": bool(args.dry_run),
    }
    _append_rows_csv(runs_path, [run_summary])

    print("\n=== Incremental Earnings Refresh Summary ===")
    print(f"Run ID: {run_id}")
    print(f"Candidates checked: {len(candidates)}")
    print(f"Likely impacted tickers (calendar): {len(refresh_candidates)}")
    print(f"Impacted tickers (actual quarterly changes): {len(impacted_tickers)}")
    print(f"Release changes detected: {len(release_changes)}")
    print(f"Affected snapshot as-of dates: {len(affected_dates_sorted)}")
    print(f"Snapshot rows invalidated: {invalidated_rows}")
    print(f"Snapshot rows recomputed: {recomputed_rows}")
    print(f"Impacted validation months: {','.join(str(m) for m in impacted_months) if impacted_months else '(none)'}")
    if scanner_output_path:
        print(f"Scanner output: {scanner_output_path}")
    print(f"Audit runs table: {runs_path}")
    print(f"Audit release table: {releases_path}")
    print(f"Audit snapshot table: {snapshots_path}")
    print(f"Audit validation table: {validation_path}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Incremental earnings refresh with targeted cache invalidation/revalidation.")
    parser.add_argument("--config", type=str, default="config/picker_config.yaml", help="Picker config path")
    parser.add_argument("--symbols", nargs="*", help="Explicit ticker list to process")
    parser.add_argument("--symbols-file", type=str, default="", help="Optional file with one ticker per line")
    parser.add_argument(
        "--technical-universe-csv",
        type=str,
        default="results/picker/three_layer_after_technical.csv",
        help="Default candidate source when symbols are not provided",
    )
    parser.add_argument("--max-symbols", type=int, default=0, help="Optional cap on candidate symbols")
    parser.add_argument(
        "--force-refresh-all-candidates",
        action="store_true",
        help="Force quarterly refresh for all candidates (skip calendar prefilter)",
    )
    parser.add_argument(
        "--months-back",
        type=str,
        default="3,6,9,12",
        help="Walk-forward months used to determine targeted validation windows",
    )
    parser.add_argument("--skip-validation", action="store_true", help="Skip targeted walk-forward rerun")
    parser.add_argument("--skip-scanner", action="store_true", help="Skip targeted scanner rerun")
    parser.add_argument(
        "--validation-windows-csv",
        type=str,
        default="results/picker/walkforward_windows_report.csv",
        help="Validation windows output path",
    )
    parser.add_argument(
        "--validation-trades-csv",
        type=str,
        default="results/picker/walkforward_trades_report.csv",
        help="Validation trades output path",
    )
    parser.add_argument(
        "--validation-md",
        type=str,
        default="results/picker/walkforward_report.md",
        help="Validation markdown report path",
    )
    parser.add_argument(
        "--scanner-max-tickers",
        type=int,
        default=200,
        help="Max impacted tickers to pass into scanner rerun",
    )
    parser.add_argument(
        "--scanner-output-dir",
        type=str,
        default="results",
        help="Output directory for targeted scanner report",
    )
    parser.add_argument("--scanner-output", type=str, default="", help="Optional explicit scanner output file")
    parser.add_argument(
        "--audit-dir",
        type=str,
        default="results/picker",
        help="Directory for audit csv tables",
    )
    parser.add_argument("--progress-every", type=int, default=50, help="Progress logging interval")
    parser.add_argument("--dry-run", action="store_true", help="Detect + plan + audit, without recompute/revalidation/scanner execution")

    args = parser.parse_args()
    rc = run(args)
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
