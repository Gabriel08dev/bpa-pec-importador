from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook, load_workbook

from app.services.spreadsheet_service import create_template, read_rows
from app.repositories.review_repository import ReviewRepository
from app.services.batch_service import BatchService
from app.services.professional_service import ProfessionalRegistry


HEADERS = ["Profissional","Dia do atendimento","CPF do paciente","Data de nascimento","CID"]


def test_excel_numeric_cpf_preserved(tmp_path):
    path=tmp_path/"in.xlsx"; book=Workbook(); sheet=book.active; sheet.append(HEADERS); sheet.append(["Pessoa Profissional","01/07/2026",848275640,"01/01/2000",""]); book.save(path)
    assert read_rows(path)[0]["cpf"]=="848275640"


def test_import_csv(tmp_path):
    path=tmp_path/"in.csv"; path.write_text(";".join(HEADERS)+"\nProfissional;01/07/2026;00848275640;01/01/2000;Z713\n",encoding="utf-8")
    assert len(read_rows(path))==1


def test_import_tsv(tmp_path):
    path=tmp_path/"in.tsv"; path.write_text("\t".join(HEADERS)+"\nProfissional\t01/07/2026\t00848275640\t01/01/2000\t\n",encoding="utf-8")
    assert read_rows(path)[0]["cpf"]=="00848275640"


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
