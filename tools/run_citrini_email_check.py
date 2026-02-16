#!/usr/bin/env python3
"""
Run Citrini email check: fetch new emails → LLM → format for Discord.

Use this to test the pipeline without running the Discord bot.
Output is printed to stdout (Discord-formatted messages).

Environment:
  ANTHROPIC_API_KEY or ZAI_API_KEY
  (Gmail OAuth uses token.json from project root)

Example:
  python tools/run_citrini_email_check.py
  python tools/run_citrini_email_check.py --no-llm  # fetch only, no LLM (dry run)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None

from src.email_analysis.citrini_discord import run_citrini_email_check


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Citrini emails and extract trade ideas.")
    parser.add_argument("--no-llm", action="store_true", help="Fetch only, skip LLM (dry run).")
    parser.add_argument("--env-file", default="", help="Optional dotenv file.")
    args = parser.parse_args()

    if args.env_file and load_dotenv:
        load_dotenv(args.env_file, override=False)

    if args.no_llm:
        # Dry run: just fetch, don't process (would need a separate code path)
        print("--no-llm: Use tools/analyze_citrini_emails.py --dry-run-fetch instead.")
        sys.exit(0)

    llm_provider = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "zai"
    messages, err = run_citrini_email_check(
        client_secret_path=str(ROOT / "secret" / "client_secret_75860045039-mgs0h9aai4488pokfqgsqlb1doo41eh4.apps.googleusercontent.com.json"),
        token_path=str(ROOT / "token.json"),
        processed_path="data/email/citrini_processed.json",
        sender="citrini@substack.com",
        limit=10,
        unseen_only=True,
        llm_provider=llm_provider,
        root_dir=ROOT,
    )

    if err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
    if not messages:
        print("No new Citrini emails to process.")
        return
    print(f"--- {len(messages)} message(s) for Discord ---\n")
    for i, msg in enumerate(messages, 1):
        print(f"--- Message {i} ---")
        print(msg)
        print()


if __name__ == "__main__":
    main()
