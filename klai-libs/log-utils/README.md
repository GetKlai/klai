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
- `SPEC-LOGGING-EXTRACT-001` (this PR) — `setup_logging` +
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
    RequestContextMiddleware,         # FastAPI/Starlette middleware (X-Request-ID, X-Org-ID, X-User-ID)
    HealthCheckAccessFilter,          # logging.Filter dropping uvicorn /health access lines
    DEFAULT_HEALTHCHECK_PATHS,        # frozenset[str] — defaults to {"/health"}
    DEFAULT_THIRD_PARTY_LEVELS,       # dict[str, int] — sqlalchemy.engine + httpx muted to WARNING
)
```

#### `setup_logging(service_name, *, healthcheck_paths=DEFAULT_HEALTHCHECK_PATHS, third_party_levels=None, extra_processors=None) -> None`

Bootstraps structlog + the standard `logging` module to emit JSON to
stdout. Call once at process start, BEFORE any logger is acquired.

```python
# In app/main.py:
from log_utils import setup_logging
setup_logging("portal-api")
```

`service_name` is REQUIRED and positional — there is no default. Each
adopting service passes its own name so log records are filterable by
`service:<name>` in VictoriaLogs.

Optional kwargs:
- `healthcheck_paths`: `Iterable[str]` — paths whose uvicorn access-log
  records the filter SHALL drop. Defaults to `{"/health"}`. Pass an
  explicit iterable to add service-specific paths
  (e.g. `{"/health", "/internal/v1/healthz"}`).
- `third_party_levels`: `dict[str, int] | None` — logger-name → level
  mapping. `None` (default) applies `DEFAULT_THIRD_PARTY_LEVELS`
  (sqlalchemy.engine + httpx muted to WARNING). Pass an explicit dict
  to override.
- `extra_processors`: `list[structlog.types.Processor] | None` — extra
  processors prepended to the shared chain. portal-api uses this for
  its `mask_secret_str` processor.

Output format is controlled by the `LOG_FORMAT` env var: `json`
(default, production) or `console` (dev — readable colour output). The
internal hard-coded log level is INFO; there is intentionally no `level`
parameter — service-level filtering should happen at the contextvar
layer, not by raising the root logger threshold.

If your service has historically called a wrapper that supplied a default
`service_name` (e.g. klai-mailer's `app/logging_setup.py::setup_logging()`
with no arguments), the wrapper is a thin shim for backward-compat with
pre-extraction call sites — audit 2026-05-05 finding A2 noted the
default-argument drift between wrapper and shared lib. New services
should call `log_utils.setup_logging` directly with an explicit
`service_name`. The shared lib intentionally does NOT supply a default
to make adoption-by-grep visible.

#### `RequestContextMiddleware`

FastAPI / Starlette middleware that binds `request_id`, `org_id`, and
optionally `user_id` to structlog context-vars for the duration of each
request, then echoes `X-Request-ID` back on the response.

```python
from fastapi import FastAPI
from log_utils import RequestContextMiddleware

app = FastAPI()
app.add_middleware(RequestContextMiddleware)
```

Optional construction kwargs:
- `service_name`: `str | None` — if provided, re-binds the `service`
  contextvar on every request. Useful when the contextvar set by
  `setup_logging` might be cleared by another middleware. Default `None`
  (no re-bind; rely on setup_logging's bind).
- `bind_user_id`: `bool` — if `True`, also bind `user_id` from the
  `X-User-ID` header when present. Default `False` — most services do
  not have a tenant-scoped user header in their inbound flow.

Reads `X-Request-ID` (set by Caddy / upstream services per
SPEC-INFRA-004) and falls back to `uuid.uuid4()` if absent. The
`X-Request-ID` is echoed back on the response so client-side correlation
works.

#### `HealthCheckAccessFilter(paths=DEFAULT_HEALTHCHECK_PATHS)`

Drops `uvicorn.access` log records for health-check paths so
VictoriaLogs is not flooded with `GET /health 200` noise.
`setup_logging` already wires this in for the canonical paths
(`DEFAULT_HEALTHCHECK_PATHS`); manual install is only needed for
service-specific paths beyond the default.

```python
import logging
from log_utils import HealthCheckAccessFilter

# Drop /health AND /internal/v1/healthz access lines:
logging.getLogger("uvicorn.access").addFilter(
    HealthCheckAccessFilter(paths={"/health", "/internal/v1/healthz"})
)
```

The constructor kwarg is `paths` (not `extra_paths`); the iterable
REPLACES the default. Pass `{*DEFAULT_HEALTHCHECK_PATHS, "/extra"}` to
ADD instead of replace.

Defensive parsing: if uvicorn's record shape changes, the filter
returns True (record passes through) rather than swallowing arbitrary
records — better to leak a healthcheck line than swallow a real
request log.

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
consuming service.

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
