# klai-log-utils

Shared log/secret-handling utilities for Klai Python services. Lives at
`klai-libs/log-utils/` in the monorepo and is wired into each consuming
service via a path dependency:

```toml
[tool.uv.sources]
klai-log-utils = { path = "../klai-libs/log-utils" }
```

Source SPECs:
- `SPEC-SEC-INTERNAL-001` v0.3.0 — secret sanitisation + constant-time compare
- `SPEC-LOGGING-EXTRACT-001` (PR #319) — `setup_logging` +
  `RequestContextMiddleware` + `HealthCheckAccessFilter` extracted from
  the klai-mailer canonical implementation

## Public API

### Secret-handling (SPEC-SEC-INTERNAL-001)

```python
from log_utils import (
    extract_secret_values,    # REQ-4.2 — Settings introspection
    sanitize_from_settings,   # REQ-4.4 — convenience wrapper
    sanitize_response_body,   # REQ-4.1 — strip secrets from upstream bodies
    verify_shared_secret,     # REQ-1.7 — constant-time inbound compare
)
```

### Structured logging (SPEC-LOGGING-EXTRACT-001)

```python
from log_utils import (
    setup_logging,                    # service-bootstrap helper
    RequestContextMiddleware,         # FastAPI middleware (request_id, org_id, user_id)
    HealthCheckAccessFilter,          # logging.Filter dropping health-check noise
    DEFAULT_HEALTHCHECK_PATHS,        # frozenset[str] — canonical /health, /ready
    DEFAULT_THIRD_PARTY_LEVELS,       # mapping logger-name → level for noisy libs
)
```

#### `setup_logging(service_name: str, *, level: str = "INFO", ...) -> None`

Bootstraps structlog + the standard `logging` module to emit JSON to
stdout. Call once at process start, BEFORE any logger is acquired.

```python
# In app/main.py:
from log_utils import setup_logging
setup_logging("portal-api")
```

`service_name` is REQUIRED — there is no default. Each adopting service
passes its own name so log records are filterable by `service:<name>`
in VictoriaLogs.

If your service has historically called a wrapper that supplied a default
(e.g. klai-mailer's `app/logging_setup.py::setup_logging()` with no
arguments), the wrapper is a thin shim that exists for backward-compat
with pre-extraction call sites — audit 2026-05-05 finding A2 noted the
default-argument drift between wrapper and shared lib. New services
should call `log_utils.setup_logging` directly with an explicit
`service_name`. The shared lib intentionally does NOT supply a default
to make adoption-by-grep visible.

#### `RequestContextMiddleware`

FastAPI / Starlette middleware that binds `request_id`, `org_id`, and
`user_id` to structlog context-vars for the duration of each request.

```python
from fastapi import FastAPI
from log_utils import RequestContextMiddleware

app = FastAPI()
app.add_middleware(RequestContextMiddleware)
```

The middleware reads the inbound `X-Request-ID` header (set by Caddy /
upstream services per SPEC-INFRA-004) and falls back to a UUID4 if
absent. It does NOT read auth headers — `org_id` / `user_id` are bound
by the service's own auth middleware AFTER request-context is set.

#### `HealthCheckAccessFilter`

Drops `uvicorn.access` log records for health-check paths so
VictoriaLogs is not flooded with `GET /health 200` noise.
`setup_logging` already wires this in for the canonical paths
(`DEFAULT_HEALTHCHECK_PATHS`); manual install is only needed for
service-specific paths.

```python
import logging
from log_utils import HealthCheckAccessFilter

logging.getLogger("uvicorn.access").addFilter(
    HealthCheckAccessFilter(extra_paths=["/internal/v1/healthz"])
)
```

### `sanitize_response_body(exc_or_response, secret_values=None, *, max_len=512) -> str`

Returns a safe-to-log string from an `httpx.HTTPStatusError`, an
`httpx.Response`, or any duck-typed object exposing `.text` (or
`.response.text`). Every occurrence of any non-empty secret value with
length ≥ 8 is replaced with the literal `<redacted>` BEFORE truncation,
so a secret straddling the 512-byte boundary cannot leave a tail
visible in the output.

Returns `""` on `None`, missing `.text`, or empty body — and emits no
log entry in those cases. When at least one redaction happens, a
`response_body_sanitized` debug entry is emitted via structlog with
`redaction_count` and `original_length`.

### `sanitize_from_settings(settings_obj, exc_or_response, *, max_len=512) -> str`

Combines `extract_secret_values(settings_obj)` and
`sanitize_response_body(...)`. The recommended call shape from each
service's wrapper module.

### `extract_secret_values(settings_obj) -> set[str]`

Walks a Pydantic-Settings instance (`model_fields`) or a plain
attribute object, returning every non-empty string value whose field
name matches the regex `(?i)(secret|password|token|pat|api_key)` and
whose length is ≥ 8 characters. Shorter values are deliberately
skipped to avoid over-redaction of common substrings.

### `verify_shared_secret(header_value, configured) -> bool`

Constant-time comparison via `hmac.compare_digest`. Raises
`ValueError` when `configured` is empty so callers cannot
inadvertently authenticate empty headers. Empty / `None` header values
return False; the comparison still runs against an equal-length dummy
buffer so the timing channel does not leak the configured length.

## Testing

```
cd klai-libs/log-utils
uv pip install -e .[dev]
uv run pytest
```

## Versioning

`0.1.0` is the initial ship — both the SPEC-SEC-INTERNAL-001 secret
helpers and the SPEC-LOGGING-EXTRACT-001 structlog setup ship in the
same package version. Breaking changes to ANY public symbol below
require a major-version bump and a coordinated update in every
consuming service:

Secret-handling (4):
  - `sanitize_response_body`
  - `sanitize_from_settings`
  - `extract_secret_values`
  - `verify_shared_secret`

Structured logging (5):
  - `setup_logging`
  - `RequestContextMiddleware`
  - `HealthCheckAccessFilter`
  - `DEFAULT_HEALTHCHECK_PATHS`
  - `DEFAULT_THIRD_PARTY_LEVELS`

Adding new symbols is a minor-version change.
