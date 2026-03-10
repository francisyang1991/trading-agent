#!/usr/bin/env python3
"""Reusable scheduler loops for Discord bot jobs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class DailySchedulerJob:
    name: str
    hour: int
    minute: int
    startup_delay_sec: int = 60
    poll_interval_sec: int = 60
    error_sleep_sec: int = 60


async def run_daily_scheduler(
    *,
    config: DailySchedulerJob,
    is_due_today_fn,
    run_job_fn,
    on_error_fn=None,
    logger=None,
):
    """Run a once-per-day weekday scheduler loop."""
    await asyncio.sleep(config.startup_delay_sec)
    last_run_date = ""

    while True:
        try:
            should_run, run_date, local_now = is_due_today_fn(
                last_run_date,
                config.hour,
                config.minute,
            )
            if should_run:
                last_run_date = run_date
                if logger:
                    logger.info(
                        f"{config.name} triggered ({local_now.strftime('%Y-%m-%d %H:%M %Z')})"
                    )
                await run_job_fn(local_now)
            await asyncio.sleep(config.poll_interval_sec)
        except Exception as e:
            if logger:
                logger.exception(f"{config.name} scheduler loop error: {e}")
            if on_error_fn:
                await on_error_fn(e)
            await asyncio.sleep(config.error_sleep_sec)


async def run_interval_scheduler(
    *,
    name: str,
    startup_delay_sec: int,
    tick_fn,
    default_interval_sec: int,
    error_interval_sec: int = 60,
    on_error_fn=None,
    logger=None,
):
    """
    Run an interval scheduler loop.

    tick_fn may return a custom interval (seconds) for the next loop.
    """
    await asyncio.sleep(startup_delay_sec)

    while True:
        sleep_for = default_interval_sec
        try:
            maybe_interval = await tick_fn()
            if isinstance(maybe_interval, (int, float)) and maybe_interval > 0:
                sleep_for = int(maybe_interval)
        except Exception as e:
            if logger:
                logger.exception(f"{name} scheduler loop error: {e}")
            if on_error_fn:
                await on_error_fn(e)
            sleep_for = error_interval_sec

        await asyncio.sleep(sleep_for)
