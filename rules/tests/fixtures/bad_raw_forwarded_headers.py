from fastapi import Request


def source_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for")


def public_origin(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return f"{proto}://{host}"
