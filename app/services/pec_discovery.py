from __future__ import annotations

import re
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, Request, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.credentials import read_credential
from app.services.patient_service import canonical, digits, normalize_date
from app.services.pec_attendance import (
    AttendanceVisit,
    parse_attendance_identity,
    parse_attendance_rows,
)
from app.services.pec_filter_controls import date_range_inputs, set_checked
from app.services.pec_page import navigate_pec, select_pec_page


_PERIOD_DATE = re.compile(r"\b\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})\b")
_PAGINATOR = re.compile(r"^(?:(\d+)\s+)?de\s+(\d+)$", re.I)
_RESULT_COUNT = re.compile(r"^\d+(?:[.,]\d{3})*\s+resultados?$", re.I)
_NEXT_PAGE = re.compile(r"pr[óo]xima|seguinte|next|chevron[-_ ]right|arrow[-_ ]forward", re.I)
_MAX_RECOVERIES = 2

_CARD_TEXT = r"""(button) => {
    if (!button.getClientRects().length || getComputedStyle(button).visibility === 'hidden') return '';
    const marker = [
        button.getAttribute('aria-label'), button.getAttribute('title'), button.innerText,
        ...[...button.querySelectorAll('mat-icon, svg')].flatMap(icon => [
            icon.getAttribute('aria-label'), icon.getAttribute('class'), icon.textContent,
        ]),
    ].filter(Boolean).join(' ').normalize('NFD').replace(/\p{Diacritic}/gu, '')
        .replace(/[_-]+/g, ' ').toUpperCase();
    if (!/\b(MORE VERT|MAIS OPCOES|ACOES|ELLIPSIS VERTICAL)\b/.test(marker)) return '';

    const status = /Em atendimento|Em escuta inicial|Atendimento realizado/i;
    for (let node = button.parentElement; node && !node.matches('body, main, [role=main]');
        node = node.parentElement) {
        const text = (node.innerText || '').trim();
        if (text.length >= 1200) break;
        const dated = /^\d{2}\/\d{2}\/\d{4}\b/.test(text);
        if ((status.test(text) || dated) && /\b\d{2}:\d{2}\b/.test(text)) return text;
    }
    return '';
}"""


class _ListInterrupted(RuntimeError):
    """A leitura pode ser retomada reabrindo a lista e reaplicando os filtros."""


class _CardPending(RuntimeError):
    """O cartão deve ser tentado novamente após a leitura dos demais."""

    def __init__(self, message: str, review: dict[str, str] | None = None):
        super().__init__(message)
        self.review = review


@dataclass(frozen=True)
class _ListRow:
    button_index: int
    text: str

    @property
    def signature(self) -> str:
        return " ".join(self.text.split())


@dataclass(frozen=True)
class _ResultsPage:
    number: int
    total: int
    rows: tuple[_ListRow, ...]
    total_label: Locator | None
    result_count: int | None = None

    @property
    def signature(self) -> tuple[str, ...]:
        return tuple(sorted(row.signature for row in self.rows))


def _display_date(normalized: str) -> str:
    return f"{normalized[6:8]}/{normalized[4:6]}/{normalized[:4]}"


def _page_transition_confirmed(
    before_page: str,
    after_page: str,
    before_results: Sequence[str],
    after_results: Sequence[str],
    expected_page: int,
) -> bool:
    """Confirma número e conteúdo da página após o avanço."""
    return (
        before_page.isdigit()
        and after_page == str(expected_page)
        and bool(before_results)
        and bool(after_results)
        and before_results != after_results
    )


def _list_row_date(row_text: str, single_day: str) -> str:
    displayed_date = _PERIOD_DATE.search(row_text)
    return normalize_date(displayed_date.group()) if displayed_date else single_day


def _visit_for_list_row(
    row_text: str, visits: list[AttendanceVisit], single_day: str = ""
) -> AttendanceVisit | None:
    """Seleciona o atendimento do cartão pelo dia e pelo profissional exibido."""
    service_date = _list_row_date(row_text, single_day)
    row = f" {canonical(row_text)} "
    candidates: list[tuple[int, int, AttendanceVisit]] = []
    for visit in visits:
        professional = canonical(visit.professional)
        if not professional or visit.service_date != service_date:
            continue
        position = row.rfind(f" {professional} ")
        if position >= 0:
            candidates.append((position + len(professional), len(professional), visit))
    if not candidates:
        return None
    # O profissional do atendimento vem depois dos demais nomes do cartão.
    # Quando os nomes terminam no mesmo ponto, o nome completo tem precedência.
    return max(candidates, key=lambda match: match[:2])[2]


def _visible(locator: Locator) -> bool:
    return locator.first.is_visible()


class _AttendanceList:
    def __init__(self, page: Page, service: Any, progress: Callable[[str], None]):
        self.page = page
        self.service = service
        self.progress = progress
        self.destination = service.base_url + "/lista-atendimento"
        self.destination_url = urlsplit(self.destination)
        self.filters = page.get_by_role("button", name=re.compile(r"^Filtros\b", re.I)).first
        self.search = page.get_by_placeholder(re.compile(r"nome.*CPF.*data de nascimento", re.I)).first
        self.password = page.locator("input[type=password]:visible").first
        self.login_attempted = False
        self.buttons = page.locator("button, [role='button']")
        self.busy = page.locator("mat-spinner, mat-progress-spinner, [role=progressbar], [aria-busy=true]")
        self.apply_filters = page.get_by_role("button", name="Filtrar", exact=True).first
        self.period_label = page.get_by_text(re.compile(r"Per[ií]odo\s*:", re.I)).filter(visible=True).first
        self.action = page.get_by_text("Visualizar atendimentos do dia", exact=True).filter(visible=True).first
        self.heading = page.get_by_text("Atendimentos no dia", exact=True).filter(visible=True).first
        self.awaiting_results = False
        self.pending_requests: set[Request] = set()
        page.on("request", self._track_request)
        page.on("requestfinished", self._finish_request)
        page.on("requestfailed", self._finish_request)

    def _same_origin(self, url: str) -> bool:
        parsed = urlsplit(url)
        return (parsed.scheme, parsed.netloc) == (self.destination_url.scheme, self.destination_url.netloc)

    def _track_request(self, request: Request) -> None:
        if self.awaiting_results and request.resource_type in {"fetch", "xhr"} and self._same_origin(request.url):
            self.pending_requests.add(request)

    def _finish_request(self, request: Request) -> None:
        self.pending_requests.discard(request)

    def _on_list(self) -> bool:
        return (
            self._same_origin(self.page.url)
            and urlsplit(self.page.url).path.rstrip("/") == self.destination_url.path
        )

    def _on_day_summary(self) -> bool:
        if not self._same_origin(self.page.url):
            return False
        path = urlsplit(self.page.url).path.rstrip("/")
        prefix = f"{self.destination_url.path}/atendimentoDoDia/"
        detail_id = path.removeprefix(prefix)
        return path.startswith(prefix) and bool(detail_id) and "/" not in detail_id

    def _auth_in_progress(self) -> bool:
        url = urlsplit(self.page.url)
        return (
            self.password.is_visible()
            or url.scheme in {"http", "https"} and not self._same_origin(self.page.url)
            or bool(re.search(r"/(?:login|autenticacao|oauth)(?:/|$)", url.path, re.I))
            or self.page.get_by_text(
                re.compile(r"Escolha um acesso para continuar|^Módulo inicial$", re.I)
            ).filter(visible=True).first.is_visible()
        )

    def _authenticated_menu(self) -> bool:
        return self._same_origin(self.page.url) and (
            urlsplit(self.page.url).path.rstrip("/").endswith("/agenda")
            or self.page.locator("a[href$='/lista-atendimento'], a[href$='/lista-atendimento/']").count() > 0
        )

    def _try_saved_login(self) -> None:
        if self.login_attempted or not self._same_origin(self.page.url) or not self.password.is_visible():
            return
        self.login_attempted = True
        try:
            credential = read_credential(self.service.credential_target)
            if not credential or self.password.input_value():
                return
            username = self.page.locator(
                "input[autocomplete='username']:visible, input[name*='usuario' i]:visible,"
                "input[name*='login' i]:visible, input[name='cpf']:visible"
            ).first
            if not username.is_visible():
                username = self.page.locator("input[type=text]:visible, input[type=email]:visible").first
            login = self.page.get_by_role(
                "button", name=re.compile(r"^(Entrar|Acessar|Login|Continuar)$", re.I)
            ).first
            if not username.is_visible() or not login.is_visible():
                return
            username.fill(credential[0])
            self.password.fill(credential[1])
            login.click()
        except (OSError, PlaywrightError):
            self.progress("O acesso salvo não pôde ser usado. Entre manualmente na janela do PEC.")

    def _open(self) -> None:
        if not self._auth_in_progress():
            navigate_pec(self.page, self.destination, wait_until="commit")
        deadline = time.monotonic() + self.service.login_timeout / 1000
        ready_by = time.monotonic() + self.service.timeout / 1000
        waiting_for_login = False
        returned_to_list = False
        while time.monotonic() < deadline:
            if (
                self._on_list()
                and self.filters.is_visible()
                and self.search.is_visible()
                and not self.password.is_visible()
            ):
                return
            if self._auth_in_progress():
                if not waiting_for_login:
                    self.progress("Conclua o login e escolha seu acesso, se solicitado, na janela do PEC.")
                    waiting_for_login = True
                self._try_saved_login()
            elif not self._on_list() and not returned_to_list and self._authenticated_menu():
                navigate_pec(self.page, self.destination, wait_until="commit")
                returned_to_list = True
                ready_by = time.monotonic() + self.service.timeout / 1000
            elif (returned_to_list or not waiting_for_login) and time.monotonic() >= ready_by:
                raise _ListInterrupted("A Lista de atendimentos não abriu. Confira o acesso escolhido no PEC.")
            self.page.wait_for_timeout(200)
        raise RuntimeError(
            "O tempo para abrir a Lista de atendimentos terminou. "
            "Inicie uma nova busca e conclua o login e a escolha de acesso na janela do PEC."
        )

    def _require_active_list(self, *, allow_summary: bool = False) -> None:
        on_expected_route = self._on_list() or (allow_summary and self._on_day_summary())
        if not on_expected_route or self.password.is_visible():
            raise _ListInterrupted("O PEC saiu da Lista de atendimentos durante a leitura.")

    def _wait(
        self,
        read: Callable[[], Any],
        message: str,
        failure: type[RuntimeError] = _ListInterrupted,
        *,
        allow_summary: bool = False,
    ) -> Any:
        deadline = time.monotonic() + self.service.timeout / 1000
        while True:
            self._require_active_list(allow_summary=allow_summary)
            result = read()
            if result:
                return result
            if time.monotonic() >= deadline:
                raise failure(message)
            self.page.wait_for_timeout(200)

    def _period_matches(self, start: str, end: str) -> bool:
        if self.apply_filters.is_visible() or not self.period_label.is_visible():
            return False
        period = self.period_label.evaluate(
            r"""(element) => {
                for (let depth = 0; element && depth < 4; depth++, element = element.parentElement) {
                    const match = (element.innerText || '').match(/Per[ií]odo\s*:\s*([^|\n]+)/i);
                    if (match) return match[1].trim();
                }
                return '';
            }"""
        )
        if canonical(period) == "HOJE":
            return start == end == date.today().strftime("%Y%m%d")
        dates = [normalize_date(value) for value in _PERIOD_DATE.findall(period)]
        return dates == [start, end] or (start == end and dates == [start])

    def open_filtered(self, start: str, end: str) -> None:
        self.awaiting_results = False
        self.pending_requests.clear()
        self._open()
        self.progress("Aplicando o período e as situações de atendimento.")
        if self.search.input_value():
            self.search.clear()
        my_attendances = "Ver somente os meus atendimentos"
        my_attendances_set = set_checked(self.page, my_attendances, False, _visible)
        self.filters.click()
        self._wait(self.apply_filters.is_visible, "O painel de filtros do PEC não abriu.")
        required = {
            "Aguardando atendimento": False,
            "Em atendimento": True,
            "Em escuta inicial": True,
            "Atendimento realizado": True,
            "Não aguardou": False,
            "Ver somente os atendimentos não finalizados": False,
        }
        if not my_attendances_set:
            required[my_attendances] = False
        for label, wanted in required.items():
            self._wait(
                lambda: set_checked(self.page, label, wanted, _visible),
                f"O filtro '{label}' não pôde ser ajustado.",
            )
        fields = self._wait(
            lambda: date_range_inputs(self.page, _visible),
            "Os dois campos de data da Lista de atendimentos não foram localizados.",
        )
        for field, requested in zip(fields, (start, end), strict=True):
            if field.get_attribute("type") == "date":
                field.fill(f"{requested[:4]}-{requested[4:6]}-{requested[6:8]}")
            else:
                field.fill(_display_date(requested))
            field.press("Tab")
        try:
            applied = [normalize_date(field.input_value()) for field in fields]
        except ValueError:
            applied = []
        if applied != [start, end]:
            raise _ListInterrupted("O PEC não aceitou as datas do período solicitado.")
        self.awaiting_results = True
        self.apply_filters.click()
        self._wait(lambda: self._period_matches(start, end), "O PEC não confirmou o período solicitado.")

    def _paginator(self) -> tuple[int, int, Locator | None]:
        label = self.page.get_by_text(_PAGINATOR).filter(visible=True).first
        if not label.is_visible():
            return 0, 0, None
        current, total = label.evaluate(
            r"""(element) => {
                const match = element.innerText.trim().match(/^(?:(\d+)\s+)?de\s+(\d+)$/i);
                if (!match) return [0, 0];
                if (match[1]) return [Number(match[1]), Number(match[2])];
                const label = element.getBoundingClientRect();
                const nearbyNumber = (elements, value) => [...elements]
                    .map(control => {
                        const box = control.getBoundingClientRect();
                        return {
                            number: value(control).trim(),
                            gap: label.left - box.right,
                            offset: Math.abs((label.top + label.bottom - box.top - box.bottom) / 2),
                            visible: box.width && box.height && getComputedStyle(control).visibility !== 'hidden',
                        };
                    })
                    .filter(item => item.visible && /^\d+$/.test(item.number) &&
                        item.gap >= 0 && item.gap <= 180 && item.offset <= 45)
                    .sort((a, b) => a.gap - b.gap)[0]?.number;
                const current = nearbyNumber(document.querySelectorAll('input'), input => input.value)
                    || nearbyNumber(document.querySelectorAll('span, div, button'), node => node.innerText);
                return [Number(current || 0), Number(match[2])];
            }"""
        )
        return current, total, label

    def _results(self) -> _ResultsPage | None:
        if self.pending_requests or self.busy.filter(visible=True).first.is_visible():
            return None
        texts = self.buttons.evaluate_all(f"(buttons) => buttons.map({_CARD_TEXT})")
        rows = tuple(_ListRow(index, value) for index, value in enumerate(texts) if value)
        current, total, label = self._paginator()
        counts = self.page.get_by_text(_RESULT_COUNT).filter(visible=True).all_text_contents()
        count = int(digits(counts[0])) if len(counts) == 1 else None
        if label is not None:
            return _ResultsPage(current, total, rows, label, count) if rows and 1 <= current <= total else None
        empty = self.page.get_by_text(
            re.compile(r"^Nenhum (?:resultado|atendimento) encontrado\.?$", re.I)
        ).filter(visible=True).first
        if count == len(rows) or (count is None and not rows and empty.is_visible()):
            return _ResultsPage(1, 1, rows, None, count)
        return None

    def wait_for_page(
        self, number: int, previous: _ResultsPage | None = None, *, required_rows: tuple[str, ...] = ()
    ) -> _ResultsPage:
        last_snapshot: tuple[int, tuple[str, ...]] | None = None
        required = Counter(required_rows)
        moved_pointer = False

        def confirmed() -> _ResultsPage | None:
            nonlocal last_snapshot, moved_pointer
            result = self._results()
            if result is None or result.number != number:
                last_snapshot = None
                return None
            if previous is not None and not _page_transition_confirmed(
                str(previous.number), str(result.number), previous.signature, result.signature, number
            ):
                last_snapshot = None
                return None
            signature = result.signature
            if required and required - Counter(signature):
                if not moved_pointer:
                    # Afasta o ponteiro dos cartões após fechar o resumo.
                    self.page.mouse.move(0, 0)
                    moved_pointer = True
                last_snapshot = None
                return None
            snapshot = (result.total, signature)
            if snapshot == last_snapshot:
                return result
            last_snapshot = snapshot
            return None

        try:
            return self._wait(confirmed, f"O PEC não confirmou os resultados da página {number}.")
        finally:
            self.awaiting_results = False
            self.pending_requests.clear()

    def advance(self, results: _ResultsPage) -> _ResultsPage:
        label = results.total_label
        if label is None:
            raise _ListInterrupted("O controle de próxima página deixou de aparecer.")
        box = label.bounding_box()
        if not box:
            raise _ListInterrupted("O controle de próxima página deixou de aparecer.")
        center_y = box["y"] + box["height"] / 2
        right = box["x"] + box["width"]
        container = label.locator(
            "xpath=ancestor::*[not(self::body or self::html or self::main) and "
            "(self::nav or @role='navigation' or contains(@class,'paginat') or "
            "(.//*[self::input or (not(*) and number(normalize-space(.)) > 0)] "
            "and (.//button or .//*[@role='button'])))][1]"
        )
        controls = container.locator("button, [role='button'], a").filter(visible=True)
        named: list[tuple[float, Locator]] = []
        unnamed: list[Locator] = []
        for control in controls.all():
            bounds = control.bounding_box()
            if not bounds or not -4 <= bounds["x"] - right <= 140:
                continue
            if abs(bounds["y"] + bounds["height"] / 2 - center_y) > 40:
                continue
            name = " ".join([
                control.get_attribute("aria-label") or "",
                control.get_attribute("title") or "",
                control.inner_text(),
            ])
            if _NEXT_PAGE.search(name + " " + (control.get_attribute("class") or "")):
                named.append((abs(bounds["x"] - right), control))
            elif not canonical(name):
                unnamed.append(control)
        if named:
            button = min(named, key=lambda item: item[0])[1]
        elif len(unnamed) == 1:
            button = unnamed[0]
        else:
            raise _ListInterrupted("O controle de próxima página não foi localizado.")
        if not button.is_enabled() or "DISABLED" in canonical(button.get_attribute("class")):
            raise _ListInterrupted("O controle de próxima página está indisponível.")
        self.awaiting_results = True
        button.click()
        return self.wait_for_page(results.number + 1, previous=results)

    def read_card(self, row: _ListRow, start: str, end: str) -> dict[str, str] | None:
        menu = self.buttons.nth(row.button_index).element_handle()
        try:
            if menu.evaluate(_CARD_TEXT) != row.text:
                raise _ListInterrupted("A lista mudou ao abrir um cartão.")
            menu.click()
        finally:
            menu.dispose()

        try:
            self._wait(self.action.is_visible, "A opção de visualizar os atendimentos não apareceu.", _CardPending)
            if not self.action.is_enabled() or self.action.evaluate(
                """element => Boolean(element.closest(
                    '[disabled], [aria-disabled=true], .disabled, .mat-menu-item-disabled, .mat-mdc-menu-item-disabled'
                ))"""
            ):
                return None
            self.awaiting_results = True
            self.action.click()
            self._wait(
                self.heading.is_visible, "O resumo do atendimento não abriu.", _CardPending,
                allow_summary=True,
            )
            dialog = self.heading.locator("xpath=ancestor::*[@role='dialog' or contains(@class,'cdk-overlay-pane')][1]")
            self._wait(
                dialog.is_visible, "A estrutura do resumo não foi reconhecida.", _CardPending,
                allow_summary=True,
            )
            table_rows = dialog.locator("tbody tr").filter(
                has=self.page.locator("td:nth-child(3)"), visible=True
            )
            self._wait(
                lambda: not self.pending_requests and table_rows.count(),
                "O resumo abriu sem os dados do atendimento.", _CardPending,
                allow_summary=True,
            )
            patient = parse_attendance_identity(dialog.inner_text())
            rows = table_rows.evaluate_all("(rows) => rows.map(row => [...row.cells].map(cell => cell.innerText))")
            single_day = start if start == end else ""
            try:
                visits = parse_attendance_rows(rows)
                visit = _visit_for_list_row(row.text, visits, single_day)
                card_date = _list_row_date(row.text, single_day)
            except ValueError:
                raise _CardPending("O resumo exibiu uma data inválida.") from None
            if visit is None and visits:
                name = canonical(patient["name"])
                if start <= card_date <= end and name and f" {name} " in f" {canonical(row.text)} ":
                    reason = (
                        "O resumo do PEC exibiu atendimentos de outra data."
                        if all(item.service_date != card_date for item in visits)
                        else "O resumo do PEC não confirmou o profissional do cartão."
                    )
                    raise _CardPending(
                        reason,
                        review={"name": patient["name"], "service_date": card_date, "pec_pending_reason": reason},
                    )
            if visit is None or not (patient["cpf"] or patient["cns"] or patient["name"]):
                raise _CardPending("O cartão não pôde ser associado com segurança ao resumo.")
            if not start <= visit.service_date <= end:
                raise _ListInterrupted("O PEC exibiu um atendimento fora do período aplicado.")
            return {
                **patient,
                "service_date": visit.service_date,
                "professional": visit.professional,
                "pec_cbo": visit.cbo,
            }
        except PlaywrightTimeoutError as exc:
            raise _CardPending("O PEC demorou para responder ao abrir o resumo.") from exc
        finally:
            self.awaiting_results = False
            self.pending_requests.clear()
            self._require_active_list(allow_summary=True)
            self.page.keyboard.press("Escape")
            self._wait(
                lambda: self._on_list() and not self.heading.is_visible() and not self.action.is_visible(),
                "O PEC não voltou à lista após fechar o menu ou resumo.",
                allow_summary=True,
            )


def _read_pages(
    reader: _AttendanceList, start: str, end: str, progress: Callable[[str], None]
) -> list[dict[str, str]]:
    confirmed: dict[int, tuple[str, ...]] = {}
    processed: set[tuple[int, str, int]] = set()
    discovered: list[dict[str, str]] = []
    discovered_keys: set[tuple[str, str, str, str]] = set()
    ignored = 0
    total = 0
    result_count: int | None = None

    for attempt in range(_MAX_RECOVERIES + 1):
        pending: dict[tuple[int, str, int], _CardPending] = {}
        retried_pages: set[int] = set()
        page_number = 1
        try:
            reader.open_filtered(start, end)
            results = reader.wait_for_page(page_number)
            while True:
                page_number = results.number
                previous_signature = confirmed.get(page_number, ())
                if previous_signature and Counter(previous_signature) - Counter(results.signature):
                    results = reader.wait_for_page(page_number, required_rows=previous_signature)
                signature = results.signature
                if total and results.total != total:
                    raise RuntimeError("Os resultados mudaram durante a busca. Inicie uma nova consulta para o período.")
                if results.result_count is not None:
                    if result_count is not None and results.result_count != result_count:
                        raise RuntimeError("A quantidade de resultados mudou durante a busca. Inicie uma nova consulta.")
                    result_count = results.result_count
                if any(previous == signature for number, previous in confirmed.items() if number != page_number):
                    raise _ListInterrupted("O PEC repetiu os resultados de outra página.")
                total = results.total
                confirmed[page_number] = signature
                progress(
                    f"Página {page_number} de {total} confirmada. "
                    f"{len(processed)} registros examinados e {len(discovered)} atendimentos únicos"
                )
                occurrences: Counter[str] = Counter()
                for row in results.rows:
                    row_signature = row.signature
                    occurrences[row_signature] += 1
                    key = (page_number, row_signature, occurrences[row_signature])
                    if key in processed or key in pending:
                        continue
                    try:
                        record = reader.read_card(row, start, end)
                    except _CardPending as exc:
                        pending[key] = exc
                        progress(f"{exc} A busca seguirá com os outros itens da página {page_number}.")
                        continue
                    processed.add(key)
                    if record is None:
                        ignored += 1
                        ignored_label = "item ignorado" if ignored == 1 else "itens ignorados"
                        progress(
                            f"{ignored} {ignored_label}. A opção Visualizar atendimentos do dia está indisponível."
                        )
                    else:
                        cpf, cns = digits(record["cpf"]), digits(record["cns"])
                        record_key = (
                            cpf or cns,
                            record["birth_date"],
                            record["service_date"],
                            canonical(record["professional"]),
                        )
                        if not (cpf or cns) or record_key not in discovered_keys:
                            discovered.append(record)
                            discovered_keys.add(record_key)
                    progress(f"{len(processed)} registros examinados e {len(discovered)} atendimentos únicos.")

                results = reader.wait_for_page(page_number, required_rows=signature)
                if results.signature != signature or results.total != total:
                    # O PEC pode terminar de renderizar outros cartões na mesma página.
                    # A próxima passagem confere que os cartões anteriores continuam presentes.
                    continue
                page_pending = {key for key in pending if key[0] == page_number}
                if page_pending and page_number not in retried_pages:
                    progress(f"Tentando ler novamente os resumos pendentes da página {page_number}.")
                    for key in page_pending:
                        del pending[key]
                    retried_pages.add(page_number)
                    continue
                if page_number == total:
                    break
                results = reader.advance(results)

            examined = len(processed) + len(pending)
            if result_count is not None and examined != result_count:
                raise _ListInterrupted(
                    f"Foram lidos {examined} de {result_count} cartões informados pelo PEC."
                )
            unreadable = sum(error.review is None for error in pending.values())
            if unreadable:
                unresolved = "1 cartão ficou" if unreadable == 1 else f"{unreadable} cartões ficaram"
                raise RuntimeError(
                    f"Foram examinados {examined} cartões em {total} páginas. "
                    f"{unresolved} sem resumo confirmado. "
                    "Confira os avisos da busca antes de criar o lote."
                )
            progress(f"Leitura concluída com {len(discovered)} atendimentos únicos.")
            for key, error in pending.items():
                assert error.review is not None
                discovered.append({
                    **error.review,
                    "pec_pending_reason": f"{error} Página {key[0]} da Lista de atendimentos.",
                })
            if pending:
                progress(f"{len(pending)} cartões com divergência serão incluídos como pendências de revisão.")
            return discovered
        except (_ListInterrupted, PlaywrightError) as exc:
            if reader.page.is_closed():
                raise RuntimeError("A janela automatizada do PEC foi fechada. Abra uma nova busca.") from None
            reason = str(exc) if isinstance(exc, _ListInterrupted) else "A comunicação com o PEC foi interrompida."
            if attempt == _MAX_RECOVERIES:
                raise RuntimeError(
                    f"Busca interrompida na página {page_number}. {reason} "
                    "O lote só pode ser criado após confirmar a leitura completa."
                ) from exc
            progress(f"{reason} Reabrindo a lista e retomando após {len(processed)} registros examinados.")

    raise AssertionError("A busca deve terminar ou informar o motivo da interrupção")


def discover_pec_attendances(
    service: Any,
    start_date: str,
    end_date: str,
    progress: Callable[[str], None],
) -> list[dict[str, str]]:
    """Lê atendimentos do período em modo somente leitura."""
    start, end = normalize_date(start_date), normalize_date(end_date)
    if not start or not end or start > end:
        raise ValueError("Período inválido para consulta no PEC")

    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=service.profile,
                channel="msedge",
                headless=False,
            )
        except PlaywrightError:
            raise RuntimeError(
                "Não foi possível abrir o Edge para acessar o PEC. "
                "Confira se outra consulta já está usando a janela automatizada."
            ) from None
        try:
            page = select_pec_page(context, service.base_url)
            page.set_default_timeout(service.timeout)
            return _read_pages(_AttendanceList(page, service, progress), start, end, progress)
        finally:
            context.close()
