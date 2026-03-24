"""
Shared test configuration.

Sets required env vars before any app module is imported.
"""

import os

# Set required env vars for pydantic-settings validation.
# The Settings class reads these at module import time.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("ZITADEL_PAT", "test-pat")
os.environ.setdefault("SSO_COOKIE_KEY", "R1c1-s96uO9Yz7k1E0kN6qz52gzd9PwNbAeZaks_PIc=")
