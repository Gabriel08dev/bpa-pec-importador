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
from app.credentials import read_credential, write_credential
from app.logging_safe import configure_logging
from app.security import LocalSecurityMiddleware, TOKEN
from app.web.routes import build_services, router


APP_HOST = "127.0.0.1"
APP_PORT = 51500
TOKEN_CREDENTIAL_TARGET = "BPA_SYSTEM_LOCAL_ACCESS"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load(); settings.prepare()
    app = FastAPI(title="Importador CEAE — PEC e BPA", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = build_services(settings)
    app.add_middleware(LocalSecurityMiddleware)
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(settings.root / "app" / "web" / "static")), name="static")
    return app


def open_browser_when_ready(port: int) -> None:
    for _ in range(150):
        try:
            with socket.create_connection((APP_HOST, port), timeout=0.2):
                webbrowser.open(f"http://localhost:{port}/?token={TOKEN}")
                return
        except OSError:
            time.sleep(0.1)


def server_is_running() -> bool:
    try:
        with socket.create_connection((APP_HOST, APP_PORT), timeout=0.4):
            return True
    except OSError:
        return False


def open_running_server() -> bool:
    if not server_is_running():
        return False
    credential = read_credential(TOKEN_CREDENTIAL_TARGET)
    if not credential or not credential[1]:
        webbrowser.open(f"http://localhost:{APP_PORT}/")
        return True
    webbrowser.open(f"http://localhost:{APP_PORT}/?token={credential[1]}")
    return True


def main() -> None:
    settings = Settings.load()
    error_log = settings.outputs / "erro_inicializacao.log"
    try:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
        error_log.unlink(missing_ok=True)
        if open_running_server():
            return
        write_credential(TOKEN_CREDENTIAL_TARGET, "BPA System", TOKEN)
        configure_logging(); app = create_app(settings)
        threading.Thread(target=open_browser_when_ready, args=(APP_PORT,), daemon=True).start()
        uvicorn.run(app, host=APP_HOST, port=APP_PORT, log_level="warning")
    except Exception:
        settings.outputs.mkdir(parents=True, exist_ok=True)
        error_log.write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__": main()
