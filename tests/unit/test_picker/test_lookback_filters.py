import pandas as pd

from src.picker.lookback import _filter_lookback_fundamentals


def _base_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "eps_yoy": None,
                "revenue_growth": 0.20,
                "revenue_acceleration": None,
                "earnings_surprise": None,
                "roe": 0.15,
                "gm_rank": 70.0,
            },
            {
                "ticker": "BBB",
                "eps_yoy": 0.30,
                "revenue_growth": None,
                "revenue_acceleration": None,
                "earnings_surprise": None,
                "roe": 0.16,
                "gm_rank": 71.0,
            },
            {
                "ticker": "CCC",
                "eps_yoy": 0.35,
                "revenue_growth": 0.15,
                "revenue_acceleration": None,
                "earnings_surprise": None,
                "roe": 0.17,
                "gm_rank": 75.0,
            },
        ]
    )


def test_lookback_filter_strict_requires_non_missing_growth():
    fund_cfg = {
        "min_eps_yoy": 0.25,
        "min_revenue_growth": 0.10,
        "min_revenue_acceleration": None,
        "require_surprise_non_negative": False,
    }
    out = _filter_lookback_fundamentals(
        _base_df(),
        fund_cfg=fund_cfg,
        skip_missing_yoy_checks=False,
    )
    assert out["ticker"].tolist() == ["CCC"]


def test_lookback_filter_allows_missing_growth_when_enabled():
    fund_cfg = {
        "min_eps_yoy": 0.25,
        "min_revenue_growth": 0.10,
        "min_revenue_acceleration": None,
        "require_surprise_non_negative": False,
    }
    out = _filter_lookback_fundamentals(
        _base_df(),
        fund_cfg=fund_cfg,
        skip_missing_yoy_checks=True,
    )
    assert set(out["ticker"].tolist()) == {"AAA", "BBB", "CCC"}
