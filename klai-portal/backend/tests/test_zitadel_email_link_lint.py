"""SPEC-PORTAL-AUTH-EMAIL-LINKS-001 REQ-6 — AST-level lint.

Scans every Python file under ``klai-portal/backend/app/`` for ``await
<client>.post(<path>, ...)`` calls where ``<path>`` matches one of the
four Zitadel v2 email-link endpoints, and asserts the JSON body contains
``urlTemplate``. The lint is a regression net against silent drift —
Zitadel caches the previous url_template per user, so a single call
without urlTemplate silently restores the previous (possibly Zitadel-default)
URL in every subsequent mail.

This is implemented as a pytest test rather than an ast-grep YAML rule
because Python's stdlib ``ast`` module gives us exact control over the
"call without literal ``urlTemplate`` key" predicate that ast-grep's
relational matchers express awkwardly.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# Endpoints whose POST body MUST contain "urlTemplate". Sourced from
# zitadel/user/v2/{user,password,email}.proto where SendInviteCode,
# SendPasswordResetLink, and SendEmailVerificationCode all carry a
# ``url_template`` field.
_ENDPOINT_PATTERNS = (
    re.compile(r"/v2/users/[^/]+(?:/invite_code(?:/resend)?|/password_reset)"),
    re.compile(r"/v2/users/[^/]+/email/(?:_send_code|_resend)"),
)


def _extract_path_str(node: ast.expr) -> str | None:
    """Return the static path string of the first argument to a .post() call,
    or None if the path is built dynamically and we cannot statically inspect it.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        # f-string: concatenate the literal parts; placeholder parts are
        # represented as FormattedValue and replaced with a generic token so
        # the regex still recognises the endpoint shape.
        out: list[str] = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out.append(part.value)
            else:
                out.append("X")  # placeholder for any FormattedValue
        return "".join(out)
    return None


def _body_contains_url_template(call: ast.Call) -> bool:
    """Check if any kwarg/dict in the call's arguments contains the literal
    string ``urlTemplate`` (either as a dict key or anywhere in a string)."""
    # Cheapest check: ast.unparse the full call and grep for the literal.
    # urlTemplate is sufficiently rare in API payloads that string-search has
    # no false positives in this codebase.
    return "urlTemplate" in ast.unparse(call)


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """Return a list of (line, endpoint_path) offences in this file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []  # leave parser errors to ruff/pyright

    offences: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match <something>.post(<path>, ...)
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "post":
            continue
        if not node.args:
            continue
        url = _extract_path_str(node.args[0])
        if url is None:
            continue
        if not any(p.search(url) for p in _ENDPOINT_PATTERNS):
            continue
        if _body_contains_url_template(node):
            continue
        offences.append((node.lineno, url))
    return offences


def test_all_zitadel_mail_calls_pass_url_template():
    """REQ-6: every production-code call to a Zitadel email-link endpoint
    MUST include ``urlTemplate`` in the JSON body. Catches silent drift —
    Zitadel's per-user url_template cache means one bad call poisons the
    next mail too."""
    offences: list[str] = []
    for py_file in APP_DIR.rglob("*.py"):
        # Skip __pycache__ and other generated dirs.
        if "__pycache__" in py_file.parts:
            continue
        for lineno, url in _scan_file(py_file):
            rel = py_file.relative_to(APP_DIR.parent.parent.parent)
            offences.append(f"  {rel}:{lineno} — POST {url}")
    assert not offences, (
        "SPEC-PORTAL-AUTH-EMAIL-LINKS-001 REQ-6: Zitadel email-link API call "
        "without urlTemplate detected. The mail will point at Zitadel's hosted "
        "UI instead of my.getklai.com. Build the template via "
        "build_url_template(AuthLinkRoute.PASSWORD_SET) and include it in "
        "the JSON body.\n\nOffences:\n" + "\n".join(offences)
    )


# ---------------------------------------------------------------------------
# Self-tests for the scanner — guard against the lint silently becoming a no-op.
# ---------------------------------------------------------------------------


_BAD_FIXTURES = [
    'await client.post(f"/v2/users/{uid}/password_reset")',
    'await client.post(f"/v2/users/{uid}/password_reset", json={})',
    'await self._http.post(f"/v2/users/{user_id}/invite_code", json={"sendCode": {}})',
    'await c.post(f"/v2/users/{uid}/invite_code/resend", json={})',
    'await c.post(f"/v2/users/{uid}/email/_send_code", json={})',
]

_GOOD_FIXTURES = [
    (
        'await client.post(f"/v2/users/{uid}/password_reset", '
        'json={"sendLink": {"notificationType": "NOTIFICATION_TYPE_Email", "urlTemplate": tpl}})'
    ),
    (
        'await c.post(f"/v2/users/{uid}/invite_code", '
        'json={"sendCode": {"urlTemplate": tpl, "applicationName": "Klai"}})'
    ),
    ('await c.post(f"/v2/users/{uid}/email/_send_code", json={"sendCode": {"urlTemplate": tpl}})'),
    'await c.post(f"/api/admin/users", json={})',  # not a target endpoint
    'await c.post(f"/v2/users", json={})',  # AddHumanUser — not in scope
]


def _parse_first_call(src: str) -> ast.Call:
    """Wrap source in an async function so ``await`` parses, then pluck the Call."""
    wrapped = "async def _():\n    " + src
    tree = ast.parse(wrapped)
    fn = tree.body[0]
    assert isinstance(fn, ast.AsyncFunctionDef)
    expr = fn.body[0]
    assert isinstance(expr, ast.Expr)
    aw = expr.value
    assert isinstance(aw, ast.Await)
    call = aw.value
    assert isinstance(call, ast.Call)
    return call


@pytest.mark.parametrize("src", _BAD_FIXTURES)
def test_scanner_flags_bad_fixture(src: str):
    call = _parse_first_call(src)
    url = _extract_path_str(call.args[0])
    assert url is not None
    assert any(p.search(url) for p in _ENDPOINT_PATTERNS), f"endpoint regex missed: {url}"
    assert not _body_contains_url_template(call), f"urlTemplate should not be in: {src}"


@pytest.mark.parametrize("src", _GOOD_FIXTURES)
def test_scanner_passes_good_fixture(src: str):
    call = _parse_first_call(src)
    url = _extract_path_str(call.args[0])
    if url is None:
        return  # non-static URL is out of scope
    if not any(p.search(url) for p in _ENDPOINT_PATTERNS):
        return  # not a Zitadel email-link endpoint
    assert _body_contains_url_template(call), f"urlTemplate missing in good fixture: {src}"
