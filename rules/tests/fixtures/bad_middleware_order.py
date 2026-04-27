# Fixture for SPEC-SEC-CORS-001 AC-18 — synthetic regression that the
# `cors-middleware-must-be-last` rule MUST flag. CORSMiddleware is registered
# BEFORE another `add_middleware` call, which puts it inside (= not outermost).

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware


class AuthMiddleware: ...


app = FastAPI()

# Bad: CORS first, Auth after — CORS ends up INNER to Auth.
app.add_middleware(CORSMiddleware, allow_origins=[])
app.add_middleware(AuthMiddleware)
