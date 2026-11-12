from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from time import sleep
from types import SimpleNamespace

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from app.models import Patient, ReviewItem, Status
from app.services.pec_service import PecService


CADASTRO = "/gestaoCadastros/cadastro-individual"


@pytest.mark.parametrize("interrupted", [False, True], ids=["recarregamento", "navegacao-interrompida"])
def test_citizen_lookup_waits_for_pec_navigation_to_the_same_url(tmp_path, monkeypatch, interrupted):
    from app.services import pec_browser

    navigation_requests = []
    search_requests = []
    cpf = "52998224725"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, body, content_type="text/html; charset=utf-8"):
            content = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            if content:
                self.wfile.write(content)

        def do_GET(self):
            if self.path == CADASTRO:
                navigation_requests.append(self.path)
                if len(navigation_requests) == 1:
                    self.respond(
                        '<script>location.replace(location.href)</script>'
                        '<script src="/pendente.js"></script>'
                    )
                    return
                self.respond(
                    '<div id="form" hidden><input name="nomeCpfCns">'
                    '<button onclick="buscar()">Buscar cadastro</button></div>'
                    '<div id="resultados"></div>'
                    '<script>'
                    'setTimeout(() => { document.querySelector("#form").hidden = false; }, 350);'
                    'async function buscar() {'
                    ' await fetch("/api/graphql?busca", {method:"POST",'
                    ' body:document.querySelector("input").value});'
                    ' document.querySelector("#resultados").innerHTML = '
                    f' \'<h2>Cadastros na base local</h2><a href="{CADASTRO}/1/informacoes">Visualizar</a>\';'
                    '}'
                    '</script>'
                )
            elif self.path == "/pendente.js":
                sleep(1)
                try:
                    self.respond("", "application/javascript")
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    pass  # O próprio PEC cancelou a página inicial.
            elif self.path == f"{CADASTRO}/1/informacoes":
                self.respond(
                    '<h1>Paciente de teste | Sexo Feminino</h1>'
                    f'<p>CPF: {cpf}</p><p>Data de nascimento: 02/01/2000</p>'
                )
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path == "/api/graphql?busca":
                identifier = self.rfile.read(int(self.headers["Content-Length"])).decode()
                search_requests.append(identifier)
                self.respond('{"data":{"cidadaos":{"content":[{"id":1}]}}}', "application/json")
            else:
                self.send_error(404)

    @contextmanager
    def headless_playwright():
        with sync_playwright() as playwright:
            def launch(**options):
                try:
                    return playwright.chromium.launch_persistent_context(**{**options, "headless": True})
                except PlaywrightError as exc:
                    if "distribution 'msedge' is not found" in str(exc):
                        pytest.skip("Microsoft Edge indisponível para simular o PEC")
                    raise

            yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))

    monkeypatch.setattr("playwright.sync_api.sync_playwright", headless_playwright)
    if interrupted:
        # O Chromium pode aguardar o novo documento ou emitir a exceção.
        # Exercita a exceção de forma determinística com a mesma página de teste.
        navigate = pec_browser.navigate_pec
        def interrupted_navigation(page, destination, **kwargs):
            navigate(page, destination, **kwargs)
            raise PlaywrightError(
                f'Page.goto: Navigation to "{destination}" is interrupted by another navigation to "{destination}"'
            )
        monkeypatch.setattr(pec_browser, "navigate_pec", interrupted_navigation)
    item = ReviewItem(
        row=1, original_identifier=cpf, professional_name="", professional_cns="",
        cbo="", cnes="", procedure="", service_date="20261020", informed_birth="20000102",
        original_cid="", patient=Patient(cpf=cpf, birth_date="20000102"),
    )
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            service = PecService(
                f"http://127.0.0.1:{server.server_port}", str(tmp_path / "profile"), "teste",
                timeout_ms=4500, login_timeout_ms=8500,
            )
            service.consult([item])
        finally:
            server.shutdown()
            thread.join()

    assert item.status == Status.PEC_CONFIRMADO, item.reason
    assert navigation_requests == [CADASTRO, CADASTRO]
    assert search_requests == [cpf]
