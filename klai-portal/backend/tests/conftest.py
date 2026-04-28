"""
Shared test configuration.

Sets required env vars before any app module is imported.
"""

import os
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Suppress 'coroutine was never awaited' from mocked-asyncio tests.
#
# When a test patches asyncio.create_task, the coroutine passed to it is
# created but never started.  Python emits this warning via sys.unraisablehook
# during GC — which happens after pytest fixtures have already cleaned up, so
# a fixture-scoped override arrives too late.  A module-level hook installed
# at import time persists for the full session including interpreter shutdown.
#
# Python 3.13 no longer exposes sys.UnraisableHookArgs as a runtime attribute,
# so we annotate the hook argument as Any.
# ---------------------------------------------------------------------------
_original_unraisablehook = sys.unraisablehook


def _hook(unraisable: Any) -> None:
    if isinstance(unraisable.exc_value, RuntimeWarning) and "was never awaited" in str(unraisable.exc_value):
        return
    _original_unraisablehook(unraisable)


sys.unraisablehook = _hook

# ---------------------------------------------------------------------------
# Env vars for pydantic-settings validation (read at module import time)
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("ZITADEL_PAT", "test-pat")
os.environ.setdefault("SSO_COOKIE_KEY", "R1c1-s96uO9Yz7k1E0kN6qz52gzd9PwNbAeZaks_PIc=")
os.environ.setdefault("PORTAL_SECRETS_KEY", "0" * 64)  # 64-char hex; test placeholder only
os.environ.setdefault("ENCRYPTION_KEY", "1" * 64)  # 64-char hex; test placeholder only
os.environ.setdefault("VEXA_WEBHOOK_SECRET", "test-vexa-webhook-secret")  # SEC-013 F-033
os.environ.setdefault("MONEYBIRD_WEBHOOK_TOKEN", "test-moneybird-webhook-token")  # SPEC-SEC-WEBHOOK-001 REQ-3
os.environ.setdefault("ZITADEL_IDP_GOOGLE_ID", "test-google-idp-id")  # SPEC-SEC-AUTH-COVERAGE-001 REQ-2.6
os.environ.setdefault("ZITADEL_IDP_MICROSOFT_ID", "test-microsoft-idp-id")  # SPEC-SEC-AUTH-COVERAGE-001 REQ-2.6

# ---------------------------------------------------------------------------
# Auto-discoverable fixtures (SPEC-SEC-AUTH-COVERAGE-001 REQ-5.6)
#
# Re-export the respx_zitadel fixture from auth_test_helpers so all auth
# endpoint test modules pick it up via pytest's conftest discovery without
# needing to import + alias it (the import-style triggers F811 redefinition
# warnings when test functions take it as a parameter).
# ---------------------------------------------------------------------------
from auth_test_helpers import respx_zitadel  # noqa: E402, F401
