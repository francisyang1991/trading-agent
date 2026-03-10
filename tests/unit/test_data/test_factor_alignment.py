import pandas as pd

from src.data.factor_alignment import align_fundamentals_to_daily


def test_align_fundamentals_uses_disclosure_date_without_leakage():
    daily = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=8, freq="D"),
            "close": [100, 101, 102, 103, 104, 105, 106, 107],
            "adj_close": [100, 101, 102, 103, 104, 105, 106, 107],
        }
    )
    fund = pd.DataFrame(
        {
            "ticker": ["ABC", "ABC"],
            "report_date": [pd.Timestamp("2023-12-31"), pd.Timestamp("2024-03-31")],
            "disclosure_date": [pd.Timestamp("2024-01-04"), pd.Timestamp("2024-01-07")],
            "eps": [1.0, 2.0],
            "revenue": [100.0, 200.0],
            "roe": [0.1, 0.2],
            "gross_margin": [0.5, 0.6],
        }
    )

    out = align_fundamentals_to_daily(daily, fund, ticker="ABC")

    # Before first disclosure there should be no EPS.
    assert pd.isna(out.loc[out["date"] == pd.Timestamp("2024-01-03"), "eps"]).all()
    # First fundamental becomes visible on 2024-01-04.
    assert out.loc[out["date"] == pd.Timestamp("2024-01-04"), "eps"].iloc[0] == 1.0
    # Second quarter only becomes visible on its own disclosure date.
    assert out.loc[out["date"] == pd.Timestamp("2024-01-06"), "eps"].iloc[0] == 1.0
    assert out.loc[out["date"] == pd.Timestamp("2024-01-07"), "eps"].iloc[0] == 2.0
