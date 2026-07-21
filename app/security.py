from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


TOKEN = secrets.token_urlsafe(32)


class LocalSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.client and request.client.host not in {"127.0.0.1", "::1"}:
            return Response("Acesso negado", status_code=403)
        supplied = request.query_params.get("token") or request.headers.get("X-Local-Token")
        if request.url.path not in {"/health"} and not secrets.compare_digest(supplied or "", TOKEN):
            return Response("Token local inválido", status_code=403)
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'",
        })
        return response
