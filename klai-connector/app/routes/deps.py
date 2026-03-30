"""Shared FastAPI dependencies for route handlers."""

from fastapi import HTTPException, Request


def get_org_id(request: Request) -> str:
    """Extract org_id from the authenticated request state.

    Raises 401 if org_id is absent (request passed auth middleware without org_id).
    """
    org_id = getattr(request.state, "org_id", None)
    if org_id is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return str(org_id)
