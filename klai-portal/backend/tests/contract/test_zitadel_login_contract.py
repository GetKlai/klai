"""The portal's login and second-factor enrolment against a real Zitadel.

Every other auth test mocks Zitadel with respx, so it proves the shape of what
the portal sends and nothing about whether the pinned Zitadel accepts it. This
module runs the portal's own route handlers (``login``, ``totp_login`` and the
TOTP, passkey and e-mail code enrolment endpoints) with the real
``ZitadelClient`` against the image pinned in ``deploy/docker-compose.yml``,
and asserts on Zitadel's real answers and on the codes it really mails.

Two facts about Zitadel shape what is asserted, both observed on v4.20.1:

- The portal finalises only after every registered factor is checked, and
  Zitadel does not repeat that check, so the tests assert that the login stops
  before finalising and that the provider takes the session once the check
  passed.
- Registering the e-mail code factor (``POST /v2/users/{id}/otp_email``) activates
  it at once and mails nothing; there is no verify route. A code is mailed only
  by a session challenge, which is how both the login and the enrolment get one,
  and Zitadel refuses that challenge for a user without the factor. So the
  enrolment registers first and keeps the factor from counting until the mailed
  code is confirmed, and a login removes a factor whose enrolment was never
  confirmed.

The database and Redis stay test doubles (``_make_db_mock``, ``fake_redis``);
only Zitadel and the mail path are real. Zitadel posts its notifications to
klai-mailer as its HTTP notification provider, as in production, and
klai-mailer sends over SMTP to Mailpit, where the codes are read.

Run by ``deploy/tests/zitadel-login-contract.sh``, which starts Zitadel, its
database, klai-mailer and Mailpit, wires the provider and sets the variables
below. Excluded from the default
run by the ``zitadel_live`` marker.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import struct
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import urlparse

import httpx
import pytest
import pytest_asyncio
from auth_test_helpers import _audit_emit_patches, _make_db_mock
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from helpers import make_request

from app.api import auth as auth_api
from app.api.auth import (
    EmailOTPConfirmRequest,
    LoginRequest,
    PasskeyConfirmRequest,
    TOTPConfirmRequest,
    TOTPLoginRequest,
)
from app.core.config import settings
from app.services import email_otp_enrolment
from app.services.email_otp_enrolment import counted_second_factors
from app.services.zitadel import ZitadelClient

pytestmark = pytest.mark.zitadel_live

BASE_URL = os.getenv("ZITADEL_CONTRACT_BASE_URL", "")
PAT = os.getenv("ZITADEL_CONTRACT_PAT", "")
MAIL_URL = os.getenv("ZITADEL_CONTRACT_MAIL_URL", "")
REDIRECT_URI = "http://localhost/api/auth/oidc/callback"
PASSWORD = "Contract-Passw0rd!"
# Projections behind the user and authentication-method queries trail the
# command that wrote them by up to about a second on v4.20.1.
_EVENTUAL_TIMEOUT_S = 15.0


# ---------------------------------------------------------------------------
# Instance setup: a login-client role, an OIDC app, an org
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def instance() -> Iterator[dict[str, str]]:
    if not (BASE_URL and PAT and MAIL_URL):
        pytest.skip("ZITADEL_CONTRACT_* not set; run deploy/tests/zitadel-login-contract.sh")
    with httpx.Client(base_url=BASE_URL, headers={"Authorization": f"Bearer {PAT}"}, timeout=20) as admin:

        def ok(resp: httpx.Response) -> dict[str, Any]:
            assert resp.is_success, f"{resp.request.method} {resp.request.url.path} -> {resp.status_code} {resp.text}"
            return resp.json()

        # finalize_auth_request needs IAM_LOGIN_CLIENT, which the production
        # service account carries next to IAM_OWNER.
        me = ok(admin.get("/auth/v1/users/me"))["user"]["id"]
        ok(admin.put(f"/admin/v1/members/{me}", json={"roles": ["IAM_OWNER", "IAM_LOGIN_CLIENT"]}))
        project = ok(admin.post("/management/v1/projects", json={"name": f"contract-{uuid.uuid4().hex[:8]}"}))["id"]
        app = ok(
            admin.post(
                f"/management/v1/projects/{project}/apps/oidc",
                json={
                    "name": "portal",
                    "redirectUris": [REDIRECT_URI],
                    "responseTypes": ["OIDC_RESPONSE_TYPE_CODE"],
                    "grantTypes": ["OIDC_GRANT_TYPE_AUTHORIZATION_CODE"],
                    "appType": "OIDC_APP_TYPE_WEB",
                    "authMethodType": "OIDC_AUTH_METHOD_TYPE_POST",
                    "devMode": True,
                },
            )
        )
        org = ok(admin.post("/management/v1/orgs", json={"name": f"Contract {uuid.uuid4().hex[:8]}"}))["id"]
    yield {"login_client": me, "client_id": app["clientId"], "org_id": org}


@pytest_asyncio.fixture
async def client(instance: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[ZitadelClient]:
    """The portal's ``ZitadelClient`` aimed at the contract instance, swapped into the route module.

    Every Zitadel response is printed as one ``method path -> status`` line,
    which is the call-by-call record of what the pinned image accepted.
    """
    monkeypatch.setattr(settings, "zitadel_base_url", BASE_URL)
    monkeypatch.setattr(settings, "zitadel_pat", PAT)
    real = ZitadelClient()

    async def record(response: httpx.Response) -> None:
        path = re.sub(r"/\d{6,}|/V2_\d+", "/{id}", response.url.path)
        print(f"  zitadel {response.request.method} {path} -> {response.status_code}")

    real._http.event_hooks["response"].append(record)
    monkeypatch.setattr(auth_api, "zitadel", real)
    monkeypatch.setattr(email_otp_enrolment, "zitadel", real)
    yield real
    await real.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _eventually(probe: Any, what: str) -> Any:
    deadline = time.monotonic() + _EVENTUAL_TIMEOUT_S
    while True:
        result = await probe()
        if result:
            return result
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.3)


async def _new_user(client: ZitadelClient, instance: dict[str, str]) -> tuple[str, str]:
    email = f"contract-{uuid.uuid4().hex[:10]}@example.com"
    created = await client.create_human_user(
        instance["org_id"], email, "Contract", "User", PASSWORD, send_codes=False, is_email_verified=True
    )
    user_id = created["userId"]

    async def visible() -> bool:
        return await client.find_user_by_email(email) is not None

    await _eventually(visible, "the new user to be searchable")
    return user_id, email


async def _methods_include(client: ZitadelClient, user_id: str, method: str) -> None:
    async def listed() -> bool:
        resp = await client._http.get(f"/v2/users/{user_id}/authentication_methods")
        return method in resp.json().get("authMethodTypes", [])

    await _eventually(listed, f"{method} in the user's authentication methods")


async def _mails_to(email: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(base_url=MAIL_URL, timeout=10) as mail:
        found = (await mail.get("/api/v1/search", params={"query": f"to:{email}"})).json()["messages"]
        return [(await mail.get(f"/api/v1/message/{m['ID']}")).json() for m in found]


async def _next_mailed_code(email: str, already: int) -> str:
    """Wait for mail number ``already + 1`` to ``email`` and return the 8-digit code in it."""

    async def arrived() -> list[dict[str, Any]]:
        mails = await _mails_to(email)
        return mails if len(mails) > already else []

    mails = await _eventually(arrived, f"mail {already + 1} to {email}")
    # Mailpit lists the newest message first.
    newest = mails[0]
    # klai-mailer leaves out the button Zitadel offers, which points at a
    # hosted login page the deployed image does not serve.
    assert "/ui/v2/login/" not in newest["HTML"]
    match = re.search(r"\b(\d{8})\b", newest["HTML"])
    assert match, f"no 8-digit code in the mail: {newest['Text'][:200]!r}"
    return match.group(1)


def _totp(secret: str, at: float) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", int(at // 30)), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return f"{(struct.unpack('>I', digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def _wrong(valid: set[str], length: int) -> str:
    return next(c * length for c in "0123456789" if c * length not in valid)


async def _auth_request(instance: dict[str, str]) -> str:
    """Start an OIDC authorization the way a relying party does and return its auth request id."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=20) as browser:
        resp = await browser.get(
            "/oauth/v2/authorize",
            params={
                "client_id": instance["client_id"],
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": "openid",
            },
            headers={"x-zitadel-login-client": instance["login_client"]},
        )
    assert resp.status_code == 302, resp.text
    return re.search(r"authRequest=([^&]+)", resp.headers["location"]).group(1)  # type: ignore[union-attr]


async def _login(instance: dict[str, str], email: str, policy: str = "required") -> tuple[Any, str, MagicMock]:
    auth_request_id = await _auth_request(instance)
    response = MagicMock()
    audit_patch, emit_patch = _audit_emit_patches()
    with audit_patch, emit_patch:
        result = await auth_api.login(
            body=LoginRequest(email=email, password=PASSWORD, auth_request_id=auth_request_id, accepts_email_code=True),
            response=response,
            request=make_request(),
            db=_make_db_mock(org_mfa_policy=policy),
        )
    return result, auth_request_id, response


async def _second_step(temp_token: str, code: str, auth_request_id: str) -> tuple[Any, MagicMock]:
    response = MagicMock()
    audit_patch, emit_patch = _audit_emit_patches()
    with audit_patch, emit_patch:
        result = await auth_api.totp_login(
            body=TOTPLoginRequest(temp_token=temp_token, code=code, auth_request_id=auth_request_id),
            response=response,
            db=_make_db_mock(),
        )
    return result, response


async def _counted(client: ZitadelClient, user_id: str) -> list[str]:
    """The factors ``/api/me`` counts as enrolled."""
    return await counted_second_factors(user_id, await client.login_second_factors(user_id), remove_unconfirmed=False)


def _assert_finalised(result: Any, response: MagicMock) -> None:
    assert result.callback_url, result
    callback = urlparse(result.callback_url)
    assert f"{callback.scheme}://{callback.netloc}{callback.path}" == REDIRECT_URI
    assert "code=" in callback.query
    response.set_cookie.assert_called_once()


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _cbor(value: Any) -> bytes:
    """The CBOR subset a WebAuthn ``none`` attestation needs: ints, bytes, text, maps."""

    def head(major: int, n: int) -> bytes:
        if n < 24:
            return bytes([major << 5 | n])
        if n < 256:
            return bytes([major << 5 | 24, n])
        return bytes([major << 5 | 25]) + struct.pack(">H", n)

    if isinstance(value, int):
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, bytes):
        return head(2, len(value)) + value
    if isinstance(value, str):
        return head(3, len(value.encode())) + value.encode()
    return head(5, len(value)) + b"".join(_cbor(k) + _cbor(v) for k, v in value.items())


def _software_passkey(options: dict[str, Any]) -> dict[str, Any]:
    """A PublicKeyCredential as ``encodeCredential`` in the setup page sends it, from a software ES256 key."""
    public = options["publicKey"]
    rp_id = public["rp"]["id"]
    numbers = ec.generate_private_key(ec.SECP256R1()).public_key().public_numbers()
    credential_id = os.urandom(16)
    cose_key = _cbor({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})
    # flags 0x45 = user present, user verified, attested credential data
    auth_data = (
        hashlib.sha256(rp_id.encode()).digest()
        + bytes([0x45])
        + struct.pack(">I", 0)
        + bytes(16)
        + struct.pack(">H", len(credential_id))
        + credential_id
        + cose_key
    )
    client_data = {"type": "webauthn.create", "challenge": public["challenge"], "origin": f"https://{rp_id}"}
    return {
        "id": _b64u(credential_id),
        "rawId": _b64u(credential_id),
        "type": "public-key",
        "response": {
            "clientDataJSON": _b64u(json.dumps(client_data).encode()),
            "attestationObject": _b64u(_cbor({"fmt": "none", "attStmt": {}, "authData": auth_data})),
        },
    }


# ---------------------------------------------------------------------------
# E-mail code
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_code_enrolment_mails_a_code_that_confirms_the_factor(
    client: ZitadelClient, instance: dict[str, str], fake_redis: Any
) -> None:
    user_id, email = await _new_user(client, instance)
    audit_patch, emit_patch = _audit_emit_patches()
    with audit_patch, emit_patch:
        await auth_api.email_otp_setup(user_id=user_id)
        first = await _next_mailed_code(email, already=0)
        # Registered at Zitadel, but not counted until the code is confirmed.
        await _methods_include(client, user_id, "AUTHENTICATION_METHOD_TYPE_OTP_EMAIL")
        assert await _counted(client, user_id) == []
        await auth_api.email_otp_resend(user_id=user_id)
        second = await _next_mailed_code(email, already=1)

        with pytest.raises(HTTPException) as wrong:
            await auth_api.email_otp_confirm(
                body=EmailOTPConfirmRequest(code=_wrong({first, second}, 8)), user_id=user_id
            )
        assert wrong.value.status_code == 400
        # A resend replaces the code: the first one no longer confirms.
        with pytest.raises(HTTPException) as stale:
            await auth_api.email_otp_confirm(body=EmailOTPConfirmRequest(code=first), user_id=user_id)
        assert stale.value.status_code == 400
        await auth_api.email_otp_confirm(body=EmailOTPConfirmRequest(code=second), user_id=user_id)

    assert await _counted(client, user_id) == ["otpEmail"]


@pytest.mark.asyncio
async def test_zitadel_mails_no_session_code_to_a_user_without_the_factor(
    client: ZitadelClient, instance: dict[str, str]
) -> None:
    """Why the enrolment has to register before the user has received a code."""
    user_id, _ = await _new_user(client, instance)
    session = await client.create_session_for_user(user_id, 60)

    with pytest.raises(httpx.HTTPStatusError) as refused:
        await client.send_session_email_code(session["sessionId"], session["sessionToken"])

    assert refused.value.response.status_code == 400
    assert "COMMAND-JKLJ3" in refused.value.response.text


@pytest.mark.asyncio
async def test_abandoned_email_code_enrolment_leaves_no_factor_at_the_next_login(
    client: ZitadelClient, instance: dict[str, str], fake_redis: Any
) -> None:
    user_id, email = await _new_user(client, instance)
    audit_patch, emit_patch = _audit_emit_patches()
    with audit_patch, emit_patch:
        await auth_api.email_otp_setup(user_id=user_id)
    await _next_mailed_code(email, already=0)
    await _methods_include(client, user_id, "AUTHENTICATION_METHOD_TYPE_OTP_EMAIL")
    # The code is never entered and its five minutes run out.
    await fake_redis.delete(f"email_otp_enrolment:{user_id}")

    result, _, response = await _login(instance, email, policy="optional")

    _assert_finalised(result, response)
    assert len(await _mails_to(email)) == 1

    async def removed() -> bool:
        return await client.login_second_factors(user_id) == []

    await _eventually(removed, "the unconfirmed e-mail factor to be removed")


@pytest.mark.asyncio
async def test_email_code_factor_registered_before_confirmation_existed_logs_in_with_the_mailed_code(
    client: ZitadelClient, instance: dict[str, str], fake_redis: Any
) -> None:
    """A factor registered without the portal's enrolment (no unconfirmed mark) is kept and asked for."""
    user_id, email = await _new_user(client, instance)
    await client.register_email_otp(user_id)
    await _methods_include(client, user_id, "AUTHENTICATION_METHOD_TYPE_OTP_EMAIL")

    result, auth_request_id, response = await _login(instance, email)

    assert result.status == "otp_email_required"
    assert result.callback_url is None
    response.set_cookie.assert_not_called()
    code = await _next_mailed_code(email, already=0)

    with pytest.raises(HTTPException) as wrong:
        await _second_step(result.temp_token, _wrong({code}, 8), auth_request_id)
    assert wrong.value.status_code == 400

    finished, response = await _second_step(result.temp_token, code, auth_request_id)
    _assert_finalised(finished, response)


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_totp_enrolment_and_login_finalise_only_after_a_valid_code(
    client: ZitadelClient, instance: dict[str, str], fake_redis: Any
) -> None:
    user_id, email = await _new_user(client, instance)
    audit_patch, emit_patch = _audit_emit_patches()
    with audit_patch, emit_patch:
        setup = await auth_api.totp_setup(user_id=user_id)
        assert setup.uri.startswith("otpauth://totp/")
        now = time.time()
        valid = {_totp(setup.secret, now + step) for step in (-30, 0, 30)}
        with pytest.raises(HTTPException) as wrong_confirm:
            await auth_api.totp_confirm(body=TOTPConfirmRequest(code=_wrong(valid, 6)), user_id=user_id)
        assert wrong_confirm.value.status_code == 400
        await auth_api.totp_confirm(body=TOTPConfirmRequest(code=_totp(setup.secret, now)), user_id=user_id)
    await _methods_include(client, user_id, "AUTHENTICATION_METHOD_TYPE_TOTP")

    result, auth_request_id, response = await _login(instance, email)

    assert result.status == "totp_required"
    assert result.callback_url is None
    response.set_cookie.assert_not_called()
    with pytest.raises(HTTPException) as wrong:
        await _second_step(result.temp_token, _wrong(valid, 6), auth_request_id)
    assert wrong.value.status_code == 400

    # The next period's code, inside Zitadel's one-step skew, so the login does
    # not depend on reusing the code that activated the factor.
    finished, response = await _second_step(result.temp_token, _totp(setup.secret, now + 30), auth_request_id)
    _assert_finalised(finished, response)


# ---------------------------------------------------------------------------
# Passkey
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passkey_enrolment_is_accepted_and_is_not_a_login_second_factor(
    client: ZitadelClient, instance: dict[str, str], fake_redis: Any
) -> None:
    user_id, email = await _new_user(client, instance)
    audit_patch, emit_patch = _audit_emit_patches()
    with audit_patch, emit_patch:
        setup = await auth_api.passkey_setup(
            request=make_request(headers={"host": "portal.example.com"}), user_id=user_id
        )
        assert setup.options["publicKey"]["rp"]["id"] == "portal.example.com"
        await auth_api.passkey_confirm(
            body=PasskeyConfirmRequest(
                passkey_id=setup.passkey_id, public_key_credential=_software_passkey(setup.options)
            ),
            user_id=user_id,
        )
    await _methods_include(client, user_id, "AUTHENTICATION_METHOD_TYPE_PASSKEY")

    assert await client.login_second_factors(user_id) == []
    result, _, response = await _login(instance, email, policy="optional")
    _assert_finalised(result, response)
