"""
Environment-aware data source routing for production, development, and backtests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace


def _normalize_mode(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    aliases = {
        "prod": "prod",
        "production": "prod",
        "live": "prod",
        "gcp": "prod",
        "dev": "dev",
        "development": "dev",
        "local": "dev",
        "backtest": "backtest",
        "research": "backtest",
    }
    return aliases.get(value, "dev")


def _safe_int(raw: str | None, default: int) -> int:
    try:
        return int(str(raw).strip())
    except Exception:
        return default


@dataclass(frozen=True)
class DataRoutingConfig:
    mode: str = "dev"
    gcloud_base_url: str = ""
    gcloud_api_key: str = ""
    local_gateway_host: str = "127.0.0.1"
    local_gateway_port: int = 4002
    local_gateway_client_id: int = 99

    @classmethod
    def from_env(cls) -> "DataRoutingConfig":
        return cls(
            mode=_normalize_mode(os.environ.get("SAIYAN_DATA_MODE") or os.environ.get("SAIYAN_ENV")),
            gcloud_base_url=(
                os.environ.get("IBKR_GCLOUD_URL")
                or os.environ.get("TRADE_API_URL")
                or ""
            ).strip().rstrip("/"),
            gcloud_api_key=(
                os.environ.get("IBKR_GCLOUD_API_KEY")
                or os.environ.get("TRADE_API_KEY")
                or ""
            ).strip(),
            local_gateway_host=(os.environ.get("IB_HOST") or "127.0.0.1").strip(),
            local_gateway_port=_safe_int(os.environ.get("IB_PORT"), 4002),
            local_gateway_client_id=_safe_int(os.environ.get("IB_CLIENT_ID"), 99),
        )

    def with_mode(self, mode: str | None) -> "DataRoutingConfig":
        if mode is None:
            return self
        return replace(self, mode=_normalize_mode(mode))

    @property
    def gcloud_enabled(self) -> bool:
        return bool(self.gcloud_base_url and self.gcloud_api_key)

    def daily_market_order(self) -> list[str]:
        if self.mode == "prod":
            order = ["gcloud", "ibkr_local", "yfinance"]
        else:
            order = ["yfinance", "gcloud", "ibkr_local"]
        if not self.gcloud_enabled:
            order = [name for name in order if name != "gcloud"]
        return order

    def fundamental_order(self) -> list[str]:
        if self.mode == "prod":
            order = ["gcloud", "yfinance", "fmp"]
        else:
            order = ["yfinance", "gcloud", "fmp"]
        if not self.gcloud_enabled:
            order = [name for name in order if name != "gcloud"]
        return order

    def intraday_order(self) -> list[str]:
        if self.mode == "prod":
            order = ["gcloud", "ibkr_db", "yfinance"]
        elif self.mode == "backtest":
            order = ["ibkr_db", "yfinance", "gcloud"]
        else:
            order = ["ibkr_db", "gcloud", "yfinance"]
        if not self.gcloud_enabled:
            order = [name for name in order if name != "gcloud"]
        return order
