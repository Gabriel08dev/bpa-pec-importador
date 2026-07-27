from __future__ import annotations

import pytest

from app.services.cid_service import normalize_cid, validate_cid
from app.services.patient_service import (format_cpf, legacy_adjust, normalize_cpf, normalize_date,
                                          normalize_pec_patient, reconcile_birth_date,
                                          swapped_day_month_date, valid_cns, valid_cpf)
from app.services.pec_service import _graphql_value, parse_pec_detail, parse_pec_graphql


@pytest.mark.parametrize("value", ["52998224725", "529.982.247-25"])
def test_valid_cpf(value): assert valid_cpf(value)


@pytest.mark.parametrize("value", ["11111111111", "52998224724", "abc", ""])
def test_invalid_cpf(value): assert not valid_cpf(value)


def test_cpf_punctuation(): assert normalize_cpf("529.982.247-25") == "52998224725"
def test_cpf_zero_initial_from_excel_number(): assert normalize_cpf(848275640) == "00848275640"
def test_exact_requested_cpf(): assert normalize_cpf("00848275640") == "00848275640" and valid_cpf("00848275640")
def test_cpf_display(): assert format_cpf("00848275640") == "008.482.756-40"


def _synthetic_valid_cns() -> str:
    prefix = "70000000000000"
    for digit in range(10):
        value = prefix + str(digit)
        if sum(int(n) * w for n, w in zip(value, range(15, 0, -1))) % 11 == 0: return value
    raise AssertionError


def test_cns_valid(): assert valid_cns(_synthetic_valid_cns())
def test_cns_invalid(): assert not valid_cns("700000000000000")
def test_date_br(): assert normalize_date("03/07/2026") == "20260703"
def test_date_compact(): assert normalize_date("20260703") == "20260703"


@pytest.mark.parametrize("value", [45881, 45881.0, "45881", "45881.0"])
def test_excel_serial_date(value):
    assert normalize_date(value) == "20250812"


def test_older_excel_serial_date():
    assert normalize_date("23387") == "19640111"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("17021999", "19990217"),
        ("1021996", "19960201"),
        (1021996, "19960201"),
        ("3012025", "20250103"),
        ("8122025", "20251208"),
        ("9022026", "20260209"),
    ],
)
def test_compact_brazilian_date_from_numeric_cell(value, expected):
    assert normalize_date(value) == expected


def test_out_of_range_number_is_not_accepted_as_excel_date():
    with pytest.raises(ValueError, match="Data inválida"):
        normalize_date("99999")


def test_swapped_day_month_date_only_returns_ambiguous_alternative():
    assert swapped_day_month_date("19950501") == "19950105"
    assert swapped_day_month_date("20250812") == "20251208"
    assert swapped_day_month_date("20220524") == ""
    assert swapped_day_month_date("20260606") == ""


def test_reconcile_equal_birth_date_without_warning():
    assert reconcile_birth_date("01/02/2000", "2000-02-01") == ("20000201", "")


def test_reconcile_swapped_birth_date_uses_pec_with_warning():
    confirmed, warning = reconcile_birth_date("19950501", "1995-01-05")
    assert confirmed == "19950105"
    assert "invertidos" in warning


def test_reconcile_true_birth_divergence_uses_pec_after_exact_cpf_gate():
    confirmed, warning = reconcile_birth_date("1974-07-25", "1974-07-26")
    assert confirmed == "19740726"
    assert "validação exata do CPF" in warning


def test_cid_dot(): assert normalize_cid("Z71.3")[0] == "Z713"
def test_cid_no_dot(): assert normalize_cid("Z713")[0] == "Z713"
def test_cid_missing_in_table(): assert validate_cid("Z713", lambda _: False)[0] == ""
def test_cid_blank(): assert normalize_cid("") == ("", "")
def test_multiple_cids_not_chosen(): assert normalize_cid("Z71.3; E11")[0] == ""
def test_long_complement_becomes_empty(): assert legacy_adjust("COMPLEMENTO MUITO LONGO", 10, empty_if_long=True) == ("", True)
def test_long_legacy_name_only_adjusted(): assert legacy_adjust("NOME COMPLETO EXTREMAMENTE LONGO PARA TESTE", 30)[0] == "NOME COMPLETO EXTREMAMENTE LON"


def test_pec_normalization_address_rules():
    patient, warnings = normalize_pec_patient({"nome":"Pessoa Sintética de Teste com Nome Muito Longo","cpf":"52998224725",
        "cns":_synthetic_valid_cns(),"data_nascimento":"01/02/2000","municipio":"Carangola","nacionalidade":"Brasileira",
        "complemento":"ACIMA DE DEZ CARACTERES"})
    assert patient.street_type == "081"
    assert patient.ibge == "311330"
    assert patient.nationality == "010"
    assert patient.complement == ""
    assert warnings


def test_other_city_does_not_get_carangola_ibge():
    patient, _ = normalize_pec_patient({"nome":"Pessoa Teste","cpf":"52998224725","cns":_synthetic_valid_cns(),
                                         "data_nascimento":"01/02/2000","municipio":"Outra Cidade"})
    assert patient.ibge == ""


def test_pec_detail_parses_values_joined_directly_to_labels():
    detail = """PACIENTE SINTÉTICO | SEXO FEMININO
CPF529.982.247-25
CNS700000000000006
Data de nascimento01/02/2000
SexoFeminino
Nome da mãePESSOA RESPONSÁVEL
Município de residênciaCarangola
CEP36800-000
"""
    parsed = parse_pec_detail(detail)
    assert parsed["cpf"] == "529.982.247-25"
    assert parsed["cns"] == "700000000000006"
    assert parsed["birth_date"] == "01/02/2000"
    assert parsed["sex"] == "Feminino"
    assert parsed["mother_name"] == "PESSOA RESPONSÁVEL"
    assert parsed["residence_city"] == "Carangola"
    assert parsed["zip_code"] == "36800-000"


def test_pec_graphql_uses_search_and_detail_without_inventing_fields():
    search = {
        "cpf": "52998224725", "cns": "700000000000006", "dataNascimento": "2000-02-01",
        "nome": "PACIENTE SINTÉTICO", "sexo": "FEMININO",
    }
    detail = {
        "nomeMae": "PESSOA RESPONSÁVEL", "racaCor": {"racaCorDbEnum": "PARDA"},
        "nacionalidade": {"nacionalidadeDbEnum": "BRASILEIRA"},
        "endereco": {"cep": "36800000", "logradouro": "RUA TESTE", "numero": "10",
                     "bairro": "CENTRO", "municipio": {"nome": "Carangola"}},
        "localidadeNascimento": {"nome": "Carangola"},
        "telefoneCelular": "(32) 99999-9999",
    }
    parsed = parse_pec_graphql(detail, search)
    assert parsed["cpf"] == search["cpf"] and parsed["cns"] == search["cns"]
    assert parsed["birth_date"] == "2000-02-01" and parsed["race"] == "03"
    assert parsed["residence_city"] == "Carangola" and parsed["phone_area"] == "32"
    assert parsed["phone"] == "999999999" and parsed["email"] == ""


def test_graphql_value_accepts_batched_pec_response():
    payload = [{"data": {"serverTime": 1}}, {"data": {"cidadao": {"id": "synthetic"}}}]
    assert _graphql_value(payload, "cidadao") == {"id": "synthetic"}
