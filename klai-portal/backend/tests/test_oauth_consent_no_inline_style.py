"""Verify that the rendered OAuth consent page uses an external stylesheet.

Calls _render_consent_page() with fake data and checks:
- No inline <style> block in the output.
- A <link rel="stylesheet" href="/static/oauth/consent.css"> is present.
"""

from __future__ import annotations

from app.api.mcp_oauth import _render_consent_page


def _render() -> str:
    response = _render_consent_page(
        request_id="req-123",
        csrf_token="csrf-abc",
        client_name="Test App",
        redirect_uri="https://example.com/callback",
        application_type="web",
        scopes=["mcp:knowledge"],
        user_email="mark@example.com",
        user_org_name="Acme Inc.",
        is_newly_registered=False,
    )
    return response.body.decode("utf-8")


def test_no_inline_style_block() -> None:
    """The rendered page must not contain an inline <style> block."""
    html = _render()
    assert "<style>" not in html, (
        "Rendered consent page still contains an inline <style> block. "
        "The inline CSS must be removed from the template."
    )


def test_external_stylesheet_linked() -> None:
    """The rendered page must link to /static/oauth/consent.css."""
    html = _render()
    assert 'href="/static/oauth/consent.css"' in html, (
        'Expected <link rel="stylesheet" href="/static/oauth/consent.css"> in the rendered consent page.'
    )


def test_rendered_page_contains_form() -> None:
    """Sanity check: the rendered page still has the consent form."""
    html = _render()
    assert 'action="/oauth/authorize"' in html
    assert 'name="decision" value="approve"' in html
    assert 'name="decision" value="deny"' in html


def test_rendered_page_escapes_client_name() -> None:
    """client_name with HTML special chars must be escaped."""
    response = _render_consent_page(
        request_id="req-x",
        csrf_token="csrf-x",
        client_name='<script>alert("xss")</script>',
        redirect_uri="https://example.com/callback",
        application_type="web",
        scopes=["mcp:knowledge"],
        user_email="mark@example.com",
        user_org_name="",
        is_newly_registered=False,
    )
    html = response.body.decode("utf-8")
    assert "<script>" not in html, "client_name was not HTML-escaped"
    assert "&lt;script&gt;" in html


def test_newly_registered_badge_rendered() -> None:
    """When is_newly_registered=True the badge and callout must appear."""
    response = _render_consent_page(
        request_id="req-y",
        csrf_token="csrf-y",
        client_name="New App",
        redirect_uri="https://example.com/cb",
        application_type="native",
        scopes=["mcp:knowledge"],
        user_email="user@example.com",
        user_org_name="",
        is_newly_registered=True,
    )
    html = response.body.decode("utf-8")
    assert 'class="badge-new"' in html
    assert 'class="warn-callout"' in html
