"""
Daily risk scan:
- hard-stop checks
- distribution-day monitor
- exposure throttle recommendation
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class PositionSnapshot:
    ticker: str
    entry_price: float
    current_price: float
    market_value: float
    stop_pct: float = 0.07


@dataclass
class RiskAlert:
    alert_type: str
    ticker: Optional[str]
    severity: str
    message: str
    payload: Dict


class DailyRiskScanner:
    def __init__(
        self,
        hard_stop_band: tuple[float, float] = (0.07, 0.08),
        distribution_window_days: int = 25,
        distribution_trigger_count: int = 5,
    ):
        self.hard_stop_band = hard_stop_band
        self.distribution_window_days = distribution_window_days
        self.distribution_trigger_count = distribution_trigger_count

    def check_hard_stops(self, positions: List[PositionSnapshot]) -> List[RiskAlert]:
        alerts: List[RiskAlert] = []
        min_stop, max_stop = self.hard_stop_band
        for p in positions:
            drawdown = (p.current_price - p.entry_price) / p.entry_price
            band = max(min_stop, min(max_stop, p.stop_pct))
            if drawdown <= -band:
                alerts.append(
                    RiskAlert(
                        alert_type="hard_stop",
                        ticker=p.ticker,
                        severity="high",
                        message=f"{p.ticker} breached hard stop ({drawdown:.2%})",
                        payload={
                            "entry_price": p.entry_price,
                            "current_price": p.current_price,
                            "drawdown_pct": drawdown,
                            "threshold_pct": -band,
                        },
                    )
                )
        return alerts

    def count_distribution_days(self, index_df: pd.DataFrame) -> int:
        """
        Distribution day:
        - close down day-over-day
        - volume above previous day
        """
        if index_df is None or index_df.empty or len(index_df) < 2:
            return 0
        df = index_df.copy().sort_index().tail(self.distribution_window_days)
        down = df["Close"].diff() < 0
        volume_up = df["Volume"].diff() > 0
        return int((down & volume_up).sum())

    def build_daily_alert(
        self,
        positions: List[PositionSnapshot],
        benchmark_df: pd.DataFrame,
    ) -> Dict:
        hard_stop_alerts = self.check_hard_stops(positions)
        dist_count = self.count_distribution_days(benchmark_df)

        exposure_scale = 1.0
        risk_state = "normal"
        if dist_count >= self.distribution_trigger_count:
            risk_state = "elevated"
            exposure_scale = 0.6
        if dist_count >= (self.distribution_trigger_count + 2):
            risk_state = "high"
            exposure_scale = 0.4

        alerts = list(hard_stop_alerts)
        if dist_count >= self.distribution_trigger_count:
            alerts.append(
                RiskAlert(
                    alert_type="distribution_days",
                    ticker=None,
                    severity="medium" if risk_state == "elevated" else "high",
                    message=f"Distribution days: {dist_count} in last {self.distribution_window_days} sessions",
                    payload={"distribution_days": dist_count, "exposure_scale": exposure_scale},
                )
            )

        return {
            "risk_state": risk_state,
            "recommended_exposure_scale": exposure_scale,
            "alerts": [a.__dict__ for a in alerts],
        }
