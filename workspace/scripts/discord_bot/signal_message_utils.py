#!/usr/bin/env python3
"""Utilities for parsing cached Discord signal messages."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

COMMON_TICKER_EXCLUDE = {
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN",
    "HAS", "HIS", "HOW", "ITS", "MAY", "NEW", "NOW", "OLD", "SEE",
    "WAY", "WHO", "BOT", "GET", "LET", "PUT", "SAY", "USE", "YES",
    "BUY", "SELL", "HOLD", "LONG", "SHORT", "WHAT", "WHEN", "THIS",
    "THAT", "WITH", "FROM", "HAVE", "WILL", "YOUR", "ABOUT", "THINK",
    "INTO", "SCALE", "DCA", "ADD", "USD", "SHARES",
    # Common uppercase fragments from prose that are not tickers
    "READY", "ABOVE", "BELOW", "BREAK", "LONGS", "SHORTS", "DATES", "PATTERN",
    "SIDE", "BANKS", "ORDER",
}

RECENT_SIGNAL_EXCLUDE = COMMON_TICKER_EXCLUDE | {
    "IMO", "FWIW", "ATH", "ATL", "EMA", "RSI", "MACD", "SMA", "GDP",
    "CPI", "IPO", "CEO", "CFO", "ETF", "OTM", "ITM", "ATM",
}


def extract_ticker(text: str) -> str | None:
    """Extract stock ticker from free-form text."""
    text_upper = text.upper()
    for pattern in (r"\$([A-Z]{1,5})\b", r"\b([A-Z]{1,5})\b"):
        for match in re.findall(pattern, text_upper):
            if match not in COMMON_TICKER_EXCLUDE and len(match) >= 2:
                return match
    return None


def _load_cached_messages(data_file: str) -> dict:
    if not os.path.exists(data_file):
        return {}
    with open(data_file, "r") as f:
        return json.load(f)


def _parse_timestamp(ts: str):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def get_ticker_messages(data_file: str, ticker: str, goku_channels: list[str], days: int = 60):
    """Get cached messages mentioning a ticker using exact regex boundaries."""
    if not os.path.exists(data_file):
        return [], None

    data = _load_cached_messages(data_file)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    goku_channel_set = {str(c) for c in goku_channels}

    ticker_upper = ticker.upper()
    ticker_patterns = [
        re.compile(r"\$" + re.escape(ticker_upper) + r"(?![A-Za-z])", re.IGNORECASE),
        re.compile(r"(?<![A-Za-z$])" + re.escape(ticker_upper) + r"(?![A-Za-z])", re.IGNORECASE),
    ]

    messages = []
    for channel_id, msgs in data.items():
        source = "Goku" if str(channel_id) in goku_channel_set else "Wilson"
        for msg in msgs:
            content = msg.get("content", "")
            ts = msg.get("timestamp", "")
            if not any(p.search(content) for p in ticker_patterns):
                continue
            try:
                dt = _parse_timestamp(ts)
            except Exception:
                continue
            if dt > cutoff:
                messages.append(
                    {
                        "source": source,
                        "date": ts[:10],
                        "content": content,
                        "author": msg.get("author", {}).get("username", "Unknown"),
                    }
                )

    messages.sort(key=lambda x: x["date"], reverse=True)
    return messages, None


def _normalize_tickers(candidates: list[str], limit: int = 8) -> list[str]:
    """Deduplicate, uppercase, and filter obvious non-ticker tokens."""
    out = []
    seen = set()
    for raw in candidates:
        t = str(raw or "").strip().upper().lstrip("$")
        if not (2 <= len(t) <= 5 and t.isalpha()):
            continue
        if t in RECENT_SIGNAL_EXCLUDE:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= limit:
            break
    return out


def _extract_recent_tickers(content: str) -> list[str]:
    content_raw = content or ""
    content_upper = content_raw.upper()
    tickers_found = [m.upper() for m in re.findall(r"\$([A-Z]{1,5})\b", content_raw, flags=re.IGNORECASE)]

    direction_match = re.findall(
        r"(?:LONG|SHORT|BUY|SELL|BOUGHT|SOLD|ADDING|ADDED|WATCHING)[:\s]+([A-Z]{2,5}(?:\s+[A-Z]{2,5})*)",
        content_upper,
        flags=re.IGNORECASE,
    )
    if direction_match:
        for group in direction_match:
            for ticker in group.split():
                t = ticker.strip()
                if 2 <= len(t) <= 5 and t.isalpha() and t not in tickers_found:
                    tickers_found.append(t)

    if tickers_found:
        return _normalize_tickers(tickers_found)

    # Fallback: use only truly uppercase tokens from original text to avoid
    # inventing tickers from normal prose after uppercasing.
    words = re.findall(r"\b([A-Z]{2,5})\b", content_raw)
    return _normalize_tickers(words)


def collect_recent_messages(data_file: str, goku_channels: list[str], days: int = 7):
    """Collect recent messages from cached data, grouped by ticker."""
    if not os.path.exists(data_file):
        return {}, []

    data = _load_cached_messages(data_file)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    goku_channel_set = {str(c) for c in goku_channels}

    all_recent = []
    ticker_map = {}
    for channel_id, msgs in data.items():
        source = "Goku" if str(channel_id) in goku_channel_set else "Wilson"
        for msg in msgs:
            content = msg.get("content", "")
            ts = msg.get("timestamp", "")
            tickers_found = _extract_recent_tickers(content)
            if not tickers_found:
                continue
            try:
                dt = _parse_timestamp(ts)
            except Exception:
                continue
            if dt <= cutoff:
                continue

            entry = {
                "source": source,
                "date": ts[:10],
                "tickers": tickers_found[:5],
                "content": content,
                "author": msg.get("author", {}).get("username", "Unknown"),
            }
            all_recent.append(entry)
            for ticker in tickers_found[:5]:
                ticker_map.setdefault(ticker, []).append(entry)

    all_recent.sort(key=lambda x: x["date"], reverse=True)
    return ticker_map, all_recent
