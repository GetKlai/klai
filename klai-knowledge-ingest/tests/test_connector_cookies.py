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
)


def _build_blobs(kek_hex: str, cookies: list[dict]) -> tuple[bytes, bytes]:
    raw_dek = os.urandom(32)
    kek_cipher = AESGCMCipher(bytes.fromhex(kek_hex))
    dek_enc = kek_cipher.encrypt(raw_dek.hex())
    dek_cipher = AESGCMCipher(raw_dek)
    encrypted = dek_cipher.encrypt(json.dumps({"cookies": cookies}))
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
