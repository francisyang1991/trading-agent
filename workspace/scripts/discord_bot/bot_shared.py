#!/usr/bin/env python3
"""Shared Discord bot IO and error-reporting helpers."""

from __future__ import annotations

from typing import Awaitable, Callable


def format_error(error: Exception, max_chars: int = 200) -> str:
    """Normalize exception text for Discord-safe error messages."""
    text = str(error).strip() or error.__class__.__name__
    text = " ".join(text.splitlines())
    return text[:max_chars]


async def safe_send(channel, text: str, logger=None, max_chars: int = 2000):
    """Send a Discord message with empty/length guards."""
    if not text or not text.strip():
        if logger:
            logger.warning("Attempted to send empty message — skipped")
        return

    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."

    await channel.send(text)


async def notify_job_exception(
    *,
    job_label: str,
    error: Exception,
    resolve_channel_fn: Callable[[], Awaitable],
    send_fn: Callable[..., Awaitable[None]],
    logger=None,
):
    """Best-effort scheduler error report to Discord and logs."""
    if logger:
        logger.warning(f"{job_label} scheduler error: {format_error(error)}")

    try:
        channel = await resolve_channel_fn()
        if channel is not None:
            await send_fn(channel, f"⚠️ {job_label} error: {format_error(error)}")
    except Exception as notify_error:
        if logger:
            logger.warning(f"Failed to notify Discord for {job_label} error: {notify_error}")
