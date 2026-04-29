# klai-log-utils

Shared log/secret-handling utilities for Klai Python services. Lives at
`klai-libs/log-utils/` in the monorepo and is wired into each consuming
service via a path dependency:

```toml
[tool.uv.sources]
klai-log-utils = { path = "../klai-libs/log-utils" }
```

Source SPEC: `SPEC-SEC-INTERNAL-001` v0.3.0.

## Public API

```python
from log_utils import (
    extract_secret_values,    # REQ-4.2 — Settings introspection
    sanitize_from_settings,   # REQ-4.4 — convenience wrapper
    sanitize_response_body,   # REQ-4.1 — strip secrets from upstream bodies
    verify_shared_secret,     # REQ-1.7 — constant-time inbound compare
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

`0.1.0` is the initial ship. The four-symbol public API
(`sanitize_response_body`, `sanitize_from_settings`,
`extract_secret_values`, `verify_shared_secret`) is stable; any
breaking change requires a major-version bump and a coordinated update
in every consuming service.
