"""AC-9: Golden-output regression for legitimate internal emails.

Covers REQ-1.5 (template files), REQ-2.1 (schema validation passes),
REQ-2.4 (branding from settings), REQ-3.1/3.2 (recipient binding).

Legitimate admin / approved flows produce emails that match the committed
golden fixtures semantically. Whitespace-only differences inside HTML tags
are normalised via lxml before comparison.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"

CANONICAL_INPUTS = {
    ("join_request_admin", "nl"): {
        "name": "Alice Example",
        "email": "alice@example.com",
        "org_id": 42,
    },
    ("join_request_admin", "en"): {
        "name": "Alice Example",
        "email": "alice@example.com",
        "org_id": 42,
    },
    ("join_request_approved", "nl"): {
        "name": "Bob Requester",
        "email": "bob@example.com",
        "workspace_url": "https://app.klai.example",
    },
    ("join_request_approved", "en"): {
        "name": "Bob Requester",
        "email": "bob@example.com",
        "workspace_url": "https://app.klai.example",
    },
}


def _normalise(html: str) -> str:
    """Collapse whitespace between / inside tags for a forgiving byte compare."""
    html = re.sub(r"\s+", " ", html)
    html = re.sub(r">\s+<", "><", html)
    return html.strip()


def _load_main(fake_redis):
    for mod in ("app.main", "app.config", "app.nonce", "app.rate_limit", "app.signature"):
        sys.modules.pop(mod, None)
    import app.nonce as nonce_mod
    import app.rate_limit as rl_mod
    nonce_mod.set_redis_client(fake_redis)
    rl_mod.set_redis_client(fake_redis)
    main = importlib.import_module("app.main")
    import app.nonce as n
    import app.rate_limit as r
    n.set_redis_client(fake_redis)
    r.set_redis_client(fake_redis)
    return main


@pytest.fixture
def client(settings_env, fake_redis, stub_smtp):
    main = _load_main(fake_redis)
    return TestClient(main.app)


@pytest.mark.parametrize(
    "template,locale",
    [
        ("join_request_admin", "nl"),
        ("join_request_admin", "en"),
        ("join_request_approved", "nl"),
        ("join_request_approved", "en"),
    ],
)
def test_golden_output(client, stub_smtp, template, locale, settings_env):
    """Rendered (template, locale) matches the committed golden fixture."""
    inputs = CANONICAL_INPUTS[(template, locale)]

    if template == "join_request_admin":
        expected_to = "admin@example.com"
        with respx.mock(assert_all_called=False) as mock:
            mock.get(url__regex=rf"^.+/internal/org/{inputs['org_id']}/admin-email$").mock(
                return_value=httpx.Response(200, json={"admin_email": expected_to})
            )
            resp = client.post(
                "/internal/send",
                headers={"X-Internal-Secret": "internal-test-secret"},
                json={
                    "template": template,
                    "to": expected_to,
                    "locale": locale,
                    "variables": inputs,
                },
            )
    else:
        expected_to = inputs["email"]
        resp = client.post(
            "/internal/send",
            headers={"X-Internal-Secret": "internal-test-secret"},
            json={
                "template": template,
                "to": expected_to,
                "locale": locale,
                "variables": inputs,
            },
        )

    assert resp.status_code == 200, resp.text

    fixture_path = GOLDEN_DIR / f"{template}.{locale}.html"
    rendered_html = stub_smtp.sent[0]["html_body"]
    assert stub_smtp.sent[0]["to_address"] == expected_to

    if not fixture_path.exists():
        # First-run capture: persist the current output so future changes
        # are regressions. This keeps the fixture honest to whatever the
        # Jinja2 sandbox produces from the committed templates + wrapper.
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
        fixture_path.write_text(rendered_html, encoding="utf-8")
        pytest.skip(
            f"golden fixture {fixture_path.name} captured on first run — "
            "commit it and re-run the suite"
        )

    expected = fixture_path.read_text(encoding="utf-8")
    assert _normalise(rendered_html) == _normalise(expected), (
        f"golden mismatch for {template}.{locale}"
    )
