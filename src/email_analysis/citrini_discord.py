"""
Citrini email → LLM → Discord pipeline.

Fetches new (unseen) emails from citrini@substack.com, extracts trade ideas via LLM,
and formats for Discord. Tracks processed message IDs to avoid re-sending.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .email_trade_ideas import (
    EmailAnalysis,
    EmailDocument,
    TradeIdea,
    analyze_emails_with_llm,
)
from .gmail_reader import (
    GMAIL_READONLY_SCOPES,
    build_gmail_service,
    fetch_gmail_emails_from_sender,
)

DEFAULT_SENDER = "citrini@substack.com"
DEFAULT_PROCESSED_FILE = "data/email/citrini_processed.json"
DEFAULT_CLIENT_SECRET = "secret/client_secret_75860045039-mgs0h9aai4488pokfqgsqlb1doo41eh4.apps.googleusercontent.com.json"
DEFAULT_TOKEN_PATH = "token.json"

logger = logging.getLogger(__name__)


def _load_processed_ids(processed_path: Path) -> set[str]:
    if not processed_path.exists():
        return set()
    try:
        data = json.loads(processed_path.read_text(encoding="utf-8"))
        return set(str(x) for x in (data.get("message_ids") or []))
    except Exception:
        return set()


def _save_processed_ids(processed_path: Path, ids: set[str]) -> None:
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    processed_path.write_text(
        json.dumps({"message_ids": list(ids)}, indent=2),
        encoding="utf-8",
    )


def _format_trade_ideas_for_discord(analyses: List[EmailAnalysis]) -> List[str]:
    """Format trade ideas into Discord messages (max ~1900 chars each)."""
    messages: List[str] = []
    for analysis in analyses:
        lines: List[str] = []
        lines.append(f"📬 *Citrini: {analysis.subject or '(no subject)'}*")
        lines.append(f"📅 {analysis.sent_at or 'unknown'} | Sentiment: {analysis.overall_sentiment or 'unclear'}")
        lines.append(f"_{analysis.summary or 'n/a'}_")
        lines.append("")

        if not analysis.trade_ideas:
            lines.append("No actionable trade ideas extracted.")
        else:
            lines.append("**Trade Ideas & Actions:**")
            for i, idea in enumerate(analysis.trade_ideas, 1):
                ticker = idea.ticker or "n/a"
                direction = idea.direction or "unknown"
                conf = idea.confidence
                thesis = (idea.thesis or "n/a")[:120]
                catalyst = (idea.catalyst or "n/a")[:80]
                lines.append(f"  {i}. **${ticker}** {direction} (conf: {conf:.2f})")
                lines.append(f"     Thesis: {thesis}")
                if catalyst != "n/a":
                    lines.append(f"     Catalyst: {catalyst}")
                lines.append("")

        text = "\n".join(lines).strip()
        text = text.replace("**", "*")
        if len(text) > 1900:
            text = text[:1897] + "..."
        messages.append(text)

    return messages


def run_citrini_email_check(
    *,
    client_secret_path: str = DEFAULT_CLIENT_SECRET,
    token_path: str = DEFAULT_TOKEN_PATH,
    processed_path: str = DEFAULT_PROCESSED_FILE,
    sender: str = DEFAULT_SENDER,
    limit: int = 10,
    unseen_only: bool = True,
    llm_provider: str = "anthropic",
    llm_api_key: Optional[str] = None,
    root_dir: Optional[Path] = None,
) -> Tuple[List[str], Optional[str]]:
    """
    Fetch new Citrini emails, run LLM, return Discord-formatted messages.

    Returns:
        (messages, error) - messages to send to Discord; error if something failed.
    """
    root = root_dir or Path.cwd()
    processed_file = root / processed_path
    client_secret = root / client_secret_path
    token_file = root / token_path

    logger.info("=== Citrini email check started ===")
    logger.info("sender=%s limit=%d unseen_only=%s llm_provider=%s", sender, limit, unseen_only, llm_provider)
    logger.info("processed_file=%s client_secret=%s", processed_file, client_secret)

    if not client_secret.exists():
        logger.error("Gmail client secret not found: %s", client_secret)
        return [], f"Gmail client secret not found: {client_secret}"

    try:
        logger.info("Building Gmail service...")
        service = build_gmail_service(
            client_secret_path=str(client_secret),
            token_path=str(token_file),
            scopes=GMAIL_READONLY_SCOPES,
        )
    except Exception as e:
        logger.exception("Gmail auth failed")
        return [], f"Gmail auth failed: {e}"

    logger.info("Fetching emails from Gmail...")
    emails = fetch_gmail_emails_from_sender(
        service=service,
        sender_email=sender,
        limit=limit,
        unseen_only=unseen_only,
        newer_than_days=14,
    )

    processed = _load_processed_ids(processed_file)
    logger.info("Loaded %d previously processed message ID(s)", len(processed))
    logger.info("Fetched %d total email(s) from Gmail", len(emails))
    for doc in emails:
        logger.debug("  Email: gmail_id=%s subject=%r", doc.gmail_id, (doc.subject or "")[:50])

    new_emails: List[EmailDocument] = []
    for doc in emails:
        mid = doc.gmail_id or doc.message_id or doc.uid
        if mid not in processed:
            new_emails.append(doc)

    logger.info("After filtering: %d new (unprocessed) email(s) to analyze", len(new_emails))
    if not new_emails:
        logger.info("No new emails to process. Exiting.")
        return [], None

    if llm_provider == "zai":
        api_key = llm_api_key or os.getenv("ZAI_API_KEY", "")
    else:
        api_key = llm_api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("API key required for %s", llm_provider)
        return [], f"{'ZAI' if llm_provider == 'zai' else 'ANTHROPIC'}_API_KEY required"

    try:
        logger.info("Running LLM analysis on %d email(s)...", len(new_emails))
        analyses = analyze_emails_with_llm(
            emails=new_emails,
            provider=llm_provider,
            api_key=api_key,
        )
    except Exception as e:
        logger.exception("LLM analysis failed")
        return [], f"LLM analysis failed: {e}"

    logger.info("LLM analysis complete: %d analysis(es)", len(analyses))
    for doc in new_emails:
        processed.add(doc.gmail_id or doc.message_id or doc.uid)
    _save_processed_ids(processed_file, processed)
    logger.info("Saved %d processed ID(s) to %s", len(processed), processed_file)

    messages = _format_trade_ideas_for_discord(analyses)
    logger.info("Formatted %d Discord message(s). === Citrini email check complete ===", len(messages))
    return messages, None
