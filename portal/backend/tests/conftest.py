"""
Shared test configuration.

Sets required env vars before any app module is imported.
"""

import os

# Set required env vars for pydantic-settings validation.
# The Settings class reads these at module import time.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("ZITADEL_PAT", "test-pat")
os.environ.setdefault("SSO_COOKIE_KEY", "dGVzdC1rZXktMTIzNDU2Nzg5MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTI=")
