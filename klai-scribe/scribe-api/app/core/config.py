from pathlib import Path
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


# SPEC-SEC-HYGIENE-001 REQ-37.1 — explicit allowlist for `whisper_server_url`.
# Operator-controlled config, NOT user-supplied at request time. The threat
# model is operator typo / env drift turning the unauthenticated /health
# endpoint into an SSRF probe. Allowlist (not blocklist) so an unrecognised
# host fails at boot rather than silently shipping.
#
# Bridge IP `172.18.0.1` is the docker0 gateway used in current prod to
# reach the cross-stack `whisper-server` on the gpu-01 host. Documented
# inline so the `validator-env-parity` pitfall does not re-bite.
_WHISPER_ALLOWED_HOSTS = frozenset(
    {
        "whisper",
        "whisper-server",
        "localhost",
        "127.0.0.1",
        "172.18.0.1",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8")

    # Zitadel JWKS validation
    zitadel_issuer: str = "https://auth.getklai.com"

    # Database — shared PostgreSQL, scribe schema
    postgres_dsn: str  # postgresql+asyncpg://...

    # whisper-server
    whisper_server_url: str = "http://172.18.0.1:8000"
    stt_provider: str = "whisper_http"
    # Provider label stored in audit column — update to "whisper-gpu" when moving to Phase 3+
    whisper_provider_name: str = "whisper-cpu"

    # Upload limits
    max_upload_mb: int = 100

    # Audio file storage (persists recordings for retry on transcription failure)
    audio_storage_dir: str = "/data/audio"

    # LiteLLM gateway (for AI summarization)
    litellm_base_url: str = "http://litellm:4000"
    litellm_master_key: str = ""
    extraction_model: str = "klai-fast"
    synthesis_model: str = "klai-primary"

    # Knowledge-ingest service (for KB ingestion)
    # SPEC-SEC-INTERNAL-001 REQ-9.4: knowledge_ingest_secret is mandatory.
    # Empty value raises ValidationError at startup. The previous
    # ``if settings.knowledge_ingest_secret: headers[...]`` silent-omit
    # in knowledge_adapter.py is gone; outbound auth is now unconditional.
    knowledge_ingest_url: str = "http://knowledge-ingest:8000"
    knowledge_ingest_secret: str = ""

    # Portal-api identity-verify (SPEC-SEC-AUDIT-2026-04 B1)
    # Used by IdentityAsserter in app.core.auth to replace JWT resourceowner
    # trust with a membership-backed lookup. Both vars are required at startup;
    # the validator below fires before the first request so a missing env var
    # produces a clear error rather than a runtime 403 storm.
    # deploy/docker-compose.yml wires PORTAL_API_INTERNAL_SECRET → PORTAL_INTERNAL_SECRET.
    portal_api_url: str = "http://portal-api:8010"
    portal_internal_secret: str = ""

    log_level: str = "INFO"

    # SPEC-SEC-HYGIENE-001 REQ-35.1 — stranded-row reaper config.
    # 60 min default: longer than every realistic scribe transcription
    # (typical meeting is 30-60 min, with worst-case ~90 min) so the reaper
    # cannot false-reap a still-running job. SPEC suggested 30 min; raised
    # to 60 after considering the false-reap UX risk in `reaper.py`.
    scribe_stranded_timeout_min: int = 60

    # SPEC-SEC-HYGIENE-001 REQ-36.2 — orphan-audio janitor config.
    scribe_janitor_grace_hours: int = 24

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @field_validator("knowledge_ingest_secret", mode="after")
    @classmethod
    def _require_knowledge_ingest_secret(cls, v: str) -> str:
        """SPEC-SEC-INTERNAL-001 REQ-9.4: outbound auth must never be empty.

        scribe-api authenticates the /ingest/v1/document POST with this header;
        an empty value would silently disable that authentication. Fail at
        Settings instantiation instead of at first call.
        """
        if not v:
            raise ValueError(
                "KNOWLEDGE_INGEST_SECRET must be a non-empty string. "
                "scribe-api authenticates outbound /ingest calls with this header. "
                "SPEC-SEC-INTERNAL-001 REQ-9.4."
            )
        return v

    @field_validator("portal_internal_secret", mode="after")
    @classmethod
    def _require_portal_internal_secret(cls, v: str) -> str:
        """SPEC-SEC-AUDIT-2026-04 B1: portal identity-verify must be reachable.

        scribe-api calls /internal/identity/verify on portal-api to derive the
        canonical org_id from the authenticated user's JWT sub. An empty secret
        means every request would be rejected by portal's _require_internal_token
        guard — fail at startup instead of at first request.

        deploy/docker-compose.yml wires PORTAL_API_INTERNAL_SECRET here;
        that key already exists in SOPS for knowledge-mcp, retrieval-api, mailer.
        See validator-env-parity pitfall in .claude/rules/klai/pitfalls/.
        """
        if not v:
            raise ValueError(
                "PORTAL_INTERNAL_SECRET must be a non-empty string. "
                "scribe-api uses this to authenticate portal /internal/identity/verify calls. "
                "SPEC-SEC-AUDIT-2026-04 B1."
            )
        return v

    @field_validator("whisper_server_url", mode="after")
    @classmethod
    def _check_whisper_server_url(cls, v: str) -> str:
        """SSRF landmine guard. See SPEC-SEC-HYGIENE-001 REQ-37.1.

        `/health` is unauthenticated, so an operator typo here turns it
        into an SSRF probe. Reject at boot rather than silently shipping.
        """
        try:
            parsed = urlparse(v)
        except Exception as exc:
            raise ValueError(f"whisper_server_url: unparseable ({exc})") from exc
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"whisper_server_url: scheme must be http or https, got {parsed.scheme!r}"
            )
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            raise ValueError("whisper_server_url: missing hostname")
        if host in _WHISPER_ALLOWED_HOSTS:
            return v
        if host.endswith(".getklai.com"):
            return v
        raise ValueError(
            f"whisper_server_url: hostname {host!r} not in allowlist "
            f"({sorted(_WHISPER_ALLOWED_HOSTS)} or *.getklai.com). "
            f"See SPEC-SEC-HYGIENE-001 REQ-37.1."
        )


settings = Settings()
