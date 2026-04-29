"""SPEC-SEC-INTERNAL-001 B3 acceptance: klai-connector.

Covers REQ-9.3 (fail-closed startup on empty outbound secrets), REQ-10
(sync_run.error_details never persists raw secrets), and the
PortalClient / KnowledgeIngestClient runtime guards that catch the
``Settings.model_construct()`` bypass path.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

_VALID_SETTINGS_KWARGS: dict[str, str] = {
    # Required pydantic-settings fields with no default.
    "database_url": "postgresql+asyncpg://test:test@localhost:5432/test",
    "zitadel_introspection_url": "http://zitadel/oauth/v2/introspect",
    "zitadel_client_id": "test-client",
    "zitadel_client_secret": "test-zitadel-secret-12345",
    "github_app_id": "12345",
    "github_app_private_key": "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----",
    "encryption_key": "0" * 64,
    "knowledge_ingest_url": "http://knowledge-ingest:8000",
    # The two fields under test default to "" and require fail-closed validators.
    "knowledge_ingest_secret": "test-ingest-secret-12345",
    "portal_internal_secret": "test-portal-secret-12345",
    # SPEC-SEC-AUDIT-2026-04 B2: audience is now mandatory (model_validator).
    "zitadel_api_audience": "klai-connector-test-audience",
}


# ---------------------------------------------------------------------------
# REQ-9.3 / AC-9.3: pydantic.ValidationError on empty secrets at startup
# ---------------------------------------------------------------------------


class TestSettingsFailClosedOnEmptySecrets:
    def test_empty_knowledge_ingest_secret_raises_validation_error(self):
        from app.core.config import Settings

        with pytest.raises(ValidationError) as exc:
            Settings(**{**_VALID_SETTINGS_KWARGS, "knowledge_ingest_secret": ""})  # type: ignore[arg-type]
        assert "KNOWLEDGE_INGEST_SECRET" in str(exc.value)

    def test_empty_portal_internal_secret_raises_validation_error(self):
        from app.core.config import Settings

        with pytest.raises(ValidationError) as exc:
            Settings(**{**_VALID_SETTINGS_KWARGS, "portal_internal_secret": ""})  # type: ignore[arg-type]
        assert "PORTAL_INTERNAL_SECRET" in str(exc.value)

    def test_full_secrets_pass_validation(self):
        from app.core.config import Settings

        # Should not raise.
        s = Settings(**_VALID_SETTINGS_KWARGS)  # type: ignore[arg-type]
        assert s.knowledge_ingest_secret == "test-ingest-secret-12345"
        assert s.portal_internal_secret == "test-portal-secret-12345"


# ---------------------------------------------------------------------------
# REQ-9.3 / AC-9.4: PortalClient never sends ``Bearer `` (empty)
# ---------------------------------------------------------------------------


class TestPortalClientNeverEmitsEmptyBearer:
    def test_headers_raises_when_secret_empty(self):
        """``Settings.model_construct()`` bypasses validation; the runtime guard
        on PortalClient catches that path so the contract still holds.
        """
        from types import SimpleNamespace

        from app.services.portal_client import PortalClient

        broken_settings = SimpleNamespace(
            portal_api_url="http://portal-api:8100",
            portal_internal_secret="",
        )
        client = PortalClient(broken_settings)  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="empty Bearer secret"):
            client._headers()

    def test_headers_emits_bearer_with_secret(self):
        from types import SimpleNamespace

        from app.services.portal_client import PortalClient

        good_settings = SimpleNamespace(
            portal_api_url="http://portal-api:8100",
            portal_internal_secret="real-portal-secret-12345",
        )
        client = PortalClient(good_settings)  # type: ignore[arg-type]
        assert client._headers() == {"Authorization": "Bearer real-portal-secret-12345"}


# ---------------------------------------------------------------------------
# REQ-9.3: KnowledgeIngestClient refuses to construct with empty secret
# ---------------------------------------------------------------------------


class TestKnowledgeIngestClientFailClosed:
    def test_constructor_raises_on_empty_secret(self):
        from app.clients.knowledge_ingest import KnowledgeIngestClient

        with pytest.raises(RuntimeError, match="empty internal secret"):
            KnowledgeIngestClient(base_url="http://test", internal_secret="")

    def test_constructor_passes_with_non_empty_secret(self):
        from app.clients.knowledge_ingest import KnowledgeIngestClient

        client = KnowledgeIngestClient(base_url="http://test", internal_secret="some-real-secret-12345")
        assert client._internal_secret == "some-real-secret-12345"


# ---------------------------------------------------------------------------
# REQ-10 / AC-10.1: sanitize_response_body wired into sync_engine error path
# ---------------------------------------------------------------------------


class TestErrorDetailsSanitization:
    def test_sanitize_response_body_strips_known_secret(self):
        """The connector wrapper around klai_log_utils.sanitize_response_body
        is wired into sync_engine. Verify it scrubs the connector's own
        secrets before persistence.
        """
        from types import SimpleNamespace

        from app.core.sanitize import sanitize_response_body

        settings = SimpleNamespace(
            knowledge_ingest_secret="secret-knowledge-ingest-12345",
            portal_internal_secret="secret-portal-internal-12345",
            zitadel_client_secret="zitadel-secret-12345",
            other_field="not-a-secret",
        )

        # Mimic an enqueue_err.response with an upstream body that
        # accidentally reflects the connector's outbound secret.
        leaked_body = "Internal server error: invalid X-Internal-Secret secret-knowledge-ingest-12345 -- denied"
        fake_response = SimpleNamespace(text=leaked_body)
        fake_exc = SimpleNamespace(response=fake_response)

        result = sanitize_response_body(settings, fake_exc, max_len=500)
        assert "secret-knowledge-ingest-12345" not in result
        assert "<redacted>" in result
        # Unrelated context survives.
        assert "Internal server error" in result

    def test_sanitize_response_body_caps_length(self):
        from types import SimpleNamespace

        from app.core.sanitize import sanitize_response_body

        settings = SimpleNamespace(knowledge_ingest_secret="abc12345-secret")
        big_body = "x" * 5000
        fake_response = SimpleNamespace(text=big_body)
        fake_exc = SimpleNamespace(response=fake_response)

        result = sanitize_response_body(settings, fake_exc, max_len=500)
        assert len(result) == 500
