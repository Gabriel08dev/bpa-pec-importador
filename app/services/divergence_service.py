from __future__ import annotations

import csv
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

from app.logging_safe import mask_identifier


def generate_pdf(payload: dict, destination: Path) -> Path:
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(destination), pagesize=landscape(A4), rightMargin=8*mm, leftMargin=8*mm, topMargin=8*mm, bottomMargin=8*mm,
                            title="Relatório de divergências — Importador CEAE")
    story = [Paragraph("Relatório de divergências — Importador CEAE / PEC / BPA", styles["Title"]),
             Paragraph(f"Lote {payload['batch_id']} | Competência {payload['competence']}", styles["Normal"])]
    items = sorted(payload.get("items", []), key=lambda i: (i.get("professional_name", ""), i.get("service_date", ""), i.get("sheet", ""), i.get("row", 0)))
    current = None
    for item in items:
        group = (item.get("professional_name"), item.get("service_date"), item.get("sheet") or "—")
        if group != current:
            if current is not None: story.append(PageBreak())
            story.append(Paragraph(f"{group[0]} — {group[1]} — folha {group[2]}", styles["Heading2"]))
            current = group
        patient = item.get("patient", {})
        rows = [["Linha", "Paciente", "CPF/CNS", "Nasc.", "Campo", "Planilha", "PEC", "BPA", "Situação", "Observação"],
                [str(item.get("row", "")), patient.get("name", ""), f"{mask_identifier(patient.get('cpf'))} / {mask_identifier(patient.get('cns'))}",
                 patient.get("birth_date") or item.get("informed_birth", ""), "registro", item.get("original_cid", ""), item.get("final_cid", ""), "",
                 item.get("status", ""), item.get("reason", "") or "; ".join(item.get("warnings", []))]]
        table = Table(rows, colWidths=[12*mm, 36*mm, 34*mm, 20*mm, 18*mm, 22*mm, 22*mm, 18*mm, 38*mm, 55*mm], repeatRows=1)
        table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#14532d")),("TEXTCOLOR",(0,0),(-1,0),colors.white),
                                   ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),6.5),("GRID",(0,0),(-1,-1),0.25,colors.grey),
                                   ("VALIGN",(0,0),(-1,-1),"TOP"),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f0fdf4")])]))
        story.append(table)
    if not items: story.append(Paragraph("Nenhum item no lote.", styles["Normal"]))
    doc.build(story)
    return destination


def generate_ignored_csv(payload: dict, destination: Path) -> Path:
    ignored = [item for item in payload.get("items", []) if item.get("status", "").startswith("IGNORADO") or item.get("status") in {"AGUARDANDO_CONFIRMACAO","ERRO_TECNICO","JA_EXISTE_NO_BPA"}]
    with destination.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["linha", "profissional", "data", "cpf_mascarado", "status", "motivo"])
        for item in ignored:
            writer.writerow([item.get("row"), item.get("professional_name"), item.get("service_date"), mask_identifier(item.get("patient", {}).get("cpf")), item.get("status"), item.get("reason")])
    return destination
