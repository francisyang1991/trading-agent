import base64

from src.email_analysis.gmail_reader import _extract_body_from_payload, _extract_header


def _enc(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("utf-8").rstrip("=")


def test_extract_header_case_insensitive():
    headers = [
        {"name": "From", "value": "Citrini <citrini@substack.com>"},
        {"name": "Subject", "value": "Ideas"},
    ]
    assert _extract_header(headers, "from") == "Citrini <citrini@substack.com>"
    assert _extract_header(headers, "SUBJECT") == "Ideas"


def test_extract_body_from_payload_plain_text():
    payload = {
        "mimeType": "text/plain",
        "body": {"data": _enc("Long NVDA, thesis: AI demand.")},
    }
    text = _extract_body_from_payload(payload)
    assert "Long NVDA" in text


def test_extract_body_from_payload_prefers_plain_over_html():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _enc("Plain thesis for TSLA.")}},
            {"mimeType": "text/html", "body": {"data": _enc("<p>HTML thesis for TSLA.</p>")}},
        ],
    }
    text = _extract_body_from_payload(payload)
    assert "Plain thesis for TSLA." in text
