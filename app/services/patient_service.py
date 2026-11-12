from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any

from app.models import Patient


def digits(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\D", "", str(value))


def normalize_cpf(value: Any) -> str:
    raw = digits(value)
    return raw.zfill(11) if raw and len(raw) <= 11 else raw


def valid_cpf(value: Any) -> bool:
    cpf = normalize_cpf(value)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for size in (9, 10):
        total = sum(int(cpf[i]) * (size + 1 - i) for i in range(size))
        check = (total * 10 % 11) % 10
        if check != int(cpf[size]):
            return False
    return True


def format_cpf(value: Any) -> str:
    cpf = normalize_cpf(value)
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}" if len(cpf) == 11 else cpf


def valid_cns(value: Any) -> bool:
    cns = digits(value)
    return len(cns) == 15 and cns[0] in "125789" and sum(int(n) * w for n, w in zip(cns, range(15, 0, -1))) % 11 == 0


def normalize_date(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    # Algumas planilhas exibem anos com dois algarismos e podem acrescentar
    # uma observação entre parênteses ao valor exportado. Converta esse formato
    # explicitamente para evitar depender da interpretação regional do Excel.
    short_br = re.fullmatch(
        r"(\d{1,2})[/-](\d{1,2})[/-](\d{2}|\d{4})(?:\s*\(.*)?",
        text,
    )
    if short_br:
        day, month, raw_year = short_br.groups()
        year = int(raw_year)
        if len(raw_year) == 2:
            year += 2000 if year <= 68 else 1900
        try:
            return datetime(year, int(month), int(day)).strftime("%Y%m%d")
        except ValueError:
            pass
    # Algumas exportações removem somente a barra entre mês e ano
    # (ex.: 16/092026). A largura fixa torna a recuperação inequívoca.
    missing_year_separator = re.fullmatch(r"(\d{1,2})/(\d{2})(\d{4})", text)
    if missing_year_separator:
        day, month, year = missing_year_separator.groups()
        try:
            return datetime(int(year), int(month), int(day)).strftime("%Y%m%d")
        except ValueError:
            pass
    for pattern in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, pattern).strftime("%Y%m%d")
        except ValueError:
            pass
    # AAAAMMDD só é aceito quando os oito algarismos realmente começam por
    # um ano plausível. O strptime aceita alguns valores de sete algarismos
    # com largura variável (ex.: 3012025), interpretando-os incorretamente
    # como ano 3012 em vez de 03/01/2025.
    if text.isdigit() and len(text) == 8 and 1900 <= int(text[:4]) <= 2100:
        try:
            return datetime.strptime(text, "%Y%m%d").strftime("%Y%m%d")
        except ValueError:
            pass
    # Células/fórmulas sem formatação também podem chegar como DDMMAAAA.
    # Quando o dia começa com zero, a conversão numérica remove esse zero
    # (ex.: 01/02/1996 vira 1021996), por isso o preenchimento à esquerda.
    if text.isdigit() and len(text) in {7, 8}:
        compact_br = text.zfill(8)
        try:
            return datetime.strptime(compact_br, "%d%m%Y").strftime("%Y%m%d")
        except ValueError:
            pass
    # Arquivos exportados pelo Google Planilhas/Excel podem entregar uma
    # data como o número serial interno (ex.: 45881 = 12/08/2025).
    # O intervalo limita a conversão a datas plausíveis e impede confundir
    # identificadores numéricos com datas.
    try:
        serial = float(text.replace(",", "."))
        if serial.is_integer() and 1 <= serial <= 73415:
            converted = datetime(1899, 12, 30) + timedelta(days=int(serial))
            return converted.strftime("%Y%m%d")
    except ValueError:
        pass
    raise ValueError(f"Data inválida: {text}")


def normalize_service_date(value: Any, competence: str) -> tuple[str, str]:
    """Normaliza atendimento e usa a competência para desfazer dia/mês trocados."""
    normalized = normalize_date(value)
    if len(competence) != 6 or not competence.isdigit():
        raise ValueError("Competência inválida ao conferir a data do atendimento")
    if normalized[:6] == competence:
        return normalized, ""
    swapped = swapped_day_month_date(normalized)
    if swapped and swapped[:6] == competence:
        return (
            swapped,
            "Dia e mês do atendimento vieram invertidos no arquivo exportado; "
            "data ajustada pela competência",
        )
    raise ValueError(
        "Data do atendimento fora da competência: "
        f"{normalized[6:8]}/{normalized[4:6]}/{normalized[:4]}"
    )


def reconcile_birth_date(informed: Any, returned_by_pec: Any) -> tuple[str, str]:
    """Confirma a data do PEC após o CPF exato já ter sido validado.

    Retorna a data canônica que deve seguir no lote e um aviso auditável.
    A função não decide identidade: o chamador precisa bloquear previamente
    qualquer CPF divergente.
    """
    informed_date = normalize_date(informed)
    pec_date = normalize_date(returned_by_pec)
    if informed_date == pec_date:
        return pec_date, ""
    if swapped_day_month_date(informed_date) == pec_date:
        return (
            pec_date,
            "Dia e mês do nascimento vieram invertidos no arquivo exportado; "
            "data confirmada pelo PEC",
        )
    return (
        pec_date,
        "Nascimento da planilha diverge do cadastro; data confirmada pelo PEC "
        "após validação exata do CPF",
    )


def swapped_day_month_date(value: Any) -> str:
    """Retorna a alternativa dia/mês apenas quando a data é ambígua.

    Alguns arquivos exportados pelo Google Planilhas exibem ``dd/mm/aaaa``,
    mas gravam internamente a data interpretada como ``mm/dd/aaaa``.
    """
    normalized = normalize_date(value)
    if not normalized:
        return ""
    year, month, day = normalized[:4], normalized[4:6], normalized[6:8]
    if day == month or int(day) > 12:
        return ""
    return f"{year}{day}{month}"


def canonical(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return re.sub(r"[^A-Z0-9]+", " ", "".join(c for c in normalized if not unicodedata.combining(c)).upper()).strip()


def legacy_adjust(value: str | None, limit: int, *, empty_if_long: bool = False) -> tuple[str, bool]:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text, False
    return ("" if empty_if_long else text[:limit]), True


def normalize_pec_patient(raw: dict[str, Any]) -> tuple[Patient, list[str]]:
    warnings: list[str] = []
    name, changed = legacy_adjust(raw.get("name") or raw.get("nome"), 30)
    if changed: warnings.append("Nome completo excede 30 caracteres; campo legado ajustado")
    mother, changed = legacy_adjust(raw.get("mother_name") or raw.get("nome_mae"), 30)
    if changed: warnings.append("Nome da mãe excede 30 caracteres; campo legado ajustado")
    street, changed = legacy_adjust(raw.get("street") or raw.get("logradouro"), 30)
    if changed: warnings.append("Logradouro excede 30 caracteres; campo legado ajustado")
    complement, changed = legacy_adjust(raw.get("complement") or raw.get("complemento"), 10, empty_if_long=True)
    if changed: warnings.append("Complemento excede 10 caracteres; será gravado vazio")
    number, changed = legacy_adjust(raw.get("number") or raw.get("numero"), 5)
    if changed: warnings.append("Número excede 5 caracteres; ajuste explícito aplicado na prévia")
    city = str(raw.get("residence_city") or raw.get("municipio_residencia") or raw.get("municipio") or "")
    nationality_text = canonical(raw.get("nationality") or raw.get("nacionalidade"))
    return Patient(
        name=name, cpf=normalize_cpf(raw.get("cpf")), cns=digits(raw.get("cns")),
        birth_date=normalize_date(raw.get("birth_date") or raw.get("data_nascimento")),
        sex=str(raw.get("sex") or raw.get("sexo") or "")[:1].upper(), race=digits(raw.get("race") or raw.get("raca"))[:2],
        mother_name=mother, father_name=str(raw.get("father_name") or raw.get("nome_pai") or ""),
        nationality="010" if nationality_text in {"BRASILEIRA", "BRASIL"} else "",
        birth_city=str(raw.get("birth_city") or raw.get("municipio_nascimento") or ""), residence_city=city,
        ibge="311330" if canonical(city) == "CARANGOLA" else digits(raw.get("ibge"))[:6],
        zip_code=digits(raw.get("zip_code") or raw.get("cep"))[:8], street_type="081", street=street,
        number=number, complement=complement, neighborhood=legacy_adjust(raw.get("neighborhood") or raw.get("bairro"), 30)[0],
        phone_area=digits(raw.get("phone_area") or raw.get("ddd"))[:2], phone=digits(raw.get("phone") or raw.get("telefone"))[-9:],
        email=legacy_adjust(raw.get("email"), 40)[0], team=str(raw.get("team") or raw.get("equipe") or ""),
        unit=str(raw.get("unit") or raw.get("unidade") or ""),
    ), warnings
