#!/usr/bin/env python3
"""Routing helpers for bot mention messages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class MentionIntent:
    kind: str
    clean_text: str
    ticker: str | None = None
    trade_command: Any = None


def _clean_mention_text(content: str) -> str:
    return re.sub(r"<@!?\d+>", "", content).strip()


def parse_mention_intent(
    message_content: str,
    trade_parse_fn: Callable[[str], Any],
    ticker_extract_fn: Callable[[str], str | None],
) -> MentionIntent:
    """Parse a mention message into trade/analyze/help intent."""
    clean = _clean_mention_text(message_content)

    trade_command = trade_parse_fn(clean)
    if trade_command:
        return MentionIntent(
            kind="trade",
            clean_text=clean,
            ticker=getattr(trade_command, "ticker", None),
            trade_command=trade_command,
        )

    ticker = ticker_extract_fn(clean)
    if ticker:
        return MentionIntent(kind="analyze", clean_text=clean, ticker=ticker)

    return MentionIntent(kind="help", clean_text=clean)
