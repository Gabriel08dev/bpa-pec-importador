from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from app.services.patient_service import canonical, normalize_cpf, normalize_date


_DATE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
_TIME = re.compile(r"\b(\d{2}:\d{2})\b")
_CPF = re.compile(r"\bCPF\s*[:\-]?\s*([0-9.\-]{11,14})\b", re.I)
_CNS = re.compile(r"\bCNS\s*[:\-]?\s*(\d{15})\b", re.I)
_BIRTH = re.compile(r"Data\s+de\s+nascimento\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})", re.I)


@dataclass(frozen=True)
class AttendanceVisit:
    service_date: str
    start_time: str
    end_time: str
    professional: str
    cbo: str = ""
    attendance_type: str = ""


@dataclass
class AttendanceDay:
    patient: dict[str, str]
    visits: list[AttendanceVisit] = field(default_factory=list)


@dataclass(frozen=True)
class AttendanceMatch:
    matched: bool
    reason: str = ""
    ambiguous: bool = False


def _first_date_and_time(value: str) -> tuple[str, str]:
    date_match = _DATE.search(value or "")
    time_match = _TIME.search(value or "")
    date_value = normalize_date(date_match.group(1)) if date_match else ""
    return date_value, time_match.group(1) if time_match else ""


def parse_attendance_identity(text: str) -> dict[str, str]:
    """Extrai somente a identidade exibida no modal de atendimentos do dia."""
    clean_lines = [" ".join(line.split()) for line in str(text or "").splitlines() if line.strip()]
    compact = "\n".join(clean_lines)
    cpf_match = _CPF.search(compact)
    cns_match = _CNS.search(compact)
    birth_match = _BIRTH.search(compact)
    name = ""
    if cpf_match:
        before_cpf = compact[: cpf_match.start()].splitlines()
        ignored = {"ATENDIMENTOS NO DIA", "CPF", "CNS"}
        candidates = [line for line in before_cpf if canonical(line) not in ignored]
        if candidates:
            name = candidates[-1]
    return {
        "name": name,
        "cpf": cpf_match.group(1) if cpf_match else "",
        "cns": cns_match.group(1) if cns_match else "",
        "birth_date": birth_match.group(1) if birth_match else "",
    }


def parse_attendance_rows(rows: list[list[Any]]) -> list[AttendanceVisit]:
    visits: list[AttendanceVisit] = []
    for cells in rows:
        values = [" ".join(str(value or "").split()) for value in cells]
        if len(values) < 3:
            continue
        service_date, start_time = _first_date_and_time(values[0])
        _, end_time = _first_date_and_time(values[1] if len(values) > 1 else "")
        if not service_date:
            continue
        visits.append(
            AttendanceVisit(
                service_date=service_date,
                start_time=start_time,
                end_time=end_time,
                professional=values[2],
                cbo=values[3] if len(values) > 3 else "",
                attendance_type=values[4] if len(values) > 4 else "",
            )
        )
    return visits


def _professional_similarity(expected: str, returned: str) -> float:
    left, right = canonical(expected), canonical(returned)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return 0.95
    ratio = SequenceMatcher(None, left, right).ratio()
    left_parts, right_parts = left.split(), right.split()
    # Nomes com o mesmo miolo podem pertencer a profissionais diferentes.
    # Para aceitar uma aproximação, pelo menos o primeiro ou o último nome
    # também precisa coincidir; o texto exato/contido já foi tratado acima.
    if not left_parts or not right_parts:
        return 0.0
    if left_parts[0] != right_parts[0]:
        return ratio * 0.5
    return ratio


def match_attendance(day: AttendanceDay, *, cpf: str, birth_date: str, service_date: str, professional: str) -> AttendanceMatch:
    returned_cpf = normalize_cpf(day.patient.get("cpf", ""))
    expected_cpf = normalize_cpf(cpf)
    if not returned_cpf:
        return AttendanceMatch(False, "A Lista de atendimentos não exibiu o CPF")
    if returned_cpf != expected_cpf:
        return AttendanceMatch(False, "CPF retornado pelo PEC diverge da planilha")
    try:
        returned_birth = normalize_date(day.patient.get("birth_date", ""))
        expected_birth = normalize_date(birth_date)
    except ValueError:
        return AttendanceMatch(False, "A Lista de atendimentos não exibiu um nascimento válido")
    if returned_birth != expected_birth:
        return AttendanceMatch(False, "Nascimento retornado pelo PEC diverge da planilha")

    matches = [
        visit for visit in day.visits
        if visit.service_date == normalize_date(service_date)
        and _professional_similarity(professional, visit.professional) >= 0.84
    ]
    if not matches:
        return AttendanceMatch(False, "Nenhum atendimento da data e do profissional da planilha foi localizado")
    distinct = {(visit.service_date, canonical(visit.professional), visit.start_time, visit.end_time) for visit in matches}
    if len(distinct) > 1:
        return AttendanceMatch(False, "Mais de um atendimento compatível foi localizado", ambiguous=True)
    return AttendanceMatch(True)


def attendance_source(found_in_list: bool) -> str:
    return "PEC_LISTA_ATENDIMENTOS" if found_in_list else "PEC_CADASTRO_INDIVIDUAL"
