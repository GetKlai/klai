# Fixture for SPEC-SEC-CORS-001 AC-18 — second synthetic regression that
# exercises the nested-if branch of the rule. CORSMiddleware is registered
# inside an `if` block while Auth/RequestContext live at the enclosing scope.
# This is the exact pattern klai-connector had today before REQ-6.4 fixed it.

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware


class AuthMiddleware: ...


class RequestContextMiddleware: ...


def create_app(allowed_origins: list[str]) -> FastAPI:
    app = FastAPI()
    if allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=allowed_origins)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(RequestContextMiddleware)
    return app
