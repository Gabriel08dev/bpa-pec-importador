from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.models import ReviewItem
from app.services.patient_service import canonical


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
        "name": name,
        "cpf": _page_value(lines, "CPF"),
        "cns": _page_value(lines, "CNS"),
        "birth_date": _page_value(lines, "Data de nascimento"),
        "sex": _page_value(lines, "Sexo"),
        "race": _page_value(lines, "Raça/cor"),
        "mother_name": _page_value(lines, "Nome da mãe"),
        "father_name": _page_value(lines, "Nome do pai"),
        "nationality": _page_value(lines, "Nacionalidade"),
        "birth_city": _page_value(lines, "Município de nascimento"),
        "residence_city": _page_value(lines, "Município de residência"),
        "zip_code": _page_value(lines, "CEP"),
        "street": _page_value(lines, "Logradouro"),
        "number": _page_value(lines, "Número"),
        "complement": _page_value(lines, "Complemento"),
        "neighborhood": _page_value(lines, "Bairro"),
        "phone_area": phone[:2] if len(phone) >= 10 else "",
        "phone": phone[-9:],
        "email": _page_value(lines, "E-mail"),
        "team": _page_value(lines, "Equipe responsável"),
        "unit": _page_value(lines, "Unidade responsável"),
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
    race_codes = {
        "BRANCA": "01",
        "PRETA": "02",
        "PARDA": "03",
        "AMARELA": "04",
        "INDIGENA": "05",
        "SEM INFORMACAO": "99",
    }
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
    def __init__(
        self,
        base_url: str,
        profile: str,
        credential_target: str,
        timeout_ms: int = 45000,
        login_timeout_ms: int = 300000,
    ):
        self.base_url = base_url.rstrip("/")
        self.profile = profile
        self.credential_target = credential_target
        self.timeout = timeout_ms
        self.login_timeout = login_timeout_ms

    def consult(
        self,
        items: list[ReviewItem],
        progress: Callable[[str], None] = lambda _: None,
        validate_attendance: bool = False,
    ) -> list[ReviewItem]:
        # Import local evita ciclo: o executor reutiliza os parsers deste módulo.
        from app.services.pec_browser import consult_pec

        return consult_pec(self, items, progress, validate_attendance=validate_attendance)

    def discover(
        self,
        start_date: str,
        end_date: str,
        progress: Callable[[str], None] = lambda _: None,
    ) -> list[dict[str, str]]:
        from app.services.pec_discovery import discover_pec_attendances

        return discover_pec_attendances(self, start_date, end_date, progress)
