from __future__ import annotations

from app.services.pec_attendance import (
    AttendanceDay,
    attendance_source,
    match_attendance,
    parse_attendance_identity,
    parse_attendance_rows,
)


CPF = "52998224725"
BIRTH = "20000201"


def synthetic_day(*rows):
    return AttendanceDay(
        patient={"name":"PACIENTE SINTÉTICO", "cpf":CPF, "cns":"700000000000005", "birth_date":"01/02/2000"},
        visits=parse_attendance_rows(list(rows)),
    )


def test_attendance_modal_identity_is_parsed_without_age_text():
    text="""Atendimentos no dia
PACIENTE SINTÉTICO
CPF 529.982.247-25  CNS 700000000000005
Data de nascimento 01/02/2000 (26 anos)
"""
    assert parse_attendance_identity(text) == {
        "name":"PACIENTE SINTÉTICO", "cpf":"529.982.247-25",
        "cns":"700000000000005", "birth_date":"01/02/2000",
    }


def test_attendance_rows_preserve_professional_date_and_cbo():
    visits=parse_attendance_rows([["20/07/2026 12:48","20/07/2026 12:51","Ana Profissional Exemplo","MÉDICO CARDIOLOGISTA","Consulta"]])
    assert len(visits)==1
    assert visits[0].service_date=="20260720" and visits[0].professional=="Ana Profissional Exemplo"
    assert visits[0].cbo=="MÉDICO CARDIOLOGISTA" and visits[0].attendance_type=="Consulta"


def test_matching_patient_and_professional_is_confirmed():
    day=synthetic_day(["20/07/2026 12:48","20/07/2026 12:51","Ana Profissional Exemplo","MÉDICO","Consulta"])
    assert match_attendance(day,cpf=CPF,birth_date=BIRTH,service_date="20260720",professional="ANA PROFISSIONAL EXEMPLO").matched


def test_same_patient_with_different_professionals_is_matched_separately():
    day=synthetic_day(
        ["20/07/2026 12:48","20/07/2026 12:51","Ana Profissional Exemplo","MÉDICO","Consulta"],
        ["20/07/2026 13:21","20/07/2026 13:31","Bruno Profissional Exemplo","NUTRICIONISTA","Consulta"],
    )
    first=match_attendance(day,cpf=CPF,birth_date=BIRTH,service_date="20260720",professional="Ana Profissional Exemplo")
    second=match_attendance(day,cpf=CPF,birth_date=BIRTH,service_date="20260720",professional="Bruno Profissional Exemplo")
    assert first.matched and second.matched


def test_cpf_divergence_is_never_confirmed():
    day=synthetic_day(["20/07/2026 12:48","20/07/2026 12:51","Ana Profissional Exemplo","MÉDICO","Consulta"])
    result=match_attendance(day,cpf="00848275640",birth_date=BIRTH,service_date="20260720",professional="Ana Profissional Exemplo")
    assert not result.matched and result.reason=="CPF retornado pelo PEC diverge da planilha"


def test_birth_divergence_is_never_confirmed():
    day=synthetic_day(["20/07/2026 12:48","20/07/2026 12:51","Ana Profissional Exemplo","MÉDICO","Consulta"])
    result=match_attendance(day,cpf=CPF,birth_date="20010201",service_date="20260720",professional="Ana Profissional Exemplo")
    assert not result.matched and result.reason=="Nascimento retornado pelo PEC diverge da planilha"


def test_professional_or_date_mismatch_requests_fallback():
    day=synthetic_day(["20/07/2026 12:48","20/07/2026 12:51","Ana Profissional Exemplo","MÉDICO","Consulta"])
    result=match_attendance(day,cpf=CPF,birth_date=BIRTH,service_date="20260721",professional="Ana Profissional Exemplo")
    assert not result.matched and "data e do profissional" in result.reason


def test_multiple_matching_visits_are_ambiguous():
    day=synthetic_day(
        ["20/07/2026 12:48","20/07/2026 12:51","Ana Profissional Exemplo","MÉDICO","Consulta"],
        ["20/07/2026 14:00","20/07/2026 14:10","Ana Profissional Exemplo","MÉDICO","Consulta"],
    )
    result=match_attendance(day,cpf=CPF,birth_date=BIRTH,service_date="20260720",professional="Ana Profissional Exemplo")
    assert not result.matched and result.ambiguous


def test_lookup_source_records_list_and_fallback_paths():
    assert attendance_source(True)=="PEC_LISTA_ATENDIMENTOS"
    assert attendance_source(False)=="PEC_CADASTRO_INDIVIDUAL"
