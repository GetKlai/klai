"""Tests for knowledge_ingest.connector_cookies (SPEC-CRAWLER-004 Fase C/D fix).

The helper is called from both the ``/ingest/v1/crawl/sync`` FastAPI
handler AND the ``run_crawl`` Procrastinate task, so plaintext cookies
never live on the task queue (REQ-05.4).
"""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from connector_credentials import AESGCMCipher, ConnectorCredentialStore
from cryptography.exceptions import InvalidTag

from knowledge_ingest.connector_cookies import (
    ConnectorDecryptError,
    ConnectorNotFoundError,
    ConnectorOrgMismatchError,
    CookieJar,
    StoredCredentials,
    load_connector_cookies,
    store_connector_cookie_jar,
)


def _build_blobs(kek_hex: str, cookies: list[dict], **extra: object) -> tuple[bytes, bytes]:
    raw_dek = os.urandom(32)
    kek_cipher = AESGCMCipher(bytes.fromhex(kek_hex))
    dek_enc = kek_cipher.encrypt(raw_dek.hex())
    dek_cipher = AESGCMCipher(raw_dek)
    encrypted = dek_cipher.encrypt(json.dumps({"cookies": cookies, **extra}))
    return encrypted, dek_enc


def _mock_pool(row: dict | None) -> MagicMock:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=row)
    return pool


@pytest.mark.asyncio
async def test_returns_cookies_for_valid_connector() -> None:
    kek_hex = os.urandom(32).hex()
    expected = [{"name": "sid", "value": "abc123"}]
    encrypted, dek_enc = _build_blobs(kek_hex, expected)

    pool = _mock_pool(
        {
            "id": uuid.UUID(int=1),
            "org_id": 7,
            "encrypted_credentials": encrypted,
            "zitadel_org_id": "42",
            "connector_dek_enc": dek_enc,
        },
    )
    out = await load_connector_cookies(
        connector_id=uuid.uuid4(),
        expected_zitadel_org_id="42",
        pool=pool,
        kek_hex=kek_hex,
    )
    assert out == expected


@pytest.mark.asyncio
async def test_empty_list_for_public_connector() -> None:
    pool = _mock_pool(
        {
            "id": uuid.UUID(int=1),
            "org_id": 7,
            "encrypted_credentials": None,
            "zitadel_org_id": "42",
            "connector_dek_enc": None,
        },
    )
    out = await load_connector_cookies(
        connector_id=uuid.uuid4(),
        expected_zitadel_org_id="42",
        pool=pool,
        kek_hex=os.urandom(32).hex(),
    )
    assert out == []


@pytest.mark.asyncio
async def test_not_found_when_row_missing() -> None:
    pool = _mock_pool(None)
    with pytest.raises(ConnectorNotFoundError):
        await load_connector_cookies(
            connector_id=uuid.uuid4(),
            expected_zitadel_org_id="42",
            pool=pool,
            kek_hex=os.urandom(32).hex(),
        )


@pytest.mark.asyncio
async def test_org_mismatch_raises() -> None:
    kek_hex = os.urandom(32).hex()
    encrypted, dek_enc = _build_blobs(kek_hex, [{"name": "x", "value": "y"}])
    pool = _mock_pool(
        {
            "id": uuid.UUID(int=1),
            "org_id": 7,
            "encrypted_credentials": encrypted,
            "zitadel_org_id": "77",
            "connector_dek_enc": dek_enc,
        },
    )
    with pytest.raises(ConnectorOrgMismatchError):
        await load_connector_cookies(
            connector_id=uuid.uuid4(),
            expected_zitadel_org_id="42",
            pool=pool,
            kek_hex=kek_hex,
        )


@pytest.mark.asyncio
async def test_wrong_kek_raises_decrypt_error() -> None:
    correct_hex = os.urandom(32).hex()
    wrong_hex = os.urandom(32).hex()
    encrypted, dek_enc = _build_blobs(correct_hex, [{"name": "x", "value": "y"}])
    pool = _mock_pool(
        {
            "id": uuid.UUID(int=1),
            "org_id": 7,
            "encrypted_credentials": encrypted,
            "zitadel_org_id": "42",
            "connector_dek_enc": dek_enc,
        },
    )
    with pytest.raises(ConnectorDecryptError):
        await load_connector_cookies(
            connector_id=uuid.uuid4(),
            expected_zitadel_org_id="42",
            pool=pool,
            kek_hex=wrong_hex,
        )


@pytest.mark.asyncio
async def test_empty_kek_raises_value_error() -> None:
    pool = _mock_pool(None)
    with pytest.raises(ValueError, match="encryption_key_not_configured"):
        await load_connector_cookies(
            connector_id=uuid.uuid4(),
            expected_zitadel_org_id="42",
            pool=pool,
            kek_hex="",
        )


@pytest.mark.asyncio
async def test_tampered_cookies_blob_raises_decrypt_error() -> None:
    kek_hex = os.urandom(32).hex()
    encrypted, dek_enc = _build_blobs(kek_hex, [{"name": "x", "value": "y"}])
    # Flip one byte in the cookies ciphertext.
    tampered = bytearray(encrypted)
    tampered[-1] ^= 0xFF
    pool = _mock_pool(
        {
            "id": uuid.UUID(int=1),
            "org_id": 7,
            "encrypted_credentials": bytes(tampered),
            "zitadel_org_id": "42",
            "connector_dek_enc": dek_enc,
        },
    )
    with pytest.raises(ConnectorDecryptError):
        await load_connector_cookies(
            connector_id=uuid.uuid4(),
            expected_zitadel_org_id="42",
            pool=pool,
            kek_hex=kek_hex,
        )


def test_shared_lib_still_raises_invalid_tag_on_wrong_kek() -> None:
    """Defense-in-depth: the shared lib still surfaces InvalidTag unwrapped
    when called directly; the connector_cookies wrapper is the one that
    translates it into ConnectorDecryptError.
    """
    kek_a = os.urandom(32).hex()
    kek_b = os.urandom(32).hex()
    store_b = ConnectorCredentialStore(kek_b)
    encrypted, dek_enc = _build_blobs(kek_a, [{"v": "x"}])
    with pytest.raises(InvalidTag):
        store_b.decrypt_credentials_from_blobs(
            encrypted_credentials=encrypted,
            connector_dek_enc=dek_enc,
        )


def _stored(kek_hex: str, cookies: list[dict], **extra: object) -> StoredCredentials:
    encrypted, dek_enc = _build_blobs(kek_hex, cookies, **extra)
    store = ConnectorCredentialStore(kek_hex)
    payload = store.decrypt_credentials_from_blobs(
        encrypted_credentials=encrypted, connector_dek_enc=dek_enc
    )
    return StoredCredentials(payload=payload, encrypted=encrypted, dek_enc=dek_enc, org_id=7)


def _written_payload(pool: MagicMock, kek_hex: str, dek_enc: bytes) -> dict:
    new_blob = pool.execute.await_args.args[1]
    return ConnectorCredentialStore(kek_hex).decrypt_credentials_from_blobs(
        encrypted_credentials=new_blob, connector_dek_enc=dek_enc
    )


@pytest.mark.asyncio
async def test_the_jar_write_is_bound_to_the_blob_the_probe_read() -> None:
    """The probe read blob A, then made its HTTP requests. If the owner pasted
    blob B meanwhile, the jar derived from A must not overwrite B: the update
    replaces A's cookies and is guarded on A itself (plus the org), never on a
    fresh re-read. Everything next to the cookies is kept."""
    kek_hex = os.urandom(32).hex()
    stored = _stored(
        kek_hex,
        [{"name": "sid", "value": "pasted", "domain": "wiki.example.com", "path": "/"}],
        auth_headers={"X-Example": "unchanged"},
        auth_chain_hosts=["idp.example.com"],
    )
    jar = CookieJar(stored.payload["cookies"], "wiki.example.com")
    jar.merge_set_cookies("https://wiki.example.com/a", ["sid=issued; Path=/"])
    pool = MagicMock()
    pool.fetchrow = AsyncMock()
    pool.execute = AsyncMock(return_value="UPDATE 1")

    written = await store_connector_cookie_jar(
        connector_id=uuid.UUID(int=1), stored=stored, pool=pool, kek_hex=kek_hex, jar=jar
    )

    assert written is True
    pool.fetchrow.assert_not_awaited()
    sql, _new_blob, connector_id, guard, org_id = pool.execute.await_args.args
    assert "org_id = $4" in sql
    assert (connector_id, guard, org_id) == (uuid.UUID(int=1), stored.encrypted, 7)
    payload = _written_payload(pool, kek_hex, stored.dek_enc)
    assert payload["cookies"][0]["value"] == "issued"
    assert payload["auth_headers"] == {"X-Example": "unchanged"}
    assert payload["auth_chain_hosts"] == ["idp.example.com"]


def test_the_jar_keeps_each_cookie_with_its_own_host() -> None:
    """A Set-Cookie updates only the cookie with the same name, host and path
    (a missing domain is the connector's own host, a missing path is "/"),
    adds one it did not know with its expiry, and never touches another
    host's cookie of the same name."""
    jar = CookieJar(
        [
            {"name": "sid", "value": "root"},
            {"name": "sid", "value": "admin", "domain": "wiki.example.com", "path": "/admin"},
            {"name": "sid", "value": "idp", "domain": "idp.example.com", "path": "/"},
            {"name": "shared", "value": "s", "domain": ".example.com", "path": "/"},
        ],
        "wiki.example.com",
    )

    jar.merge_set_cookies(
        "https://wiki.example.com/a",
        ["sid=issued; Path=/", "XSRF-TOKEN=x1; Path=/; Secure; Max-Age=7200"],
    )

    assert jar.jar_for_host("wiki.example.com") == {
        "sid": "issued",
        "shared": "s",
        "XSRF-TOKEN": "x1",
    }
    assert jar.jar_for_host("idp.example.com") == {"sid": "idp", "shared": "s"}
    assert [c["value"] for c in jar.cookies[:3]] == ["issued", "admin", "idp"]
    xsrf = jar.cookies[-1]
    assert (xsrf["domain"], xsrf["secure"]) == ("wiki.example.com", True)
    assert xsrf["expires"] is not None
    assert jar.issued_hosts == {"wiki.example.com"}
