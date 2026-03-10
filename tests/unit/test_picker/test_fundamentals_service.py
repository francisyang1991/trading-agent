from datetime import date

import pandas as pd
import pytest

from src.picker.fundamentals_service import FundamentalSnapshotService


class _FakeDataManager:
    def __init__(self, quarterly: pd.DataFrame):
        self._quarterly = quarterly
        self.quarterly_calls = 0
        self.fundamental_calls = 0

    def get_quarterly_fundamentals(self, symbol: str, force_refresh: bool = False) -> pd.DataFrame:
        self.quarterly_calls += 1
        return self._quarterly.copy()

    def get_fundamentals(self, symbol: str, validate: bool = False, strict: bool = False):
        self.fundamental_calls += 1
        return {"debt_to_equity": 0.8}


class _FakeProvider:
    def __init__(self, debt_to_equity=None):
        self.debt_to_equity = debt_to_equity
        self.calls = 0

    def get_factor_fundamentals(self, symbol: str):
        self.calls += 1
        return {"ticker": symbol, "debt_to_equity": self.debt_to_equity}


def _build_quarterly() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ticker": "AAA", "report_date": "2024-06-30", "disclosure_date": "2024-06-30", "eps": 1.0, "revenue": 100.0, "roe": 0.10, "gross_margin": 0.40},
            {"ticker": "AAA", "report_date": "2024-09-30", "disclosure_date": "2024-09-30", "eps": 1.2, "revenue": 110.0, "roe": 0.11, "gross_margin": 0.41},
            {"ticker": "AAA", "report_date": "2024-12-31", "disclosure_date": "2024-12-31", "eps": 1.4, "revenue": 120.0, "roe": 0.12, "gross_margin": 0.42},
            {"ticker": "AAA", "report_date": "2025-03-31", "disclosure_date": "2025-03-31", "eps": 1.1, "revenue": 130.0, "roe": 0.13, "gross_margin": 0.43},
            {"ticker": "AAA", "report_date": "2025-06-30", "disclosure_date": "2025-06-30", "eps": 1.5, "revenue": 140.0, "roe": 0.14, "gross_margin": 0.44},
        ]
    )


def test_load_for_tickers_as_of_uses_point_in_time_quarterlies(tmp_path):
    dm = _FakeDataManager(_build_quarterly())
    svc = FundamentalSnapshotService(dm)
    svc._provider = _FakeProvider(debt_to_equity=None)
    svc._snapshot_cache_dir = tmp_path / "snapshots"
    svc._snapshot_cache_dir.mkdir(parents=True, exist_ok=True)

    out = svc.load_for_tickers(["AAA"], as_of_date=date(2025, 7, 1), use_persistent_cache=True)
    row = out.iloc[0]

    assert row["ticker"] == "AAA"
    assert row["eps_yoy"] == pytest.approx(0.5)
    assert row["revenue_growth"] == pytest.approx(0.4)
    assert round(float(row["revenue_acceleration"]), 4) == -0.0064
    assert row["roe"] == 0.14
    assert row["gross_margin"] == 0.44
    assert row["debt_to_equity"] == 0.8
    assert dm.quarterly_calls == 1
    assert dm.fundamental_calls == 1


def test_load_for_tickers_as_of_reuses_persistent_cache(tmp_path):
    dm = _FakeDataManager(_build_quarterly())
    svc = FundamentalSnapshotService(dm)
    svc._provider = _FakeProvider(debt_to_equity=None)
    svc._snapshot_cache_dir = tmp_path / "snapshots"
    svc._snapshot_cache_dir.mkdir(parents=True, exist_ok=True)

    as_of = date(2025, 7, 1)
    first = svc.load_for_tickers(["AAA"], as_of_date=as_of, use_persistent_cache=True)
    second = svc.load_for_tickers(["AAA"], as_of_date=as_of, use_persistent_cache=True)

    assert first["ticker"].tolist() == ["AAA"]
    assert second["ticker"].tolist() == ["AAA"]
    assert dm.quarterly_calls == 1
    assert dm.fundamental_calls == 1


def test_historical_growth_uses_proxy_when_yoy_unavailable(tmp_path):
    short_quarterly = pd.DataFrame(
        [
            {"ticker": "BBB", "report_date": "2024-12-31", "disclosure_date": "2024-12-31", "eps": 1.0, "revenue": 100.0, "roe": 0.10, "gross_margin": 0.40},
            {"ticker": "BBB", "report_date": "2025-03-31", "disclosure_date": "2025-03-31", "eps": 1.1, "revenue": 108.0, "roe": 0.11, "gross_margin": 0.41},
        ]
    )
    dm = _FakeDataManager(short_quarterly)
    svc = FundamentalSnapshotService(dm)
    svc._provider = _FakeProvider(debt_to_equity=None)
    svc._snapshot_cache_dir = tmp_path / "snapshots"
    svc._snapshot_cache_dir.mkdir(parents=True, exist_ok=True)

    out = svc.load_for_tickers(["BBB"], as_of_date=date(2025, 4, 1), use_persistent_cache=False)
    row = out.iloc[0]

    # 1-quarter annualized proxy fallback:
    # eps: (1.1/1.0)^4 - 1, revenue: (108/100)^4 - 1
    assert row["eps_yoy"] == pytest.approx((1.1 / 1.0) ** 4 - 1.0)
    assert row["revenue_growth"] == pytest.approx((108.0 / 100.0) ** 4 - 1.0)
