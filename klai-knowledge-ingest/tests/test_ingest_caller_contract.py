"""Contract test — SPEC-SEC-AUDIT-2026-04 finding C3.

/ingest/v1/document accepts org_id + user_id from the request body without
performing its own identity verification. This is BY-DESIGN safe because every
production caller verifies identity upstream before forwarding. This test makes
the implicit caller set EXPLICIT: if a new caller is added without updating this
file, the test fails and forces a security review.

Failure means: "I added a new caller to /ingest/v1/document. Did I verify identity
before forwarding org_id/user_id? If yes, add my service to EXPECTED_CALLERS below
and update the @MX:NOTE in routes/ingest.py."

SPEC-SEC-AUDIT-2026-04 C3 — do NOT delete or loosen this test without a SPEC.
"""
from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# EXPECTED CALLERS
#
# Each entry is a repo-root-relative path (forward slashes) of a Python file
# that sends POST /ingest/v1/document.
#
# When you add a new caller: (1) verify identity upstream, (2) add it here,
# (3) update the @MX:NOTE block in knowledge_ingest/routes/ingest.py.
# ---------------------------------------------------------------------------
EXPECTED_CALLERS: frozenset[str] = frozenset(
    {
        # portal-api (main KB ingest path): verifies session cookie before forwarding org_id/user_id
        "klai-portal/backend/app/services/knowledge_ingest_client.py",
        # portal-api (partner API path): org_id is verified via portal-api auth middleware
        "klai-portal/backend/app/services/partner_knowledge.py",
        # portal-api (meeting transcript ingest): org_id comes from the authenticated meeting record
        "klai-portal/backend/app/services/knowledge_adapter.py",
        # knowledge-mcp: calls portal-api /internal/identity/verify
        "klai-knowledge-mcp/main.py",
        # scribe-api: calls portal-api /internal/identity/verify (post-B1 fix)
        "klai-scribe/scribe-api/app/services/knowledge_adapter.py",
        # klai-connector: org_id comes from connector row (DB-persisted, not caller input);
        # user_id is never forwarded by the connector — no identity assertion gap.
        "klai-connector/app/clients/knowledge_ingest.py",
    }
)

# Repo root is two levels up from this test file:
# klai-knowledge-ingest/tests/test_ingest_caller_contract.py -> repo root
_REPO_ROOT = Path(__file__).parent.parent.parent


def _find_callers() -> frozenset[str]:
    """Scan the repo for Python files that POST to /ingest/v1/document.

    Returns a set of repo-root-relative paths (forward slashes).
    """
    callers: set[str] = set()
    for py_file in _REPO_ROOT.rglob("*.py"):
        # Skip .venv directories, __pycache__, and this test file itself
        parts = py_file.parts
        if any(p in {".venv", "__pycache__", "site-packages"} for p in parts):
            continue
        if py_file == Path(__file__):
            continue
        try:
            text = py_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "ingest/v1/document" in text:
            rel = py_file.relative_to(_REPO_ROOT).as_posix()
            callers.add(rel)

    return frozenset(callers)


def test_ingest_v1_document_caller_set_is_known() -> None:
    """Assert that every file calling /ingest/v1/document is in the expected set.

    If this test fails because you added a new caller:
      1. Confirm you call portal-api /internal/identity/verify (or equivalent)
         BEFORE forwarding org_id / user_id in the request body.
      2. Add your file to EXPECTED_CALLERS in this test.
      3. Add your service to the @MX:NOTE block in
         knowledge_ingest/routes/ingest.py.

    Do NOT add a caller without reading SPEC-SEC-AUDIT-2026-04 C3 first.
    """
    # Respect CI environments where the full monorepo is not present.
    # When running in a context that only has klai-knowledge-ingest checked out,
    # skip rather than fail on a partial scan.
    if not (_REPO_ROOT / "klai-portal").exists():
        import pytest  # noqa: PLC0415

        pytest.skip("Full monorepo not present — skipping cross-service caller scan")

    found = _find_callers()

    # Remove spec files, docs, and test files — they reference the path as text
    # but are not production callers.
    noise_patterns = (".moai/", "docs/", "tests/", ".serena/", ".claude/")
    found = frozenset(f for f in found if not any(f.startswith(p) for p in noise_patterns))

    unexpected = found - EXPECTED_CALLERS
    missing = EXPECTED_CALLERS - found

    messages: list[str] = []
    if unexpected:
        messages.append(
            "NEW callers found that are NOT in EXPECTED_CALLERS.\n"
            "Read SPEC-SEC-AUDIT-2026-04 C3 before adding them:\n"
            + "\n".join(f"  + {p}" for p in sorted(unexpected))
        )
    if missing:
        messages.append(
            "Expected callers NOT found in codebase (file moved or deleted?):\n"
            + "\n".join(f"  - {p}" for p in sorted(missing))
        )

    assert not messages, "\n\n".join(messages)
