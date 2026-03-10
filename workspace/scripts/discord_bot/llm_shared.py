#!/usr/bin/env python3
"""Shared LLM adapter for Discord bots."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_CORE_ANALYSIS_DIR = _SCRIPT_DIR.parent / "core_analysis"
if str(_CORE_ANALYSIS_DIR) not in sys.path:
    sys.path.append(str(_CORE_ANALYSIS_DIR))

from llm_analyzer import _call_minimax_anthropic, get_stock_context as _get_stock_context  # noqa: E402


def call_llm_raw(prompt, max_tokens=2000, stock_ctx=None, ticker="GENERIC", messages=None):
    """Shared raw LLM call signature used by interactive and pipeline flows."""
    return _call_minimax_anthropic(
        prompt,
        max_tokens,
        stock_ctx or {},
        ticker,
        messages or [],
    )


def extract_llm_text(response, max_chars: int | None = 1900) -> str:
    """Extract and normalize text from mixed LLM response formats."""
    if not isinstance(response, dict):
        return ""
    content = response.get("raw_response") or response.get("raw_llm") or ""
    if not content:
        return ""

    content = content.replace("```", "").replace("**", "*").strip()
    if max_chars is not None and len(content) > max_chars:
        return content[: max_chars - 3] + "..."
    return content


def get_stock_context(ticker: str):
    """Shared stock-context retrieval so all bots use one provider path."""
    return _get_stock_context(ticker)
