from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse


TOKEN = secrets.token_urlsafe(32)
SESSION_COOKIE = "bpa_system_session"
SESSION_TTL_SECONDS = 8 * 60 * 60

_sessions: dict[str, float] = {}
_sessions_lock = threading.Lock()
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _hostname(value: str) -> str:
    try:
        parsed = urlsplit(f"//{value}")
        if parsed.username or parsed.password:
            return ""
        return (parsed.hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def allowed_hostname(hostname: str) -> bool:
    host = hostname.rstrip(".").lower()
    return host in LOOPBACK_HOSTS or (
        host.endswith(".trycloudflare.com")
        and host != "trycloudflare.com"
    )


def _same_allowed_origin(source_host: str, request_host: str) -> bool:
    if source_host in LOOPBACK_HOSTS and request_host in LOOPBACK_HOSTS:
        return True
    return source_host == request_host


def _request_source_is_valid(request: Request, header_name: str) -> bool:
    value = request.headers.get(header_name)
    if not value:
        return True
    try:
        source = urlsplit(value)
    except ValueError:
        return False
    request_host = _hostname(request.headers.get("host", ""))
    source_host = (source.hostname or "").rstrip(".").lower()
    if not allowed_hostname(source_host) or not _same_allowed_origin(source_host, request_host):
        return False
    if source_host.endswith(".trycloudflare.com") and source.scheme.lower() != "https":
        return False
    return source.scheme.lower() in {"http", "https"}


def _new_session() -> str:
    now = time.time()
    session_id = secrets.token_urlsafe(32)
    with _sessions_lock:
        expired = [key for key, expires_at in _sessions.items() if expires_at <= now]
        for key in expired:
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


def end_session(request: Request) -> None:
    session_id = request.cookies.get(SESSION_COOKIE, "")
    if session_id:
        with _sessions_lock:
            _sessions.pop(session_id, None)


def set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=SESSION_TTL_SECONDS,
        expires=datetime.now(timezone.utc) + timedelta(seconds=SESSION_TTL_SECONDS),
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )


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


def _error_response(message: str) -> Response:
    response = Response(message, status_code=403, media_type="text/plain")
    _security_headers(response)
    return response


class LocalSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        hostname = _hostname(request.headers.get("host", ""))
        if not allowed_hostname(hostname):
            return _error_response("Acesso negado: domínio não autorizado")

        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            if (
                not _request_source_is_valid(request, "origin")
                or not _request_source_is_valid(request, "referer")
                or request.headers.get("sec-fetch-site", "").lower() == "cross-site"
            ):
                return _error_response("Acesso negado: origem não autorizada")

        session_id = request.cookies.get(SESSION_COOKIE, "")
        valid_session = _valid_session(session_id)

        if "token" in request.query_params and valid_session and request.method.upper() in {"GET", "HEAD"}:
            response = RedirectResponse(_without_token(request), status_code=303)
            _security_headers(response)
            return response

        supplied = request.query_params.get("token") or request.headers.get("X-Local-Token")
        valid_token = secrets.compare_digest(supplied or "", TOKEN)

        if valid_token and request.method.upper() in {"GET", "HEAD"}:
            session_id = _new_session()
            response = RedirectResponse(_without_token(request), status_code=303)
            set_session_cookie(response, session_id)
            _security_headers(response)
            return response

        if not valid_session and not valid_token:
            return _error_response("Token ou sessão inválidos")

        request.state.session_id = session_id
        response = await call_next(request)
        if valid_token and not valid_session:
            set_session_cookie(response, _new_session())
        _security_headers(response)
        return response
