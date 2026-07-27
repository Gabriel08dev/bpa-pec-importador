from __future__ import annotations

import os
import socket
import sys
import threading
import time
import traceback
import webbrowser

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.logging_safe import configure_logging
from app.security import LocalSecurityMiddleware, TOKEN
from app.web.routes import build_services, router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load(); settings.prepare()
    app = FastAPI(title="Importador CEAE — PEC e BPA", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = build_services(settings)
    app.add_middleware(LocalSecurityMiddleware)
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(settings.root / "app" / "web" / "static")), name="static")
    return app


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); return sock.getsockname()[1]


def open_browser_when_ready(port: int) -> None:
    for _ in range(150):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                webbrowser.open(f"http://127.0.0.1:{port}/?token={TOKEN}")
                return
        except OSError:
            time.sleep(0.1)


def main() -> None:
    settings = Settings.load()
    error_log = settings.outputs / "erro_inicializacao.log"
    try:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
        error_log.unlink(missing_ok=True)
        configure_logging(); port = free_port(); app = create_app(settings)
        threading.Thread(target=open_browser_when_ready, args=(port,), daemon=True).start()
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except Exception:
        settings.outputs.mkdir(parents=True, exist_ok=True)
        error_log.write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__": main()
