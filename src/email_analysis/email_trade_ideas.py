"""
Local email ingestion + LLM trade-idea extraction workflow.

This module is intentionally provider-light:
- IMAP (stdlib) for email retrieval
- Anthropic SDK (already in requirements) for analysis
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from email import header as email_header
from email import message_from_bytes
from email.message import Message
from email.policy import default as default_policy
from email.utils import parseaddr, parsedate_to_datetime
import imaplib
import json
import re
from html import unescape
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class ImapConfig:
    host: str
    username: str
    password: str
    port: int = 993
    use_ssl: bool = True
    folder: str = "INBOX"


@dataclass(frozen=True)
class EmailDocument:
    uid: str
    message_id: str
    from_header: str
    from_address: str
    subject: str
    sent_at: Optional[datetime]
    body_text: str
    gmail_id: Optional[str] = None  # Gmail API message ID for tracking processed


@dataclass(frozen=True)
class TradeIdea:
    ticker: str
    direction: str
    thesis: str
    catalyst: str
    risks: List[str] = field(default_factory=list)
    time_horizon: str = "unspecified"
    confidence: float = 0.0
    source_quote: str = ""


@dataclass(frozen=True)
class EmailAnalysis:
    uid: str
    message_id: str
    from_address: str
    subject: str
    sent_at: Optional[str]
    summary: str
    overall_sentiment: str
    trade_ideas: List[TradeIdea] = field(default_factory=list)
    raw_llm_output: str = ""


def _parse_bool(value: str, default: bool = True) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def load_imap_config_from_env(prefix: str = "") -> Optional[ImapConfig]:
    get = lambda key, default="": __import__("os").getenv(f"{prefix}{key}", default)

    host = get("IMAP_HOST", "").strip()
    username = get("IMAP_USERNAME", "").strip()
    password = get("IMAP_PASSWORD", "")
    if not host or not username or not password:
        return None

    port_raw = get("IMAP_PORT", "993").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 993

    use_ssl = _parse_bool(get("IMAP_USE_SSL", "true"), default=True)
    folder = get("IMAP_FOLDER", "INBOX").strip() or "INBOX"
    return ImapConfig(
        host=host,
        username=username,
        password=password,
        port=port,
        use_ssl=use_ssl,
        folder=folder,
    )


def _decode_mime_header(value: Optional[str]) -> str:
    if not value:
        return ""
    chunks: List[str] = []
    for part, encoding in email_header.decode_header(value):
        if isinstance(part, bytes):
            enc = encoding or "utf-8"
            try:
                chunks.append(part.decode(enc, errors="replace"))
            except LookupError:
                chunks.append(part.decode("utf-8", errors="replace"))
        else:
            chunks.append(part)
    return "".join(chunks).strip()


def _decode_part_payload(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return str(raw) if raw is not None else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    # Remove style/script blocks first to avoid noisy output.
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_message_body_text(msg: Message) -> str:
    if not msg.is_multipart():
        ctype = (msg.get_content_type() or "").lower()
        content = _decode_part_payload(msg)
        if ctype == "text/html":
            return _html_to_text(content)
        return content.strip()

    plain_parts: List[str] = []
    html_parts: List[str] = []
    for part in msg.walk():
        ctype = (part.get_content_type() or "").lower()
        disposition = (part.get_content_disposition() or "").lower()
        if disposition == "attachment":
            continue
        if ctype not in {"text/plain", "text/html"}:
            continue
        content = _decode_part_payload(part).strip()
        if not content:
            continue
        if ctype == "text/plain":
            plain_parts.append(content)
        else:
            html_parts.append(content)

    if plain_parts:
        return "\n\n".join(plain_parts).strip()
    if html_parts:
        html_text = "\n\n".join(_html_to_text(p) for p in html_parts if p.strip())
        return html_text.strip()
    return ""


def _normalize_email_address(value: str) -> str:
    return parseaddr(value or "")[1].strip().lower()


def _matches_sender(from_header: str, sender_email: str) -> bool:
    target = sender_email.strip().lower()
    if not target:
        return False
    address = _normalize_email_address(from_header)
    if address == target:
        return True
    return target in (from_header or "").lower()


def _to_imap_since_date(since_days: Optional[int]) -> Optional[str]:
    if since_days is None:
        return None
    if since_days < 0:
        raise ValueError("since_days must be >= 0")
    start = date.today() - timedelta(days=since_days)
    return start.strftime("%d-%b-%Y")


def fetch_emails_from_sender(
    *,
    config: ImapConfig,
    sender_email: str,
    limit: int = 20,
    unseen_only: bool = False,
    since_days: Optional[int] = None,
) -> List[EmailDocument]:
    if limit <= 0:
        return []

    search_tokens: List[str] = [f'FROM "{sender_email}"']
    if unseen_only:
        search_tokens.append("UNSEEN")
    since_token = _to_imap_since_date(since_days)
    if since_token:
        search_tokens.append(f"SINCE {since_token}")
    search_criteria = f"({' '.join(search_tokens)})"

    imap_client: Optional[imaplib.IMAP4] = None
    try:
        if config.use_ssl:
            imap_client = imaplib.IMAP4_SSL(config.host, config.port)
        else:
            imap_client = imaplib.IMAP4(config.host, config.port)

        imap_client.login(config.username, config.password)
        status, _ = imap_client.select(config.folder, readonly=True)
        if status != "OK":
            raise RuntimeError(f"Failed to open IMAP folder '{config.folder}'")

        status, data = imap_client.search(None, search_criteria)
        if status != "OK" or not data:
            return []

        all_uids = data[0].split()
        selected = list(reversed(all_uids[-limit:]))

        docs: List[EmailDocument] = []
        for uid in selected:
            uid_str = uid.decode("utf-8", errors="replace")
            status, msg_data = imap_client.fetch(uid, "(RFC822)")
            if status != "OK" or not msg_data:
                continue

            raw_blob = None
            for item in msg_data:
                if isinstance(item, tuple) and len(item) >= 2:
                    raw_blob = item[1]
                    break
            if raw_blob is None:
                continue

            msg = message_from_bytes(raw_blob, policy=default_policy)
            from_header = _decode_mime_header(msg.get("From"))
            if not _matches_sender(from_header, sender_email):
                continue

            subject = _decode_mime_header(msg.get("Subject"))
            body_text = _extract_message_body_text(msg)
            message_id = _decode_mime_header(msg.get("Message-ID"))
            sent_at = None
            date_raw = _decode_mime_header(msg.get("Date"))
            if date_raw:
                try:
                    sent_at = parsedate_to_datetime(date_raw)
                    if sent_at is not None and sent_at.tzinfo is None:
                        sent_at = sent_at.replace(tzinfo=timezone.utc)
                except Exception:
                    sent_at = None

            docs.append(
                EmailDocument(
                    uid=uid_str,
                    message_id=message_id or uid_str,
                    from_header=from_header,
                    from_address=_normalize_email_address(from_header),
                    subject=subject,
                    sent_at=sent_at,
                    body_text=body_text,
                )
            )
        return docs
    finally:
        if imap_client is not None:
            try:
                imap_client.logout()
            except Exception:
                pass


def format_email_for_llm(email_doc: EmailDocument) -> str:
    sent_at = email_doc.sent_at.isoformat() if email_doc.sent_at else "unknown"
    return (
        f"Message-ID: {email_doc.message_id}\n"
        f"From: {email_doc.from_header}\n"
        f"Subject: {email_doc.subject}\n"
        f"Date: {sent_at}\n\n"
        f"{email_doc.body_text}"
    )


def _extract_text_from_anthropic_response(response: Any) -> str:
    blocks = getattr(response, "content", None)
    if not blocks:
        return ""
    parts: List[str] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _extract_text_from_openai_compatible_response(response: Any) -> str:
    """Extract text from OpenAI-compatible chat completion (e.g. Z.AI)."""
    try:
        choices = getattr(response, "choices", None) or []
        if choices:
            msg = getattr(choices[0], "message", None)
            if msg:
                content = getattr(msg, "content", None)
                if content is not None:
                    return str(content).strip()
    except (IndexError, AttributeError, TypeError):
        pass
    return ""


def _extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        snippet = cleaned[start : end + 1]
        data = json.loads(snippet)
        if isinstance(data, dict):
            return data
    raise ValueError("LLM response does not contain a valid JSON object.")


def _sanitize_trade_idea(raw: Dict[str, Any]) -> TradeIdea:
    risks_raw = raw.get("risks") or []
    if isinstance(risks_raw, str):
        risks = [r.strip() for r in risks_raw.split(";") if r.strip()]
    elif isinstance(risks_raw, list):
        risks = [str(x).strip() for x in risks_raw if str(x).strip()]
    else:
        risks = []

    confidence_raw = raw.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return TradeIdea(
        ticker=str(raw.get("ticker") or "").upper().strip(),
        direction=str(raw.get("direction") or "unknown").strip().lower(),
        thesis=str(raw.get("thesis") or "").strip(),
        catalyst=str(raw.get("catalyst") or "").strip(),
        risks=risks,
        time_horizon=str(raw.get("time_horizon") or "unspecified").strip(),
        confidence=confidence,
        source_quote=str(raw.get("source_quote") or "").strip(),
    )


def analyze_emails_with_anthropic(
    *,
    emails: Iterable[EmailDocument],
    model: str = "claude-3-5-sonnet-latest",
    max_tokens: int = 1400,
    temperature: float = 0.0,
    api_key: Optional[str] = None,
) -> List[EmailAnalysis]:
    try:
        from anthropic import Anthropic  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "anthropic SDK is required for LLM analysis. Install dependencies from requirements.txt."
        ) from exc

    if not api_key:
        api_key = __import__("os").getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for email analysis.")

    client = Anthropic(api_key=api_key)
    analyses: List[EmailAnalysis] = []

    system_prompt = (
        "You are an equity research analyst. Read one newsletter email and extract concrete trade ideas. "
        "Do not fabricate symbols or facts. If no trade ideas are present, return an empty list."
    )

    for email_doc in emails:
        email_payload = format_email_for_llm(email_doc)
        user_prompt = (
            "Extract any trade idea(s) from this email.\n\n"
            "Return JSON only, exactly in this schema:\n"
            "{\n"
            '  "summary": "short summary of the email",\n'
            '  "overall_sentiment": "bullish|bearish|mixed|unclear",\n'
            '  "trade_ideas": [\n'
            "    {\n"
            '      "ticker": "uppercase ticker or empty",\n'
            '      "direction": "long|short|watchlist|unknown",\n'
            '      "thesis": "core thesis",\n'
            '      "catalyst": "near-term catalyst if any",\n'
            '      "risks": ["risk1", "risk2"],\n'
            '      "time_horizon": "short-term|swing|long-term|unspecified",\n'
            '      "confidence": 0.0,\n'
            '      "source_quote": "verbatim supporting quote from email"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Email content:\n"
            f"{email_payload}"
        )

        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_output = _extract_text_from_anthropic_response(response)
        parsed = _extract_json_object(raw_output)

        raw_ideas = parsed.get("trade_ideas") or []
        trade_ideas: List[TradeIdea] = []
        if isinstance(raw_ideas, list):
            for item in raw_ideas:
                if isinstance(item, dict):
                    trade_ideas.append(_sanitize_trade_idea(item))

        analyses.append(
            EmailAnalysis(
                uid=email_doc.uid,
                message_id=email_doc.message_id,
                from_address=email_doc.from_address,
                subject=email_doc.subject,
                sent_at=email_doc.sent_at.isoformat() if email_doc.sent_at else None,
                summary=str(parsed.get("summary") or "").strip(),
                overall_sentiment=str(parsed.get("overall_sentiment") or "unclear").strip().lower(),
                trade_ideas=trade_ideas,
                raw_llm_output=raw_output,
            )
        )

    return analyses


def analyze_emails_with_zai(
    *,
    emails: Iterable[EmailDocument],
    model: str = "glm-5",
    max_tokens: int = 1400,
    temperature: float = 0.0,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> List[EmailAnalysis]:
    """Analyze emails using Z.AI (GLM) chat completion API."""
    try:
        from zai import ZaiClient  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "zai-sdk is required for Z.AI analysis. Install: pip install zai-sdk"
        ) from exc

    if not api_key:
        api_key = __import__("os").getenv("ZAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("ZAI_API_KEY is required for Z.AI email analysis.")

    kwargs: Dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = ZaiClient(**kwargs)
    analyses: List[EmailAnalysis] = []

    system_prompt = (
        "You are an equity research analyst. Read one newsletter email and extract concrete trade ideas. "
        "Do not fabricate symbols or facts. If no trade ideas are present, return an empty list."
    )

    for email_doc in emails:
        email_payload = format_email_for_llm(email_doc)
        user_prompt = (
            "Extract any trade idea(s) from this email.\n\n"
            "Return JSON only, exactly in this schema:\n"
            "{\n"
            '  "summary": "short summary of the email",\n'
            '  "overall_sentiment": "bullish|bearish|mixed|unclear",\n'
            '  "trade_ideas": [\n'
            "    {\n"
            '      "ticker": "uppercase ticker or empty",\n'
            '      "direction": "long|short|watchlist|unknown",\n'
            '      "thesis": "core thesis",\n'
            '      "catalyst": "near-term catalyst if any",\n'
            '      "risks": ["risk1", "risk2"],\n'
            '      "time_horizon": "short-term|swing|long-term|unspecified",\n'
            '      "confidence": 0.0,\n'
            '      "source_quote": "verbatim supporting quote from email"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Email content:\n"
            f"{email_payload}"
        )

        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw_output = _extract_text_from_openai_compatible_response(response)
        parsed = _extract_json_object(raw_output)

        raw_ideas = parsed.get("trade_ideas") or []
        trade_ideas: List[TradeIdea] = []
        if isinstance(raw_ideas, list):
            for item in raw_ideas:
                if isinstance(item, dict):
                    trade_ideas.append(_sanitize_trade_idea(item))

        analyses.append(
            EmailAnalysis(
                uid=email_doc.uid,
                message_id=email_doc.message_id,
                from_address=email_doc.from_address,
                subject=email_doc.subject,
                sent_at=email_doc.sent_at.isoformat() if email_doc.sent_at else None,
                summary=str(parsed.get("summary") or "").strip(),
                overall_sentiment=str(parsed.get("overall_sentiment") or "unclear").strip().lower(),
                trade_ideas=trade_ideas,
                raw_llm_output=raw_output,
            )
        )

    return analyses


def analyze_emails_with_llm(
    *,
    emails: Iterable[EmailDocument],
    provider: str = "anthropic",
    model: Optional[str] = None,
    max_tokens: int = 1400,
    temperature: float = 0.0,
    api_key: Optional[str] = None,
    zai_base_url: Optional[str] = None,
) -> List[EmailAnalysis]:
    """Unified entry point: dispatch to Anthropic or Z.AI based on provider."""
    provider_lower = provider.strip().lower()
    if provider_lower == "zai" or provider_lower == "glm":
        model = model or "glm-5"
        return analyze_emails_with_zai(
            emails=emails,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            api_key=api_key,
            base_url=zai_base_url,
        )
    if provider_lower == "anthropic":
        model = model or "claude-3-5-sonnet-latest"
        return analyze_emails_with_anthropic(
            emails=emails,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            api_key=api_key,
        )
    raise ValueError(f"Unknown LLM provider: {provider}. Use 'anthropic' or 'zai'.")


def analyses_to_dict(analyses: Iterable[EmailAnalysis]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for analysis in analyses:
        record = asdict(analysis)
        record["trade_ideas"] = [asdict(x) for x in analysis.trade_ideas]
        rows.append(record)
    return rows


def emails_to_dict(emails: Iterable[EmailDocument]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for doc in emails:
        rows.append(
            {
                "uid": doc.uid,
                "message_id": doc.message_id,
                "from_header": doc.from_header,
                "from_address": doc.from_address,
                "subject": doc.subject,
                "sent_at": doc.sent_at.isoformat() if doc.sent_at else None,
                "body_text": doc.body_text,
            }
        )
    return rows


def build_ticker_summary(analyses: Iterable[EmailAnalysis]) -> List[Dict[str, Any]]:
    bucket: Dict[str, Dict[str, Any]] = {}
    for analysis in analyses:
        for idea in analysis.trade_ideas:
            ticker = idea.ticker.strip().upper()
            if not ticker:
                continue
            row = bucket.setdefault(
                ticker,
                {
                    "ticker": ticker,
                    "mentions": 0,
                    "directions": {},
                    "avg_confidence": 0.0,
                    "theses": [],
                },
            )
            row["mentions"] += 1
            directions = row["directions"]
            directions[idea.direction] = directions.get(idea.direction, 0) + 1
            row["avg_confidence"] += idea.confidence
            thesis = idea.thesis.strip()
            if thesis:
                row["theses"].append(thesis)

    out: List[Dict[str, Any]] = []
    for ticker, row in bucket.items():
        mentions = max(1, int(row["mentions"]))
        out.append(
            {
                "ticker": ticker,
                "mentions": row["mentions"],
                "directions": row["directions"],
                "avg_confidence": round(float(row["avg_confidence"]) / mentions, 3),
                "theses": row["theses"][:5],
            }
        )

    out.sort(key=lambda x: (x["mentions"], x["avg_confidence"]), reverse=True)
    return out
