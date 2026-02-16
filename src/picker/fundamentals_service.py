"""
Fundamental snapshot enrichment service for picker pipelines.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
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
        self._snapshot_cache_dir = Path("data") / "picker" / "fundamental_snapshots"
        self._snapshot_cache_dir.mkdir(parents=True, exist_ok=True)
        self._profile_de_cache: Dict[str, Optional[float]] = {}

    def load_for_tickers(
        self,
        tickers: list[str],
        existing_snapshot_path: Optional[Path] = None,
        progress_every: int = 50,
        as_of_date: Optional[date] = None,
        use_persistent_cache: bool = True,
        refresh_persistent_cache: bool = False,
    ) -> pd.DataFrame:
        run_date = as_of_date or date.today()
        cache_map = self._load_existing(existing_snapshot_path)
        persistent_map = {}
        if use_persistent_cache and not refresh_persistent_cache:
            persistent_map = self._load_persistent(run_date)
        rows = []
        total = len(tickers)
        start = time.time()

        for idx, ticker in enumerate(tickers, 1):
            symbol = str(ticker).upper()
            if symbol in cache_map:
                rows.append(cache_map[symbol])
                self._report_progress(idx, total, start, progress_every)
                continue
            if symbol in persistent_map:
                rows.append(persistent_map[symbol])
                self._report_progress(idx, total, start, progress_every)
                continue

            if as_of_date is None:
                snapshot = self._fetch_snapshot(symbol)
            else:
                snapshot = self._fetch_snapshot_as_of(symbol, as_of_date)
            rows.append(snapshot)
            persistent_map[symbol] = snapshot
            self._report_progress(idx, total, start, progress_every)
            time.sleep(0.02)

        if use_persistent_cache:
            self._save_persistent(run_date, persistent_map)

        return self._to_frame(rows)

    def list_persistent_cache_dates(self) -> list[date]:
        """
        Return all as-of dates currently present in the persistent snapshot cache.
        """
        dates: list[date] = []
        for path in self._snapshot_cache_dir.glob("*.csv"):
            try:
                dates.append(date.fromisoformat(path.stem))
            except Exception:
                continue
        return sorted(set(dates))

    def get_persistent_snapshot_row(self, as_of_date: date, ticker: str) -> Optional[Dict]:
        symbol = str(ticker).upper()
        cache_map = self._load_persistent(as_of_date)
        row = cache_map.get(symbol)
        if row is None:
            return None
        return dict(row)

    def invalidate_persistent_tickers(self, as_of_date: date, tickers: list[str]) -> int:
        """
        Remove specific tickers from one as-of snapshot cache file.
        Returns number of removed rows.
        """
        symbols = {str(t).upper() for t in tickers if str(t).strip()}
        if not symbols:
            return 0

        cache_map = self._load_persistent(as_of_date)
        if not cache_map:
            return 0

        removed = 0
        for symbol in symbols:
            if symbol in cache_map:
                del cache_map[symbol]
                removed += 1

        if removed > 0:
            if cache_map:
                self._save_persistent(as_of_date, cache_map)
            else:
                path = self._snapshot_cache_path(as_of_date)
                if path.exists():
                    path.unlink()
        return removed

    def recompute_persistent_tickers(
        self,
        as_of_date: date,
        tickers: list[str],
        progress_every: int = 50,
    ) -> pd.DataFrame:
        """
        Rebuild cache rows for specific tickers on one as-of date.
        """
        symbols = [str(t).upper() for t in tickers if str(t).strip()]
        if not symbols:
            return self._to_frame([])

        self.invalidate_persistent_tickers(as_of_date, symbols)
        return self.load_for_tickers(
            symbols,
            existing_snapshot_path=None,
            progress_every=progress_every,
            as_of_date=as_of_date,
            use_persistent_cache=True,
            refresh_persistent_cache=False,
        )

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

    def _fetch_snapshot_as_of(self, symbol: str, as_of_date: date) -> Dict:
        """
        Build a point-in-time fundamentals snapshot using only disclosed quarterly rows
        up to `as_of_date`, then enrich debt metrics from cached profile data.
        """
        quarterly = self._dm.get_quarterly_fundamentals(symbol, force_refresh=False)
        if quarterly is None or quarterly.empty:
            return self._empty_snapshot(symbol)

        q = quarterly.copy()
        q["report_date"] = pd.to_datetime(q["report_date"]).dt.date
        q["disclosure_date"] = pd.to_datetime(q["disclosure_date"]).dt.date
        visible = q[q["disclosure_date"] <= as_of_date].sort_values("disclosure_date")
        if visible.empty:
            return self._empty_snapshot(symbol)

        latest = visible.iloc[-1]
        eps_yoy = self._historical_growth(visible["eps"])
        revenue_growth = self._historical_growth(visible["revenue"])
        revenue_accel = self._revenue_acceleration(visible)

        # D/E is not present in canonical quarterly schema, so use a profile proxy.
        debt_to_equity = self._get_profile_debt_to_equity(symbol)

        return {
            "ticker": symbol,
            "eps_yoy": eps_yoy,
            "revenue_growth": revenue_growth,
            "revenue_acceleration": revenue_accel,
            "earnings_surprise": None,
            "roe": self._safe_float(latest.get("roe")),
            "gross_margin": self._safe_float(latest.get("gross_margin")),
            "debt_to_equity": debt_to_equity,
        }

    def _empty_snapshot(self, symbol: str) -> Dict:
        return {
            "ticker": symbol,
            "eps_yoy": None,
            "revenue_growth": None,
            "revenue_acceleration": None,
            "earnings_surprise": None,
            "roe": None,
            "gross_margin": None,
            "debt_to_equity": None,
        }

    def _historical_growth(self, series: pd.Series) -> Optional[float]:
        """
        Compute point-in-time growth using only visible historical quarters.

        Priority:
        1) True YoY when 5+ quarters are available.
        2) 2-quarter annualized proxy when 3+ quarters are available.
        3) 1-quarter annualized proxy when 2+ quarters are available.

        Proxies are conservative fallbacks to reduce nulls for older as-of dates
        without introducing lookahead from current snapshot fields.
        """
        s = pd.to_numeric(series, errors="coerce").dropna()
        if s.empty:
            return None

        if len(s) >= 5:
            current = float(s.iloc[-1])
            prev_year = float(s.iloc[-5])
            if prev_year != 0:
                return current / prev_year - 1.0

        # Use only positive denominators for annualized proxy stability.
        if len(s) >= 3:
            current = float(s.iloc[-1])
            prev2 = float(s.iloc[-3])
            if current > 0 and prev2 > 0:
                return (current / prev2) ** 2 - 1.0

        if len(s) >= 2:
            current = float(s.iloc[-1])
            prev1 = float(s.iloc[-2])
            if current > 0 and prev1 > 0:
                return (current / prev1) ** 4 - 1.0

        return None

    def _revenue_acceleration(self, df: pd.DataFrame) -> Optional[float]:
        rev = pd.to_numeric(df["revenue"], errors="coerce").dropna()
        if len(rev) < 3:
            return None
        r0 = float(rev.iloc[-1])
        r1 = float(rev.iloc[-2])
        r2 = float(rev.iloc[-3])
        if r1 == 0 or r2 == 0:
            return None
        return (r0 / r1 - 1.0) - (r1 / r2 - 1.0)

    def _safe_float(self, value) -> Optional[float]:
        try:
            if value is None or pd.isna(value):
                return None
            return float(value)
        except Exception:
            return None

    def _get_profile_debt_to_equity(self, symbol: str) -> Optional[float]:
        symbol_u = str(symbol).upper()
        if symbol_u in self._profile_de_cache:
            return self._profile_de_cache[symbol_u]

        de = None
        try:
            snap = self._provider.get_factor_fundamentals(symbol_u)
            de = self._safe_float((snap or {}).get("debt_to_equity"))
        except Exception:
            de = None

        # Fallback to DataManager profile only when provider value is unavailable.
        if de is None:
            profile = self._dm.get_fundamentals(symbol_u, validate=False, strict=False)
            de = self._safe_float((profile or {}).get("debt_to_equity"))

        # Guard against clearly corrupted values.
        if de is not None and abs(de) > 1_000_000:
            de = None
        if de in (0.0,):
            de = None

        self._profile_de_cache[symbol_u] = de
        return de

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

    def _snapshot_cache_path(self, as_of_date: date) -> Path:
        return self._snapshot_cache_dir / f"{as_of_date.isoformat()}.csv"

    def _load_persistent(self, as_of_date: date) -> Dict[str, Dict]:
        path = self._snapshot_cache_path(as_of_date)
        if not path.exists():
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

    def _save_persistent(self, as_of_date: date, cache_map: Dict[str, Dict]) -> None:
        if not cache_map:
            return
        rows = []
        stamp = datetime.now(UTC).isoformat()
        for symbol, raw in cache_map.items():
            row = dict(raw or {})
            row["ticker"] = str(symbol).upper()
            row["as_of_date"] = as_of_date.isoformat()
            row["cached_at_utc"] = stamp
            rows.append(row)
        df = pd.DataFrame(rows)
        for col in self.REQUIRED_COLUMNS:
            if col not in df.columns:
                df[col] = pd.NA
        cols = list(self.REQUIRED_COLUMNS) + ["as_of_date", "cached_at_utc"]
        df = df[cols].sort_values("ticker")
        df.to_csv(self._snapshot_cache_path(as_of_date), index=False)

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
