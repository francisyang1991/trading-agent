#!/usr/bin/env python3
"""
Cache health preflight for signal generation.

Use this before running picker/scanner to validate whether cached price data is
safe enough for signal generation under configurable thresholds.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data_manager import DataManager
from src.universe.listed_symbols import load_all_listed_us_symbols


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


def _invalid_row_condition() -> str:
    return (
        "open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL "
        "OR open <= 0 OR high <= 0 OR low <= 0 OR close <= 0 OR high < low"
    )


def _to_date(value) -> Optional[date]:
    try:
        if value is None or pd.isna(value):
            return None
        return pd.to_datetime(value, errors="coerce").date()
    except Exception:
        return None


def _json_safe(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _records_json_safe(rows: List[Dict]) -> List[Dict]:
    out: List[Dict] = []
    for row in rows:
        out.append({k: _json_safe(v) for k, v in row.items()})
    return out


@dataclass
class HealthThresholds:
    max_invalid_rows: int
    max_missing_pct: float
    min_fresh_pct: float
    min_minbars_pct: float
    max_stale_days: int
    min_bars: int
    period_days: int
    include_quarterly: bool
    min_quarterly_coverage_pct: float


@dataclass
class HealthSummary:
    as_of_date: str
    scope: str
    symbol_count: int
    db_symbol_count: int
    invalid_rows_global: int
    universe_missing_symbols: int
    universe_fresh_symbols: int
    universe_minbars_symbols: int
    universe_has_period_span_symbols: int
    fresh_pct: float
    minbars_pct: float
    missing_pct: float
    period_span_pct: float
    quarterly_coverage_pct: Optional[float]
    status: str


def _resolve_scope_symbols(args) -> tuple[List[str], str]:
    if args.symbols:
        return _stable_unique(args.symbols), "explicit_symbols"

    if args.symbols_file:
        return _load_symbols_file(Path(args.symbols_file)), "symbols_file"

    scope = str(args.scope).lower().strip()
    if scope == "listed":
        return _stable_unique(load_all_listed_us_symbols()), "listed"

    if scope == "technical":
        tech_path = Path(args.technical_universe_csv)
        if tech_path.exists():
            try:
                df = pd.read_csv(tech_path)
                if "ticker" in df.columns:
                    return _stable_unique(df["ticker"].astype(str).tolist()), "technical"
            except Exception:
                pass
        return _stable_unique(load_all_listed_us_symbols()), "listed_fallback"

    # db scope
    conn = sqlite3.connect(str(ROOT / "data" / "stock_cache.db"))
    db = pd.read_sql_query("SELECT DISTINCT symbol FROM stock_daily", conn)
    conn.close()
    return _stable_unique(db["symbol"].astype(str).tolist()), "db"


def _pct(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return float(part) / float(total)


def run(args) -> int:
    dm = DataManager()

    if args.repair_invalid_first:
        removed = dm.purge_invalid_daily_rows(None)
        print(f"Repaired invalid rows before audit: removed={removed}")

    symbols, resolved_scope = _resolve_scope_symbols(args)
    if args.max_symbols and args.max_symbols > 0:
        symbols = symbols[: args.max_symbols]

    if not symbols:
        print("No symbols found for health check.")
        return 1

    as_of = date.today()
    stale_cutoff = as_of - timedelta(days=args.max_stale_days)
    period_start = as_of - timedelta(days=args.period_days)

    conn = sqlite3.connect(str(ROOT / "data" / "stock_cache.db"))
    invalid_rows = int(
        pd.read_sql_query(
            f"SELECT COUNT(*) AS n FROM stock_daily WHERE {_invalid_row_condition()}", conn
        ).iloc[0]["n"]
        or 0
    )

    agg = pd.read_sql_query(
        """
        SELECT
            symbol,
            MIN(date) AS min_date,
            MAX(date) AS max_date,
            COUNT(*) AS total_rows,
            SUM(CASE WHEN date >= ? THEN 1 ELSE 0 END) AS rows_in_period
        FROM stock_daily
        GROUP BY symbol
        """,
        conn,
        params=(period_start.isoformat(),),
    )
    conn.close()

    agg["symbol"] = agg["symbol"].astype(str).str.upper().str.strip()
    agg["min_date"] = pd.to_datetime(agg["min_date"], errors="coerce").dt.date
    agg["max_date"] = pd.to_datetime(agg["max_date"], errors="coerce").dt.date
    agg["total_rows"] = pd.to_numeric(agg["total_rows"], errors="coerce").fillna(0).astype(int)
    agg["rows_in_period"] = pd.to_numeric(agg["rows_in_period"], errors="coerce").fillna(0).astype(int)

    universe = pd.DataFrame({"symbol": symbols})
    joined = universe.merge(agg, on="symbol", how="left")

    joined["in_db"] = joined["total_rows"].notna()
    joined["is_fresh"] = joined["max_date"].apply(lambda d: (_to_date(d) is not None and _to_date(d) >= stale_cutoff))
    joined["has_min_bars"] = joined["rows_in_period"].fillna(0).astype(int) >= int(args.min_bars)
    joined["has_period_span"] = (
        joined["min_date"].apply(lambda d: (_to_date(d) is not None and _to_date(d) <= period_start))
        & joined["is_fresh"]
    )

    n = len(joined)
    missing = int((~joined["in_db"]).sum())
    fresh = int(joined["is_fresh"].sum())
    minbars = int(joined["has_min_bars"].sum())
    period_span = int(joined["has_period_span"].sum())

    quarterly_cov_pct: Optional[float] = None
    if args.include_quarterly:
        qstats = dm.validate_quarterly_fundamentals_coverage(symbols)
        q_total = int(qstats.get("symbols_total", 0) or 0)
        q_have = int(qstats.get("symbols_with_quarterly_data", 0) or 0)
        quarterly_cov_pct = _pct(q_have, q_total)

    thresholds = HealthThresholds(
        max_invalid_rows=int(args.max_invalid_rows),
        max_missing_pct=float(args.max_missing_pct),
        min_fresh_pct=float(args.min_fresh_pct),
        min_minbars_pct=float(args.min_minbars_pct),
        max_stale_days=int(args.max_stale_days),
        min_bars=int(args.min_bars),
        period_days=int(args.period_days),
        include_quarterly=bool(args.include_quarterly),
        min_quarterly_coverage_pct=float(args.min_quarterly_coverage_pct),
    )

    checks = {
        "invalid_rows_ok": invalid_rows <= thresholds.max_invalid_rows,
        "missing_ok": _pct(n - missing, n) >= (1.0 - thresholds.max_missing_pct),
        "fresh_ok": _pct(fresh, n) >= thresholds.min_fresh_pct,
        "minbars_ok": _pct(minbars, n) >= thresholds.min_minbars_pct,
    }
    if thresholds.include_quarterly:
        checks["quarterly_ok"] = (quarterly_cov_pct or 0.0) >= thresholds.min_quarterly_coverage_pct

    status = "PASS" if all(checks.values()) else "FAIL"

    summary = HealthSummary(
        as_of_date=as_of.isoformat(),
        scope=resolved_scope,
        symbol_count=n,
        db_symbol_count=int(agg["symbol"].nunique()),
        invalid_rows_global=invalid_rows,
        universe_missing_symbols=missing,
        universe_fresh_symbols=fresh,
        universe_minbars_symbols=minbars,
        universe_has_period_span_symbols=period_span,
        fresh_pct=_pct(fresh, n),
        minbars_pct=_pct(minbars, n),
        missing_pct=_pct(missing, n),
        period_span_pct=_pct(period_span, n),
        quarterly_coverage_pct=quarterly_cov_pct,
        status=status,
    )

    worst_missing = joined[~joined["in_db"]]["symbol"].head(args.show_samples).tolist()
    worst_stale = (
        joined[joined["in_db"]]
        .sort_values(["max_date", "rows_in_period"], ascending=[True, True])
        .head(args.show_samples)[["symbol", "max_date", "rows_in_period"]]
        .to_dict("records")
    )
    worst_short = (
        joined[joined["in_db"]]
        .sort_values(["rows_in_period", "max_date"], ascending=[True, True])
        .head(args.show_samples)[["symbol", "rows_in_period", "max_date"]]
        .to_dict("records")
    )

    report = {
        "summary": asdict(summary),
        "thresholds": asdict(thresholds),
        "checks": checks,
        "samples": {
            "missing_symbols": worst_missing,
            "stale_or_oldest": _records_json_safe(worst_stale),
            "shortest_period_bars": _records_json_safe(worst_short),
        },
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print("=== Cache Health Check ===")
    print(f"Status: {summary.status}")
    print(f"Scope: {summary.scope}")
    print(f"Universe symbols: {summary.symbol_count}")
    print(f"Invalid rows (global): {summary.invalid_rows_global}")
    print(f"Missing in DB: {summary.universe_missing_symbols} ({summary.missing_pct*100:.2f}%)")
    print(f"Fresh symbols: {summary.universe_fresh_symbols} ({summary.fresh_pct*100:.2f}%)")
    print(f"Min bars >= {thresholds.min_bars}: {summary.universe_minbars_symbols} ({summary.minbars_pct*100:.2f}%)")
    print(f"Has full period span: {summary.universe_has_period_span_symbols} ({summary.period_span_pct*100:.2f}%)")
    if summary.quarterly_coverage_pct is not None:
        print(f"Quarterly coverage: {summary.quarterly_coverage_pct*100:.2f}%")
    print(f"Report: {out_path}")

    if summary.status != "PASS" and args.strict:
        return 2
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate cache health before generating signals")
    parser.add_argument("symbols", nargs="*", help="Optional explicit symbol list")
    parser.add_argument("--symbols-file", type=str, default="", help="Optional file with one ticker per line")
    parser.add_argument(
        "--scope",
        type=str,
        default="technical",
        choices=["technical", "listed", "db"],
        help="Universe scope when symbols are not provided",
    )
    parser.add_argument(
        "--technical-universe-csv",
        type=str,
        default="results/picker/three_layer_after_technical.csv",
        help="CSV used for technical scope",
    )
    parser.add_argument("--max-symbols", type=int, default=0, help="Optional cap on symbols")

    parser.add_argument("--period-days", type=int, default=730, help="History window size used for min-bars check")
    parser.add_argument("--min-bars", type=int, default=260, help="Minimum bars required in period")
    parser.add_argument("--max-stale-days", type=int, default=7, help="Symbol considered stale if last bar older than this")

    parser.add_argument("--max-invalid-rows", type=int, default=0, help="Allowed invalid rows in global cache")
    parser.add_argument("--max-missing-pct", type=float, default=0.02, help="Max missing symbols ratio")
    parser.add_argument("--min-fresh-pct", type=float, default=0.98, help="Minimum fresh symbols ratio")
    parser.add_argument("--min-minbars-pct", type=float, default=0.98, help="Minimum min-bars-pass ratio")

    parser.add_argument("--include-quarterly", action="store_true", help="Also check quarterly fundamentals cache coverage")
    parser.add_argument(
        "--min-quarterly-coverage-pct",
        type=float,
        default=0.50,
        help="Minimum quarterly fundamentals coverage ratio when --include-quarterly is used",
    )

    parser.add_argument("--repair-invalid-first", action="store_true", help="Purge invalid OHLCV rows before auditing")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on failed checks")
    parser.add_argument("--show-samples", type=int, default=20, help="Number of sample symbols in report")
    parser.add_argument(
        "--output",
        type=str,
        default="results/picker/cache_health_report.json",
        help="JSON report output path",
    )

    args = parser.parse_args()
    rc = run(args)
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
