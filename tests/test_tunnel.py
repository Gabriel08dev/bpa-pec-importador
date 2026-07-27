from pathlib import Path
from urllib.error import HTTPError

from app import tunnel


def test_cloudflared_command_does_not_override_external_host(monkeypatch, tmp_path):
    captured = {}

    class Output:
        def __iter__(self):
            return iter(
                ["https://safe-example.trycloudflare.com\n"]
            )

    class Process:
        stdout = Output()

        def wait(self):
            return 0

    def fake_popen(command, **kwargs):
        captured["command"] = command
        return Process()

    monkeypatch.setattr(tunnel.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(tunnel, "write_credential", lambda *args: None)
    monkeypatch.setattr(tunnel, "_wait_for_tunnel", lambda *args: True)
    monkeypatch.setattr(tunnel, "_open_authenticated", lambda *args: None)
    tunnel._run_tunnel(tmp_path, Path("cloudflared.exe"), "secret")
    assert "--http-host-header" not in captured["command"]
    assert captured["command"][-2:] == ["--url", "http://127.0.0.1:51500"]


def test_authenticated_tunnel_link_contains_token_only_for_initial_exchange(monkeypatch):
    opened = []
    monkeypatch.setattr(tunnel.webbrowser, "open", opened.append)
    tunnel._open_authenticated("https://demo.trycloudflare.com", "a token/value")
    assert opened == [
        "https://demo.trycloudflare.com/?token=a+token%2Fvalue"
    ]


def test_saved_active_tunnel_is_reused(monkeypatch):
    opened = []
    monkeypatch.setattr(tunnel, "_server_is_running", lambda: True)
    monkeypatch.setattr(tunnel, "_access_token", lambda: "secret")
    monkeypatch.setattr(
        tunnel, "_saved_tunnel_url", lambda: "https://demo.trycloudflare.com"
    )
    monkeypatch.setattr(tunnel, "_tunnel_is_alive", lambda url: True)
    monkeypatch.setattr(tunnel, "_open_authenticated", lambda url, token: opened.append((url, token)))
    monkeypatch.setattr(tunnel, "_run_tunnel", lambda *args: (_ for _ in ()).throw(AssertionError()))
    tunnel.main()
    assert opened == [("https://demo.trycloudflare.com", "secret")]


def test_probe_treats_protected_403_as_live(monkeypatch):
    def blocked(*args, **kwargs):
        raise HTTPError("https://demo.trycloudflare.com", 403, "Forbidden", {}, None)

    monkeypatch.setattr(tunnel.urllib.request, "urlopen", blocked)
    assert tunnel._tunnel_is_alive("https://demo.trycloudflare.com")
    assert not tunnel._tunnel_is_alive("https://example.com")


def test_waits_until_tunnel_dns_is_ready(monkeypatch):
    states = iter([False, False, True])
    monkeypatch.setattr(tunnel, "_tunnel_is_alive", lambda url: next(states))
    monkeypatch.setattr(tunnel.time, "sleep", lambda seconds: None)
    assert tunnel._wait_for_tunnel("https://demo.trycloudflare.com", timeout=10)
