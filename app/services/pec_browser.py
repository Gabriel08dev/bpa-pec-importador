from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from app.credentials import read_credential
from app.logging_safe import mask_identifier, sanitize
from app.models import ReviewItem, Status
from app.services.patient_service import (
    canonical,
    normalize_date,
    normalize_pec_patient,
    reconcile_birth_date,
)
from app.services.pec_attendance import (
    AttendanceDay,
    attendance_source,
    match_attendance,
    parse_attendance_identity,
    parse_attendance_rows,
)
from app.services.pec_filter_controls import click_action_by_text, date_range_inputs, set_checked
from app.services.pec_page import navigate_pec, select_pec_page
from app.services.pec_service import _graphql_value, parse_pec_detail, parse_pec_graphql


class _PecAreaUnavailable(RuntimeError):
    pass


def _route(url: str) -> tuple[str, str, str]:
    parsed = urlsplit(url)
    return parsed.scheme, parsed.netloc, parsed.path.rstrip("/")


def _display_date(value: str) -> str:
    normalized = normalize_date(value)
    return f"{normalized[6:8]}/{normalized[4:6]}/{normalized[:4]}"


def _search_candidates(search_result: Any) -> list[dict[str, Any]]:
    if not isinstance(search_result, dict):
        return []
    content = search_result.get("content")
    if not isinstance(content, list):
        return []
    return [candidate for candidate in content if isinstance(candidate, dict)]


def _local_result_count(search_result: Any, visible_actions: int) -> int:
    """Prioriza os cartões realmente exibidos pelo PEC.

    Uma mesma busca pode disparar mais de uma resposta GraphQL. Em algumas
    versões do PEC, uma resposta vazia chega depois da resposta da base local.
    A interface, porém, continua mostrando o cadastro encontrado.
    """
    if visible_actions > 0:
        return visible_actions
    return len(_search_candidates(search_result))


def consult_pec(
    service: Any,
    items: list[ReviewItem],
    progress: Callable[[str], None],
    *,
    validate_attendance: bool = False,
) -> list[ReviewItem]:
    """Consulta o PEC em modo estritamente somente leitura.

    O Cadastro individual fornece os dados demográficos. A validação opcional
    na Lista de atendimentos permanece isolada e desligada por padrão, pois a
    planilha pode conter produção legítima que ainda não aparece naquela lista.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    eligible = [item for item in items if item.status == Status.REVISAO_INICIAL]
    if not eligible:
        return items

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=service.profile,
            channel="msedge",
            headless=False,
        )
        try:
            page = select_pec_page(context, service.base_url)
            page.set_default_timeout(service.timeout)
            graphql_results: dict[str, Any] = {}

            def collect_graphql(response: Any) -> None:
                if "/api/graphql" not in response.url:
                    return
                try:
                    payload = response.json()
                except Exception:
                    return
                for key in ("cidadaos", "cidadao"):
                    value = _graphql_value(payload, key)
                    if value is not None:
                        if (
                            key == "cidadaos"
                            and _search_candidates(graphql_results.get(key))
                            and not _search_candidates(value)
                        ):
                            # Não deixe uma resposta vazia tardia apagar a
                            # resposta da base local desta mesma pesquisa.
                            continue
                        graphql_results[key] = value

            page.on("response", collect_graphql)

            def visible(locator: Any) -> bool:
                try:
                    return locator.count() > 0 and locator.first.is_visible()
                except Exception:
                    return False

            def visible_count(locator: Any) -> int:
                try:
                    return sum(
                        1 for index in range(locator.count()) if locator.nth(index).is_visible()
                    )
                except Exception:
                    return 0

            def search_field() -> Any:
                return page.locator("input[name='nomeCpfCns']").first

            def attendance_search_field() -> Any:
                return page.get_by_placeholder(
                    re.compile(r"Pesquise por nome, CPF ou data de nascimento", re.I)
                ).first

            password = page.locator("input[type='password']:visible").first
            pec_origin = _route(service.base_url)[:2]

            def fill_login() -> bool:
                credential = read_credential(service.credential_target)
                if not credential or not visible(password) or password.input_value():
                    return False
                username = page.locator(
                    "input[autocomplete='username'],input[name*='usuario' i],input[name*='login' i]"
                ).first
                if not visible(username):
                    username = page.locator("input[type='text'],input[type='email']").first
                if not visible(username):
                    return False
                username.fill(credential[0])
                password.fill(credential[1])
                button = page.get_by_role(
                    "button", name=re.compile("Entrar|Acessar|Login|Continuar", re.I)
                ).first
                if visible(button):
                    button.click()
                return True

            def open_area(path: str, ready: Callable[[], Any], label: str) -> None:
                destination = service.base_url + path
                target_route = _route(destination)

                def go_to_area() -> None:
                    try:
                        navigate_pec(page, destination, wait_until="commit")
                    except PlaywrightError as exc:
                        replacement = re.search(r'is interrupted by another navigation to "([^"]+)"', str(exc))
                        if replacement and _route(replacement.group(1)) == target_route:
                            # A página do PEC assumiu a navegação. Aguarde o formulário
                            # da nova página antes de decidir se é preciso tentar de novo.
                            return
                        raise _PecAreaUnavailable(f"Não foi possível abrir {label} no PEC.") from exc

                def authenticating() -> bool:
                    return visible(password) or bool(
                        re.search(r"/(?:login|autenticacao|oauth)(?:/|$)", urlsplit(page.url).path, re.I)
                    ) or visible(page.get_by_text(
                        re.compile(r"Escolha um acesso para continuar|^Módulo inicial$", re.I)
                    ))

                if not authenticating():
                    go_to_area()
                deadline = time.monotonic() + service.login_timeout / 1000
                ready_by = time.monotonic() + service.timeout / 1000
                attempted_login = False
                waiting_for_login = False
                redirected_to_area = False
                while time.monotonic() < deadline:
                    if page.is_closed():
                        raise _PecAreaUnavailable("A janela automatizada do PEC foi fechada.")
                    current_route = _route(page.url)
                    if current_route == target_route and visible(ready()) and not visible(password):
                        return
                    if authenticating():
                        if not waiting_for_login:
                            progress(
                                "Conclua o login e escolha seu acesso na janela do PEC, se solicitado."
                            )
                            waiting_for_login = True
                        if not attempted_login and current_route[:2] == pec_origin and visible(password):
                            attempted_login = True
                            try:
                                fill_login()
                            except (OSError, PlaywrightError):
                                progress("O acesso salvo não pôde ser usado. Entre manualmente na janela do PEC.")
                    elif (
                        not redirected_to_area
                        and current_route != target_route
                        and current_route[:2] == pec_origin
                        and (
                            current_route[2].endswith("/agenda")
                            or page.locator(f'a[href="{path}"], a[href="{destination}"]').count() > 0
                        )
                    ):
                        go_to_area()
                        redirected_to_area = True
                        ready_by = time.monotonic() + service.timeout / 1000
                    elif (redirected_to_area or not waiting_for_login) and time.monotonic() >= ready_by:
                        raise _PecAreaUnavailable(f"O PEC não terminou de abrir {label}.")
                    page.wait_for_timeout(200)
                raise RuntimeError(f"Tempo de login no PEC esgotado ao abrir {label}")

            def open_citizen() -> None:
                # O e-SUS PEC 5.x publicado em Carangola expõe o Cadastro
                # individual dentro do módulo gestaoCadastros. Instalações
                # antigas usavam /cidadao; a rota nova também mantém o nome
                # de campo nomeCpfCns esperado abaixo.
                open_area(
                    "/gestaoCadastros/cadastro-individual",
                    search_field,
                    "Cadastro individual",
                )

            def open_attendance_list() -> None:
                open_area("/lista-atendimento", attendance_search_field, "Lista de atendimentos")

            list_available = True
            list_configured = False
            list_unavailable_reason = ""
            attendance_cache: dict[tuple[str, str, str], AttendanceDay | None] = {}
            detail_cache: dict[tuple[str, str], tuple[Status, str, dict[str, Any] | None]] = {}

            def configure_attendance_list() -> None:
                nonlocal list_configured
                open_attendance_list()
                filters = page.get_by_role("button", name=re.compile(r"^Filtros", re.I)).first
                if not visible(filters):
                    raise RuntimeError("O botão Filtros não foi localizado na Lista de atendimentos")
                filters.click()

                required = {
                    "Aguardando atendimento": False,
                    "Em atendimento": True,
                    "Em escuta inicial": True,
                    "Atendimento realizado": True,
                    "Não aguardou": False,
                    "Ver somente os meus atendimentos": False,
                    "Ver somente os atendimentos não finalizados": False,
                }
                missing = [
                    label for label, value in required.items()
                    if not set_checked(page, label, value, visible)
                ]
                if missing:
                    raise RuntimeError("Os controles obrigatórios da Lista de atendimentos mudaram")

                visible_inputs = date_range_inputs(page, visible)
                if len(visible_inputs) != 2:
                    raise RuntimeError("O período da Lista de atendimentos não foi localizado com segurança")
                dates = sorted(normalize_date(item.service_date) for item in eligible)
                visible_inputs[0].fill(_display_date(dates[0]))
                visible_inputs[1].fill(_display_date(dates[-1]))
                visible_inputs[1].press("Tab")

                if not click_action_by_text(page, "Filtrar"):
                    raise RuntimeError("O botão Filtrar não foi localizado")
                page.get_by_text(re.compile(r"Período:"), exact=False).first.wait_for(
                    state="visible", timeout=service.timeout
                )
                list_configured = True

            def safe_row_menu(service_date: str) -> Any | None:
                expected_date = _display_date(service_date)
                buttons = page.locator("button")
                candidates: list[Any] = []
                for button_index in range(buttons.count()):
                    button = buttons.nth(button_index)
                    if not button.is_visible():
                        continue
                    marker = " ".join(
                        filter(
                            None,
                            [
                                button.get_attribute("aria-label"),
                                button.get_attribute("title"),
                                button.inner_text(),
                                " ".join(button.locator("mat-icon").all_text_contents()),
                            ],
                        )
                    )
                    marker_key = canonical(marker)
                    if not any(token in marker_key for token in ("MORE VERT", "MAIS OPCOES", "ACOES")):
                        continue
                    nearby = button.evaluate(
                        """(element) => {
                            let node = element.parentElement;
                            while (node && node !== document.body) {
                                const text = (node.innerText || '').trim();
                                if (/\\d{2}\\/\\d{2}\\/\\d{4}/.test(text) && text.length < 1200) return text;
                                node = node.parentElement;
                            }
                            return '';
                        }"""
                    )
                    if expected_date in nearby:
                        candidates.append(button)
                # Um cidadão pode ter várias linhas no mesmo dia. Todas abrem o
                # mesmo resumo diário; a identidade e os atendimentos do modal
                # ainda serão validados antes de confirmar qualquer item.
                return candidates[0] if candidates else None

            def read_attendance_day(item: ReviewItem) -> AttendanceDay | None:
                nonlocal list_available, list_unavailable_reason
                cache_key = (item.patient.cpf, item.informed_birth, item.service_date)
                if cache_key in attendance_cache:
                    return attendance_cache[cache_key]
                if not list_available:
                    return None
                try:
                    if not list_configured or not visible(attendance_search_field()):
                        # Ao voltar do Cadastro individual, o PEC pode descartar
                        # os filtros; por isso eles são reaplicados explicitamente.
                        configure_attendance_list()

                    attendance_search_field().fill(item.patient.cpf)
                    page.wait_for_timeout(800)
                    no_results = page.get_by_text("Nenhum resultado encontrado.", exact=True).first
                    if visible(no_results):
                        attendance_cache[cache_key] = None
                        return None

                    menu = safe_row_menu(item.service_date)
                    if menu is None:
                        attendance_cache[cache_key] = None
                        return None
                    menu.click()
                    safe_action = page.get_by_text("Visualizar atendimentos do dia", exact=True).first
                    if not visible(safe_action):
                        raise RuntimeError("A ação segura Visualizar atendimentos do dia não foi localizada")
                    safe_action.click()

                    heading = page.get_by_role("heading", name="Atendimentos no dia", exact=True).first
                    heading.wait_for(state="visible", timeout=service.timeout)
                    dialog = page.get_by_role("dialog").first
                    if not visible(dialog):
                        dialog = heading.locator("xpath=ancestor::*[contains(@class,'cdk-overlay-pane')][1]")
                    if not visible(dialog):
                        raise RuntimeError("O modal Atendimentos no dia mudou de estrutura")
                    identity = parse_attendance_identity(dialog.inner_text())
                    rows: list[list[str]] = []
                    table_rows = dialog.locator("tbody tr")
                    for row_index in range(table_rows.count()):
                        cells = table_rows.nth(row_index).locator("td").all_text_contents()
                        if cells:
                            rows.append(cells)
                    day = AttendanceDay(patient=identity, visits=parse_attendance_rows(rows))
                    page.keyboard.press("Escape")
                    attendance_cache[cache_key] = day
                    return day
                except Exception as exc:
                    if page.is_closed() or "has been closed" in str(exc).lower():
                        raise
                    list_available = False
                    list_unavailable_reason = sanitize(str(exc)) or exc.__class__.__name__
                    progress("Lista de atendimentos indisponível; usando o Cadastro individual")
                    return None

            def consult_individual(
                item: ReviewItem, identifier: str
            ) -> tuple[Status, str, dict[str, Any] | None]:
                key = (item.patient.cpf, item.informed_birth)
                if key in detail_cache:
                    return detail_cache[key]
                def search_once() -> tuple[Status, str, dict[str, Any] | None]:
                    open_citizen()
                    search_field().fill(identifier)
                    search_button = page.get_by_role(
                        "button", name=re.compile(r"Buscar (?:cadastro|cidad[aã]o)", re.I)
                    ).first
                    graphql_results.pop("cidadaos", None)
                    search_button.wait_for(state="visible", timeout=service.timeout)
                    search_button.click()
                    marker = page.get_by_text(
                        re.compile(
                            r"Cadastros na base local|cidad[aã]o encontrado|Nenhum cidad[aã]o",
                            re.I,
                        )
                    ).first
                    try:
                        marker.wait_for(state="visible", timeout=service.timeout)
                    except PlaywrightTimeoutError as exc:
                        raise RuntimeError("O PEC não concluiu a busca na base local") from exc
                    deadline = time.monotonic() + service.timeout / 1000
                    while "cidadaos" not in graphql_results and time.monotonic() < deadline:
                        page.wait_for_timeout(100)
                    search_result = graphql_results.get("cidadaos")
                    views = page.get_by_role("button", name="Visualizar", exact=True)
                    if views.count() == 0:
                        views = page.get_by_role("link", name="Visualizar", exact=True)
                    if views.count() == 0:
                        views = page.get_by_text("Visualizar", exact=True)
                    no_results = page.get_by_text(
                        re.compile(
                            r"Nenhum (?:cadastro|cidad[aã]o).*(?:encontrado|localizado)",
                            re.I,
                        )
                    ).first
                    # O cabeçalho da seção pode aparecer alguns instantes antes
                    # do cartão. Aguarde a interface estabilizar.
                    ui_deadline = time.monotonic() + min(service.timeout, 3000) / 1000
                    visible_actions = visible_count(views)
                    while (
                        visible_actions == 0
                        and not _search_candidates(search_result)
                        and not visible(no_results)
                        and time.monotonic() < ui_deadline
                    ):
                        page.wait_for_timeout(100)
                        search_result = graphql_results.get("cidadaos")
                        visible_actions = visible_count(views)
                    content = _search_candidates(search_result)
                    count = (
                        0
                        if visible_actions == 0 and visible(no_results)
                        else _local_result_count(search_result, visible_actions)
                    )
                    if count == 0:
                        return (
                            Status.IGNORADO_PEC,
                            "Nenhum cadastro local encontrado no PEC",
                            None,
                        )
                    if count > 1:
                        return (
                            Status.AGUARDANDO_CONFIRMACAO,
                            f"PEC retornou {count} cadastros locais; escolha manual obrigatória",
                            None,
                        )
                    search_patient = content[0] if content else {}
                    graphql_results.pop("cidadao", None)
                    views.first.wait_for(state="visible", timeout=service.timeout)
                    views.first.click()
                    page.wait_for_url(
                        re.compile(
                            r"/(?:cidadao|gestaoCadastros/cadastro-individual)/\d+/informacoes"
                        ),
                        timeout=service.timeout,
                    )
                    deadline = time.monotonic() + service.timeout / 1000
                    while "cidadao" not in graphql_results and time.monotonic() < deadline:
                        page.wait_for_timeout(100)
                    detail_patient = graphql_results.get("cidadao")
                    raw_patient = (
                        parse_pec_graphql(detail_patient, search_patient)
                        if isinstance(detail_patient, dict)
                        else parse_pec_detail(page.locator("body").inner_text())
                    )
                    return Status.PEC_CONFIRMADO, "", raw_patient

                # O CPF/CNS identifica a busca. A data de nascimento é
                # conferida depois nos detalhes retornados pelo PEC; usá-la
                # como filtro aqui faria um arquivo com dia/mês invertidos
                # ocultar um cadastro local válido.
                try:
                    result = search_once()
                except _PecAreaUnavailable:
                    if page.is_closed():
                        raise
                    progress("A abertura do Cadastro individual foi interrompida. Tentando novamente.")
                    result = search_once()
                detail_cache[key] = result
                return result

            progress("Mantenha a janela automatizada do PEC aberta até a conclusão da consulta")
            if validate_attendance:
                progress("PEC em modo somente leitura: a Lista de atendimentos será conferida")
            else:
                progress("PEC em modo somente leitura: consultando somente o Cadastro individual")

            for index, item in enumerate(eligible, 1):
                try:
                    progress(f"Buscando no PEC: paciente {index} de {len(eligible)}")
                    found_in_list = item.sources.get("pec_lookup") == "PEC_LISTA_ATENDIMENTOS"
                    identifier = item.patient.cns if found_in_list and item.patient.cns else item.patient.cpf
                    if validate_attendance:
                        day = read_attendance_day(item)
                        found_in_list = False
                        identifier = item.patient.cpf
                        if day is not None:
                            attendance_match = match_attendance(
                                day,
                                cpf=item.patient.cpf,
                                birth_date=item.informed_birth,
                                service_date=item.service_date,
                                professional=item.professional_name,
                            )
                            if attendance_match.matched:
                                found_in_list = True
                                identifier = day.patient.get("cns") or item.patient.cpf
                            elif attendance_match.ambiguous or "diverge da planilha" in attendance_match.reason:
                                item.status = Status.AGUARDANDO_CONFIRMACAO
                                item.reason = attendance_match.reason
                                item.sources["pec_lookup"] = attendance_source(True)
                                continue
                            else:
                                item.warnings.append(
                                    attendance_match.reason + "; validação feita pelo Cadastro individual"
                                )
                        else:
                            fallback_reason = "Atendimento não localizado na Lista de atendimentos"
                            if not list_available and list_unavailable_reason:
                                fallback_reason = "Lista de atendimentos indisponível"
                            item.warnings.append(fallback_reason + "; usando o Cadastro individual")

                    status, reason, raw_patient = consult_individual(item, identifier)
                    item.status, item.reason = status, reason
                    item.sources["pec_lookup"] = attendance_source(found_in_list)
                    item.sources["pec_detail"] = "PEC_CADASTRO_INDIVIDUAL"
                    if raw_patient is None:
                        continue
                    patient, warnings = normalize_pec_patient(raw_patient)
                    if patient.cpf != item.patient.cpf:
                        item.status = Status.AGUARDANDO_CONFIRMACAO
                        item.reason = "CPF retornado pelo PEC diverge da planilha"
                        continue
                    else:
                        original_birth = normalize_date(item.informed_birth)
                        confirmed_birth, birth_warning = reconcile_birth_date(
                            original_birth, patient.birth_date
                        )
                        if birth_warning:
                            item.sources["birth_date_planilha_original"] = original_birth
                            item.informed_birth = confirmed_birth
                            item.warnings.append(birth_warning)
                    item.patient = patient
                    item.warnings.extend(warnings)
                    item.sources.update({field: "PEC" for field in patient.__dataclass_fields__})
                    item.sources["pec_lookup"] = attendance_source(found_in_list)
                    item.sources["pec_detail"] = "PEC_CADASTRO_INDIVIDUAL"
                    progress(
                        f"Paciente {index} de {len(eligible)} confirmado: {mask_identifier(patient.cpf)}"
                    )
                except Exception as exc:
                    if page.is_closed() or "has been closed" in str(exc).lower():
                        raise RuntimeError(
                            "A janela automatizada do PEC foi fechada antes do fim. "
                            "Abra a consulta novamente e mantenha essa janela aberta."
                        ) from exc
                    item.status = Status.ERRO_TECNICO
                    item.reason = sanitize(str(exc)) or exc.__class__.__name__
                    progress(f"Falha individual no paciente {index}; o lote continuará")
        finally:
            context.close()
    return items
