#!/usr/bin/env python3
"""
Fetch upcoming earnings calendar with release session (pre-market/post-market).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, List

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
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


def _load_candidates(args) -> tuple[List[str], str]:
    if args.symbols:
        return _stable_unique(args.symbols), "cli_symbols"

    if args.symbols_file:
        return _load_symbols_file(Path(args.symbols_file)), "symbols_file"

    if args.all_listed:
        return _stable_unique(load_all_listed_us_symbols()), "listed_universe"

    path = Path(args.technical_universe_csv)
    if path.exists():
        try:
            df = pd.read_csv(path)
            if "ticker" in df.columns:
                return _stable_unique(df["ticker"].astype(str).tolist()), "technical_universe_csv"
        except Exception:
            pass

    return _stable_unique(load_all_listed_us_symbols()), "listed_universe"


def run(args) -> int:
    from_date = date.fromisoformat(args.from_date) if args.from_date else date.today()
    to_date = date.fromisoformat(args.to_date) if args.to_date else (from_date + timedelta(days=args.days_ahead))
    if to_date < from_date:
        raise ValueError("to-date must be >= from-date")

    symbols, source = _load_candidates(args)
    if args.max_symbols and args.max_symbols > 0:
        symbols = symbols[: args.max_symbols]

    if not symbols:
        print("No symbols found.")
        return 1

    print(f"Candidates: {len(symbols)} (source={source})")
    print(f"Window: {from_date} -> {to_date}")

    dm = DataManager()

    rows = []
    total = len(symbols)
    for i, sym in enumerate(symbols, 1):
        cal = dm.get_earnings_calendar(sym)
        if cal is None or cal.empty:
            if i % args.progress_every == 0 or i == total:
                print(f"  [calendar] {i}/{total} rows={len(rows)}")
            continue

        d = cal.copy()
        for col in ["report_date", "disclosure_date"]:
            if col in d.columns:
                d[col] = pd.to_datetime(d[col], errors="coerce").dt.date
        if "disclosure_date" in d.columns:
            d = d[(d["disclosure_date"] >= from_date) & (d["disclosure_date"] <= to_date)]
        elif "report_date" in d.columns:
            d = d[(d["report_date"] >= from_date) & (d["report_date"] <= to_date)]

        if d.empty:
            if i % args.progress_every == 0 or i == total:
                print(f"  [calendar] {i}/{total} rows={len(rows)}")
            continue

        d["ticker"] = sym
        if "release_session" not in d.columns:
            d["release_session"] = "unknown"
        if "release_time_et" not in d.columns:
            d["release_time_et"] = ""

        keep_cols = [
            "ticker",
            "report_date",
            "disclosure_date",
            "release_time_et",
            "release_session",
            "source",
        ]
        for col in keep_cols:
            if col not in d.columns:
                d[col] = ""
        rows.extend(d[keep_cols].to_dict("records"))

        if i % args.progress_every == 0 or i == total:
            print(f"  [calendar] {i}/{total} rows={len(rows)}")

    out = pd.DataFrame(rows)
    if out.empty:
        print("No earnings rows found in the requested window.")
        return 0

    out = out.drop_duplicates(subset=["ticker", "disclosure_date", "release_time_et"], keep="last")
    out = out.sort_values(["disclosure_date", "release_session", "ticker"]).reset_index(drop=True)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)

    summary = out["release_session"].fillna("unknown").value_counts().to_dict()
    print("\n=== Upcoming Earnings Session Summary ===")
    for key in sorted(summary):
        print(f"{key}: {summary[key]}")

    print(f"\nSaved: {out_path}")
    print(out.head(args.preview_rows).to_string(index=False))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Upcoming earnings calendar with pre/post-market session labels")
    parser.add_argument("symbols", nargs="*", help="Explicit ticker list")
    parser.add_argument("--symbols-file", type=str, default="", help="Optional file with one ticker per line")
    parser.add_argument("--all-listed", action="store_true", help="Use full listed universe")
    parser.add_argument(
        "--technical-universe-csv",
        type=str,
        default="results/picker/three_layer_after_technical.csv",
        help="Default candidate source when symbols are not passed",
    )
    parser.add_argument("--max-symbols", type=int, default=0, help="Optional cap on symbols")
    parser.add_argument("--from-date", type=str, default="", help="Start date YYYY-MM-DD (default: today)")
    parser.add_argument("--to-date", type=str, default="", help="End date YYYY-MM-DD")
    parser.add_argument("--days-ahead", type=int, default=14, help="Used when --to-date is omitted")
    parser.add_argument(
        "--output",
        type=str,
        default="results/picker/earnings_calendar_upcoming.csv",
        help="Output CSV path",
    )
    parser.add_argument("--progress-every", type=int, default=50, help="Progress logging interval")
    parser.add_argument("--preview-rows", type=int, default=25, help="Rows to print from output")

    args = parser.parse_args()
    rc = run(args)
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
