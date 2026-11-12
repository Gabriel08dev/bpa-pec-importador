from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from time import sleep
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from app.config import ROOT
from app.models import Status
from app.repositories.review_repository import ReviewRepository
from app.services import pec_discovery
from app.services.batch_service import BatchService
from app.services.pec_service import PecService
from app.services.professional_service import ProfessionalRegistry


LOGIN = """
<form method="post" action="/autenticar">
  <input name="cpf" autocomplete="username">
  <input name="senha" type="password">
  <button>Entrar</button>
</form>
"""
LIST = """
<meta charset="utf-8">
<input placeholder="Pesquise por nome, CPF, CNS ou data de nascimento">
<label><input type="checkbox" checked>Ver somente os meus atendimentos</label>
<button onclick="filters.hidden = false">Filtros</button>
<div id="filters" hidden>
  <p>Status do atendimento</p>
  <label><input type="checkbox" checked>Aguardando atendimento</label>
  <label><input type="checkbox">Em atendimento</label>
  <label><input type="checkbox">Em escuta inicial</label>
  <label><input type="checkbox">Atendimento realizado</label>
  <label><input type="checkbox" checked>Não aguardou</label>
  <label><input type="checkbox" checked>Ver somente os atendimentos não finalizados</label>
  <input id="start" aria-label="Data inicial" type="date">
  <input id="end" aria-label="Data final" type="date">
  <button onclick="apply()">Filtrar</button>
</div>
<p><strong>Período:</strong> <span id="period">Hoje</span></p>
<div id="loading" role="progressbar" hidden>Carregando</div>
<div id="results">Nenhum resultado encontrado.</div>
<div id="overlay"></div>
<script>
const mode = '__MODE__';
const display = (date) => date.split('-').reverse().join('/');
const pagination = '<nav style="display:flex; gap:6px; align-items:center">' +
    '<input id="number" value="1" style="width:30px"> <span>de 2</span> ' +
    '<button aria-label="Próxima página" onclick="next()">❯</button></nav>';
function apply() {
    const checks = [...document.querySelectorAll('input[type=checkbox]')].map(input => input.checked);
    if (checks.join() !== 'false,false,true,true,true,false,false') return;
    filters.hidden = true;
    period.textContent = display(start.value) + ' até ' + display(end.value);
    if (mode === 'single') {
        fetch('/carregar-lista').then(response => response.text()).then(() => {
            results.innerHTML = card(1, false) + '<p>1 resultado</p>';
        });
        return;
    }
    loading.hidden = false;
    setTimeout(() => {
        loading.hidden = true;
        results.innerHTML = '<div id="cards">' + card(1) + card(2) + '</div>' + pagination;
    }, 400);
}
function card(index, showDate = true) {
    const day = index <= 2 ? '15/07/2026' : '16/07/2026';
    return '<article>' + (showDate ? day + ' - ' : '') + '08:00 Paciente ' + index +
        ' Atendimento realizado Profissional Exemplo ' +
        '<button aria-label="Mais opções" onclick="openMenu(' + index + ')">⋮</button></article>';
}
function next() {
    number.value = '2';
    if (mode !== 'repeated') setTimeout(() => { cards.innerHTML = card(3) + card(4); }, 400);
}
function openMenu(index) {
    setTimeout(() => {
        overlay.innerHTML = '<div role="menu"><button role="menuitem" ' +
            (index === 2 && mode === 'expired' ? 'disabled ' : '') +
            'onclick="summary(' + index + ')"><span>Visualizar atendimentos do dia</span></button>' +
            '<button role="menuitem" onclick="edit()">Editar</button></div>';
    }, 250);
}
function edit() { fetch('/editar', {method: 'POST'}); }
function summary(index) {
    history.pushState(null, '', '/lista-atendimento/atendimentoDoDia/' + index);
    overlay.innerHTML = '<div role="dialog"><h2>Atendimentos no dia</h2>' +
        '<p>Paciente ' + index + '<br>CPF ' + String(index).padStart(11, '0') +
        '<br>Data de nascimento 02/01/1990</p><table><tbody id="visits"></tbody></table></div>';
    fetch('/resumo/' + index).then(response => response.text()).then(professional => {
        if (professional === 'expire') { location.href = '/login'; return; }
        const day = mode === 'unmatched' && index === 2
            ? '13/07/2026' : index <= 2 ? '15/07/2026' : '16/07/2026';
        setTimeout(() => {
            visits.innerHTML = '<tr><td>' + day + ' 08:00</td><td>08:15</td><td>' +
                professional + '</td><td>CBO de teste</td></tr>';
        }, 400);
    });
}
document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
        overlay.innerHTML = '';
        if (location.pathname.includes('/atendimentoDoDia/')) {
            if (mode === 'growing' && location.pathname.endsWith('/4') && !cards.dataset.expanded) {
                cards.dataset.expanded = 'true';
                cards.insertAdjacentHTML('afterbegin', card(5));
            }
            if (mode === 'growing' && location.pathname.endsWith('/5')) {
                const menu = cards.children[1].querySelector('button');
                menu.removeAttribute('aria-label');
                setTimeout(() => menu.setAttribute('aria-label', 'Mais opções'), 500);
            }
            history.back();
        }
    }
});
</script>
"""


@pytest.fixture
def run_discovery(tmp_path, monkeypatch):
    def run(mode, messages, requests):
        authorized = False
        logins = 0
        expired = False

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, body="", status=200, location=""):
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                if location:
                    self.send_header("Location", location)
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))

            def do_GET(self):
                nonlocal authorized, logins, expired
                path = urlsplit(self.path).path
                if path == "/login":
                    logins += 1
                    # No segundo acesso, simula o operador autenticando manualmente.
                    submit = "<script>setTimeout(() => document.forms[0].requestSubmit(), 300)</script>"
                    self.respond(LOGIN + (submit if logins > 1 else ""))
                elif path == "/selecionar-acesso":
                    self.respond(
                        '<h1>Escolha um acesso para continuar</h1><a href="/entrar">Unidade de teste</a>'
                        '<script>setTimeout(() => document.querySelector("a").click(), 350)</script>'
                    )
                elif path == "/entrar":
                    authorized = True
                    self.respond(status=303, location="/inicio")
                elif path == "/inicio":
                    self.respond('<a href="/lista-atendimento">Lista de atendimentos</a>')
                elif path == "/lista-atendimento":
                    if authorized:
                        self.respond(LIST.replace("__MODE__", mode))
                    else:
                        self.respond(status=303, location="/login")
                elif path == "/carregar-lista":
                    sleep(0.7)
                    self.respond("ok")
                elif path.startswith("/resumo/"):
                    index = int(path.rsplit("/", 1)[1])
                    requests.append(index)
                    if mode == "expired" and index == 4 and not expired:
                        expired = True
                        authorized = False
                        self.respond("expire")
                    else:
                        mismatch = index == 2 and (
                            mode == "unmatched" or (mode == "pending" and requests.count(index) == 1)
                        )
                        self.respond("Outro Profissional" if mismatch else "Profissional Exemplo")
                else:
                    self.respond(status=404)

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.path == "/autenticar":
                    self.respond(status=303, location="/selecionar-acesso")
                else:
                    requests.append(self.path)
                    self.respond(status=403)

        @contextmanager
        def simulated_playwright():
            with sync_playwright() as playwright:
                def launch(**options):
                    try:
                        context = playwright.chromium.launch_persistent_context(**{**options, "headless": True})
                    except PlaywrightError as exc:
                        if "distribution 'msedge' is not found" in str(exc):
                            pytest.skip("Microsoft Edge indisponível para simular o PEC")
                        raise
                    return context

                yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))

        monkeypatch.setattr(pec_discovery, "sync_playwright", simulated_playwright)
        monkeypatch.setattr(pec_discovery, "read_credential", lambda _: ("teste", "senha-ficticia"))
        end = "2026-07-15" if mode == "single" else "2026-07-16"
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                service = PecService(base_url, str(tmp_path / "profile"), "fake", timeout_ms=2500, login_timeout_ms=10000)
                return service.discover("2026-07-15", end, messages.append)
            finally:
                server.shutdown()
                thread.join()

    return run


def test_recovers_session_after_non_agenda_login_and_resumes_same_page(run_discovery):
    messages, requests = [], []

    records = run_discovery("expired", messages, requests)

    assert [int(record["cpf"]) for record in records] == [1, 3, 4]
    assert requests == [1, 3, 4, 4]
    assert any("1 item ignorado" in message for message in messages)
    assert any("retomando após 3 registros examinados" in message for message in messages)


def test_retries_unmatched_card_on_the_same_page(run_discovery):
    messages, requests = [], []

    records = run_discovery("pending", messages, requests)

    assert sorted(int(record["cpf"]) for record in records) == [1, 2, 3, 4]
    assert requests == [1, 2, 2, 3, 4]
    assert any("resumos pendentes da página 1" in message for message in messages)
    assert sum("Aplicando o período" in message for message in messages) == 1


def test_completes_single_page_when_the_pec_only_shows_result_count(run_discovery):
    records = run_discovery("single", [], [])

    assert len(records) == 1
    assert records[0]["service_date"] == "20260715"


def test_reads_late_card_without_reapplying_filters(run_discovery):
    messages, requests = [], []

    records = run_discovery("growing", messages, requests)

    assert [int(record["cpf"]) for record in records] == [1, 2, 3, 4, 5]
    assert requests == [1, 2, 3, 4, 5]
    assert sum("Aplicando o período" in message for message in messages) == 1


def test_sends_card_with_divergent_date_to_review_without_reapplying_filters(run_discovery, tmp_path):
    messages, requests = [], []

    records = run_discovery("unmatched", messages, requests)

    assert requests == [1, 2, 2, 3, 4]
    assert sum("Aplicando o período" in message for message in messages) == 1
    service = BatchService(
        ProfessionalRegistry(ROOT / "data" / "profissionais.example.json"), ReviewRepository(tmp_path / "lotes")
    )
    _, payload = service.import_pec_attendances(records, "202607", "2026-07-15", "2026-07-16")
    pending = payload["items"][-1]
    assert pending["status"] == Status.AGUARDANDO_CONFIRMACAO
    assert pending["reason"] == "O resumo do PEC exibiu atendimentos de outra data. Página 1 da Lista de atendimentos."
    assert pending["patient"]["name"] == "Paciente 2"
    assert not pending["patient"]["cpf"] and not pending["professional_cns"]
    assert pending["service_date"] == "20260715"


def test_does_not_complete_a_batch_when_the_paginator_repeats_its_content(run_discovery):
    messages, requests = [], []

    with pytest.raises(RuntimeError, match="não confirmou os resultados da página 2"):
        run_discovery("repeated", messages, requests)

    assert requests == [1, 2]
    assert not any("Leitura concluída" in message for message in messages)
