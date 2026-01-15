#!/usr/bin/env python3
"""
Daily Scanner + Email Report
===========================

Runs `tools/scanner.py` on a schedule and emails the report to you.

Two common ways to use this:
  1) Long-running scheduler (recommended if you want "always on"):
     python tools/daily_scanner_email.py --daily --time 07:00 --high-conviction

  2) Cron (recommended for simplicity/robustness):
     python tools/daily_scanner_email.py --once --high-conviction

Email configuration is taken from environment variables:
  SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_USE_TLS
  EMAIL_SENDER, EMAIL_RECIPIENTS, EMAIL_SUBJECT_PREFIX
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import os
import sys
from pathlib import Path
import time
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:  # pragma: no cover
    load_dotenv = None

from src.utils.emailer import (
    build_email_message,
    load_email_config_from_env,
    load_email_config_from_settings_yaml,
    read_text_file_truncated,
    send_email_smtp,
)

from tools.scanner import load_universe, run_scan


def _parse_hhmm(value: str) -> Tuple[int, int]:
    v = value.strip()
    if ":" not in v:
        raise ValueError("Time must be in HH:MM format.")
    hh, mm = v.split(":", 1)
    h = int(hh)
    m = int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("Time must be in HH:MM format (00:00 to 23:59).")
    return h, m


def _next_run_at(hhmm: str) -> datetime:
    h, m = _parse_hhmm(hhmm)
    now = datetime.now()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return target


def _resolve_symbols(args) -> Tuple[List[str], Optional[str]]:
    universe = load_universe()
    theme_name = None

    if args.symbols:
        symbols = [s.upper() for s in args.symbols]
    elif args.all:
        symbols = universe.get("all_symbols", [])
        theme_name = "FULL UNIVERSE"
    elif args.theme:
        theme_key = args.theme
        if theme_key in universe.get("themes", {}):
            theme = universe["themes"][theme_key]
            symbols = theme.get("symbols", [])
            theme_name = theme.get("name", theme_key)
        else:
            raise SystemExit(
                f"Theme '{theme_key}' not found. Available: {', '.join(universe.get('themes', {}).keys())}"
            )
    elif args.high_conviction:
        symbols = universe.get("high_conviction", ["NVDA", "GOOGL", "MSFT", "PLTR", "CRWD"])
        theme_name = "HIGH CONVICTION"
    else:
        symbols = universe.get("high_conviction", ["NVDA", "GOOGL", "MSFT", "PLTR", "CRWD"])
        theme_name = "HIGH CONVICTION"

    return symbols, theme_name


def run_scanner_and_email(args) -> str:
    """
    Runs the scanner once and emails the produced report.
    Returns the report path (or empty string on failure).
    """
    symbols, theme_name = _resolve_symbols(args)

    report_path = run_scan(
        symbols=symbols,
        output_file=None,
        to_console=False,
        plan_mode=args.plan,
        quick_mode=args.quick,
        theme_name=theme_name,
    )

    if not report_path:
        raise RuntimeError("Scanner did not produce a report file path.")

    report_file = Path(report_path)
    if not report_file.exists():
        raise RuntimeError(f"Report file not found after scan: {report_file}")

    smtp, email = load_email_config_from_env(prefix=args.env_prefix)
    if smtp is None or email is None:
        smtp, email = load_email_config_from_settings_yaml(args.settings)

    if smtp is None or email is None:
        if args.dry_run_email:
            print("⚠️ [DRY RUN] Email config not found; skipping SMTP send.")
            print(f"   Report generated at: {report_file}")
            return str(report_file)
        raise RuntimeError(
            "Email config not found. Set SMTP_HOST (and other SMTP_/EMAIL_ vars) or provide an env file."
        )

    if not email.sender:
        if args.dry_run_email:
            print("⚠️ [DRY RUN] EMAIL_SENDER is missing; skipping SMTP send.")
            print(f"   Report generated at: {report_file}")
            return str(report_file)
        raise RuntimeError("EMAIL_SENDER is missing (or SMTP_USERNAME).")

    recipients = email.recipients or [email.sender]

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    title_bits = ["Scanner Report", now]
    if theme_name:
        title_bits.insert(1, theme_name)
    subject = " - ".join(title_bits)
    if email.subject_prefix:
        subject = f"{email.subject_prefix} {subject}".strip()

    body = read_text_file_truncated(report_file)
    body = f"Generated: {now}\nReport file: {report_file}\n\n{body}"

    msg = build_email_message(
        subject=subject,
        body_text=body,
        sender=email.sender,
        recipients=recipients,
        attachments=[report_file],
    )

    if args.dry_run_email:
        print("\n[DRY RUN] Email would be sent with:")
        print(f"  From: {email.sender}")
        print(f"  To: {', '.join(recipients)}")
        print(f"  Subject: {subject}")
        print(f"  Attachment: {report_file}")
        return str(report_file)

    send_email_smtp(smtp=smtp, message=msg, timeout_seconds=30)
    print(f"✅ Email sent to: {', '.join(recipients)}")

    return str(report_file)


def main():
    parser = argparse.ArgumentParser(description="Run scanner daily and email the report.")

    # Scheduling
    parser.add_argument("--daily", action="store_true", help="Run continuously and execute daily at --time.")
    parser.add_argument("--time", default="07:00", help="Daily run time (HH:MM, local time).")
    parser.add_argument("--once", action="store_true", help="Run once immediately and exit.")
    parser.add_argument("--run-now", action="store_true", help="When --daily, run once immediately on startup.")

    # Scanner selection (mirrors tools/scanner.py logic)
    parser.add_argument("symbols", nargs="*", help="Symbols to scan (e.g. AAPL NVDA).")
    parser.add_argument("--theme", type=str, help="Theme key from config/stock_universe.yaml (e.g. crypto).")
    parser.add_argument("--all", action="store_true", help="Scan full universe.")
    parser.add_argument("--high-conviction", action="store_true", help="Scan the high_conviction list.")
    parser.add_argument("--plan", action="store_true", help="Detailed plan mode (per-stock plans).")
    parser.add_argument("--quick", action="store_true", help="Quick scan mode only.")

    # Email / env
    parser.add_argument(
        "--settings",
        default="config/settings.yaml",
        help="Optional settings.yaml to read email defaults from (notifications.channels.email).",
    )
    parser.add_argument("--env-file", default="", help="Optional dotenv file to load (e.g. .env or env.list).")
    parser.add_argument("--env-prefix", default="", help="Optional env var prefix (e.g. SAIYAN_).")
    parser.add_argument("--dry-run-email", action="store_true", help="Do everything except sending SMTP.")

    args = parser.parse_args()

    if args.env_file:
        if load_dotenv is None:
            raise SystemExit(
                "python-dotenv is not installed, so --env-file is not supported in this environment."
            )
        load_dotenv(args.env_file, override=False)

    if not args.daily and not args.once:
        # Sensible default: run once (cron-friendly).
        args.once = True

    if args.once:
        run_scanner_and_email(args)
        return

    # Daily scheduler loop
    def job():
        try:
            run_scanner_and_email(args)
        except Exception as e:
            # Best effort: print so the scheduler doesn't silently die.
            print(f"❌ Daily scanner job failed: {e}")

    # Validate schedule time early.
    _parse_hhmm(args.time)
    print(f"⏰ Scheduled daily scanner at {args.time} (local time).")

    if args.run_now:
        job()

    while True:
        target = _next_run_at(args.time)
        while True:
            now = datetime.now()
            if now >= target:
                break
            remaining = (target - now).total_seconds()
            # Sleep in chunks so Ctrl+C is responsive.
            time.sleep(min(30, max(1, int(remaining))))
        job()


if __name__ == "__main__":
    main()

