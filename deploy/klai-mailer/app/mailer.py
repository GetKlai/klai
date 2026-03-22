"""
Async SMTP email sender using aiosmtplib.

Supports both STARTTLS (port 587) and implicit TLS (port 465).
Fails loudly on SMTP errors so Zitadel can retry via HTTP 5xx.
"""

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import html as html_lib
import re

from email.utils import formataddr, formatdate, make_msgid

import aiosmtplib

from app.config import settings

logger = logging.getLogger(__name__)


def _html_to_text(html: str) -> str:
    """Strip HTML tags to produce a plain-text MIME fallback."""
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def send_email(to_address: str, subject: str, html_body: str) -> None:
    """
    Send a transactional email.

    Raises on failure — the caller (FastAPI endpoint) should let the
    exception propagate so FastAPI returns HTTP 500, which signals
    Zitadel to retry the notification.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.smtp_from_name, settings.smtp_from))
    msg["To"] = to_address
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=settings.smtp_from.split("@")[1])

    # Plain-text fallback (required for deliverability; prevents spam scoring)
    msg.attach(MIMEText(_html_to_text(html_body), "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    smtp_kwargs: dict = {
        "hostname": settings.smtp_host,
        "port": settings.smtp_port,
        "username": settings.smtp_username,
        "password": settings.smtp_password,
    }

    if settings.smtp_ssl:
        # Implicit TLS (port 465)
        smtp_kwargs["use_tls"] = True
    elif settings.smtp_tls:
        # STARTTLS (port 587)
        smtp_kwargs["start_tls"] = True

    logger.info("Sending email to=%s subject=%r", to_address, subject)
    await aiosmtplib.send(msg, **smtp_kwargs)
    logger.info("Email sent successfully to=%s", to_address)
