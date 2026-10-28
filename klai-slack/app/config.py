"""Settings for klai-slack (SPEC-SLACK-001 §7.5).

Every secret is mandatory and validated at load time, so a missing or empty
value stops the container at startup instead of failing on the first Slack
event. The encryption key is additionally decoded and length-checked, because
an AES-256 key of the wrong size would only surface on the first token write.
"""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REQUIRED_SECRETS = (
    "slack_client_id",
    "slack_client_secret",
    "slack_signing_secret",
    "portal_internal_secret",
    "internal_secret",
    "database_url",
)


class Settings(BaseSettings):
    # validate_default: the "" defaults below must run through the validators,
    # otherwise an unset env var would silently pass as an empty secret.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", validate_default=True
    )

    slack_client_id: str = ""
    slack_client_secret: str = ""
    slack_signing_secret: str = ""
    # base64 of exactly 32 bytes; encrypts bot tokens and the Klai partner key.
    slack_encryption_key: str = ""

    database_url: str = ""

    portal_api_url: str = "http://portal-api:8010"
    # klai-slack -> portal-api /internal/slack/* (Bearer).
    portal_internal_secret: str = ""
    # portal-api -> klai-slack /internal/installations/* (Bearer).
    internal_secret: str = ""

    public_base_url: str = "https://api.getklai.com"
    klai_env: str = "production"

    @field_validator(*_REQUIRED_SECRETS, mode="after")
    @classmethod
    def _require_non_empty(cls, v: str, info: ValidationInfo) -> str:
        if not v or not v.strip():
            raise ValueError(f"Missing required: {(info.field_name or '').upper()}")
        return v

    @field_validator("slack_encryption_key", mode="after")
    @classmethod
    def _require_valid_encryption_key(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "SLACK_ENCRYPTION_KEY must be a non-empty base64-encoded 32-byte AES-256 key."
            )
        try:
            decoded = base64.b64decode(v, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("SLACK_ENCRYPTION_KEY must be valid base64.") from exc
        if len(decoded) != 32:
            raise ValueError(
                f"SLACK_ENCRYPTION_KEY must decode to exactly 32 bytes (AES-256), "
                f"got {len(decoded)}. Generate one with: python -c 'import secrets, base64; "
                "print(base64.b64encode(secrets.token_bytes(32)).decode())'"
            )
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
