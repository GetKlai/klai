"""Bolt OAuth storage with tokens encrypted at rest (SPEC-SLACK-001 §6.1).

slack_sdk 3.45 ships ``AsyncSQLAlchemyInstallationStore`` and
``AsyncSQLAlchemyOAuthStateStore``. The installation store is reused as is,
except that every token column gets the ``EncryptedToken`` column type: the
store's own queries then encrypt on write and decrypt on read without any of
its methods being overridden. Token columns are TEXT in the migration because
a base64 AES-GCM ciphertext of a rotating token exceeds the store's
VARCHAR(200).
"""

from __future__ import annotations

from typing import Any

from slack_sdk.oauth.installation_store.sqlalchemy import (
    AsyncSQLAlchemyInstallationStore,
    SQLAlchemyInstallationStore,
)
from slack_sdk.oauth.state_store.sqlalchemy import AsyncSQLAlchemyOAuthStateStore
from sqlalchemy import MetaData, Table, Text
from sqlalchemy.types import TypeDecorator

from app.crypto import decrypt, encrypt

INSTALLATIONS_TABLE = "slack_installations"
BOTS_TABLE = "slack_bots"
OAUTH_STATES_TABLE = "slack_oauth_states"

TOKEN_COLUMNS = ("bot_token", "bot_refresh_token", "user_token", "user_refresh_token")


class EncryptedToken(TypeDecorator[str]):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        return None if value is None else encrypt(value)

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        return None if value is None else decrypt(value)


def _encrypt_token_columns(table: Table) -> Table:
    for name in TOKEN_COLUMNS:
        if name in table.c:
            table.c[name].type = EncryptedToken()
    return table


def build_installations_table(metadata: MetaData, table_name: str = INSTALLATIONS_TABLE) -> Table:
    return _encrypt_token_columns(
        SQLAlchemyInstallationStore.build_installations_table(metadata, table_name)
    )


def build_bots_table(metadata: MetaData, table_name: str = BOTS_TABLE) -> Table:
    return _encrypt_token_columns(
        SQLAlchemyInstallationStore.build_bots_table(metadata, table_name)
    )


def build_oauth_states_table(metadata: MetaData, table_name: str = OAUTH_STATES_TABLE) -> Table:
    return AsyncSQLAlchemyOAuthStateStore.build_oauth_states_table(metadata, table_name)


class EncryptedInstallationStore(AsyncSQLAlchemyInstallationStore):
    @classmethod
    def build_installations_table(cls, metadata: MetaData, table_name: str) -> Table:
        return build_installations_table(metadata, table_name)

    @classmethod
    def build_bots_table(cls, metadata: MetaData, table_name: str) -> Table:
        return build_bots_table(metadata, table_name)
