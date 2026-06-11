from fastapi import Request

from app.services.request_ip import resolve_caller_ip


def source_ip(request: Request) -> str:
    return resolve_caller_ip(request)


def public_origin(request: Request) -> str:
    host = request.headers.get("host", "")
    return f"{request.url.scheme}://{host}"
