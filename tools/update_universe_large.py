#!/usr/bin/env python3
"""
Build a large universe snapshot (S&P 500, Nasdaq-100/QQQ, Russell 2000, China ADRs).
Outputs:
  - config/stock_universe_large.yaml
  - data/universe_snapshots/large_universe_YYYYMMDD.yaml
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List
import urllib.request
import re

import pandas as pd
from bs4 import BeautifulSoup
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_OUT = ROOT / "config" / "stock_universe_large.yaml"
SNAPSHOT_DIR = ROOT / "data" / "universe_snapshots"


def _clean_symbols(symbols: List[str]) -> List[str]:
    invalid = {"USD", "CASH", "CAD", "EUR"}
    pattern = re.compile(r"^[A-Z0-9]{1,5}(-[A-Z0-9]{1,2})?$")
    cleaned = []
    for s in symbols:
        if not isinstance(s, str):
            continue
        sym = s.strip().upper().replace(".", "-")
        if not sym or sym in invalid:
            continue
        if not pattern.match(sym):
            continue
        cleaned.append(sym)
    return sorted(set(cleaned))


def fetch_sp500() -> List[str]:
    csv_url = "https://datahub.io/core/s-and-p-500-companies/r/constituents.csv"
    try:
        df = pd.read_csv(csv_url)
        if "Symbol" in df.columns:
            return _clean_symbols(df["Symbol"].tolist())
    except Exception:
        return []


def fetch_nasdaq100() -> List[str]:
    url = "https://en.wikipedia.org/wiki/Nasdaq-100"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")
        for table in tables:
            header = table.find("tr")
            if not header:
                continue
            cols = [th.get_text(strip=True) for th in header.find_all(["th", "td"])]
            if "Ticker" in cols:
                idx = cols.index("Ticker")
            elif "Symbol" in cols:
                idx = cols.index("Symbol")
            else:
                continue
            symbols = []
            for row in table.find_all("tr")[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if len(cells) <= idx:
                    continue
                symbols.append(cells[idx])
            return _clean_symbols(symbols)
    except Exception:
        return []
    return []


def fetch_russell2000() -> List[str]:
    url = (
        "https://www.ishares.com/us/products/239710/"
        "ishares-russell-2000-etf/1467271812596.ajax"
        "?fileType=csv&fileName=IWM_holdings&dataType=fund"
    )
    with urllib.request.urlopen(url) as resp:
        text = resp.read().decode("utf-8", errors="ignore")
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("Ticker,"):
            header_idx = i
            break
    if header_idx is None:
        return []
    df = pd.read_csv(url, skiprows=header_idx)
    if "Ticker" not in df.columns:
        return []
    return _clean_symbols(df["Ticker"].tolist())


def china_adrs() -> List[str]:
    symbols = [
        "BABA", "JD", "PDD", "BIDU", "NTES", "TME", "ZTO", "YMM",
        "BILI", "IQ", "BEKE", "XPEV", "LI", "NIO", "DADA",
        "TAL", "EDU", "GDS", "ZLAB", "YSG", "KC", "TUYA",
    ]
    return _clean_symbols(symbols)


def build_universe() -> Dict:
    sp500 = fetch_sp500()
    qqq = fetch_nasdaq100()
    russell2000 = fetch_russell2000()
    adr = china_adrs()

    all_symbols = sorted(set(sp500 + qqq + russell2000 + adr))

    return {
        "metadata": {
            "generated_at": datetime.utcnow().strftime("%Y-%m-%d"),
            "sources": {
                "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                "qqq": "https://en.wikipedia.org/wiki/Nasdaq-100",
                "russell2000": "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf",
                "china_adr": "manual_list",
            },
            "counts": {
                "sp500": len(sp500),
                "qqq": len(qqq),
                "russell2000": len(russell2000),
                "china_adr": len(adr),
                "all_symbols": len(all_symbols),
            },
        },
        "themes": {
            "sp500": {
                "name": "S&P 500",
                "description": "All S&P 500 constituents",
                "symbols": sp500,
            },
            "qqq": {
                "name": "Nasdaq-100 / QQQ",
                "description": "Nasdaq-100 constituents (QQQ proxy)",
                "symbols": qqq,
            },
            "russell2000": {
                "name": "Russell 2000 / IWM",
                "description": "Russell 2000 constituents via IWM holdings",
                "symbols": russell2000,
            },
            "china_adr": {
                "name": "China ADRs",
                "description": "Selected China ADRs",
                "symbols": adr,
            },
        },
        "all_symbols": all_symbols,
    }


def main():
    universe = build_universe()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / f"large_universe_{datetime.utcnow().strftime('%Y%m%d')}.yaml"

    for path in [CONFIG_OUT, snapshot_path]:
        with open(path, "w") as f:
            yaml.safe_dump(universe, f, sort_keys=False)
    print(f"✅ Wrote {CONFIG_OUT}")
    print(f"✅ Wrote {snapshot_path}")


if __name__ == "__main__":
    main()
