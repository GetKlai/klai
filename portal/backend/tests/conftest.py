"""
Test environment setup.

Sets required pydantic-settings env vars so tests can import app modules
without a real .env file. Only sets vars that have no defaults and are
required by Settings at module-load time.
"""

import os

# These are required fields in Settings (no defaults)
os.environ.setdefault("ZITADEL_PAT", "test_pat")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault(
    "SSO_COOKIE_KEY",
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",  # 44-char base64 placeholder
)
