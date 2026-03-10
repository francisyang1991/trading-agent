"""
Daily alert payload builder (Discord/email adapter-ready).
"""

from __future__ import annotations

from typing import Dict, List

from src.risk.daily_risk_scan import DailyRiskScanner, PositionSnapshot


def build_daily_alert_payload(
    new_picks: List[Dict],
    current_positions: List[Dict],
    benchmark_df,
) -> Dict:
    scanner = DailyRiskScanner()
    snapshots = [
        PositionSnapshot(
            ticker=p["ticker"],
            entry_price=float(p["entry_price"]),
            current_price=float(p["current_price"]),
            market_value=float(p.get("market_value", 0.0)),
            stop_pct=float(p.get("stop_pct", 0.07)),
        )
        for p in current_positions
    ]
    risk = scanner.build_daily_alert(snapshots, benchmark_df)

    return {
        "new_picks": new_picks,
        "risk_state": risk["risk_state"],
        "recommended_exposure_scale": risk["recommended_exposure_scale"],
        "risk_triggered_positions": [
            a for a in risk["alerts"] if a.get("alert_type") == "hard_stop"
        ],
        "market_risk_alerts": [
            a for a in risk["alerts"] if a.get("alert_type") == "distribution_days"
        ],
    }
