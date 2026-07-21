from __future__ import annotations

import logging

import pytest

from app.firebird.legacy_runner import FirebirdError, LegacyRunner, normalize_sql
from app.firebird.sql_builder import build_insert_script, professional_inserts
from app.logging_safe import mask_identifier, sanitize
from app.models import Patient, ReviewItem, Status
from app.repositories.firebird_repository import FirebirdRepository


def ready_item():
    return ReviewItem(row=1,original_identifier="",professional_name="Profissional Teste",professional_cns="700000000000006",
        cbo="225250",cnes="4998782",procedure="0301010072",service_date="20260701",informed_birth="20000101",original_cid="",final_cid="",
        patient=Patient(name="PACIENTE SINTETICO",cpf="52998224725",cns="700000000000006",birth_date="20000101",sex="F",ibge="311330"),sheet="001",sequence="01",status=Status.PRONTO_PARA_ADICIONAR)


def test_sql_has_no_update(): assert "UPDATE" not in build_insert_script([ready_item()],"202607").upper()
def test_sql_has_no_delete(): assert "DELETE" not in build_insert_script([ready_item()],"202607").upper()
def test_sql_ends_newline(): assert build_insert_script([ready_item()],"202607").endswith("\n")
def test_sql_cp1252(): build_insert_script([ready_item()],"202607").encode("cp1252")
def test_single_commit(): assert build_insert_script([ready_item()],"202607").upper().count("COMMIT;")==1
def test_production_uses_not_exists(): assert "NOT EXISTS" in build_insert_script([ready_item()],"202607")
def test_production_uses_bpi_origin_and_verifies_visibility():
    sql = build_insert_script([ready_item()], "202607")
    assert "'BPI'" in sql and "PRD_ORG='BPI'" in sql
    assert "'BPA'" not in sql
def test_optional_professional_insert_planned_once():
    row=ready_item(); row.professional_name="Bruno Profissional Exemplo"; row.professional_cns="700000000000013"
    row.cbo="223710"; row.cnes="0000000"; row.procedure="0301010048"; row.create_professional_if_missing=True
    sql=build_insert_script([row,row],"202607"); assert sql.count("INSERT INTO CADMED (")==1 and "223710" in sql
def test_other_professional_not_created(): assert professional_inserts(ready_item())==""
def test_runner_blocks_update_before_credentials(tmp_path):
    runner=LegacyRunner(tmp_path/"none.exe","db","target")
    with pytest.raises(FirebirdError): runner.run("UPDATE X SET A=1")
def test_runner_blocks_delete_before_credentials(tmp_path):
    runner=LegacyRunner(tmp_path/"none.exe","db","target")
    with pytest.raises(FirebirdError): runner.run("DELETE FROM X")
def test_runner_blocks_insert_in_readonly(tmp_path):
    runner=LegacyRunner(tmp_path/"none.exe","db","target")
    with pytest.raises(FirebirdError): runner.run("INSERT INTO X VALUES(1)")
def test_eof_text_is_classified_by_runner_source():
    import inspect
    assert "Expected end of statement, encountered EOF" in inspect.getsource(LegacyRunner.run)
def test_mask_cpf(): assert mask_identifier("52998224725")=="529.***.***-25"
def test_mask_cns(): assert mask_identifier("700000000000006")=="700*********006"
def test_sanitize_logs():
    text=sanitize("CPF 529.982.247-25 CNS 700000000000006")
    assert "52998224725" not in text and "000000000" not in text
def test_no_password_field_in_example_config():
    from pathlib import Path
    text=(Path(__file__).parents[1]/"data/config.example.json").read_text(encoding="utf-8").lower()
    assert "password" not in text and "senha" not in text


def test_cid_validation_uses_firebird_15_typed_column_instead_of_union():
    class ReadOnlyRunner:
        sql = ""

        def run(self, sql: str) -> str:
            self.sql = sql
            return "CID_VALUE                     Z713\n\n"

    runner = ReadOnlyRunner()
    assert FirebirdRepository(runner).validate_cids(["Z713", "E119"]) == {"Z713"}
    assert "UNION ALL" not in runner.sql.upper()
    assert "CD_COD IN ('E119','Z713')" in runner.sql
