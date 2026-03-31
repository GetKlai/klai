"""
Email renderer: Zitadel templateData → styled HTML email.

Zitadel pre-renders the message texts and sends them in templateData.
This module converts that plain text to HTML and injects it into the
Klai-branded email wrapper template.

Flow:
  1. Build body HTML from templateData.greeting + templateData.text
  2. Inject inline styles for email client compatibility
  3. Render the Klai HTML wrapper with all fields
"""

import html as html_lib
import logging
import re
from pathlib import Path
from typing import Any

from jinja2 import FileSystemLoader, Environment, select_autoescape

from app.models import ZitadelPayload

logger = logging.getLogger(__name__)


def _append_lang_to_url(url: str, lang: str | None) -> str:
    """
    Append ?lang=<lang> (or &lang=<lang>) to a URL.

    If lang is None, the URL is returned unchanged and a warning is logged.
    No silent fallback: a missing language is visible in logs.
    """
    if not url:
        return url
    if lang is None:
        logger.warning(
            "preferred_language unknown for email link — lang param NOT appended to URL. "
            "The user's browser locale or existing localStorage value will be used instead."
        )
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}lang={lang}"

# Inline styles injected on plain-text-to-HTML converted elements.
# Applied via regex so they survive Outlook's CSS stripping.
_ELEMENT_STYLES: list[tuple[str, str]] = [
    (r"<p>", '<p style="margin:0 0 1em 0;font-size:16px;line-height:1.625;color:#1a0f40;">'),
    (r"<a ", '<a style="color:#7c6aff;text-decoration:underline;" '),
    (r"<strong>", '<strong style="font-weight:600;">'),
]


def _text_to_html(text: str) -> str:
    """
    Convert Zitadel's plain text (templateData.text / .greeting) to HTML.

    Double newlines → paragraph breaks.
    Single newlines → <br> within a paragraph.
    """
    if not text:
        return ""
    paragraphs = re.split(r"\n\n+", text.strip())
    parts = []
    for para in paragraphs:
        para = para.strip()
        if para:
            # Escape HTML entities first, then replace newlines with <br> tags
            inner = html_lib.escape(para).replace("\n", "<br>")
            parts.append(f"<p>{inner}</p>")
    html = "\n".join(parts)
    for pattern, replacement in _ELEMENT_STYLES:
        html = re.sub(pattern, replacement, html)
    return html


class Renderer:
    def __init__(self, theme_dir: Path):
        self._theme_env = Environment(  # nosemgrep: direct-use-of-jinja2
            loader=FileSystemLoader(str(theme_dir)),
            autoescape=select_autoescape(["html", "j2"]),
        )

    def render(self, payload: ZitadelPayload, lang: str | None = None) -> dict[str, Any]:
        """
        Build the render context from Zitadel's pre-rendered templateData.

        Pass ``lang`` (e.g. "nl" or "en") to append ?lang=<lang> to the button URL
        so that verify/password-reset links open the portal in the user's language.
        When lang is None, the URL is left unchanged and a warning is logged.

        Always returns a dict — every notification type gets the same treatment.
        Raises on unexpected errors (FastAPI returns 500, Zitadel retries).
        """
        td = payload.templateData

        # Greeting and body are separate fields in Zitadel; combine them.
        parts = []
        if td and td.greeting:
            parts.append(_text_to_html(td.greeting))
        if td and td.text:
            parts.append(_text_to_html(td.text))
        body_html = "\n".join(parts)

        return {
            "subject": payload.subject(),
            "preheader": payload.pre_header(),
            "body_html": body_html,
            "has_button": payload.has_button(),
            "button_text": payload.button_text(),
            "button_url": _append_lang_to_url(payload.button_url(), lang),
            "footer_note": payload.footer_note(),
        }

    def wrap(self, render_result: dict[str, Any], branding: dict[str, Any]) -> str:
        """Inject rendered content into the Klai HTML email wrapper."""
        template = self._theme_env.get_template("email.html.j2")
        return template.render(**render_result, **branding)
