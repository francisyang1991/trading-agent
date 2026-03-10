#!/usr/bin/env python3
"""
Runner for intraday core-position T+0 MVP (paper only).
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from loguru import logger

# Ensure project root is importable when running from tools/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.intraday_t0_engine import IntradayT0Engine
from src.utils.logger import setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Intraday T+0 MVP runner (paper mode)")
    parser.add_argument(
        "--config",
        default="config/intraday_t0_config.yaml",
        help="Path to intraday T+0 config YAML",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (DEBUG/INFO/WARNING/ERROR)",
    )
    return parser.parse_args()


async def _main_async(args: argparse.Namespace) -> int:
    setup_logging(log_dir="logs", level=args.log_level)
    cfg = IntradayT0Engine.load_from_yaml(args.config)
    engine = IntradayT0Engine(cfg)

    stop_event = asyncio.Event()

    def _stop_handler(*_):
        logger.warning("Shutdown signal received, flattening and exiting...")
        stop_event.set()

    signal.signal(signal.SIGINT, _stop_handler)
    signal.signal(signal.SIGTERM, _stop_handler)

    await engine.setup()
    run_task = asyncio.create_task(engine.run())
    stop_task = asyncio.create_task(stop_event.wait())

    done, _ = await asyncio.wait({run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    if stop_task in done:
        await engine.force_flatten_all(reason="manual_shutdown")
        await engine.shutdown()
        return 0
    return 0 if run_task.exception() is None else 1


def main() -> None:
    args = parse_args()
    try:
        code = asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        code = 130
    except Exception as exc:
        logger.exception(f"Runner failed: {exc}")
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
