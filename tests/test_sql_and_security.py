from __future__ import annotations

import logging

import pytest

from app.firebird.legacy_runner import FirebirdError, LegacyRunner, normalize_sql
from app.firebird.sql_builder import build_insert_script, build_write_script, professional_inserts
from app.firebird.verifier import verify_write_output
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
def test_write_script_bails_before_commit_on_any_statement_error():
    sql = build_insert_script([ready_item()], "202607").upper()
    assert "SET BAIL ON;" in sql
    assert sql.index("SET BAIL ON;") < sql.index("SET TRANSACTION;") < sql.index("COMMIT;")
def test_production_uses_not_exists(): assert "NOT EXISTS" in build_insert_script([ready_item()],"202607")
def test_production_duplicate_guard_uses_patient_professional_and_exact_date():
    sql=build_insert_script([ready_item()],"202607")
    assert "P.PRD_CNSMED='700000000000006'" in sql
    assert "P.PRD_DTATEN='20260701'" in sql
    assert "P.PRD_CPF_PCNTE='52998224725' OR P.PRD_CNSPAC='700000000000006'" in sql
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


def test_runner_rejects_broad_update_even_when_update_flag_is_enabled(tmp_path):
    runner=LegacyRunner(tmp_path/"none.exe","db","target")
    with pytest.raises(FirebirdError, match="somente CNES/folha/sequência e CID"):
        runner.run(
            "UPDATE S_PRD SET PRD_NMPAC='X' WHERE PRD_CPF_PCNTE='52998224725';",
            allow_update=True,
        )


def test_cid_update_sql_only_fills_empty_cid_on_exact_existing_row():
    row = ready_item()
    row.status = Status.PRONTO_PARA_ATUALIZAR_CID
    row.final_cid = "Z713"
    row.sheet = "010"
    row.sequence = "07"
    sql = build_write_script([], [row], "202607")
    assert "UPDATE S_PRD SET PRD_CID='Z713'" in sql
    assert "PRD_CNSMED='700000000000006'" in sql
    assert "PRD_DTATEN='20260701'" in sql
    assert "PRD_FLH='010'" in sql and "PRD_SEQ='07'" in sql
    assert "PRD_CPF_PCNTE='52998224725'" in sql
    assert "PRD_CID IS NULL OR PRD_CID=''" in sql
    assert "TRIM(" not in sql.upper()
    assert "VERIFY_UPDATE_1" in sql
    assert "DELETE" not in sql.upper()
    LegacyRunner._validate_cid_updates(sql, True)


def test_combined_update_moves_exact_row_to_ceae_and_fills_only_empty_cid():
    row = ready_item()
    row.status = Status.PRONTO_PARA_CORRIGIR_CNES_E_CID
    row.final_cid = "Z713"
    row.sheet = "008"
    row.sequence = "02"
    row.sources.update(
        {
            "existing_cnes": "2114771",
            "existing_sheet": "004",
            "existing_sequence": "03",
            "existing_origin": "BPI",
            "existing_cid": "",
        }
    )
    sql = build_write_script([], [row], "202607")
    assert "SET PRD_UID='4998782',PRD_FLH='008',PRD_SEQ='02',PRD_CID='Z713'" in sql
    assert "AND PRD_UID='2114771'" in sql
    assert "AND PRD_FLH='004'" in sql and "AND PRD_SEQ='03'" in sql
    assert "AND PRD_ORG='BPI'" in sql
    assert "PRD_CID IS NULL OR PRD_CID=''" in sql
    assert "VERIFY_UPDATE_1" in sql
    LegacyRunner._validate_cid_updates(sql, True)


def test_cnes_only_update_does_not_touch_existing_cid():
    row = ready_item()
    row.status = Status.PRONTO_PARA_CORRIGIR_CNES
    row.final_cid = "Z713"
    row.sheet = "008"
    row.sequence = "02"
    row.sources.update(
        {
            "existing_cnes": "2114771",
            "existing_sheet": "004",
            "existing_sequence": "03",
            "existing_cid": "E119",
        }
    )
    sql = build_write_script([], [row], "202607")
    update = sql.split("UPDATE S_PRD", 1)[1].split(";", 1)[0]
    assert "PRD_CID=" not in update
    assert "PRD_CID='E119'" in sql
    LegacyRunner._validate_cid_updates(sql, True)


def test_runner_rejects_partial_cnes_move_without_new_sheet_and_sequence():
    sql = (
        "UPDATE S_PRD SET PRD_UID='4998782' WHERE "
        "PRD_CNSMED='700000000000006' AND PRD_UID='2114771' "
        "AND PRD_CBO='225250' AND PRD_CMP='202607' "
        "AND PRD_DTATEN='20260701' AND PRD_FLH='004' AND PRD_SEQ='03' "
        "AND PRD_CPF_PCNTE='52998224725';"
    )
    with pytest.raises(FirebirdError, match="nova folha e sequência"):
        LegacyRunner._validate_cid_updates(sql, True)


def test_write_verifier_counts_inserts_and_cid_updates_separately():
    output = "VERIFY_1                     1\n\nVERIFY_UPDATE_1              1\n\n"
    assert verify_write_output(output, 1, 1) == ([], [])
    insert_failures, update_failures = verify_write_output("", 1, 1)
    assert len(insert_failures) == 1
    assert len(update_failures) == 1
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


def test_existing_production_reads_every_origin_for_duplicate_detection():
    class ReadOnlyRunner:
        sql = ""

        def run(self, sql: str) -> str:
            self.sql = sql
            return ""

    runner=ReadOnlyRunner()
    FirebirdRepository(runner).existing_production("202607",["700000000000006"])
    where_clause=runner.sql.split("FROM S_PRD",1)[1]
    assert "PRD_CMP='202607'" in where_clause
    assert "PRD_ORG" not in where_clause
