from src.picker.price_loader import stage_load_prices


class _DummyDataManager:
    def load_cached_prices(self, symbols, period="1y", min_bars=60, progress_hook=None):
        return {}

    def persist_prices(self, prices, progress_hook=None):
        return None


def test_stage_load_prices_prefers_ibkr_fallback_in_prod(monkeypatch):
    calls = []

    monkeypatch.setenv("SAIYAN_DATA_MODE", "prod")

    def _fake_yahoo(**kwargs):
        calls.append("yahoo")
        return {}

    def _fake_fallback(**kwargs):
        calls.append("fallback")
        return None, "none"

    monkeypatch.setattr("src.picker.price_loader.batch_download_daily_ohlcv", _fake_yahoo)
    monkeypatch.setattr("src.picker.price_loader.fallback_price_fetch", _fake_fallback)

    stage_load_prices(
        _DummyDataManager(),
        ["NVDA"],
        {"data": {"period": "1y"}, "ibkr": {}},
    )

    assert calls == ["fallback", "yahoo"]


def test_stage_load_prices_prefers_yahoo_in_dev(monkeypatch):
    calls = []

    monkeypatch.setenv("SAIYAN_DATA_MODE", "dev")

    def _fake_yahoo(**kwargs):
        calls.append("yahoo")
        return {}

    def _fake_fallback(**kwargs):
        calls.append("fallback")
        return None, "none"

    monkeypatch.setattr("src.picker.price_loader.batch_download_daily_ohlcv", _fake_yahoo)
    monkeypatch.setattr("src.picker.price_loader.fallback_price_fetch", _fake_fallback)

    stage_load_prices(
        _DummyDataManager(),
        ["NVDA"],
        {"data": {"period": "1y"}, "ibkr": {}},
    )

    assert calls == ["yahoo", "fallback"]
