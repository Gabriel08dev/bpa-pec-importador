from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook, load_workbook

from app.services.spreadsheet_service import create_template, read_cid_rows, read_rows
from app.repositories.review_repository import ReviewRepository
from app.services.batch_service import BatchService
from app.services.professional_service import ProfessionalRegistry


HEADERS = ["Profissional","Dia do atendimento","CPF do paciente","Data de nascimento","CID"]
CID_HEADERS = ["Profissional","Dia do atendimento","CPF do paciente","CID"]


def test_excel_numeric_cpf_preserved(tmp_path):
    path=tmp_path/"in.xlsx"; book=Workbook(); sheet=book.active; sheet.append(HEADERS); sheet.append(["Pessoa Profissional","01/07/2026",848275640,"01/01/2000",""]); book.save(path)
    assert read_rows(path)[0]["cpf"]=="848275640"


def test_excel_serial_birth_date_is_normalized_when_importing_batch(tmp_path):
    path=tmp_path/"serial-date.xlsx"; book=Workbook(); sheet=book.active
    sheet.append(HEADERS)
    sheet.append(["Ana Profissional Exemplo","22/07/2026",52998224725,45881.0,"B86"])
    book.save(path)
    root=Path(__file__).parents[1]
    service=BatchService(
        ProfessionalRegistry(root/"data"/"profissionais.example.json"),
        ReviewRepository(tmp_path/"outputs"),
    )
    _,payload=service.import_spreadsheet(path,"202607")
    item=payload["items"][0]
    assert item["status"]=="REVISAO_INICIAL"
    assert item["service_date"]=="20260722"
    assert item["informed_birth"]=="20250812"


def test_numeric_compact_brazilian_birth_date_is_normalized(tmp_path):
    path=tmp_path/"compact-date.xlsx"; book=Workbook(); sheet=book.active
    sheet.append(HEADERS)
    sheet.append(["Ana Profissional Exemplo","22/07/2026",52998224725,1021996,"B86"])
    book.save(path)
    root=Path(__file__).parents[1]
    service=BatchService(
        ProfessionalRegistry(root/"data"/"profissionais.example.json"),
        ReviewRepository(tmp_path/"outputs"),
    )
    _,payload=service.import_spreadsheet(path,"202607")
    item=payload["items"][0]
    assert item["status"]=="REVISAO_INICIAL"
    assert item["informed_birth"]=="19960201"


def test_import_csv(tmp_path):
    path=tmp_path/"in.csv"; path.write_text(";".join(HEADERS)+"\nProfissional;01/07/2026;00848275640;01/01/2000;Z713\n",encoding="utf-8")
    assert len(read_rows(path))==1


def test_import_tsv(tmp_path):
    path=tmp_path/"in.tsv"; path.write_text("\t".join(HEADERS)+"\nProfissional\t01/07/2026\t00848275640\t01/01/2000\t\n",encoding="utf-8")
    assert read_rows(path)[0]["cpf"]=="00848275640"


def test_cid_cross_sheet_does_not_require_birth_date(tmp_path):
    path=tmp_path/"cids.csv"
    path.write_text(
        ";".join(CID_HEADERS)+"\n"
        "Ana Profissional Exemplo;20/07/2026;52998224725;Z71.3\n",
        encoding="utf-8",
    )
    assert read_cid_rows(path)[0]["cid"]=="Z71.3"


def test_more_than_100_patients(tmp_path):
    path=tmp_path/"in.csv"; lines=[";".join(HEADERS)]+[f"Profissional;01/07/2026;{i:011d};01/01/2000;" for i in range(101)]
    path.write_text("\n".join(lines),encoding="utf-8"); assert len(read_rows(path))==101


def test_template_has_300_rows_dropdown_and_text_cpf(tmp_path):
    path=create_template(tmp_path/"modelo.xlsx",["A","B"]); book=load_workbook(path); sheet=book["Atendimento"]
    assert sheet.max_row>=304 and sheet["C5"].number_format=="@" and len(sheet.data_validations.dataValidation)==1
    assert [sheet.cell(4, column).value for column in range(1, 6)] == HEADERS
    assert sheet["F4"].value is None
    book.close()


def test_template_can_be_read_back(tmp_path):
    path=create_template(tmp_path/"modelo.xlsx",["Profissional Teste"]); book=load_workbook(path); sheet=book["Atendimento"]
    sheet["A5"]="Profissional Teste"; sheet["B5"]="01/07/2026"; sheet["C5"]="00848275640"; sheet["D5"]="01/01/2000"; book.save(path)
    assert read_rows(path)[0]["cpf"]=="00848275640"


def test_new_model_sheet_is_selected_and_unnecessary_columns_are_ignored(tmp_path):
    path=tmp_path/"new-model.xlsx"; book=Workbook(); book.active.title="Painel"
    sheet=book.create_sheet("Atendimento")
    headers=HEADERS+["Município do paciente (PPI/sede)"]
    for column,value in enumerate(headers,1): sheet.cell(4,column,value)
    values=["Profissional Teste","01/07/2026","00848275640","01/01/2000","Z71.3","Carangola"]
    for column,value in enumerate(values,1): sheet.cell(5,column,value)
    book.save(path)
    rows=read_rows(path)
    assert rows == [{
        "professional":"Profissional Teste", "service_date":"01/07/2026",
        "cpf":"00848275640", "birth_date":"01/01/2000", "cid":"Z71.3", "row":5,
    }]


def test_header_can_be_on_row_four(tmp_path):
    path=tmp_path/"header-row-4.xlsx"; book=Workbook(); sheet=book.active; sheet.title="Atendimentos"
    sheet["A1"]="CONTROLE DE ATENDIMENTOS"; sheet["A2"]="Instruções de preenchimento"
    for column,value in enumerate(HEADERS,1): sheet.cell(4,column,value)
    for column,value in enumerate(["Profissional Teste","01/07/2026",848275640,"01/01/2000","Z71.3"],1): sheet.cell(5,column,value)
    book.save(path)
    rows=read_rows(path)
    assert rows[0]["row"]==5 and rows[0]["cpf"]=="848275640" and rows[0]["cid"]=="Z71.3"


def test_same_patient_with_different_doctors_is_kept(tmp_path):
    source=tmp_path/"different-doctors.csv"
    source.write_text(
        ";".join(HEADERS)+"\n"
        "Ana Profissional Exemplo;01/07/2026;00848275640;01/01/2000;Z713\n"
        "Bruno Profissional Exemplo;01/07/2026;00848275640;01/01/2000;Z713\n",
        encoding="utf-8",
    )
    root=Path(__file__).parents[1]
    service=BatchService(ProfessionalRegistry(root/"data"/"profissionais.example.json"),ReviewRepository(tmp_path/"outputs"))
    _,payload=service.import_spreadsheet(source,"202607")
    assert len(payload["items"])==2
    assert [item["status"] for item in payload["items"]]==["REVISAO_INICIAL","REVISAO_INICIAL"]
    assert len({item["professional_cns"] for item in payload["items"]})==2


def test_pec_list_can_create_batch_without_spreadsheet_and_keep_different_doctors(tmp_path):
    root=Path(__file__).parents[1]
    service=BatchService(ProfessionalRegistry(root/"data"/"profissionais.example.json"),ReviewRepository(tmp_path/"outputs"))
    records=[
        {"name":"Paciente Sintético","cpf":"52998224725","cns":"700000000000006","birth_date":"01/02/2000","service_date":"20260720","professional":"Ana Profissional Exemplo"},
        {"name":"Paciente Sintético","cpf":"52998224725","cns":"700000000000006","birth_date":"01/02/2000","service_date":"20260720","professional":"Bruno Profissional Exemplo"},
    ]
    _,payload=service.import_pec_attendances(records,"202607","2026-07-01","2026-07-31")
    assert payload["origin"]=="PEC_LISTA_ATENDIMENTOS"
    assert [item["status"] for item in payload["items"]]==["REVISAO_INICIAL","REVISAO_INICIAL"]
    assert len({item["professional_cns"] for item in payload["items"]})==2
    assert BatchService.source_unchanged(payload)


def test_pec_list_source_manifest_detects_change(tmp_path):
    root=Path(__file__).parents[1]
    service=BatchService(ProfessionalRegistry(root/"data"/"profissionais.example.json"),ReviewRepository(tmp_path/"outputs"))
    records=[{"name":"Paciente Sintético","cpf":"52998224725","cns":"700000000000006","birth_date":"01/02/2000","service_date":"20260720","professional":"Ana Profissional Exemplo"}]
    _,payload=service.import_pec_attendances(records,"202607","2026-07-01","2026-07-31")
    payload["source_manifest"]["period_end"]="20260801"
    assert not BatchService.source_unchanged(payload)


def test_cids_are_crossed_by_cpf_date_and_professional_without_mixing_visits(tmp_path):
    root=Path(__file__).parents[1]
    reviews=ReviewRepository(tmp_path/"outputs")
    service=BatchService(ProfessionalRegistry(root/"data"/"profissionais.example.json"),reviews)
    records=[
        {"name":"Paciente Sintético","cpf":"52998224725","cns":"700000000000006","birth_date":"01/02/2000","service_date":"20260720","professional":"Ana Profissional Exemplo"},
        {"name":"Paciente Sintético","cpf":"52998224725","cns":"700000000000006","birth_date":"01/02/2000","service_date":"20260720","professional":"Bruno Profissional Exemplo"},
        {"name":"Paciente Sintético","cpf":"52998224725","cns":"700000000000006","birth_date":"01/02/2000","service_date":"20260721","professional":"Ana Profissional Exemplo"},
    ]
    batch_id,payload=service.import_pec_attendances(records,"202607","2026-07-01","2026-07-31")
    final=dict(payload)
    final["items"]=[dict(item,status="PEC_CONFIRMADO") for item in payload["items"]]
    service.update_review(batch_id,final)
    reviews.write(batch_id,"previa_firebird.json",{"stale":True})

    sheet=tmp_path/"cids.csv"
    sheet.write_text(
        ";".join(CID_HEADERS)+"\n"
        "Ana Profissional Exemplo;20/07/2026;52998224725;Z71.3\n"
        "Bruno Profissional Exemplo;20/07/2026;52998224725;E11.9\n"
        "Ana Profissional Exemplo;21/07/2026;52998224725;F32.0\n"
        "Ana Profissional Exemplo;22/07/2026;52998224725;J45.9\n",
        encoding="utf-8",
    )
    summary=service.import_cids_into_pec_batch(batch_id,sheet,"cids-preenchidos.csv")

    assert summary["matched_keys"]==3
    assert summary["matched_items"]==3
    assert summary["unmatched_spreadsheet_rows"]==[5]
    assert not (reviews.batch_dir(batch_id)/"previa_firebird.json").exists()
    for filename in ("revisao_planilha.json","revisao_final.json"):
        updated=reviews.read(batch_id,filename)
        assert [item["final_cid"] for item in updated["items"]]==["Z713","E119","F320"]
        assert all(item["sources"]["cid"]=="PLANILHA_CRUZADA_CPF_DATA_PROFISSIONAL" for item in updated["items"])


def test_conflicting_cids_for_same_cpf_date_and_professional_are_not_applied(tmp_path):
    root=Path(__file__).parents[1]
    reviews=ReviewRepository(tmp_path/"outputs")
    service=BatchService(ProfessionalRegistry(root/"data"/"profissionais.example.json"),reviews)
    records=[
        {"name":"Paciente Sintético","cpf":"52998224725","cns":"700000000000006","birth_date":"01/02/2000","service_date":"20260720","professional":"Ana Profissional Exemplo"},
        {"name":"Paciente Sintético","cpf":"52998224725","cns":"700000000000006","birth_date":"01/02/2000","service_date":"20260720","professional":"Bruno Profissional Exemplo"},
    ]
    batch_id,_=service.import_pec_attendances(records,"202607","2026-07-01","2026-07-31")
    sheet=tmp_path/"conflict.csv"
    sheet.write_text(
        ";".join(CID_HEADERS)+"\n"
        "Ana Profissional Exemplo;20/07/2026;52998224725;Z71.3\n"
        "Ana Profissional Exemplo;20/07/2026;52998224725;E11.9\n"
        "Bruno Profissional Exemplo;20/07/2026;52998224725;F32.0\n",
        encoding="utf-8",
    )
    summary=service.import_cids_into_pec_batch(batch_id,sheet,"conflict.csv")
    updated=reviews.read(batch_id,"revisao_planilha.json")

    assert summary["conflict_rows"]==[2,3]
    assert [item["final_cid"] for item in updated["items"]]==["","F320"]
