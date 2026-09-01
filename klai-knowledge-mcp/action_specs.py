"""Local ActionSpec convention for klai-knowledge-mcp actions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

ACTION_KINDS = frozenset(
    {
        "mcp_tool",
        "connector_action",
        "retrieval_stage",
        "procrastinate_task",
        "litellm_hook_action",
        "http_endpoint",
        "scheduled_task",
        "internal_boundary",
    }
)
AUTH_MODES = frozenset(
    {
        "oauth_user",
        "oauth_client",
        "internal_secret",
        "service_jwt",
        "oauth_or_internal_secret",
        "public_none",
        "worker_internal",
    }
)
ACCESS_MODES = frozenset({"read", "write", "read_write", "none"})
CONCURRENCY_CLASSES = frozenset({"interactive_io", "bulk_io", "llm", "cpu", "operator", "unknown"})
FAILURE_MODES = frozenset({"fail_open", "fail_closed", "fail_loud_degraded"})
MODEL_FACING_KINDS = frozenset({"mcp_tool", "connector_action", "litellm_hook_action"})


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """Metadata describing an action boundary without changing its execution."""

    action_id: str
    owner_service: str
    entrypoint: str
    kind: str
    input: dict[str, Any]
    auth: dict[str, Any]
    effects: dict[str, Any]
    execution: dict[str, Any]
    failure: dict[str, Any]
    telemetry: dict[str, Any]
    result_policy: dict[str, Any] | None
    tests: dict[str, Any]
    docs: dict[str, Any]


_REQUIRED_FIELDS = (
    "action_id",
    "owner_service",
    "entrypoint",
    "kind",
    "input",
    "auth",
    "effects",
    "execution",
    "failure",
    "telemetry",
    "tests",
    "docs",
)


def _required_mapping(payload: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = payload[field]
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return value


def _require_key(payload: Mapping[str, Any], path: str, key: str) -> Any:
    if key not in payload or payload[key] is None or payload[key] == "":
        raise ValueError(f"missing required field: {path}.{key}")
    return payload[key]


def _validate_enum(path: str, value: Any, allowed: frozenset[str]) -> None:
    if value not in allowed:
        raise ValueError(f"invalid {path}: {value!r}")


def validate_action_spec(spec: ActionSpec | Mapping[str, Any]) -> None:
    """Raise ``ValueError`` when an ActionSpec does not satisfy Phase 1 rules."""

    payload: Mapping[str, Any] = asdict(spec) if isinstance(spec, ActionSpec) else spec
    missing = [field for field in _REQUIRED_FIELDS if field not in payload or not payload[field]]
    if missing:
        raise ValueError(f"missing required fields: {', '.join(missing)}")

    input_spec = _required_mapping(payload, "input")
    auth = _required_mapping(payload, "auth")
    effects = _required_mapping(payload, "effects")
    execution = _required_mapping(payload, "execution")
    failure = _required_mapping(payload, "failure")
    telemetry = _required_mapping(payload, "telemetry")
    docs = _required_mapping(payload, "docs")

    _require_key(input_spec, "input", "schema")
    _require_key(auth, "auth", "tenant_identity")
    _require_key(telemetry, "telemetry", "events")
    _require_key(docs, "docs", "spec")

    _validate_enum("kind", payload["kind"], ACTION_KINDS)
    _validate_enum("auth.mode", _require_key(auth, "auth", "mode"), AUTH_MODES)
    _validate_enum("effects.access", _require_key(effects, "effects", "access"), ACCESS_MODES)
    _validate_enum(
        "execution.concurrency_class",
        _require_key(execution, "execution", "concurrency_class"),
        CONCURRENCY_CLASSES,
    )
    _validate_enum("failure.mode", _require_key(failure, "failure", "mode"), FAILURE_MODES)

    if "destructive" not in effects or not isinstance(effects["destructive"], bool):
        raise ValueError("effects.destructive must be an explicit boolean")

    if effects.get("external_calls"):
        timeout_ms = execution.get("timeout_ms")
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
            raise ValueError("execution.timeout_ms must be explicit for HTTP-calling actions")

    if payload["kind"] in MODEL_FACING_KINDS and not payload.get("result_policy"):
        raise ValueError("result_policy is required for model-facing actions")


SEARCH_KNOWLEDGE_SPEC = ActionSpec(
    action_id="knowledge-mcp.search_knowledge",
    owner_service="klai-knowledge-mcp",
    entrypoint="klai-knowledge-mcp/main.py::search_knowledge",
    kind="mcp_tool",
    input={
        "schema": (
            "search_knowledge(query: str, ctx: Context, top_k: int = 8, "
            "scope: Literal['org', 'personal', 'both'] = 'both', "
            "kb_slugs: list[str] | None = None)"
        ),
        "validation": {
            "query.max_chars": 2000,
            "top_k": "clamp[1,15]",
            "scope": ["org", "personal", "both"],
            "kb_slugs": "truncate to 20; ignored for personal scope",
        },
    },
    auth={
        "mode": "oauth_or_internal_secret",
        "caller_identity": "oauth_client | librechat_internal",
        "tenant_identity": {
            "requires_user_id": True,
            "requires_org_id": True,
            "source": "_identify_request(ctx)",
            "verified_by": ("portal-api /internal/mcp-token/verify or /internal/identity/verify"),
        },
    },
    effects={
        "access": "read",
        "destructive": False,
        "external_calls": ["retrieval-api /retrieve"],
    },
    execution={
        "concurrency_class": "interactive_io",
        "timeout_ms": 3000,
        "retry_policy": "none",
        "idempotency": "read_only",
    },
    failure={
        "mode": "fail_closed",
        "user_surface": "ToolError generic bilingual message for retrieval failures",
        "log_fields": ["failure_type", "status_code", "client_id"],
    },
    telemetry={
        "events": [
            "retrieval_log",
            "retrieval-api product_events.knowledge.queried",
            "gap_event when classified",
        ],
        "delivery": "retrieval_log and gap_event are fire-and-forget",
        "correlation": ["org_id", "user_id", "caller_client_id"],
    },
    result_policy={
        "max_items": 15,
        "allowed_fields": ["title", "source_url", "text", "artifact_id", "score", "scope"],
        "text_cap": "retrieval-api policy; MCP does not apply an additional cap",
        "cross_tenant_leak_guard": "retrieval-api RLS plus verified identity",
    },
    tests={
        "unit": [
            "tests/test_search_knowledge.py",
            "tests/test_action_specs.py",
        ],
        "integration": "cross-tenant isolation is owned by retrieval-api tests",
    },
    docs={"spec": ".moai/specs/SPEC-ACTION-CONTRACT-001/spec.md"},
)
