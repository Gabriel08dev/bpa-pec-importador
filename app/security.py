from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlencode

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse


TOKEN = secrets.token_urlsafe(32)
SESSION_COOKIE = "bpa_system_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

_sessions: dict[str, float] = {}
_sessions_lock = threading.Lock()


def _new_session() -> str:
    now = time.time()
    session_id = secrets.token_urlsafe(32)
    with _sessions_lock:
        for key, expires_at in list(_sessions.items()):
            if expires_at <= now:
                _sessions.pop(key, None)
        _sessions[session_id] = now + SESSION_TTL_SECONDS
    return session_id


def _valid_session(session_id: str) -> bool:
    if not session_id:
        return False
    now = time.time()
    with _sessions_lock:
        expires_at = _sessions.get(session_id, 0)
        if expires_at <= now:
            _sessions.pop(session_id, None)
            return False
    return True


def _without_token(request: Request) -> str:
    query = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key.lower() != "token"
    ]
    target = request.url.path
    if query:
        target += "?" + urlencode(query, doseq=True)
    return target


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=SESSION_TTL_SECONDS,
        path="/",
        secure=False,
        httponly=True,
        samesite="strict",
    )


def _security_headers(response: Response) -> None:
    response.headers.update({
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "form-action 'self'; frame-ancestors 'none'"
        ),
    })


def _denied(message: str) -> Response:
    response = Response(message, status_code=403, media_type="text/plain")
    _security_headers(response)
    return response


class LocalSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        client_host = request.client.host if request.client else ""
        request_host = request.url.hostname or ""
        if client_host not in {"127.0.0.1", "::1"} or request_host not in LOOPBACK_HOSTS:
            return _denied("Acesso negado")

        if request.url.path == "/health":
            response = await call_next(request)
            _security_headers(response)
            return response

        session_id = request.cookies.get(SESSION_COOKIE, "")
        valid_session = _valid_session(session_id)
        supplied = request.query_params.get("token") or request.headers.get("X-Local-Token")
        valid_token = secrets.compare_digest(supplied or "", TOKEN)

        if valid_token and request.method.upper() in {"GET", "HEAD"}:
            response = RedirectResponse(_without_token(request), status_code=303)
            _set_session_cookie(response, _new_session())
            _security_headers(response)
            return response

        if not valid_session and not valid_token:
            return _denied("Token local inválido")

        response = await call_next(request)
        if valid_token and not valid_session:
            _set_session_cookie(response, _new_session())
        _security_headers(response)
        return response
