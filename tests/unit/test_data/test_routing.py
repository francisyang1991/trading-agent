from src.data.routing import DataRoutingConfig
from src.data_manager import DataManager


def test_routing_orders_prod():
    routing = DataRoutingConfig(
        mode="prod",
        gcloud_base_url="http://example",
        gcloud_api_key="secret",
    )
    assert routing.daily_market_order() == ["gcloud", "ibkr_local", "yfinance"]
    assert routing.fundamental_order() == ["gcloud", "yfinance", "fmp"]
    assert routing.intraday_order() == ["gcloud", "ibkr_db", "yfinance"]


def test_routing_orders_dev_without_gcloud():
    routing = DataRoutingConfig(mode="dev")
    assert routing.daily_market_order() == ["yfinance", "ibkr_local"]
    assert routing.fundamental_order() == ["yfinance", "fmp"]
    assert routing.intraday_order() == ["ibkr_db", "yfinance"]


def test_data_manager_uses_prod_market_order():
    dm = DataManager(
        routing=DataRoutingConfig(
            mode="prod",
            gcloud_base_url="http://example",
            gcloud_api_key="secret",
        )
    )
    assert dm.market_provider_order == ["gcloud", "ibkr_local", "yfinance"]
    assert dm.fundamental_provider_order == ["gcloud", "yfinance", "fmp"]
