# Widget Integration Runbook

> Embed instructions for the Klai partner widget (chat completions on customer
> sites). Authoritative reference for partner-facing JS snippets and the
> server-side CORS contract.

## TL;DR for partners

Embed snippet — drop into the customer site:

```html
<script>
  (function () {
    fetch('https://my.getklai.com/partner/v1/widget-config?id=<WIDGET_ID>', {
      credentials: 'omit',  // REQUIRED — see CORS contract below
    })
      .then(function (r) { return r.json(); })
      .then(function (cfg) {
        // cfg.session_token is a 1h JWT; use it as Authorization Bearer for chat calls.
        window.__klai_widget_token = cfg.session_token;
      });
  })();
</script>
```

Subsequent chat calls:

```js
fetch('https://my.getklai.com/partner/v1/chat/completions', {
  method: 'POST',
  credentials: 'omit',  // REQUIRED — never include cookies on /partner/v1/*
  headers: {
    'Authorization': 'Bearer ' + window.__klai_widget_token,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({ messages: [...] }),
});
```

## CORS contract — `/partner/v1/*` is cookie-less by design

SPEC-SEC-CORS-001 REQ-2.2 / REQ-3 mandates that `/partner/v1/*` responses
NEVER carry `Access-Control-Allow-Credentials: true`. Browser side, this
means partner widgets MUST use `credentials: 'omit'`. If you use
`credentials: 'include'` the browser will refuse the response because the
server policy is incompatible.

This applies to every partner endpoint:
- `/partner/v1/widget-config` — public widget bootstrap
- `/partner/v1/chat/completions` — chat (Bearer auth, no cookies)
- `/partner/v1/feedback` — feedback (Bearer auth, no cookies)
- `/partner/v1/knowledge` — knowledge query (Bearer auth, no cookies)

## Why it matters

Before SPEC-SEC-CORS-001, portal-api ran a wildcard CORS regex
(`r".*"`) with `Access-Control-Allow-Credentials: true`. A malicious site
could in theory send a cross-origin credentialed request to any portal-api
endpoint while attached to the BFF session cookie of a logged-in tenant
user — including `/api/auth/login` and `/api/signup`, which were on the
CSRF-exempt list because they pre-date the BFF session. The combination
created a probing surface that the audit (Cornelis 2026-04-22, finding #1
+ #17) flagged as critical.

The new contract:
- First-party portal traffic (`https://my.getklai.com`,
  `https://<tenant>.getklai.com`) keeps full credentialed CORS support via
  an explicit allowlist (REQ-1).
- Widget traffic (`/partner/v1/*` from any customer origin in the widget's
  stored `allowed_origins`) uses a separate non-credentialed CORS policy
  (REQ-2). The Bearer token in `Authorization` is the auth, not cookies.

Partners who set `credentials: 'omit'` see zero behavioural change. Partners
who currently use `credentials: 'include'` MUST switch to `'omit'` after
SPEC-SEC-CORS-001 ships, or chat calls will fail browser-side with a
CORS error.

## Per-widget origin allowlist

Each widget has a stored `allowed_origins` list in
`widgets.widget_config.allowed_origins`. The widget-config handler validates
the request `Origin` against this list per request and returns 403 if the
origin is not allowlisted. Customer-side CORS preflight then fails because
the response carries no `Access-Control-Allow-Origin` header for an
unlisted origin.

To add an origin for a partner widget, update the widget row through the
admin portal — there is no env var for this. The list is fail-closed: an
empty `allowed_origins` rejects every request.

## Test the contract

For partners who want to verify the cookie-less constraint locally:

```bash
# 1. Confirm widget-config preflight returns Allow-Origin without Allow-Credentials
curl -i -X OPTIONS 'https://my.getklai.com/partner/v1/widget-config?id=<WIDGET_ID>' \
  -H 'Origin: https://your-customer-site.com' \
  -H 'Access-Control-Request-Method: GET'
# expected: 204 with Access-Control-Allow-Origin echoed and NO
# Access-Control-Allow-Credentials header

# 2. Confirm cookie-only POST is rejected
curl -i -X POST 'https://my.getklai.com/partner/v1/chat/completions' \
  -H 'Cookie: bff_session=<expired-or-foreign>' \
  -H 'Content-Type: application/json' \
  -d '{"messages": []}'
# expected: 401 Unauthorized (no Bearer token)
```

## Cross-references

- SPEC: `.moai/specs/SPEC-SEC-CORS-001/spec.md` REQ-2, REQ-3, REQ-3.3
- Acceptance: `.moai/specs/SPEC-SEC-CORS-001/acceptance.md` AC-9, AC-10, AC-11
- Per-widget origin handler: `klai-portal/backend/app/api/partner.py`
  (`widget_config` GET around line 432, OPTIONS preflight around line 484)
- Origin validation helper: `klai-portal/backend/app/services/widget_auth.py`
  (`origin_allowed`)
- Custom CORS middleware: `klai-portal/backend/app/middleware/klai_cors.py`
  (first-party policy) and `app/main.py` partner CORS middleware
