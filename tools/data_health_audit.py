#!/usr/bin/env python3
"""
Data Foundation Health Audit — Weekly/Monthly Check-in

Scans the database and fundamental stores to report:
1. Price data: what we have, what's missing, coverage by symbol
2. Fundamental data: quarterly parquet, snapshot cache, field-level coverage
3. Optional: add problematic symbols to blacklist

Run weekly or monthly via cron. Output: results/picker/data_health_report.md

Usage:
    python tools/data_health_audit.py
    python tools/data_health_audit.py --add-to-blacklist  # Add failed symbols to blacklist
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.blacklist_store import DataBlacklistStore
from src.data_manager import DataManager
from src.universe.listed_symbols import load_all_listed_us_symbols


def _pct(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return 100.0 * part / total


def _audit_prices(dm: DataManager, listed: list[str], db_path: Path) -> dict:
    """Scan stock_daily and compare to listed universe."""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("SELECT COUNT(DISTINCT symbol) FROM stock_daily")
    db_symbols = int(cur.fetchone()[0] or 0)

    cur.execute("SELECT COUNT(*) FROM stock_daily")
    total_rows = int(cur.fetchone()[0] or 0)

    agg = conn.execute(
        """
        SELECT symbol, MIN(date) AS min_date, MAX(date) AS max_date, COUNT(*) AS bars
        FROM stock_daily
        GROUP BY symbol
        """
    ).fetchall()

    conn.close()

    listed_set = {s.upper() for s in listed}
    db_by_sym = {str(row[0]).upper(): {"min": row[1], "max": row[2], "bars": int(row[3])} for row in agg}

    in_db = [s for s in listed_set if s in db_by_sym]
    missing = [s for s in listed_set if s not in db_by_sym]

    # Bar count distribution
    bars_260_plus = sum(1 for r in db_by_sym.values() if r["bars"] >= 260)
    bars_60_259 = sum(1 for r in db_by_sym.values() if 60 <= r["bars"] < 260)
    bars_under_60 = sum(1 for r in db_by_sym.values() if r["bars"] < 60)

    return {
        "listed_count": len(listed_set),
        "db_symbol_count": db_symbols,
        "total_rows": total_rows,
        "in_db_count": len(in_db),
        "missing_count": len(missing),
        "missing_pct": _pct(len(missing), len(listed_set)),
        "bars_260_plus": bars_260_plus,
        "bars_60_259": bars_60_259,
        "bars_under_60": bars_under_60,
        "missing_sample": sorted(missing)[:50],
        "db_only_symbols": len(db_by_sym) - len(in_db),  # In DB but not in listed
    }


def _audit_fundamentals(dm: DataManager, listed: list[str]) -> dict:
    """Scan parquet quarterly + snapshot cache, report field coverage."""
    parquet_fund = ROOT / "data" / "fundamental"
    snapshot_dir = ROOT / "data" / "picker" / "fundamental_snapshots"

    listed_set = {s.upper() for s in listed}

    # Parquet quarterly
    parquet_files = list(parquet_fund.glob("*.parquet")) + list(parquet_fund.glob("*.csv"))
    parquet_symbols = {f.stem.upper() for f in parquet_files}

    # Snapshot cache (by date)
    snapshot_dates = []
    if snapshot_dir.exists():
        for f in snapshot_dir.glob("*.csv"):
            try:
                snapshot_dates.append(date.fromisoformat(f.stem))
            except Exception:
                pass
    snapshot_dates = sorted(set(snapshot_dates))
    latest_snapshot = snapshot_dates[-1] if snapshot_dates else None

    latest_snapshot_symbols = set()
    latest_snapshot_rows = 0
    if latest_snapshot:
        p = snapshot_dir / f"{latest_snapshot.isoformat()}.csv"
        if p.exists():
            try:
                df = __import__("pandas").read_csv(p)
                if "ticker" in df.columns:
                    latest_snapshot_symbols = {str(s).upper() for s in df["ticker"].tolist()}
                    latest_snapshot_rows = len(df)
            except Exception:
                pass

    # Field-level coverage from parquet (sample)
    import pandas as pd

    fields = ["eps", "revenue", "roe", "gross_margin", "disclosure_date"]
    field_counts = {f: 0 for f in fields}
    sampled = 0
    for sym in list(parquet_symbols)[:500]:  # Sample 500
        try:
            q = dm.parquet_store.load_quarterly_fundamentals(sym)
            if q is not None and not q.empty:
                sampled += 1
                q = q.sort_values("report_date")
                latest = q.iloc[-1]
                for f in fields:
                    if f in latest.index and pd.notna(latest.get(f)):
                        field_counts[f] += 1
        except Exception:
            pass

    return {
        "parquet_quarterly_count": len(parquet_symbols),
        "parquet_quarterly_pct": _pct(len(parquet_symbols), len(listed_set)),
        "snapshot_dates_count": len(snapshot_dates),
        "latest_snapshot_date": latest_snapshot.isoformat() if latest_snapshot else None,
        "latest_snapshot_symbols": len(latest_snapshot_symbols),
        "latest_snapshot_rows": latest_snapshot_rows,
        "missing_quarterly": len(listed_set - parquet_symbols),
        "missing_quarterly_pct": _pct(len(listed_set - parquet_symbols), len(listed_set)),
        "field_coverage_sample": {f: _pct(field_counts[f], sampled) if sampled else 0 for f in fields},
        "field_counts_sample": field_counts,
        "sampled_symbols": sampled,
    }


def _write_report(report: dict, out_path: Path) -> None:
    """Write markdown report."""
    p = report["prices"]
    f = report["fundamentals"]
    bl = report.get("blacklist", {})

    lines = [
        "# Data Foundation Health Report",
        "",
        f"Generated: {report.get('generated_at', '')}",
        "",
        "## 1. Price Data (stock_daily)",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Listed universe | {p['listed_count']} |",
        f"| In DB | {p['in_db_count']} |",
        f"| Missing from DB | {p['missing_count']} ({p['missing_pct']:.1f}%) |",
        f"| Total rows | {p['total_rows']} |",
        f"| Symbols with ≥260 bars | {p['bars_260_plus']} |",
        f"| Symbols with 60–259 bars | {p['bars_60_259']} |",
        f"| Symbols with <60 bars | {p['bars_under_60']} |",
        "",
        "### Missing symbols (sample)",
        "",
        ", ".join(p["missing_sample"][:30]) + (" ..." if len(p["missing_sample"]) > 30 else ""),
        "",
        "## 2. Fundamental Data",
        "",
        "### Quarterly (parquet)",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Symbols with quarterly data | {f['parquet_quarterly_count']} ({f['parquet_quarterly_pct']:.1f}%) |",
        f"| Missing from parquet | {f['missing_quarterly']} ({f['missing_quarterly_pct']:.1f}%) |",
        "",
        "### Snapshot cache (per-date)",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Snapshot dates | {f['snapshot_dates_count']} |",
        f"| Latest snapshot | {f['latest_snapshot_date']} |",
        f"| Symbols in latest | {f['latest_snapshot_symbols']} |",
        "",
        "### Field coverage (sample of 500 symbols)",
        "",
    ]
    for field, pct in f["field_coverage_sample"].items():
        lines.append(f"- **{field}**: {pct:.1f}%")
    lines.extend(["", "## 3. Blacklist", "", f"| Symbol count | {bl.get('count', 0)} |", ""])

    if report.get("blacklist_added"):
        lines.append(f"**Added to blacklist this run:** {report['blacklist_added']} symbols")
        lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def run(args) -> int:
    dm = DataManager()
    db_path = ROOT / "data" / "stock_cache.db"
    blacklist_store = DataBlacklistStore(dm)
    blacklist = blacklist_store.load()

    print("[Data Health Audit] Loading listed universe...")
    listed = load_all_listed_us_symbols()
    listed = [s for s in listed if s not in blacklist]
    print(f"  Listed (ex blacklist): {len(listed)}")

    print("[Data Health Audit] Scanning price data...")
    prices = _audit_prices(dm, listed, db_path)

    print("[Data Health Audit] Scanning fundamentals...")
    fundamentals = _audit_fundamentals(dm, listed)

    report = {
        "prices": prices,
        "fundamentals": fundamentals,
        "blacklist": {"count": len(blacklist)},
        "generated_at": datetime.now().isoformat(),
    }

    # Optionally add missing symbols to blacklist
    if args.add_to_blacklist and prices["missing_count"] > 0:
        conn = sqlite3.connect(str(db_path))
        db_syms = {r[0].upper() for r in conn.execute("SELECT DISTINCT symbol FROM stock_daily").fetchall()}
        conn.close()
        missing_set = {s.upper() for s in listed if s.upper() not in db_syms}
        if missing_set:
            blacklist_store.save(missing_set, reason="data_health_audit:missing_price")
            report["blacklist_added"] = len(missing_set)
            print(f"  Added {len(missing_set)} symbols to blacklist (missing price)")

    out_path = Path(args.output)
    _write_report(report, out_path)
    print(f"\nReport saved: {out_path}")

    # Console summary
    print("\n=== Summary ===")
    print(f"Price: {prices['in_db_count']}/{prices['listed_count']} ({100 - prices['missing_pct']:.1f}% coverage)")
    print(f"  ≥260 bars: {prices['bars_260_plus']} | 60–259: {prices['bars_60_259']} | <60: {prices['bars_under_60']}")
    print(f"Fundamentals: {fundamentals['parquet_quarterly_count']} quarterly ({fundamentals['parquet_quarterly_pct']:.1f}%)")
    print(f"  Latest snapshot: {fundamentals['latest_snapshot_date']} ({fundamentals['latest_snapshot_symbols']} symbols)")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Data foundation health audit — weekly/monthly check-in"
    )
    parser.add_argument(
        "--add-to-blacklist",
        action="store_true",
        help="Add symbols missing price data to blacklist",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="results/picker/data_health_report.md",
        help="Report output path",
    )
    args = parser.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
