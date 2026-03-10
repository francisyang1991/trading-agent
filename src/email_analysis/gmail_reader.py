"""
Gmail API OAuth reader for email ingestion.

First run triggers browser consent flow and saves token cache for reuse.
"""

from __future__ import annotations

import base64
import logging

logger = logging.getLogger(__name__)
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .email_trade_ideas import EmailDocument

GMAIL_READONLY_SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)


def _normalize_address(value: str) -> str:
    return parseaddr(value or "")[1].strip().lower()


def _extract_header(headers: Sequence[Dict[str, Any]], key: str) -> str:
    want = key.strip().lower()
    for row in headers:
        name = str(row.get("name") or "").strip().lower()
        if name == want:
            return str(row.get("value") or "").strip()
    return ""


def _decode_gmail_body_data(data: str) -> str:
    if not data:
        return ""
    # Gmail uses URL-safe base64, often without padding.
    pad = "=" * ((4 - len(data) % 4) % 4)
    raw = base64.urlsafe_b64decode((data + pad).encode("utf-8"))
    return raw.decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    import re
    from html import unescape

    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_body_from_payload(payload: Dict[str, Any]) -> str:
    if not payload:
        return ""

    mime = str(payload.get("mimeType") or "").lower()
    body = payload.get("body") or {}
    direct_data = str(body.get("data") or "")
    direct_text = _decode_gmail_body_data(direct_data).strip() if direct_data else ""

    if mime == "text/plain" and direct_text:
        return direct_text
    if mime == "text/html" and direct_text:
        return _html_to_text(direct_text)

    plain_parts: List[str] = []
    html_parts: List[str] = []
    for part in payload.get("parts") or []:
        child_text = _extract_body_from_payload(part)
        child_mime = str(part.get("mimeType") or "").lower()
        if not child_text:
            continue
        if child_mime == "text/plain":
            plain_parts.append(child_text)
        elif child_mime == "text/html":
            html_parts.append(child_text)
        else:
            # multipart/alternative children bubble up here; preserve best effort.
            plain_parts.append(child_text)

    if plain_parts:
        return "\n\n".join(x for x in plain_parts if x.strip()).strip()
    if html_parts:
        return "\n\n".join(_html_to_text(x) for x in html_parts if x.strip()).strip()
    return direct_text


def build_gmail_service(
    *,
    client_secret_path: str,
    token_path: str = "token.json",
    scopes: Sequence[str] = GMAIL_READONLY_SCOPES,
):
    """
    Build an authorized Gmail API service.

    On first run, launches browser OAuth consent flow and writes token.json.
    """
    try:
        from google.auth.transport.requests import Request  # type: ignore
        from google.oauth2.credentials import Credentials  # type: ignore
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Missing Gmail dependencies. Install: "
            "google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        ) from exc

    client_secret = Path(client_secret_path)
    if not client_secret.exists():
        raise FileNotFoundError(f"Gmail client secret file not found: {client_secret}")

    token_file = Path(token_path)
    creds = None
    if token_file.exists():
        logger.debug("Loading cached OAuth token from %s", token_file)
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired OAuth token")
            creds.refresh(Request())
        else:
            logger.info("Starting OAuth consent flow (browser will open)")
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), scopes)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")
        logger.debug("Saved OAuth token to %s", token_file)

    logger.info("Gmail API service built successfully")
    return build("gmail", "v1", credentials=creds)


def fetch_gmail_emails_from_sender(
    *,
    service,
    sender_email: str,
    limit: int = 20,
    newer_than_days: Optional[int] = None,
    unseen_only: bool = False,
) -> List[EmailDocument]:
    if limit <= 0:
        return []

    query_parts = [f"from:{sender_email}"]
    if unseen_only:
        query_parts.append("is:unread")
    if newer_than_days is not None:
        if newer_than_days < 0:
            raise ValueError("newer_than_days must be >= 0")
        # Gmail search supports newer_than:<N>d
        query_parts.append(f"newer_than:{newer_than_days}d")
    query = " ".join(query_parts).strip()

    logger.info(
        "Fetching Gmail messages: sender=%s limit=%d unseen_only=%s newer_than_days=%s",
        sender_email, limit, unseen_only, newer_than_days,
    )
    logger.debug("Gmail query: %s", query)

    listed = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=limit)
        .execute()
    )
    message_refs = listed.get("messages") or []
    logger.info("Gmail list returned %d message(s)", len(message_refs))
    docs: List[EmailDocument] = []

    for ref in message_refs:
        msg_id = str(ref.get("id") or "").strip()
        if not msg_id:
            continue
        raw_msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_id, format="full")
            .execute()
        )
        payload = raw_msg.get("payload") or {}
        headers = payload.get("headers") or []

        from_header = _extract_header(headers, "From")
        from_address = _normalize_address(from_header)
        if from_address != sender_email.strip().lower():
            # Defensive filter in case query matches alias/forward unexpectedly.
            continue

        subject = _extract_header(headers, "Subject")
        message_id = _extract_header(headers, "Message-ID") or msg_id
        internal_ms = raw_msg.get("internalDate")
        sent_at = None
        if internal_ms:
            try:
                sent_at = datetime.fromtimestamp(int(internal_ms) / 1000, tz=timezone.utc)
            except Exception:
                sent_at = None
        body_text = _extract_body_from_payload(payload)

        logger.debug(
            "Fetched email: gmail_id=%s subject=%r sent_at=%s body_len=%d",
            msg_id, subject[:60] + "..." if len(subject) > 60 else subject,
            sent_at, len(body_text),
        )
        docs.append(
            EmailDocument(
                uid=str(raw_msg.get("threadId") or msg_id),
                message_id=message_id,
                from_header=from_header,
                from_address=from_address,
                subject=subject,
                sent_at=sent_at,
                body_text=body_text,
                gmail_id=msg_id,
            )
        )

    # Gmail list returns newest first in practice; keep explicit sort safety.
    docs.sort(key=lambda x: x.sent_at or (datetime.now(timezone.utc) - timedelta(days=36500)), reverse=True)
    result = docs[:limit]
    logger.info("Returning %d email(s) from Gmail fetch", len(result))
    return result

