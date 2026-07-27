from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from app.credentials import read_credential
from app.services.patient_service import canonical, normalize_date
from app.services.pec_attendance import parse_attendance_identity, parse_attendance_rows
from app.services.pec_filter_controls import click_action_by_text, date_range_inputs, set_checked
from app.services.pec_page import navigate_pec, select_pec_page


def _display_date(value: str) -> str:
    normalized = normalize_date(value)
    return f"{normalized[6:8]}/{normalized[4:6]}/{normalized[:4]}"


def _page_transition_confirmed(
    before_page: str,
    after_page: str,
    before_results: str,
    after_results: str,
    expected_page: int,
) -> bool:
    """Confirma numero e conteudo; um clique isolado nunca basta."""
    return (
        before_page.isdigit()
        and after_page == str(expected_page)
        and bool(before_results)
        and bool(after_results)
        and before_results != after_results
    )


def discover_pec_attendances(
    service: Any,
    start_date: str,
    end_date: str,
    progress: Callable[[str], None],
) -> list[dict[str, str]]:
    """Lê atendimentos do período sem alterar qualquer informação no PEC."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    start = normalize_date(start_date)
    end = normalize_date(end_date)
    if not start or not end or start > end:
        raise ValueError("Período inválido para consulta no PEC")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=service.profile,
            channel="msedge",
            headless=False,
        )
        try:
            page = select_pec_page(context, service.base_url)
            page.set_default_timeout(service.timeout)

            def visible(locator: Any) -> bool:
                try:
                    return locator.count() > 0 and locator.first.is_visible()
                except Exception:
                    return False

            def list_search() -> Any:
                return page.get_by_placeholder(
                    re.compile(r"Pesquise por nome, CPF ou data de nascimento", re.I)
                ).first

            def fill_login() -> bool:
                credential = read_credential(service.credential_target)
                password = page.locator("input[type='password']").first
                if not credential or not visible(password):
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
                login = page.get_by_role(
                    "button", name=re.compile("Entrar|Acessar|Login|Continuar", re.I)
                ).first
                if visible(login):
                    login.click()
                return True

            destination = service.base_url + "/lista-atendimento"
            navigate_pec(page, destination)
            deadline = time.monotonic() + service.login_timeout / 1000
            login_attempted = False
            login_notice_sent = False
            while time.monotonic() < deadline:
                if visible(list_search()):
                    break
                password = page.locator("input[type='password']").first
                if visible(password):
                    if not login_attempted:
                        login_attempted = fill_login()
                    if not login_notice_sent:
                        progress("Aguardando login no PEC para abrir a Lista de atendimentos")
                        login_notice_sent = True
                elif "/agenda" in page.url.lower():
                    # O redirecionamento para a Agenda confirma que o login
                    # terminou. Antes disso, não recarregamos a autenticação.
                    navigate_pec(page, destination)
                elif not login_notice_sent:
                    progress(
                        "Conclua o login na janela do PEC; a página não será recarregada durante a autenticação"
                    )
                    login_notice_sent = True
                page.wait_for_timeout(500)
            else:
                raise RuntimeError("Tempo de login no PEC esgotado")

            progress("Aplicando período e situações de atendimento")
            filters = page.get_by_role("button", name=re.compile(r"^Filtros", re.I)).first
            if not visible(filters):
                raise RuntimeError("O botão Filtros não foi localizado")
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
            if any(not set_checked(page, label, value, visible) for label, value in required.items()):
                raise RuntimeError("Os controles obrigatórios da Lista de atendimentos mudaram")

            visible_inputs = date_range_inputs(page, visible)
            if len(visible_inputs) != 2:
                raise RuntimeError("O período não foi localizado com segurança")
            visible_inputs[0].fill(_display_date(start))
            visible_inputs[1].fill(_display_date(end))
            visible_inputs[1].press("Tab")
            if not click_action_by_text(page, "Filtrar"):
                raise RuntimeError("O botão Filtrar não foi localizado")
            page.get_by_text(re.compile(r"Período:"), exact=False).first.wait_for(
                state="visible", timeout=service.timeout
            )

            processed_rows: set[str] = set()
            discovered: list[dict[str, str]] = []
            discovered_keys: set[tuple[str, str, str, str]] = set()
            ignored_rows = 0
            stable_scrolls = 0
            previous_height = -1
            page_number = 1
            known_total_pages = 0
            seen_page_signatures: set[str] = set()

            def result_page_signature() -> str:
                signatures: set[str] = set()
                buttons = page.locator("button")
                for index in range(buttons.count()):
                    button = buttons.nth(index)
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
                    if not any(
                        token in canonical(marker)
                        for token in ("MORE VERT", "MAIS OPCOES", "ACOES")
                    ):
                        continue
                    row_text = button.evaluate(
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
                    signature = canonical(row_text)
                    if signature:
                        signatures.add(signature)
                return "|".join(sorted(signatures))

            def next_safe_menu() -> tuple[Any | None, str]:
                buttons = page.locator("button")
                for index in range(buttons.count()):
                    button = buttons.nth(index)
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
                    if not any(
                        token in canonical(marker)
                        for token in ("MORE VERT", "MAIS OPCOES", "ACOES")
                    ):
                        continue
                    row_text = button.evaluate(
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
                    signature = canonical(row_text)
                    if signature and signature not in processed_rows:
                        return button, signature
                return None, ""

            def paginator_info() -> tuple[str, int, dict[str, float] | None]:
                nonlocal known_total_pages
                total_pages = 0
                total_box = None
                totals = page.get_by_text(re.compile(r"^de\s+\d+$", re.I))
                for total_index in range(totals.count()):
                    total = totals.nth(total_index)
                    if not total.is_visible():
                        continue
                    match = re.fullmatch(r"de\s+(\d+)", total.inner_text().strip(), re.I)
                    box = total.bounding_box()
                    if match and box:
                        total_pages = int(match.group(1))
                        known_total_pages = max(known_total_pages, total_pages)
                        total_box = box
                        break

                current_page = ""
                input_candidates: list[tuple[float, str]] = []
                inputs = page.locator("input")
                for input_index in range(inputs.count()):
                    field = inputs.nth(input_index)
                    if not field.is_visible():
                        continue
                    value = field.input_value().strip()
                    box = field.bounding_box()
                    if not value.isdigit() or not box:
                        continue
                    if total_box:
                        field_center_y = box["y"] + box["height"] / 2
                        total_center_y = total_box["y"] + total_box["height"] / 2
                        if box["x"] < total_box["x"] and abs(field_center_y - total_center_y) <= 45:
                            gap = total_box["x"] - (box["x"] + box["width"])
                            if 0 <= gap <= 180:
                                input_candidates.append((gap, value))
                if input_candidates:
                    current_page = min(input_candidates, key=lambda pair: pair[0])[1]

                if not current_page and total_box:
                    numbers = page.get_by_text(re.compile(r"^\d+$"))
                    candidates: list[tuple[float, str]] = []
                    total_center_y = total_box["y"] + total_box["height"] / 2
                    for number_index in range(numbers.count()):
                        number = numbers.nth(number_index)
                        if not number.is_visible():
                            continue
                        box = number.bounding_box()
                        if not box or box["x"] >= total_box["x"]:
                            continue
                        center_y = box["y"] + box["height"] / 2
                        gap = total_box["x"] - (box["x"] + box["width"])
                        if 0 <= gap <= 160 and abs(center_y - total_center_y) <= 45:
                            candidates.append((gap, number.inner_text().strip()))
                    if candidates:
                        current_page = min(candidates, key=lambda pair: pair[0])[1]
                return current_page, max(total_pages, known_total_pages), total_box

            def paginator_state() -> str:
                return paginator_info()[0]

            def require_paginator() -> tuple[str, int, dict[str, float]]:
                deadline = time.monotonic() + service.timeout / 1000
                while time.monotonic() < deadline:
                    current, total, total_box = paginator_info()
                    if current.isdigit() and total > 0 and total_box:
                        return current, total, total_box
                    if visible(page.get_by_text("Nenhum resultado encontrado.", exact=True).first):
                        raise RuntimeError("A Lista de atendimentos nao possui resultados para paginar")
                    page.wait_for_timeout(150)
                raise RuntimeError(
                    "O total de paginas do PEC nao pôde ser confirmado; a busca foi interrompida para nao gerar lote incompleto"
                )

            def advance_to_next_page() -> bool:
                nonlocal page_number
                before, total_pages, total_box = require_paginator()
                before_results = result_page_signature()
                current_page_number = int(before)
                if current_page_number != page_number:
                    raise RuntimeError(
                        f"O PEC exibe a pagina {current_page_number}, mas a busca esperava a pagina {page_number}; processamento interrompido"
                    )
                if total_pages and current_page_number >= total_pages:
                    return False
                if not before_results:
                    raise RuntimeError(
                        f"O conteudo da pagina {page_number} nao pôde ser confirmado; processamento interrompido"
                    )
                seen_page_signatures.add(before_results)
                clicked = False
                if total_box:
                    total_right = total_box["x"] + total_box["width"]
                    total_center_y = total_box["y"] + total_box["height"] / 2
                    controls = page.locator("button, [role='button'], a")
                    right_controls: list[tuple[float, Any]] = []
                    for control_index in range(controls.count()):
                        control = controls.nth(control_index)
                        if not control.is_visible():
                            continue
                        control_box = control.bounding_box()
                        if not control_box:
                            continue
                        gap = control_box["x"] - total_right
                        center_y = control_box["y"] + control_box["height"] / 2
                        if -4 <= gap <= 90 and abs(center_y - total_center_y) <= 40:
                            right_controls.append((abs(gap), control))
                    if right_controls:
                        candidate = min(right_controls, key=lambda pair: pair[0])[1]
                        disabled = candidate.get_attribute("disabled") is not None
                        disabled = disabled or candidate.get_attribute("aria-disabled") == "true"
                        disabled = disabled or "DISABLED" in canonical(
                            candidate.get_attribute("class") or ""
                        )
                        if not disabled:
                            candidate.evaluate("element => element.click()")
                            clicked = True
                    if not clicked:
                        # Fallback para a versao em que a seta nao possui elemento
                        # acessivel, mas permanece imediatamente a direita de "de N".
                        page.mouse.click(total_right + 18, total_center_y)
                        clicked = True
                if not clicked:
                    clicked = bool(
                        page.evaluate(
                            r"""() => {
                            const visible = (element) => Boolean(element && element.getClientRects().length);
                            const enabled = (element) => !element.disabled
                                && element.getAttribute('aria-disabled') !== 'true'
                                && !/disabled/i.test(String(element.className || ''));
                            const descriptor = (element) => [
                                element.getAttribute('aria-label'),
                                element.getAttribute('title'),
                                element.className,
                                element.innerText,
                                ...[...element.querySelectorAll('mat-icon')].map(icon => icon.textContent),
                            ].filter(Boolean).join(' ').replace(/[_-]+/g, ' ').toUpperCase();
                            const buttons = [...document.querySelectorAll('button')].filter(visible);
                            let next = buttons.find((button) => enabled(button) &&
                                /PR[ÓO]XIMA|SEGUINTE|NEXT|CHEVRON RIGHT|ARROW FORWARD|PAGINATOR NAVIGATION NEXT/.test(descriptor(button)));
                            if (!next) {
                                const pageInputs = [...document.querySelectorAll('input')]
                                    .filter(input => visible(input) && /^\d+$/.test(String(input.value || '').trim()));
                                for (const input of pageInputs) {
                                    let node = input.parentElement;
                                    for (let depth = 0; node && depth < 7; depth++, node = node.parentElement) {
                                        if (!/\bde\s+\d+\b/i.test(String(node.innerText || ''))) continue;
                                        const local = [...node.querySelectorAll('button, [role="button"], a')].filter(visible);
                                        if (local.length >= 2) {
                                            const candidate = local[local.length - 1];
                                            next = enabled(candidate) ? candidate : null;
                                            break;
                                        }
                                    }
                                    if (next) break;
                                }
                            }
                            if (!next) {
                                const totals = [...document.querySelectorAll('span, div')]
                                    .filter(element => visible(element) && /^de\s+\d+$/i.test(String(element.innerText || '').trim()));
                                for (const total of totals) {
                                    let node = total.parentElement;
                                    for (let depth = 0; node && depth < 7; depth++, node = node.parentElement) {
                                        const local = [...node.querySelectorAll('button')].filter(visible);
                                        if (local.length >= 2) {
                                            const candidate = local[local.length - 1];
                                            next = enabled(candidate) ? candidate : null;
                                            break;
                                        }
                                    }
                                    if (next) break;
                                }
                            }
                            if (!next) return false;
                            next.click();
                            return true;
                            }"""
                        )
                    )
                if not clicked:
                    page_inputs = page.locator("input")
                    clickables = page.locator("button, [role='button'], a")
                    for input_index in range(page_inputs.count()):
                        current = page_inputs.nth(input_index)
                        if not current.is_visible() or not current.input_value().strip().isdigit():
                            continue
                        current_box = current.bounding_box()
                        if not current_box:
                            continue
                        nearby: list[tuple[float, Any]] = []
                        current_right = current_box["x"] + current_box["width"]
                        current_center_y = current_box["y"] + current_box["height"] / 2
                        for control_index in range(clickables.count()):
                            control = clickables.nth(control_index)
                            if not control.is_visible():
                                continue
                            control_box = control.bounding_box()
                            if not control_box:
                                continue
                            control_center_y = control_box["y"] + control_box["height"] / 2
                            horizontal_gap = control_box["x"] - current_right
                            if 0 <= horizontal_gap <= 180 and abs(control_center_y - current_center_y) <= 40:
                                nearby.append((horizontal_gap, control))
                        if not nearby:
                            continue
                        candidate = max(nearby, key=lambda pair: pair[0])[1]
                        disabled = candidate.get_attribute("disabled") is not None
                        disabled = disabled or candidate.get_attribute("aria-disabled") == "true"
                        disabled = disabled or "disabled" in canonical(candidate.get_attribute("class") or "")
                        if disabled:
                            continue
                        candidate.evaluate("element => element.click()")
                        clicked = True
                        break
                if not clicked:
                    if total_pages > current_page_number:
                        raise RuntimeError(
                            "Ainda existem páginas de atendimentos, mas o controle de próxima página não foi localizado"
                        )
                    return False
                deadline = time.monotonic() + service.timeout / 1000
                advanced = False
                expected_page = page_number + 1
                while time.monotonic() < deadline:
                    after = paginator_state()
                    after_results = result_page_signature()
                    if _page_transition_confirmed(
                        before,
                        after,
                        before_results,
                        after_results,
                        expected_page,
                    ):
                        if after_results in seen_page_signatures:
                            raise RuntimeError(
                                f"O PEC repetiu o conteudo de uma pagina anterior ao tentar abrir a pagina {expected_page}"
                            )
                        seen_page_signatures.add(after_results)
                        advanced = True
                        break
                    page.wait_for_timeout(150)
                if not advanced:
                    raise RuntimeError(
                        f"O PEC permaneceu na página {page_number}; a busca foi interrompida para não gerar lote incompleto"
                    )
                page_number = expected_page
                progress(
                    f"Página {page_number} de {total_pages} confirmada; "
                    f"{len(processed_rows)} registros examinados e {len(discovered)} atendimentos unicos"
                )
                return True

            while True:
                menu, signature = next_safe_menu()
                if menu is None:
                    if visible(page.get_by_text("Nenhum resultado encontrado.", exact=True).first):
                        break
                    height = page.evaluate("document.documentElement.scrollHeight")
                    page.keyboard.press("End")
                    page.wait_for_timeout(350)
                    new_height = page.evaluate("document.documentElement.scrollHeight")
                    stable_scrolls = stable_scrolls + 1 if new_height == height == previous_height else 0
                    previous_height = new_height
                    if stable_scrolls >= 2:
                        if advance_to_next_page():
                            stable_scrolls = 0
                            previous_height = -1
                            continue
                        break
                    continue

                processed_rows.add(signature)
                menu.click()
                safe_action = page.get_by_text("Visualizar atendimentos do dia", exact=True).first
                if not visible(safe_action):
                    raise RuntimeError("A ação segura Visualizar atendimentos do dia não foi localizada")
                action_disabled = safe_action.get_attribute("aria-disabled") == "true"
                action_disabled = action_disabled or safe_action.get_attribute("disabled") is not None
                action_disabled = action_disabled or "DISABLED" in canonical(
                    safe_action.get_attribute("class") or ""
                )
                if action_disabled:
                    ignored_rows += 1
                    reason = safe_action.get_attribute("title") or (
                        "Visualizar atendimentos do dia está indisponível"
                    )
                    page.keyboard.press("Escape")
                    progress(
                        f"{ignored_rows} itens ignorados; último aviso: {reason}. Seguindo para o próximo"
                    )
                    continue
                safe_action.click()
                heading = page.get_by_role("heading", name="Atendimentos no dia", exact=True).first
                try:
                    heading.wait_for(state="visible", timeout=service.timeout)
                except PlaywrightTimeoutError as exc:
                    raise RuntimeError("O PEC não abriu o resumo de atendimentos") from exc
                dialog = page.get_by_role("dialog").first
                if not visible(dialog):
                    dialog = heading.locator("xpath=ancestor::*[contains(@class,'cdk-overlay-pane')][1]")
                if not visible(dialog):
                    raise RuntimeError("O modal Atendimentos no dia mudou de estrutura")

                patient = parse_attendance_identity(dialog.inner_text())
                rows: list[list[str]] = []
                table_rows = dialog.locator("tbody tr")
                for row_index in range(table_rows.count()):
                    cells = table_rows.nth(row_index).locator("td").all_text_contents()
                    if cells:
                        rows.append(cells)
                visits = parse_attendance_rows(rows)
                for visit in visits:
                    if not start <= visit.service_date <= end:
                        continue
                    key = (
                        patient.get("cpf", ""),
                        patient.get("birth_date", ""),
                        visit.service_date,
                        canonical(visit.professional),
                    )
                    if key in discovered_keys:
                        continue
                    discovered_keys.add(key)
                    discovered.append(
                        {
                            "name": patient.get("name", ""),
                            "cpf": patient.get("cpf", ""),
                            "cns": patient.get("cns", ""),
                            "birth_date": patient.get("birth_date", ""),
                            "service_date": visit.service_date,
                            "professional": visit.professional,
                            "pec_cbo": visit.cbo,
                        }
                    )
                page.keyboard.press("Escape")
                progress(f"Resumo {len(processed_rows)} consultado; {len(discovered)} atendimentos únicos")

            if not processed_rows and not visible(
                page.get_by_text("Nenhum resultado encontrado.", exact=True).first
            ):
                raise RuntimeError("Nenhuma linha consultável foi localizada; a tela do PEC pode ter mudado")
            progress(f"Leitura concluída: {len(discovered)} atendimentos únicos encontrados")
            return discovered
        finally:
            context.close()
