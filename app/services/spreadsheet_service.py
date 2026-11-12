from __future__ import annotations

import csv
import hashlib
import io
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo

from app.services.patient_service import canonical


HEADERS = {
    "PROFISSIONAL": "professional",
    "DIA DO ATENDIMENTO": "service_date",
    "DATA DO ATENDIMENTO": "service_date",
    "CPF DO PACIENTE": "cpf",
    "CPF": "cpf",
    "DATA DE NASCIMENTO": "birth_date",
    "NASCIMENTO": "birth_date",
    "CID": "cid",
}
REQUIRED = {"professional", "service_date", "cpf", "birth_date"}
CID_REQUIRED = {"professional", "service_date", "cpf", "cid"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _read_rows(path: Path, required: set[str]) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    worksheets: list[list[list[Any]]]
    if suffix == ".xlsx":
        book = load_workbook(path, read_only=True, data_only=True)
        preferred_names = ("Atendimento", "Atendimentos")
        ordered_sheets = [book[name] for name in preferred_names if name in book.sheetnames]
        ordered_sheets.extend(sheet for sheet in book.worksheets if sheet not in ordered_sheets)
        worksheets = [
            [list(row) for row in sheet.iter_rows(values_only=True)]
            for sheet in ordered_sheets
        ]
        book.close()
    elif suffix in {".csv", ".tsv"}:
        text = path.read_text(encoding="utf-8-sig", errors="strict")
        delimiter = "\t" if suffix == ".tsv" else csv.Sniffer().sniff(text[:4096], delimiters=";,\t|").delimiter
        worksheets = [list(csv.reader(io.StringIO(text), delimiter=delimiter))]
    else:
        raise ValueError("Formato não aceito; use .xlsx, .csv ou .tsv")
    if not any(worksheets):
        raise ValueError("Planilha vazia")
    header_index = -1
    mapped: dict[int, str] = {}
    best_found: set[str] = set()
    raw_rows: list[list[Any]] = []
    for candidate_rows in worksheets:
        for candidate_index, candidate_row in enumerate(candidate_rows[:50]):
            candidate_mapping = {
                index: HEADERS[canonical(value)]
                for index, value in enumerate(candidate_row)
                if canonical(value) in HEADERS
            }
            found = set(candidate_mapping.values())
            if len(found) > len(best_found):
                best_found = found
            if required <= found:
                raw_rows = candidate_rows
                header_index = candidate_index
                mapped = candidate_mapping
                break
        if header_index >= 0:
            break
    if header_index < 0:
        missing = required - best_found
        raise ValueError(
            "Não foi possível localizar a linha de cabeçalho nas primeiras 50 linhas. "
            "Colunas obrigatórias ausentes: " + ", ".join(sorted(missing))
        )
    rows: list[dict[str, Any]] = []
    for row_number, values in enumerate(raw_rows[header_index + 1:], header_index + 2):
        item = {name: _text(values[index] if index < len(values) else "") for index, name in mapped.items()}
        if any(item.values()):
            item["row"] = row_number
            rows.append(item)
    return rows


def read_rows(path: Path) -> list[dict[str, Any]]:
    return _read_rows(path, REQUIRED)


def read_cid_rows(path: Path) -> list[dict[str, Any]]:
    """Lê uma planilha usada apenas para cruzar CID com um lote vindo do PEC."""
    return _read_rows(path, CID_REQUIRED)


def create_template(path: Path, professional_names: list[str]) -> Path:
    book = Workbook()
    sheet = book.active
    sheet.title = "Atendimento"
    professionals = book.create_sheet("Profissionais")
    sheet.merge_cells("A1:E1")
    sheet["A1"] = "CONTROLE DE ATENDIMENTOS"
    sheet["A1"].font = Font(size=16, bold=True, color="FFFFFF")
    sheet["A1"].fill = PatternFill("solid", fgColor="1F4E78")
    sheet["A1"].alignment = Alignment(horizontal="center")
    headers = ["Profissional", "Dia do atendimento", "CPF do paciente", "Data de nascimento", "CID"]
    for column, value in enumerate(headers, 1):
        cell = sheet.cell(4, column, value)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2F75B5")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    input_fill = PatternFill("solid", fgColor="FFFBE6")
    for row in range(5, 305):
        for column in range(1, 6):
            sheet.cell(row, column).fill = input_fill
        sheet.cell(row, 2).number_format = "dd/mm/yyyy"
        sheet.cell(row, 3).number_format = "@"
        sheet.cell(row, 4).number_format = "dd/mm/yyyy"
        sheet.cell(row, 5).number_format = "@"
    for row, name in enumerate(professional_names, 1):
        professionals.cell(row, 1, name)
    professionals.sheet_state = "hidden"
    validation = DataValidation(type="list", formula1=f"=Profissionais!$A$1:$A${len(professional_names)}", allow_blank=False)
    sheet.add_data_validation(validation)
    validation.add("A5:A304")
    sheet.add_table(Table(displayName="AtendimentosCEAE", ref="A4:E304"))
    sheet.tables["AtendimentosCEAE"].tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=False)
    sheet.freeze_panes = "A5"
    sheet.sheet_view.showGridLines = False
    sheet.row_dimensions[1].height = 24
    sheet.row_dimensions[4].height = 30
    widths = {"A": 42, "B": 20, "C": 22, "D": 20, "E": 14}
    for column, width in widths.items(): sheet.column_dimensions[column].width = width
    sheet.conditional_formatting.add("C5:C304", FormulaRule(formula=['AND(C5<>"",LEN(SUBSTITUTE(SUBSTITUTE(C5,".",""),"-",""))<>11)'], fill=PatternFill("solid", fgColor="FEE2E2")))
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)
    return path
