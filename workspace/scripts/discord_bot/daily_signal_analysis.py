#!/usr/bin/env python3
"""Reusable daily signal-analysis helpers for Discord bots."""

from __future__ import annotations

try:
    from .llm_shared import extract_llm_text
except ImportError:
    from llm_shared import extract_llm_text


def build_signal_summaries(top_tickers, get_stock_context_fn):
    """Build compact signal summaries for LLM prompt context."""
    signal_summaries = []
    for ticker, msgs in top_tickers:
        goku_msgs = [m for m in msgs if m["source"] == "Goku"]
        wilson_msgs = [m for m in msgs if m["source"] == "Wilson"]

        ctx_data = get_stock_context_fn(ticker)
        price_str = ""
        if ctx_data and "error" not in ctx_data:
            price_str = (
                f"Price: ${ctx_data.get('current_price', '?')} | "
                f"EMA8: ${ctx_data.get('ema8', '?')} | "
                f"EMA21: ${ctx_data.get('ema21', '?')} | "
                f"RSI: {ctx_data.get('rsi', '?')}"
            )

        goku_text = "\n".join(
            f"  [{m['date']}] {m['author']}: {m['content'][:150]}"
            for m in goku_msgs[:5]
        )
        wilson_text = "\n".join(
            f"  [{m['date']}] {m['author']}: {m['content'][:150]}"
            for m in wilson_msgs[:5]
        )

        signal_summaries.append(
            f"${ticker} ({len(msgs)} mentions, Goku:{len(goku_msgs)} Wilson:{len(wilson_msgs)})\n"
            f"  {price_str}\n"
            f"  Goku signals:\n{goku_text or '    None'}\n"
            f"  Wilson signals:\n{wilson_text or '    None'}"
        )

    return "\n\n".join(signal_summaries)


def build_pattern_context(top_tickers, pattern_library_module, logger=None):
    """Attach historical pattern stats relevant to top discussed tickers."""
    pattern_context = ""
    try:
        pat_records = pattern_library_module.load_pattern_db()
        if not pat_records:
            return pattern_context

        pat_stats = pattern_library_module.compute_pattern_stats(pat_records)
        pat_lines = []
        for ticker_name, msgs_list in top_tickers:
            found = {}
            for msg in msgs_list:
                content = msg.get("content", "")
                recs = pattern_library_module.extract_patterns(content, ticker_name)
                for rec in recs:
                    stat = pat_stats.get(rec.pattern_name)
                    if stat and stat.tracked >= 3:
                        found[rec.pattern_name] = (
                            rec.pattern_display,
                            stat.win_rate,
                            stat.avg_return_5d,
                            stat.avg_return_10d,
                            stat.avg_return_20d,
                        )

            if not found:
                continue

            parts = []
            for _, (display, win_rate, r5, r10, r20) in found.items():
                best_hold = "5d" if r5 >= r10 and r5 >= r20 else ("10d" if r10 >= r20 else "20d")
                parts.append(f"{display}: {win_rate:.0%} WR, best hold={best_hold}")
            pat_lines.append(f"${ticker_name}: {'; '.join(parts)}")

        if pat_lines:
            pattern_context = (
                "\n\nPATTERN LIBRARY (historical win rates from 1-year backtest):\n"
                + "\n".join(pat_lines)
            )
    except Exception as e:
        if logger:
            logger.debug(f"Pattern context error: {e}")

    return pattern_context


def build_daily_prompt(days: int, signals_block: str, pattern_context: str):
    """Build the daily LLM analysis prompt from signal and pattern context."""
    return f"""You are an elite trading desk analyst. Analyze these recent Discord signals
from two trader channels (Goku = technical, Wilson = fundamental) and determine
which tickers are worth trading TODAY.

RECENT SIGNALS (last {days} days):
{signals_block}
{pattern_context}

For each ticker, evaluate:
1. Signal strength (how many mentions, consistency of bias)
2. Technical setup (price vs EMAs, RSI)
3. Pattern history (use the PATTERN LIBRARY data — patterns with >70% WR are strong setups)
4. Risk level and recommended hold period (based on pattern best hold period)
5. Whether it's ACTIONABLE now or should WAIT

Format your response for Discord (<1800 chars):
*Daily Signal Analysis*

For each ticker use this format:
🟢 $TICKER — BUY (pattern name, WR%, entry zone, hold X days)
🔴 $TICKER — SELL/SHORT (reason, stop level)
🟡 $TICKER — WAIT (trigger to watch, expected setup)
⚪ $TICKER — NO TRADE (why skip)

End with a 1-line overall market read.
Use single asterisks for bold. Be specific with price levels and hold periods."""


def run_daily_llm_analysis(
    *,
    top_tickers,
    days: int,
    llm_fn,
    get_stock_context_fn,
    pattern_library_module,
    logger=None,
):
    """Return normalized daily analysis text from LLM; empty string on failure."""
    signals_block = build_signal_summaries(top_tickers, get_stock_context_fn)
    pattern_context = build_pattern_context(top_tickers, pattern_library_module, logger=logger)
    prompt = build_daily_prompt(days, signals_block, pattern_context)

    response = llm_fn(prompt, 2000, {}, "DAILY", [])
    return extract_llm_text(response, max_chars=1900)


def build_basic_daily_summary(top_tickers, get_stock_context_fn):
    """Fallback summary when LLM output is unavailable."""
    out = "*Daily Signal Summary* (LLM unavailable — raw counts)\n\n"
    for ticker, msgs in top_tickers:
        goku = sum(1 for m in msgs if m["source"] == "Goku")
        wilson = sum(1 for m in msgs if m["source"] == "Wilson")
        ctx_data = get_stock_context_fn(ticker)
        price_str = ""
        if ctx_data and "error" not in ctx_data:
            price = ctx_data.get("current_price", "?")
            rsi = ctx_data.get("rsi", "?")
            price_str = f" — ${price} (RSI: {rsi})"

        out += f"• *${ticker}*{price_str} — {len(msgs)} mentions "
        out += f"(📊Goku:{goku} 📈Wilson:{wilson})\n"
        latest = msgs[0]["content"][:80].replace("\n", " ")
        out += f"  ↳ _{latest}_...\n\n"

    out += "💡 Use `!analyze TICKER` for deep analysis on any ticker."
    return out
