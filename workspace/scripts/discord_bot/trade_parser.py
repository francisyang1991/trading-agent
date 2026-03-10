"""
Trade Command Parser
====================
Parses Discord messages into structured trade commands.

Supports:
  buy HOOD              → single entry, auto sizing
  sell TSLA             → short, single entry
  buy HOOD 2x           → 2 scaled entries
  buy HOOD 5000usd      → $5,000 worth of HOOD
  buy HOOD 50 shares    → exactly 50 shares
  scale into HOOD       → 2 scaled entries (auto)
  DCA into NVDA 3x      → 3 scaled entries

Returns a TradeCommand dataclass or None.
"""

import re
import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class TradeCommand:
    """Parsed trade command."""
    direction: str          # "long" or "short"
    ticker: str             # e.g. "HOOD"
    num_entries: int = 1    # 1 = single, 2+ = scaled
    dollar_amount: float = 0.0   # e.g. 5000.0  (0 = auto size)
    share_count: int = 0         # e.g. 50       (0 = auto size)


def parse(text: str) -> Optional[TradeCommand]:
    """
    Parse a trade command from message text.

    Returns TradeCommand or None if not a trade command.
    """
    text_upper = text.strip().upper()

    # ---- Pattern 1: "scale into TICKER" / "DCA into TICKER" ----
    m = re.match(
        r'^(?:SCALE|DCA)\s+INTO\s+\$?([A-Z]{1,5})\b'
        r'(?:\s+(\d)\s*(?:X|ENTRIES|BATCHES|PARTS))?',
        text_upper,
    )
    if m:
        n = int(m.group(2)) if m.group(2) else 2
        return TradeCommand(
            direction="long",
            ticker=m.group(1),
            num_entries=_clamp_entries(n),
        )

    # ---- Pattern 2: "BUY/SELL TICKER [modifiers]" ----
    m = re.match(
        r'^(BUY|SELL|LONG|SHORT|ADD)\s+\$?([A-Z]{1,5})\b(.*)',
        text_upper,
    )
    if not m:
        return None

    action = m.group(1)
    ticker = m.group(2)
    rest = m.group(3).strip()
    direction = "long" if action in ("BUY", "LONG", "ADD") else "short"

    dollar_amount = 0.0
    share_count = 0
    num_entries = 1

    # --- Check for dollar amount: "5000usd", "$5000", "5000 dollars", "5k" ---
    dm = re.search(
        r'(\d[\d,]*(?:\.\d+)?)\s*(?:USD|DOLLARS?|\$)'
        r'|'
        r'\$\s*(\d[\d,]*(?:\.\d+)?)'
        r'|'
        r'(\d+(?:\.\d+)?)\s*K\b',
        rest,
    )
    if dm:
        raw = dm.group(1) or dm.group(2) or None
        if raw:
            dollar_amount = float(raw.replace(",", ""))
        elif dm.group(3):
            dollar_amount = float(dm.group(3)) * 1000

    # --- Check for share count: "50 shares", "100 sh" ---
    sm = re.search(r'(\d+)\s*(?:SHARES?|SH)\b', rest)
    if sm and dollar_amount == 0:
        share_count = int(sm.group(1))

    # --- Check for entry count: "2x", "with 2 entries", etc. ---
    entry_patterns = [
        r'(\d)\s*X\b',
        r'(?:WITH|IN)\s+(\d)\s*(?:ENTRIES|BATCHES|PARTS|LOTS)',
        r'(\d)\s*(?:ENTRIES|BATCHES|PARTS|LOTS)',
        r'SPLIT\s+(\d)',
    ]
    for pat in entry_patterns:
        em = re.search(pat, rest)
        if em:
            num_entries = int(em.group(1))
            break

    return TradeCommand(
        direction=direction,
        ticker=ticker,
        num_entries=_clamp_entries(num_entries),
        dollar_amount=dollar_amount,
        share_count=share_count,
    )


def _clamp_entries(n: int) -> int:
    return max(1, min(n, 5))


def format_command(cmd: TradeCommand) -> str:
    """Human-readable summary of a parsed command."""
    action = "BUY" if cmd.direction == "long" else "SELL"
    parts = [f"{action} {cmd.ticker}"]
    if cmd.dollar_amount > 0:
        parts.append(f"${cmd.dollar_amount:,.0f}")
    elif cmd.share_count > 0:
        parts.append(f"{cmd.share_count} shares")
    if cmd.num_entries > 1:
        parts.append(f"{cmd.num_entries}x scaled")
    return " ".join(parts)
