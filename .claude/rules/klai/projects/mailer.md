---
paths:
  - "klai-mailer/**"
---
# Mailer Patterns & Pitfalls

## Template rendering is a security boundary (CRIT)

Never interpolate mail templates with `str.format`, `format_map`, or another
unrestricted string evaluator. Keep `app/renderer.py` on Jinja2's
`SandboxedEnvironment` with `StrictUndefined` and HTML autoescape. Internal
template context must first pass the per-template Pydantic schema in
`app/schemas.py`; unknown templates and extra fields are rejected.

When changing templates or renderer setup, run
`tests/test_internal_send_sandbox.py` and the golden-render tests. Test missing
variables, unexpected context, HTML escaping, and forbidden attribute access.

## Zitadel webhook payload — no preferredLanguage field (HIGH)

Zitadel's HTTP notification provider does NOT include `preferredLanguage`
in its webhook payload. Confirmed against Zitadel source:
`internal/notification/types/user_email.go` — the serialised struct
contains only `recipientEmailAddress`, `url`, `templateData`, etc.

**Why this matters:** It is tempting to add a `preferred_language()`
accessor on `ZitadelPayload` assuming the claim is present. Historically
a defensive stub existed on the model that always returned `None` with
a docstring documenting this. The stub was removed in commit 8432601a
(DEAD-022) because it had no callers; the knowledge is preserved here
instead.

**Prevention:** When adding i18n to email rendering, read the locale
from `X-Org-ID` → portal `/internal/org/{id}/preferred-language`
(`portal_client.py:38`) rather than from the Zitadel payload. If
`preferredLanguage` ever appears in a future Zitadel release, check
the webhook payload schema explicitly — never assume it based on the
`ZitadelPayload` Python model shape.

**Affected paths:**
- `klai-mailer/app/models.py` — `ZitadelPayload` struct
- `klai-mailer/app/portal_client.py` — locale lookup fallback
- `klai-mailer/app/renderer.py` — lang-query-param append logic
