"""Conftest for research-api tests.

Sets required environment variables before any module import so pydantic-settings
can instantiate Settings without a real .env file.
"""
import os

os.environ.setdefault("POSTGRES_DSN", "postgresql+asyncpg://test:test@localhost/test")
