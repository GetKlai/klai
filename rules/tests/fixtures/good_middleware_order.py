# Fixture for SPEC-SEC-CORS-001 AC-18 — canonical correct registration order.
# CORSMiddleware is the LAST `add_middleware(...)` call → outermost at runtime →
# CORS headers wrap every response including 401s emitted by inner middlewares.

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware


class AuthMiddleware: ...


app = FastAPI()

# Good: register inner first, CORS last so it becomes the outermost wrapper.
app.add_middleware(AuthMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=[])
