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
    load_connector_cookies,
    store_refreshed_connector_cookies,
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


def _row(encrypted: bytes, dek_enc: bytes) -> dict:
    return {
        "id": uuid.UUID(int=1),
        "encrypted_credentials": encrypted,
        "zitadel_org_id": "42",
        "connector_dek_enc": dek_enc,
    }


@pytest.mark.asyncio
async def test_refreshed_session_cookie_replaces_the_stored_value() -> None:
    """A rolling session only survives if the value the site re-issues is the
    one replayed next time. Only the refreshed value changes; everything else
    in the encrypted payload stays, and the write is a compare-and-swap on the
    blob it read so a concurrent cookie paste in the portal is never
    overwritten."""
    kek_hex = os.urandom(32).hex()
    encrypted, dek_enc = _build_blobs(
        kek_hex,
        [
            {"name": "sid", "value": "pasted", "domain": "wiki.example.com", "path": "/"},
            {"name": "xsrf", "value": "keep", "domain": ".wiki.example.com", "path": "/"},
        ],
        auth_headers={"X-Example": "unchanged"},
    )
    pool = _mock_pool(_row(encrypted, dek_enc))
    pool.execute = AsyncMock(return_value="UPDATE 1")

    changed = await store_refreshed_connector_cookies(
        connector_id=uuid.UUID(int=1),
        expected_zitadel_org_id="42",
        pool=pool,
        kek_hex=kek_hex,
        hostname="wiki.example.com",
        refreshed={"sid": "issued", "unrelated": "ignored"},
    )

    assert changed == 1
    new_blob, connector_id, guard = pool.execute.await_args.args[1:]
    assert connector_id == uuid.UUID(int=1)
    assert guard == encrypted
    payload = ConnectorCredentialStore(kek_hex).decrypt_credentials_from_blobs(
        encrypted_credentials=new_blob, connector_dek_enc=dek_enc
    )
    assert payload == {
        "cookies": [
            {"name": "sid", "value": "issued", "domain": "wiki.example.com", "path": "/"},
            {"name": "xsrf", "value": "keep", "domain": ".wiki.example.com", "path": "/"},
        ],
        "auth_headers": {"X-Example": "unchanged"},
    }


@pytest.mark.asyncio
async def test_cookie_scoped_to_another_host_is_never_rewritten() -> None:
    """Saved cookies are host-scoped credentials: a response from one host
    must not rewrite a same-named cookie saved for a different host."""
    kek_hex = os.urandom(32).hex()
    encrypted, dek_enc = _build_blobs(
        kek_hex, [{"name": "sid", "value": "pasted", "domain": "sso.example.com", "path": "/"}]
    )
    pool = _mock_pool(_row(encrypted, dek_enc))
    pool.execute = AsyncMock(return_value="UPDATE 1")

    changed = await store_refreshed_connector_cookies(
        connector_id=uuid.UUID(int=1),
        expected_zitadel_org_id="42",
        pool=pool,
        kek_hex=kek_hex,
        hostname="wiki.example.com",
        refreshed={"sid": "issued"},
    )

    assert changed == 0
    pool.execute.assert_not_awaited()
