"""
Email utilities (SMTP).

Designed for simple operational notifications like daily scanner reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from email.message import EmailMessage
import mimetypes
import os
from pathlib import Path
import smtplib
import ssl
from typing import Iterable, List, Optional, Sequence, Tuple


def _split_recipients(value: str) -> List[str]:
    # Supports comma/space-separated lists.
    parts = [p.strip() for p in value.replace(";", ",").replace(" ", ",").split(",")]
    return [p for p in parts if p]


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int = 587
    username: str = ""
    password: str = ""
    use_tls: bool = True


@dataclass(frozen=True)
class EmailConfig:
    sender: str
    recipients: List[str] = field(default_factory=list)
    subject_prefix: str = ""


def load_email_config_from_env(prefix: str = "") -> Tuple[Optional[SmtpConfig], Optional[EmailConfig]]:
    """
    Load email configuration from environment variables.

    Supported variables (optionally prefixed, e.g. "SAIYAN_"):
      - SMTP_HOST
      - SMTP_PORT
      - SMTP_USERNAME
      - SMTP_PASSWORD
      - SMTP_USE_TLS (true/false)
      - EMAIL_SENDER
      - EMAIL_RECIPIENTS (comma-separated)
      - EMAIL_SUBJECT_PREFIX
    """
    get = lambda k, default="": os.getenv(f"{prefix}{k}", default)

    host = get("SMTP_HOST", "").strip()
    if not host:
        return None, None

    port_raw = get("SMTP_PORT", "587").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 587

    use_tls_raw = get("SMTP_USE_TLS", "true").strip().lower()
    use_tls = use_tls_raw not in {"0", "false", "no", "off"}

    smtp = SmtpConfig(
        host=host,
        port=port,
        username=get("SMTP_USERNAME", "").strip(),
        password=get("SMTP_PASSWORD", ""),
        use_tls=use_tls,
    )

    sender = get("EMAIL_SENDER", "").strip() or smtp.username.strip()
    recipients = _split_recipients(get("EMAIL_RECIPIENTS", ""))
    subject_prefix = get("EMAIL_SUBJECT_PREFIX", "").strip()

    email = EmailConfig(
        sender=sender,
        recipients=recipients,
        subject_prefix=subject_prefix,
    )
    return smtp, email


def build_email_message(
    *,
    subject: str,
    body_text: str,
    sender: str,
    recipients: Sequence[str],
    attachments: Optional[Iterable[Path]] = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body_text)

    for path in attachments or []:
        p = Path(path)
        if not p.exists():
            continue

        ctype, encoding = mimetypes.guess_type(str(p))
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)

        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)

    return msg


def send_email_smtp(
    *,
    smtp: SmtpConfig,
    message: EmailMessage,
    timeout_seconds: int = 30,
) -> None:
    """
    Send an EmailMessage using SMTP.
    """
    if smtp.use_tls:
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp.host, smtp.port, timeout=timeout_seconds) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            if smtp.username:
                server.login(smtp.username, smtp.password)
            server.send_message(message)
    else:
        with smtplib.SMTP(smtp.host, smtp.port, timeout=timeout_seconds) as server:
            server.ehlo()
            if smtp.username:
                server.login(smtp.username, smtp.password)
            server.send_message(message)


def read_text_file_truncated(path: Path, max_bytes: int = 180_000) -> str:
    """
    Read a text file, truncating to max_bytes to keep emails reasonable.
    """
    data = Path(path).read_bytes()
    if len(data) <= max_bytes:
        return data.decode("utf-8", errors="replace")

    head = data[: max_bytes]
    text = head.decode("utf-8", errors="replace")
    text += "\n\n... (truncated) ..."
    return text

