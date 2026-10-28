from __future__ import annotations

import base64

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import MetaData, create_engine, insert, select, text

from app.crypto import decrypt, encrypt
from app.oauth_store import EncryptedInstallationStore, build_bots_table

TOKEN = "xoxb-test-0000-synthetic"


def test_roundtrip_and_random_nonce() -> None:
    first, second = encrypt(TOKEN), encrypt(TOKEN)
    assert first != second
    assert TOKEN not in first
    assert decrypt(first) == decrypt(second) == TOKEN


def test_tampered_ciphertext_is_rejected() -> None:
    raw = bytearray(base64.b64decode(encrypt(TOKEN)))
    raw[-1] ^= 0x01
    with pytest.raises(InvalidTag):
        decrypt(base64.b64encode(bytes(raw)).decode())


def test_bolt_store_token_columns_are_encrypted_at_rest() -> None:
    """The store's own SQL writes ciphertext and reads plaintext through the column type."""
    engine = create_engine("sqlite://")
    bots = build_bots_table(MetaData())
    bots.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            insert(bots).values(
                client_id="1111.2222",
                app_id="A1",
                team_id="T1",
                bot_token=TOKEN,
                bot_refresh_token="xoxe-1-synthetic",
                is_enterprise_install=False,
            )
        )
        stored = conn.execute(text("SELECT bot_token, bot_refresh_token FROM slack_bots")).one()
        loaded = conn.execute(select(bots.c.bot_token, bots.c.bot_refresh_token)).one()
    assert TOKEN not in stored.bot_token
    assert decrypt(stored.bot_token) == TOKEN
    assert tuple(loaded) == (TOKEN, "xoxe-1-synthetic")


def test_installation_store_uses_encrypted_tables() -> None:
    store = EncryptedInstallationStore(client_id="1111.2222", engine=None)  # pyright: ignore[reportArgumentType]
    for table in (store.installations, store.bots):
        assert type(table.c.bot_token.type).__name__ == "EncryptedToken"
        assert type(table.c.bot_refresh_token.type).__name__ == "EncryptedToken"
