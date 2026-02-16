"""Email analysis package."""

from .email_trade_ideas import (
    EmailAnalysis,
    EmailDocument,
    ImapConfig,
    TradeIdea,
    analyses_to_dict,
    analyze_emails_with_anthropic,
    analyze_emails_with_llm,
    analyze_emails_with_zai,
    build_ticker_summary,
    emails_to_dict,
    fetch_emails_from_sender,
    format_email_for_llm,
    load_imap_config_from_env,
)
from .citrini_discord import run_citrini_email_check
from .gmail_reader import (
    GMAIL_READONLY_SCOPES,
    build_gmail_service,
    fetch_gmail_emails_from_sender,
)

__all__ = [
    "EmailAnalysis",
    "EmailDocument",
    "ImapConfig",
    "TradeIdea",
    "analyses_to_dict",
    "analyze_emails_with_anthropic",
    "analyze_emails_with_llm",
    "analyze_emails_with_zai",
    "build_ticker_summary",
    "emails_to_dict",
    "fetch_emails_from_sender",
    "format_email_for_llm",
    "load_imap_config_from_env",
    "run_citrini_email_check",
    "GMAIL_READONLY_SCOPES",
    "build_gmail_service",
    "fetch_gmail_emails_from_sender",
]
