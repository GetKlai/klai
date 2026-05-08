"""Detect drift between vendored ``deploy/litellm/klai_service_auth.py``
and the canonical ``klai-libs/service-auth/klai_service_auth/client.py``.

SPEC-SEC-SERVICE-AUTH-001 Phase C-1. The vendored copy exists because the
LiteLLM container is a stock upstream image without a path-dep mechanism.
This test fails when the canonical library changes but the vendored copy
isn't updated to match.

Phase D plan: replace the vendored file with a proper ``pip install`` of
``klai-service-auth`` in a custom litellm Dockerfile, and delete this test.

Implementation note
-------------------

Python's import system deduplicates by module name, so we cannot ``import
klai_service_auth`` once for the vendored copy and once for the canonical
package and expect to get two different namespaces. We use explicit
``importlib.util.spec_from_file_location`` to load each file under a
unique synthetic name (``_drift_vendored``, ``_drift_canonical_client``,
``_drift_canonical_scopes``).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL_DIR = _REPO_ROOT / "klai-libs" / "service-auth" / "klai_service_auth"
_VENDORED_PATH = _REPO_ROOT / "deploy" / "litellm" / "klai_service_auth.py"


def _load(name: str, path: Path) -> ModuleType:
    """Load ``path`` as a fresh module under ``name`` (no name-dedup with sys.path)."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"could not build module spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_vendored_zitadel_token_client_matches_canonical_public_api():
    """Public attribute / method names on the vendored class MUST match the
    canonical class. Dropping a method silently breaks the LiteLLM hook;
    adding one creates inconsistent behaviour between callers."""
    vendored = _load("_drift_vendored", _VENDORED_PATH)
    canonical = _load("_drift_canonical_client", _CANONICAL_DIR / "client.py")

    vendored_public = {n for n in dir(vendored.ZitadelTokenClient) if not n.startswith("_")}
    canonical_public = {n for n in dir(canonical.ZitadelTokenClient) if not n.startswith("_")}

    assert vendored_public == canonical_public, (
        f"Public API drift between vendored and canonical ZitadelTokenClient.\n"
        f"  In vendored, missing in canonical: {vendored_public - canonical_public}\n"
        f"  In canonical, missing in vendored: {canonical_public - vendored_public}\n"
        f"  Update deploy/litellm/klai_service_auth.py to match "
        f"klai-libs/service-auth/klai_service_auth/client.py."
    )


def test_vendored_service_auth_error_exists():
    """ServiceAuthError must remain importable from both copies."""
    vendored = _load("_drift_vendored_error", _VENDORED_PATH)
    canonical = _load("_drift_canonical_error", _CANONICAL_DIR / "client.py")

    assert isinstance(vendored.ServiceAuthError, type)
    assert issubclass(vendored.ServiceAuthError, Exception)
    assert isinstance(canonical.ServiceAuthError, type)
    assert issubclass(canonical.ServiceAuthError, Exception)


def test_vendored_constants_match():
    """Constants (refresh fraction, min TTL) must match — drift here would
    silently change cache behaviour in LiteLLM vs other callers."""
    vendored = _load("_drift_vendored_consts", _VENDORED_PATH)
    canonical = _load("_drift_canonical_consts", _CANONICAL_DIR / "client.py")

    assert vendored._REFRESH_FRACTION == canonical._REFRESH_FRACTION, (
        "refresh fraction drift between vendored and canonical"
    )
    assert vendored._MIN_TTL_SECONDS == canonical._MIN_TTL_SECONDS, (
        "min TTL drift between vendored and canonical"
    )


def test_vendored_body_excerpt_sanitizer_matches_canonical():
    """The vendored copy must redact client secrets from IdP error bodies."""
    vendored = _load("_drift_vendored_sanitize", _VENDORED_PATH)
    canonical = _load("_drift_canonical_sanitize", _CANONICAL_DIR / "client.py")

    body = "invalid client_secret=super-secret-client-value"
    secrets = ("super-secret-client-value",)

    assert vendored._sanitize_body_excerpt(body, secrets) == canonical._sanitize_body_excerpt(
        body,
        secrets,
    )
    assert "super-secret-client-value" not in vendored._sanitize_body_excerpt(body, secrets)


def test_vendored_scope_constant_matches_library():
    """The retrieval-query scope constant must match across vendored copy
    and canonical scopes module — receivers compare to a single string."""
    vendored = _load("_drift_vendored_scope", _VENDORED_PATH)
    canonical_scopes = _load("_drift_canonical_scopes", _CANONICAL_DIR / "scopes.py")

    assert vendored.SCOPE_RETRIEVAL_QUERY == canonical_scopes.RETRIEVAL_QUERY
