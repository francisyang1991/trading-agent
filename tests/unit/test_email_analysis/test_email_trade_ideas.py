from email.message import EmailMessage

from src.email_analysis.email_trade_ideas import _extract_message_body_text, _matches_sender


def test_matches_sender_exact_and_display_name():
    assert _matches_sender("citrini@substack.com", "citrini@substack.com")
    assert _matches_sender("Citrini Research <citrini@substack.com>", "citrini@substack.com")
    assert not _matches_sender("other@substack.com", "citrini@substack.com")


def test_extract_message_body_text_prefers_plain_over_html():
    msg = EmailMessage()
    msg["From"] = "Citrini <citrini@substack.com>"
    msg["Subject"] = "Test"
    msg.set_content("Plain body with TSLA long thesis.")
    msg.add_alternative("<html><body><p>HTML body</p></body></html>", subtype="html")

    text = _extract_message_body_text(msg)
    assert "Plain body with TSLA long thesis." in text
    assert "HTML body" not in text


def test_extract_message_body_text_uses_html_when_plain_missing():
    msg = EmailMessage()
    msg["From"] = "Citrini <citrini@substack.com>"
    msg["Subject"] = "HTML only"
    msg.add_alternative("<html><body><p>Buy NVDA on weakness.</p></body></html>", subtype="html")

    text = _extract_message_body_text(msg)
    assert "Buy NVDA on weakness." in text
