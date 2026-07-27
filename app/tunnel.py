from __future__ import annotations

import ctypes
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

from app.credentials import read_credential, write_credential
from app.main import APP_HOST, APP_PORT, TOKEN_CREDENTIAL_TARGET


TUNNEL_CREDENTIAL_TARGET = "BPA_SYSTEM_CLOUDFLARE_URL"
TUNNEL_URL_PATTERN = re.compile(
    r"https://[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.trycloudflare\.com",
    re.IGNORECASE,
)
STARTUP_TIMEOUT_SECONDS = 40


def _message(text: str, *, error: bool = False) -> None:
    title = "BPA System — Acesso externo"
    flags = 0x10 if error else 0x40
    ctypes.windll.user32.MessageBoxW(None, text, title, flags)


def _server_is_running() -> bool:
    try:
        with socket.create_connection((APP_HOST, APP_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def _wait_for_server(timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _server_is_running():
            return True
        time.sleep(0.2)
    return False


def _start_local_server(root: Path) -> None:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [sys.executable, "-m", "app.main"],
        cwd=root,
        creationflags=creationflags,
        close_fds=True,
    )
    if not _wait_for_server():
        raise RuntimeError("O servidor local não iniciou na porta 51500.")


def _find_cloudflared() -> Path:
    candidates = [
        os.environ.get("BPA_CLOUDFLARED", ""),
        r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
        r"C:\Program Files\cloudflared\cloudflared.exe",
        r"C:\Program Files\Cloudflare\cloudflared.exe",
    ]
    for value in candidates:
        if value and Path(value).is_file():
            return Path(value)
    raise FileNotFoundError(
        "cloudflared.exe não foi localizado. Instale o Cloudflare Tunnel e tente novamente."
    )


def _tunnel_is_alive(url: str) -> bool:
    if not TUNNEL_URL_PATTERN.fullmatch(url.rstrip("/")):
        return False
    try:
        request = urllib.request.Request(url.rstrip("/") + "/", method="HEAD")
        urllib.request.urlopen(request, timeout=4).close()
        return True
    except urllib.error.HTTPError as exc:
        return exc.code in {302, 303, 401, 403, 405}
    except (OSError, urllib.error.URLError, ValueError):
        return False


def _wait_for_tunnel(url: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _tunnel_is_alive(url):
            return True
        time.sleep(1)
    return False


def _open_authenticated(url: str, token: str) -> None:
    query = urlencode({"token": token})
    webbrowser.open(f"{url.rstrip('/')}/?{query}")


def _saved_tunnel_url() -> str:
    credential = read_credential(TUNNEL_CREDENTIAL_TARGET)
    return credential[1].strip().rstrip("/") if credential else ""


def _access_token() -> str:
    credential = read_credential(TOKEN_CREDENTIAL_TARGET)
    if not credential or not credential[1]:
        raise RuntimeError(
            "O token local ainda não está disponível. Abra primeiro o atalho principal do BPA System."
        )
    return credential[1]


def _run_tunnel(root: Path, executable: Path, token: str) -> None:
    log_path = root / "outputs" / "cloudflare_tunnel.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    command = [
        str(executable),
        "tunnel",
        "--no-autoupdate",
        "--url",
        f"http://{APP_HOST}:{APP_PORT}",
    ]
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(
            command,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        opened = False
        assert process.stdout is not None
        for line in process.stdout:
            log.write(line)
            log.flush()
            if not opened:
                match = TUNNEL_URL_PATTERN.search(line)
                if match:
                    url = match.group(0).rstrip("/")
                    write_credential(TUNNEL_CREDENTIAL_TARGET, "BPA System", url)
                    if not _wait_for_tunnel(url):
                        process.terminate()
                        raise RuntimeError(
                            "O endereço foi criado, mas não ficou acessível dentro do tempo esperado."
                        )
                    _open_authenticated(url, token)
                    opened = True
            if not opened and time.monotonic() > deadline:
                process.terminate()
                raise RuntimeError(
                    "O Cloudflare não forneceu um endereço dentro do tempo esperado."
                )
        return_code = process.wait()
        if not opened:
            raise RuntimeError(
                f"O Cloudflare Tunnel foi encerrado antes de criar o endereço (código {return_code})."
            )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    error_log = root / "outputs" / "erro_tunel.log"
    try:
        error_log.unlink(missing_ok=True)
        if not _server_is_running():
            _start_local_server(root)
        token = _access_token()
        saved_url = _saved_tunnel_url()
        if saved_url and _tunnel_is_alive(saved_url):
            _open_authenticated(saved_url, token)
            return
        _run_tunnel(root, _find_cloudflared(), token)
    except Exception as exc:
        error_log.parent.mkdir(parents=True, exist_ok=True)
        error_log.write_text(str(exc), encoding="utf-8")
        _message(str(exc), error=True)


if __name__ == "__main__":
    main()
