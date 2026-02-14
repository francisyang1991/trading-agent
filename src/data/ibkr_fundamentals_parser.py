"""
Parse IBKR reqFundamentalData XML (Reuters Global Fundamentals).

Report types handled:
  - ReportsFinSummary   → key ratios (ROE, gross margin, D/E, market cap)
  - ReportsFinStatements → quarterly income statement + balance sheet
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd


# ── ReportsFinSummary helpers ──────────────────────────────────────────────

# Mapping from IBKR ratio IDs → our canonical field names.
_RATIO_MAP: Dict[str, str] = {
    # ROE
    "TTMROEPCT": "roe",
    "AROEPCT": "roe",          # annual fallback
    # Gross margin
    "TTMGROSMGN": "gross_margin",
    "AGROSMGN": "gross_margin",
    # Debt-to-equity
    "QTOTD2EQ": "debt_to_equity",
    "ATOTD2EQ": "debt_to_equity",
    # PE
    "PEEXCLXOR": "pe_ratio",
    "TTMPR2REV": "price_to_revenue",
    # Market cap (in millions)
    "MKTCAP": "market_cap",
    # EPS
    "TTMEPSXCLX": "eps",
    "AEPSXCLXOR": "eps",
    # Revenue (TTM)
    "TTMREV": "revenue",
}


def parse_fin_summary(xml_text: str) -> Optional[Dict[str, Any]]:
    """
    Parse ReportsFinSummary XML → flat dict with canonical fields.

    Returns dict like:
        {"roe": 0.25, "gross_margin": 0.45, "debt_to_equity": 0.8,
         "market_cap": 2.5e12, "eps": 6.5, "revenue": 3.94e11, ...}
    """
    if not xml_text or not xml_text.strip():
        return None

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    result: Dict[str, Any] = {}

    # Walk all <Ratio> elements regardless of nesting depth.
    for ratio_el in root.iter("Ratio"):
        field_id = ratio_el.get("FieldName") or ratio_el.get("ID") or ""
        if field_id not in _RATIO_MAP:
            continue
        canon = _RATIO_MAP[field_id]
        if canon in result:
            continue  # keep first (TTM preferred over annual)
        try:
            val = float(ratio_el.text.strip())
        except (TypeError, ValueError, AttributeError):
            continue
        # Convert percentages → fractions for ROE and gross margin.
        if canon in ("roe", "gross_margin") and abs(val) > 1.5:
            val = val / 100.0
        result[canon] = val

    # Market cap often in millions → convert to raw.
    if "market_cap" in result and result["market_cap"] < 1e9:
        result["market_cap"] = result["market_cap"] * 1e6

    return result if result else None


# ── ReportsFinStatements helpers ───────────────────────────────────────────

# Income statement line-item names used by Reuters XML.
_INCOME_KEYS = {
    "eps": [
        "Diluted EPS Excluding ExtraOrd Items",
        "Diluted Normalized EPS",
        "Basic EPS Excluding Extraordinary Items",
    ],
    "revenue": [
        "Total Revenue",
        "Revenue",
        "Net Revenue",
    ],
    "gross_profit": [
        "Gross Profit",
    ],
    "net_income": [
        "Net Income",
        "Net Income Before Taxes",  # fallback
    ],
}

_BALANCE_KEYS = {
    "shareholders_equity": [
        "Total Equity",
        "Total Stockholders' Equity",
        "Total Common Equity",
        "Stockholders' Equity",
    ],
}


def parse_fin_statements(xml_text: str) -> Optional[pd.DataFrame]:
    """
    Parse ReportsFinStatements XML → quarterly fundamentals DataFrame.

    Returns DataFrame with columns:
        ticker, report_date, disclosure_date, eps, revenue, roe, gross_margin
    """
    if not xml_text or not xml_text.strip():
        return None

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    # Locate the financial statements section.
    # Reuters XML nests under <FinancialStatements> → <AnnualPeriods> / <InterimPeriods>
    interim = None
    for el in root.iter("InterimPeriods"):
        interim = el
        break
    if interim is None:
        # Try alternate nesting
        for el in root.iter("FiscalPeriod"):
            if el.get("Type") == "Interim":
                interim = el
                break

    if interim is None:
        return None

    rows: List[Dict[str, Any]] = []

    # Each <FiscalPeriod> or <Period> contains one quarter's data.
    for period_el in interim.iter("FiscalPeriod"):
        _parse_period(period_el, rows)

    # Alternate structure: direct <Period> children
    if not rows:
        for period_el in interim.iter("Period"):
            _parse_period_alt(period_el, rows)

    if not rows:
        return None

    df = pd.DataFrame(rows)
    df = df.sort_values("report_date", ascending=False).head(20)
    return df


def _parse_period(period_el: ET.Element, rows: List[Dict]) -> None:
    """Parse a <FiscalPeriod> element from Reuters XML."""
    end_date_str = period_el.get("EndDate") or period_el.get("FiscalPeriodEndDate")
    if not end_date_str:
        return

    try:
        report_date = date.fromisoformat(end_date_str[:10])
    except (ValueError, TypeError):
        return

    # Collect all line items keyed by coaCode or mapItem
    items: Dict[str, float] = {}
    for stmt_el in period_el.iter("Statement"):
        for item_el in stmt_el.iter("lineItem"):
            label = item_el.get("coaCode") or item_el.text or ""
            try:
                val = float(item_el.text) if item_el.text else None
            except (ValueError, TypeError):
                val = None
            if val is not None:
                items[label] = val

    row = _extract_fundamentals(items, report_date)
    if row:
        rows.append(row)


def _parse_period_alt(period_el: ET.Element, rows: List[Dict]) -> None:
    """Parse alternate <Period> structure."""
    end_date_str = period_el.get("EndDate") or period_el.get("endDate")
    if not end_date_str:
        return

    try:
        report_date = date.fromisoformat(end_date_str[:10])
    except (ValueError, TypeError):
        return

    items: Dict[str, float] = {}
    for item_el in period_el.iter():
        tag = item_el.tag
        if item_el.text:
            try:
                items[tag] = float(item_el.text)
            except (ValueError, TypeError):
                pass

    row = _extract_fundamentals(items, report_date)
    if row:
        rows.append(row)


def _extract_fundamentals(items: Dict[str, float], report_date: date) -> Optional[Dict]:
    """Extract canonical fundamentals from a dict of line items."""

    def _find(keys: List[str]) -> Optional[float]:
        for k in keys:
            if k in items:
                return items[k]
            # Case-insensitive fallback
            for ik, iv in items.items():
                if ik.lower() == k.lower():
                    return iv
        return None

    eps = _find(_INCOME_KEYS["eps"])
    revenue = _find(_INCOME_KEYS["revenue"])
    gross_profit = _find(_INCOME_KEYS["gross_profit"])
    net_income = _find(_INCOME_KEYS["net_income"])
    equity = _find(_BALANCE_KEYS["shareholders_equity"])

    # Need at least one useful field
    if eps is None and revenue is None:
        return None

    gross_margin = None
    if gross_profit is not None and revenue is not None and revenue != 0:
        gross_margin = gross_profit / revenue

    roe = None
    if net_income is not None and equity is not None and equity != 0:
        roe = net_income / equity

    return {
        "ticker": "",  # Caller must fill this
        "report_date": report_date,
        "disclosure_date": report_date,  # IBKR doesn't provide disclosure date
        "eps": eps,
        "revenue": revenue,
        "roe": roe,
        "gross_margin": gross_margin,
    }
