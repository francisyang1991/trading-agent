#!/usr/bin/env python3
"""
Fetch and analyze citrini@substack.com emails for trade ideas.

Supports two fetch providers:
- Gmail OAuth API (default): browser consent on first run, token cache after.
- IMAP (fallback): credential-based mailbox access.

Environment variables:
  ZAI_API_KEY (for --llm-provider zai) or ANTHROPIC_API_KEY (for --llm-provider anthropic)
  [IMAP mode only] IMAP_HOST, IMAP_PORT, IMAP_USERNAME, IMAP_PASSWORD, IMAP_USE_SSL, IMAP_FOLDER

Examples:
  python3 tools/analyze_citrini_emails.py --provider gmail
  python3 tools/analyze_citrini_emails.py --provider gmail --limit 25 --since-days 120
  python3 tools/analyze_citrini_emails.py --provider imap --limit 10 --dry-run-fetch
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from typing import List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:  # pragma: no cover
    load_dotenv = None

from src.email_analysis.email_trade_ideas import (
    EmailAnalysis,
    EmailDocument,
    analyses_to_dict,
    analyze_emails_with_llm,
    build_ticker_summary,
    emails_to_dict,
    fetch_emails_from_sender,
    load_imap_config_from_env,
)
from src.email_analysis.gmail_reader import (
    GMAIL_READONLY_SCOPES,
    build_gmail_service,
    fetch_gmail_emails_from_sender,
)


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _setup_logging(verbose: bool, log_file: str | None = None) -> None:
    """Configure logging for full monitor output. Verbose enables DEBUG."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s - %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, handlers=handlers, force=True)
    for name in ("src.email_analysis", "src.email_analysis.gmail_reader", "src.email_analysis.email_trade_ideas"):
        logging.getLogger(name).setLevel(level)


def _render_markdown(
    *,
    sender: str,
    fetched_emails: List[EmailDocument],
    analyses: List[EmailAnalysis],
    ticker_summary,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: List[str] = []
    lines.append("# Citrini Email Trade Ideas")
    lines.append("")
    lines.append(f"- Generated: {now}")
    lines.append(f"- Sender filter: `{sender}`")
    lines.append(f"- Emails fetched: {len(fetched_emails)}")
    lines.append(f"- Emails analyzed: {len(analyses)}")
    lines.append("")

    lines.append("## Ticker Summary")
    lines.append("")
    if not ticker_summary:
        lines.append("No ticker ideas were extracted.")
    else:
        lines.append("| Ticker | Mentions | Avg Confidence | Directions |")
        lines.append("|---|---:|---:|---|")
        for row in ticker_summary:
            directions = ", ".join(f"{k}:{v}" for k, v in sorted(row["directions"].items()))
            lines.append(
                f"| {row['ticker']} | {row['mentions']} | {row['avg_confidence']:.3f} | {directions or 'n/a'} |"
            )
    lines.append("")

    lines.append("## Per-Email Analysis")
    lines.append("")
    for item in analyses:
        lines.append(f"### {item.subject or '(no subject)'}")
        lines.append("")
        lines.append(f"- From: `{item.from_address or 'unknown'}`")
        lines.append(f"- Sent At: `{item.sent_at or 'unknown'}`")
        lines.append(f"- Sentiment: `{item.overall_sentiment or 'unclear'}`")
        lines.append(f"- Summary: {item.summary or 'n/a'}")
        lines.append("")

        if not item.trade_ideas:
            lines.append("No actionable trade ideas extracted.")
            lines.append("")
            continue

        lines.append("| Ticker | Direction | Confidence | Horizon | Catalyst | Thesis |")
        lines.append("|---|---|---:|---|---|---|")
        for idea in item.trade_ideas:
            thesis = idea.thesis.replace("|", "/")
            catalyst = idea.catalyst.replace("|", "/")
            lines.append(
                f"| {idea.ticker or 'n/a'} | {idea.direction or 'unknown'} | "
                f"{idea.confidence:.3f} | {idea.time_horizon or 'unspecified'} | "
                f"{catalyst or 'n/a'} | {thesis or 'n/a'} |"
            )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and analyze citrini@substack.com emails.")
    parser.add_argument(
        "--provider",
        choices=["gmail", "imap"],
        default="gmail",
        help="Email source provider (default: gmail).",
    )
    parser.add_argument("--sender", default="citrini@substack.com", help="Exact sender email to filter.")
    parser.add_argument("--limit", type=int, default=20, help="Max emails to fetch (latest first).")
    parser.add_argument("--since-days", type=int, default=None, help="Only include emails from the last N days.")
    parser.add_argument("--unseen-only", action="store_true", help="Only fetch unread emails.")
    parser.add_argument(
        "--gmail-client-secret",
        default="secret/client_secret_75860045039-mgs0h9aai4488pokfqgsqlb1doo41eh4.apps.googleusercontent.com.json",
        help="Path to Google OAuth client secret JSON (gmail provider).",
    )
    parser.add_argument(
        "--gmail-token",
        default="token.json",
        help="Path for cached OAuth user token JSON (gmail provider).",
    )
    parser.add_argument("--env-file", default="", help="Optional dotenv file path.")
    parser.add_argument("--env-prefix", default="", help="Optional env var prefix (e.g. PROD_).")
    parser.add_argument(
        "--llm-provider",
        choices=["zai", "anthropic"],
        default="zai",
        help="LLM provider for trade idea extraction (default: zai).",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Model name (default: glm-4-plus for zai, claude-3-5-sonnet-latest for anthropic).",
    )
    parser.add_argument(
        "--output-json",
        default="results/email/citrini_trade_ideas.json",
        help="Where to save JSON output.",
    )
    parser.add_argument(
        "--output-md",
        default="results/email/citrini_trade_ideas.md",
        help="Where to save markdown report.",
    )
    parser.add_argument(
        "--dry-run-fetch",
        action="store_true",
        help="Fetch emails and save raw output only (skip LLM analysis).",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Full monitor log (DEBUG level).")
    parser.add_argument("--log-file", default="", help="Also write logs to this file.")
    args = parser.parse_args()

    _setup_logging(args.verbose, args.log_file or None)

    if args.env_file:
        if load_dotenv is None:
            raise SystemExit("python-dotenv is not available, so --env-file cannot be used.")
        load_dotenv(args.env_file, override=False)

    if args.provider == "gmail":
        service = build_gmail_service(
            client_secret_path=args.gmail_client_secret,
            token_path=args.gmail_token,
            scopes=GMAIL_READONLY_SCOPES,
        )
        emails = fetch_gmail_emails_from_sender(
            service=service,
            sender_email=args.sender,
            limit=args.limit,
            unseen_only=args.unseen_only,
            newer_than_days=args.since_days,
        )
    else:
        imap_cfg = load_imap_config_from_env(prefix=args.env_prefix)
        if imap_cfg is None:
            raise SystemExit(
                "Missing IMAP credentials. Required env vars: IMAP_HOST, IMAP_USERNAME, IMAP_PASSWORD "
                "(optional: IMAP_PORT, IMAP_USE_SSL, IMAP_FOLDER)."
            )
        emails = fetch_emails_from_sender(
            config=imap_cfg,
            sender_email=args.sender,
            limit=args.limit,
            unseen_only=args.unseen_only,
            since_days=args.since_days,
        )
    print(f"Fetched {len(emails)} email(s) from {args.sender}.")

    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    _ensure_parent(out_json)
    _ensure_parent(out_md)

    if args.dry_run_fetch:
        payload = {
            "generated_at": datetime.now().isoformat(),
            "provider": args.provider,
            "sender": args.sender,
            "email_count": len(emails),
            "emails": emails_to_dict(emails),
            "analysis_skipped": True,
        }
        out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        out_md.write_text(
            "# Citrini Email Fetch (Dry Run)\n\n"
            f"- Provider: `{args.provider}`\n"
            f"- Sender: `{args.sender}`\n"
            f"- Emails fetched: {len(emails)}\n"
            f"- JSON output: `{out_json}`\n",
            encoding="utf-8",
        )
        print(f"Saved dry-run fetch output to {out_json}")
        print(f"Saved dry-run report to {out_md}")
        return

    if args.llm_provider == "zai":
        api_key = os.getenv("ZAI_API_KEY", "")
        if not api_key:
            raise SystemExit("ZAI_API_KEY is required unless --dry-run-fetch is used.")
        model = args.model or "glm-4-plus"
    else:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise SystemExit("ANTHROPIC_API_KEY is required unless --dry-run-fetch is used.")
        model = args.model or "claude-3-5-sonnet-latest"

    zai_base_url = os.getenv("ZAI_BASE_URL", "").strip() or None
    analyses = analyze_emails_with_llm(
        emails=emails,
        provider=args.llm_provider,
        model=model,
        api_key=api_key,
        zai_base_url=zai_base_url,
    )
    ticker_summary = build_ticker_summary(analyses)

    payload = {
        "generated_at": datetime.now().isoformat(),
        "provider": args.provider,
        "llm_provider": args.llm_provider,
        "sender": args.sender,
        "email_count": len(emails),
        "analysis_count": len(analyses),
        "ticker_summary": ticker_summary,
        "analyses": analyses_to_dict(analyses),
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    out_md.write_text(
        _render_markdown(
            sender=args.sender,
            fetched_emails=emails,
            analyses=analyses,
            ticker_summary=ticker_summary,
        ),
        encoding="utf-8",
    )

    print(f"Saved analysis JSON: {out_json}")
    print(f"Saved markdown report: {out_md}")
    if ticker_summary:
        print("Top extracted tickers:")
        for row in ticker_summary[:10]:
            print(f"  - {row['ticker']}: mentions={row['mentions']}, avg_conf={row['avg_confidence']:.3f}")


if __name__ == "__main__":
    main()
