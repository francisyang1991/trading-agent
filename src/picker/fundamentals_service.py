"""
Fundamental snapshot enrichment service for picker pipelines.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from src.data.providers.yfinance_provider import YFinanceProvider


class FundamentalSnapshotService:
    REQUIRED_COLUMNS = (
        "ticker",
        "eps_yoy",
        "revenue_growth",
        "revenue_acceleration",
        "earnings_surprise",
        "roe",
        "gross_margin",
        "debt_to_equity",
    )

    def __init__(self, data_manager):
        self._dm = data_manager
        self._provider = YFinanceProvider()

    def load_for_tickers(
        self,
        tickers: list[str],
        existing_snapshot_path: Optional[Path] = None,
        progress_every: int = 50,
    ) -> pd.DataFrame:
        cache_map = self._load_existing(existing_snapshot_path)
        rows = []
        total = len(tickers)
        start = time.time()

        for idx, ticker in enumerate(tickers, 1):
            symbol = str(ticker).upper()
            if symbol in cache_map:
                rows.append(cache_map[symbol])
                self._report_progress(idx, total, start, progress_every)
                continue

            snapshot = self._fetch_snapshot(symbol)
            rows.append(snapshot)
            self._report_progress(idx, total, start, progress_every)
            time.sleep(0.02)

        return self._to_frame(rows)

    def _fetch_snapshot(self, symbol: str) -> Dict:
        try:
            snap = self._provider.get_factor_fundamentals(symbol)
        except Exception:
            snap = {"ticker": symbol}

        missing_core = any(
            snap.get(key) is None for key in ("eps_yoy", "revenue_growth", "roe", "gross_margin")
        )
        if not missing_core:
            return snap

        # Fallback to DataManager cached profile. But NEVER treat 0 or 0.0
        # as real data — the old get_company_profile() defaulted missing
        # values to 0, which turns "no data" into "zero growth".
        fallback = self._dm.get_fundamentals(
            symbol,
            validate=True,
            strict=True,
            required_fields=["revenue_growth", "earnings_growth"],
        )

        def _safe_fallback(primary, fallback_val):
            """Return primary if not None, else fallback — but reject bare 0/0.0 as likely fake."""
            if primary is not None:
                return primary
            if fallback_val is None or fallback_val == 0 or fallback_val == 0.0:
                return None  # Unknown, not zero
            return fallback_val

        snap["eps_yoy"] = _safe_fallback(snap.get("eps_yoy"), fallback.get("earnings_growth"))
        snap["revenue_growth"] = _safe_fallback(snap.get("revenue_growth"), fallback.get("revenue_growth"))
        snap["roe"] = _safe_fallback(snap.get("roe"), fallback.get("roe"))
        snap["gross_margin"] = _safe_fallback(
            snap.get("gross_margin"),
            fallback.get("gross_margin") if fallback.get("gross_margin") is not None else fallback.get("profit_margin"),
        )
        snap["debt_to_equity"] = _safe_fallback(snap.get("debt_to_equity"), fallback.get("debt_to_equity"))
        # revenue_acceleration: None means unknown, not zero.
        if snap.get("revenue_acceleration") is None:
            snap["revenue_acceleration"] = None
        return snap

    def _load_existing(self, path: Optional[Path]) -> Dict[str, Dict]:
        if not path or not path.exists():
            return {}
        try:
            prev = pd.read_csv(path)
            if "ticker" not in prev.columns:
                return {}
            mapping = {}
            for _, row in prev.iterrows():
                symbol = str(row.get("ticker", "")).upper().strip()
                if symbol:
                    mapping[symbol] = row.to_dict()
            return mapping
        except Exception:
            return {}

    def _report_progress(self, idx: int, total: int, start: float, progress_every: int) -> None:
        if idx % progress_every == 0 or idx == total:
            elapsed = time.time() - start
            print(f"[fundamentals] {idx}/{total} ({elapsed:.1f}s)")

    def _to_frame(self, rows: list[Dict]) -> pd.DataFrame:
        df = pd.DataFrame(rows)
        for col in self.REQUIRED_COLUMNS:
            if col not in df.columns:
                df[col] = pd.NA
        return df[list(self.REQUIRED_COLUMNS)]
