"""
Listed US symbol loaders.

Single responsibility: fetch and validate listed symbols from Nasdaq Trader feeds.
"""

from __future__ import annotations

import re
from io import StringIO
from urllib.request import urlopen

import pandas as pd


def _is_valid_ticker(symbol: str) -> bool:
    return isinstance(symbol, str) and bool(re.fullmatch(r"[A-Z]{1,5}([.-][A-Z])?", symbol))


def _is_probable_common_stock(symbol: str) -> bool:
    s = symbol.upper()
    return not s.endswith((".W", ".U", ".R", "-W", "-U", "-R"))


def _fetch_nasdaq_trader_table(url: str) -> pd.DataFrame:
    with urlopen(url, timeout=20) as resp:
        payload = resp.read().decode("utf-8", errors="ignore")
    lines = [line for line in payload.splitlines() if line and "File Creation Time" not in line]
    return pd.read_csv(StringIO("\n".join(lines)), sep="|")


def load_all_listed_us_symbols() -> list[str]:
    """
    Pull full US listed symbols from NasdaqTrader feeds.
    """
    symbols = set()
    nasdaq_url = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
    other_url = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"

    try:
        ndq = _fetch_nasdaq_trader_table(nasdaq_url)
        ndq = ndq[(ndq["Test Issue"] == "N") & (ndq["ETF"] == "N")]
        symbols.update(ndq["Symbol"].astype(str).str.upper().tolist())
    except Exception as exc:
        print(f"⚠️ Nasdaq listed download failed: {exc}")

    try:
        oth = _fetch_nasdaq_trader_table(other_url)
        sym_col = "ACT Symbol" if "ACT Symbol" in oth.columns else "Symbol"
        if "Test Issue" in oth.columns:
            oth = oth[oth["Test Issue"] == "N"]
        if "ETF" in oth.columns:
            oth = oth[oth["ETF"] == "N"]
        symbols.update(oth[sym_col].astype(str).str.upper().tolist())
    except Exception as exc:
        print(f"⚠️ Other listed download failed: {exc}")

    return sorted(
        {
            symbol
            for symbol in symbols
            if _is_valid_ticker(symbol) and _is_probable_common_stock(symbol)
        }
    )
