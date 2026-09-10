"""The widget-public CORS marker — the route declares, the middleware obeys.

The widget bundle (klai-widget/) runs on the customer's own domain and calls
a subset of the Partner API cross-origin. Each of those routes declares it
here, via ``openapi_extra`` on its decorator, instead of a path list in
``app/middleware/klai_cors.py`` that every new widget endpoint had to be
remembered in — forgetting one 400s its preflight only on customer domains
and silently (the help.voys.nl breakage, PR #1361).

Two modes, because the CORS ownership genuinely differs:

- ``WIDGET_PUBLIC_CORS`` (``"echo"``): the middleware answers the preflight
  and injects ``Access-Control-Allow-Origin`` — the origin is echoed,
  credentials are never allowed. The security boundary on these routes is
  the widget session JWT, not the origin allowlist.
- ``WIDGET_ROUTE_DECIDES_CORS`` (``"route"``): the middleware passes the
  request through untouched; the route builds its own CORS headers. For
  ``/widget-config``, whose per-widget ``allowed_origins`` allowlist needs a
  DB read and which must answer a disallowed origin WITHOUT an
  ``Access-Control-Allow-Origin`` (SPEC-SEC-CORS-001 AC-10) — a generic
  middleware echo cannot express that.

@MX:NOTE: The security behaviour this replaced is pinned by
tests/test_cors_allowlist.py and tests/test_partner_cors.py. Adding a widget
endpoint = mark its route here, never edit the middleware.
"""

from __future__ import annotations

WIDGET_CORS_MARKER = "x-klai-widget-cors"

MODE_ECHO = "echo"
MODE_ROUTE_DECIDES = "route"

WIDGET_PUBLIC_CORS: dict[str, str] = {WIDGET_CORS_MARKER: MODE_ECHO}
WIDGET_ROUTE_DECIDES_CORS: dict[str, str] = {WIDGET_CORS_MARKER: MODE_ROUTE_DECIDES}


def widget_cors_mode_of(route: object) -> str | None:
    """Return the route's declared widget CORS mode, or ``None`` when the
    route did not declare one. Reads only the ``openapi_extra`` attribute —
    present on APIRoute and on FastAPI's effective include contexts, absent
    on containers (Mount, _IncludedRouter) and on routes that did not
    declare one, which are therefore never widget-public."""
    extra = getattr(route, "openapi_extra", None)
    if isinstance(extra, dict):
        mode = extra.get(WIDGET_CORS_MARKER)
        if mode == MODE_ECHO or mode == MODE_ROUTE_DECIDES:
            return str(mode)
    return None
