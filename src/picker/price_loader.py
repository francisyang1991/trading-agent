"""
Price loading for picker pipeline.

Stage A-C: load cached prices, Yahoo bulk fetch, fallback chain.
Shared by run_three_layer_picker and validate_three_layer_walkforward.
"""

from __future__ import annotations

import time
from typing import Any, Dict

import pandas as pd

from src.data.providers.resilient import fallback_price_fetch
from src.data.providers.yfinance_provider import batch_download_daily_ohlcv


def _parse_ibkr_endpoint(endpoint: str) -> tuple[str, int]:
    """Parse 'host:port' string to (host, port)."""
    try:
        host, port = endpoint.split(":")
        return host.strip(), int(port.strip())
    except Exception:
        return "127.0.0.1", 4002


def stage_load_prices(
    dm: Any, symbols: list[str], cfg: dict
) -> Dict[str, pd.DataFrame]:
    """
    Stage A-C: load cached prices, Yahoo bulk, fallback chain.

    Args:
        dm: DataManager instance (from src.data_manager)
        symbols: List of ticker symbols
        cfg: Picker config dict (data, ibkr sections)

    Returns:
        Dict[symbol, DataFrame] of OHLCV data
    """
    data_cfg = cfg.get("data", {})
    ibkr_cfg = cfg.get("ibkr", {})
    period = data_cfg.get("period", "2y")

    # A: cache (DB only - no API; slow when many symbols due to per-symbol queries)
    print("\n[A] Load cached prices from DB")
    t0 = time.time()
    prices = dm.load_cached_prices(
        symbols, period=period, min_bars=60,
        progress_hook=lambda i, total, loaded: (
            print(f"  [cache] {i}/{total} ({100*i/total:.1f}%) loaded={loaded} ({time.time()-t0:.0f}s)") if (i % 200 == 0 or i == total) and total else None
        ),
    )
    missing = [s for s in symbols if s not in prices]
    print(f"  cache={len(prices)} missing={len(missing)} ({time.time()-t0:.1f}s)")

    # B: Yahoo bulk
    if missing:
        print("\n[B] Yahoo bulk fetch")
        t1 = time.time()
        yahoo = batch_download_daily_ohlcv(
            symbols=missing, period=period,
            batch_size=data_cfg.get("batch_size", 200),
            threads=data_cfg.get("yf_threads", False),
            progress_hook=lambda done, total: print(f"  [yahoo] {done}/{total} ({100*done/total:.1f}%)"),
        )
        prices.update(yahoo)
        dm.persist_prices(yahoo, progress_hook=lambda i, t: (
            print(f"  [persist] {i}/{t} ({100*i/t:.1f}%)") if (i % 500 == 0 or i == t) else None
        ))
        missing = [s for s in symbols if s not in prices]
        print(f"  yahoo={len(yahoo)} missing={len(missing)} ({time.time()-t1:.1f}s)")

    # C: Fallback (IBKR GCloud -> local -> Stooq)
    if missing:
        print(f"\n[C] Fallback for {len(missing)} remaining")
        t2 = time.time()
        ib_host, ib_port = _parse_ibkr_endpoint(
            ibkr_cfg.get("local_gateway_url", "127.0.0.1:4002")
        )
        fallback = {}
        for i, sym in enumerate(missing, 1):
            data, _ = fallback_price_fetch(
                symbol=sym, period=period,
                gcloud_base_url=ibkr_cfg.get("gcloud_trade_api_url", "").rstrip("/"),
                gcloud_api_key=ibkr_cfg.get("gcloud_trade_api_key", ""),
                local_ibkr_host=ib_host, local_ibkr_port=ib_port,
                local_ibkr_client_id=ibkr_cfg.get("local_client_id", 99),
            )
            if data is not None and not data.empty:
                prices[sym] = data
                fallback[sym] = data
            if i % 100 == 0 or i == len(missing):
                pct = 100 * i / len(missing) if missing else 0
                print(f"  [fallback] {i}/{len(missing)} ({pct:.1f}%)")
        if fallback:
            dm.persist_prices(fallback)
        print(f"  resolved={len(fallback)} ({time.time()-t2:.1f}s)")

    return prices
