from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from app.credentials import read_credential
from app.logging_safe import mask_identifier
from app.models import ReviewItem, Status
from app.services.patient_service import canonical, normalize_date, normalize_pec_patient


def _page_value(lines: list[str], label: str) -> str:
    label_key = canonical(label)
    for index, line in enumerate(lines):
        compact = " ".join(line.split())
        key = canonical(compact)
        if key == label_key and index + 1 < len(lines):
            return " ".join(lines[index + 1].split())
        # Nesta versão do PEC, rótulo e valor ficam em spans irmãos sem
        # separador textual (por exemplo, ``CPF529.982.247-25``).
        if key.startswith(label_key):
            value = compact[len(label):].lstrip(" :")
            if value:
                return value
    return ""


def parse_pec_detail(text: str) -> dict[str, str]:
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    name = ""
    for line in lines:
        if "|" in line and "SEXO" in canonical(line):
            name = line.split("|", 1)[0].strip()
            break
    phone = re.sub(r"\D", "", _page_value(lines, "Telefone"))
    return {
        "name": name, "cpf": _page_value(lines, "CPF"), "cns": _page_value(lines, "CNS"),
        "birth_date": _page_value(lines, "Data de nascimento"), "sex": _page_value(lines, "Sexo"),
        "race": _page_value(lines, "Raça/cor"), "mother_name": _page_value(lines, "Nome da mãe"),
        "father_name": _page_value(lines, "Nome do pai"), "nationality": _page_value(lines, "Nacionalidade"),
        "birth_city": _page_value(lines, "Município de nascimento"),
        "residence_city": _page_value(lines, "Município de residência"),
        "zip_code": _page_value(lines, "CEP"), "street": _page_value(lines, "Logradouro"),
        "number": _page_value(lines, "Número"), "complement": _page_value(lines, "Complemento"),
        "neighborhood": _page_value(lines, "Bairro"), "phone_area": phone[:2] if len(phone) >= 10 else "",
        "phone": phone[-9:], "email": _page_value(lines, "E-mail"),
        "team": _page_value(lines, "Equipe responsável"), "unit": _page_value(lines, "Unidade responsável"),
    }


def _graphql_value(payload: Any, key: str) -> Any:
    entries = payload if isinstance(payload, list) else [payload]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        data = entry.get("data")
        if isinstance(data, dict) and key in data:
            return data[key]
    return None


def parse_pec_graphql(detail: dict[str, Any], search: dict[str, Any] | None = None) -> dict[str, str]:
    """Converte os dados que o próprio PEC entregou ao navegador, sem nova requisição."""
    merged = dict(search or {})
    merged.update({key: value for key, value in (detail or {}).items() if value is not None})
    address = merged.get("endereco") if isinstance(merged.get("endereco"), dict) else {}
    birth_place = merged.get("localidadeNascimento") if isinstance(merged.get("localidadeNascimento"), dict) else {}
    municipality = address.get("municipio") if isinstance(address.get("municipio"), dict) else {}
    race = merged.get("racaCor") if isinstance(merged.get("racaCor"), dict) else {}
    nationality = merged.get("nacionalidade") if isinstance(merged.get("nacionalidade"), dict) else {}
    linkage = merged.get("cidadaoVinculacaoEquipe") if isinstance(merged.get("cidadaoVinculacaoEquipe"), dict) else {}
    team = linkage.get("equipe") if isinstance(linkage.get("equipe"), dict) else {}
    unit = linkage.get("unidadeSaude") if isinstance(linkage.get("unidadeSaude"), dict) else {}
    race_codes = {"BRANCA": "01", "PRETA": "02", "PARDA": "03", "AMARELA": "04", "INDIGENA": "05", "SEM INFORMACAO": "99"}
    race_text = canonical(race.get("racaCorDbEnum") or race.get("nome"))
    phone = merged.get("telefoneCelular") or merged.get("telefoneResidencial") or merged.get("telefoneContato") or ""
    return {
        "name": str(merged.get("nome") or merged.get("nomeSocial") or ""),
        "cpf": str(merged.get("cpf") or ""),
        "cns": str(merged.get("cns") or ""),
        "birth_date": str(merged.get("dataNascimento") or ""),
        "sex": str(merged.get("sexo") or ""),
        "race": race_codes.get(race_text, ""),
        "mother_name": str(merged.get("nomeMae") or ""),
        "father_name": str(merged.get("nomePai") or ""),
        "nationality": str(nationality.get("nacionalidadeDbEnum") or ""),
        "birth_city": str(birth_place.get("nome") or ""),
        "residence_city": str(municipality.get("nome") or ""),
        "zip_code": str(address.get("cep") or ""),
        "street": str(address.get("logradouro") or ""),
        "number": str(address.get("numero") or ""),
        "complement": str(address.get("complemento") or ""),
        "neighborhood": str(address.get("bairro") or ""),
        "phone_area": re.sub(r"\D", "", str(phone))[:2],
        "phone": re.sub(r"\D", "", str(phone))[-9:],
        "email": str(merged.get("email") or ""),
        "team": str(team.get("nome") or ""),
        "unit": str(unit.get("nome") or ""),
    }


class PecService:
    def __init__(self, base_url: str, profile: str, credential_target: str, timeout_ms: int = 45000, login_timeout_ms: int = 300000):
        self.base_url = base_url.rstrip("/")
        self.profile = profile
        self.credential_target = credential_target
        self.timeout = timeout_ms
        self.login_timeout = login_timeout_ms

    def consult(self, items: list[ReviewItem], progress: Callable[[str], None] = lambda _: None) -> list[ReviewItem]:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        eligible = [item for item in items if item.status == Status.REVISAO_INICIAL]
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(user_data_dir=self.profile, channel="msedge", headless=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(self.timeout)
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
                            graphql_results[key] = value

                page.on("response", collect_graphql)

                def visible(locator: Any) -> bool:
                    try: return locator.count() > 0 and locator.first.is_visible()
                    except Exception: return False

                def search_field():
                    return page.locator("input[name='nomeCpfCns']").first

                def wait_for_search_field(timeout: int = 10000) -> bool:
                    try:
                        search_field().wait_for(state="visible", timeout=timeout)
                        return True
                    except PlaywrightTimeoutError:
                        return False

                def fill_login() -> bool:
                    credential = read_credential(self.credential_target)
                    password = page.locator("input[type='password']").first
                    if not credential or not visible(password): return False
                    username = page.locator("input[autocomplete='username'],input[name*='usuario' i],input[name*='login' i]").first
                    if not visible(username): username = page.locator("input[type='text'],input[type='email']").first
                    if not visible(username): return False
                    username.fill(credential[0]); password.fill(credential[1])
                    button = page.get_by_role("button", name=re.compile("Entrar|Acessar|Login|Continuar", re.I)).first
                    if visible(button): button.click()
                    return True

                def open_citizen() -> None:
                    destination = self.base_url + "/cidadao"
                    page.goto(destination, wait_until="domcontentloaded")
                    deadline = time.monotonic() + self.login_timeout / 1000
                    attempted = False
                    login_notice_sent = False
                    loading_notice_sent = False
                    while time.monotonic() < deadline:
                        if wait_for_search_field(3000): return
                        if "/agenda" in page.url.lower():
                            page.goto(destination, wait_until="domcontentloaded"); continue
                        password = page.locator("input[type='password']").first
                        if visible(password):
                            if not attempted: attempted = fill_login()
                            if not login_notice_sent:
                                progress("Aguardando login no PEC; após entrar, a área Cidadão será aberta automaticamente")
                                login_notice_sent = True
                        elif not loading_notice_sent:
                            progress("Carregando a área Cadastro individual do PEC")
                            loading_notice_sent = True
                        page.wait_for_timeout(1000)
                    raise RuntimeError("Tempo de login no PEC esgotado")

                open_citizen()
                progress("Mantenha a janela automatizada do PEC aberta até a conclusão da consulta")
                progress("PEC em modo somente leitura: somente a ação Visualizar será usada")
                cache: dict[tuple[str, str], tuple[Status, str, dict[str, Any] | None]] = {}
                for index, item in enumerate(eligible, 1):
                    key = (item.patient.cpf, item.informed_birth)
                    try:
                        if key in cache:
                            status, reason, raw_patient = cache[key]
                        else:
                            progress(f"Buscando no PEC: paciente {index} de {len(eligible)}")
                            page.goto(self.base_url + "/cidadao", wait_until="domcontentloaded")
                            if not wait_for_search_field(): open_citizen()
                            search_field().fill(item.patient.cpf)
                            birth = page.locator("input[placeholder*='dd/mm']").first
                            if not visible(birth): birth = page.get_by_label(re.compile("Data de nascimento", re.I)).first
                            if visible(birth) and item.informed_birth:
                                birth.fill(f"{item.informed_birth[6:8]}/{item.informed_birth[4:6]}/{item.informed_birth[:4]}")
                                birth.press("Escape")
                            search_button = page.get_by_role(
                                "button", name=re.compile(r"Buscar (?:cadastro|cidad[aã]o)", re.I)
                            ).first
                            graphql_results.pop("cidadaos", None)
                            search_button.wait_for(state="visible", timeout=self.timeout)
                            search_button.click()
                            marker = page.get_by_text(re.compile(r"Cadastros na base local|cidad[aã]o encontrado|Nenhum cidad[aã]o", re.I)).first
                            try: marker.wait_for(state="visible", timeout=self.timeout)
                            except PlaywrightTimeoutError as exc: raise RuntimeError("O PEC não concluiu a busca na base local") from exc
                            deadline = time.monotonic() + self.timeout / 1000
                            while "cidadaos" not in graphql_results and time.monotonic() < deadline:
                                page.wait_for_timeout(100)
                            search_result = graphql_results.get("cidadaos")
                            content = search_result.get("content", []) if isinstance(search_result, dict) else []
                            views = page.get_by_text("Visualizar", exact=True)
                            count = len(content) if isinstance(search_result, dict) and isinstance(content, list) else views.count()
                            if count == 0:
                                status, reason, raw_patient = Status.IGNORADO_PEC, "Nenhum cadastro local encontrado no PEC", None
                            elif count > 1:
                                status, reason, raw_patient = Status.AGUARDANDO_CONFIRMACAO, f"PEC retornou {count} cadastros locais; escolha manual obrigatória", None
                            else:
                                search_patient = content[0] if content and isinstance(content[0], dict) else {}
                                graphql_results.pop("cidadao", None)
                                views.first.click()
                                page.wait_for_url(re.compile(r"/cidadao/\d+/informacoes"), timeout=self.timeout)
                                deadline = time.monotonic() + self.timeout / 1000
                                while "cidadao" not in graphql_results and time.monotonic() < deadline:
                                    page.wait_for_timeout(100)
                                detail_patient = graphql_results.get("cidadao")
                                if isinstance(detail_patient, dict):
                                    raw_patient = parse_pec_graphql(detail_patient, search_patient)
                                else:
                                    raw_patient = parse_pec_detail(page.locator("body").inner_text())
                                status, reason = Status.PEC_CONFIRMADO, ""
                            cache[key] = (status, reason, raw_patient)
                        item.status, item.reason = status, reason
                        if raw_patient is None: continue
                        patient, warnings = normalize_pec_patient(raw_patient)
                        if patient.cpf != item.patient.cpf:
                            item.status, item.reason = Status.AGUARDANDO_CONFIRMACAO, "CPF retornado pelo PEC diverge da planilha"
                        elif patient.birth_date != normalize_date(item.informed_birth):
                            item.status, item.reason = Status.AGUARDANDO_CONFIRMACAO, "Nascimento retornado pelo PEC diverge da planilha"
                        else:
                            item.patient = patient
                            item.warnings.extend(warnings)
                            item.sources.update({field: "PEC" for field in patient.__dataclass_fields__})
                            progress(f"Paciente {index} de {len(eligible)} confirmado: {mask_identifier(patient.cpf)}")
                    except Exception as exc:
                        if page.is_closed() or "has been closed" in str(exc).lower():
                            raise RuntimeError(
                                "A janela automatizada do PEC foi fechada antes do fim. "
                                "Abra a consulta novamente e mantenha essa janela aberta."
                            ) from exc
                        item.status, item.reason = Status.ERRO_TECNICO, str(exc) or exc.__class__.__name__
                        progress(f"Falha individual no paciente {index}; o lote continuará")
            finally:
                context.close()
        return items
