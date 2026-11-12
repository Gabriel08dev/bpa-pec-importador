from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.credentials import write_credential
from app.models import ReviewItem, Status
from app.repositories.audit_repository import AuditRepository
from app.repositories.firebird_repository import FirebirdRepository
from app.repositories.review_repository import ReviewRepository
from app.services.batch_service import BatchService, rows_missing_address, validate_competence
from app.services.dashboard_service import environment_summary, recent_batches
from app.services.divergence_service import generate_ignored_csv, generate_pdf
from app.services.firebird_workflow import FirebirdWorkflow
from app.services.patient_service import valid_cpf
from app.services.pec_service import PecService
from app.services.professional_service import ProfessionalRegistry
from app.services.spreadsheet_service import create_template


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
JOBS: dict[str, dict[str, Any]] = {}
PEC_JOB_KINDS = {"PEC_DISCOVERY", "PEC_CONSULT"}
JOB_START_LOCK = threading.Lock()
def services(request: Request): return request.app.state.services


def start_job(label: str, action, kind: str = "") -> str:
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status":"RUNNING","message":label,"progress":[],"kind":kind}
    def run():
        try:
            result = action(lambda message: JOBS[job_id]["progress"].append(message))
            JOBS[job_id].update(status="DONE", message="Concluído", result=result)
        except Exception as exc:
            JOBS[job_id].update(status="ERROR", message=str(exc))
    threading.Thread(target=run, daemon=True).start()
    return job_id


def pec_job_running() -> bool:
    return any(
        job.get("status") == "RUNNING" and job.get("kind") in PEC_JOB_KINDS
        for job in tuple(JOBS.values())
    )


def start_pec_job(request: Request, label: str, action, kind: str):
    with JOB_START_LOCK:
        if pec_job_running():
            return templates.TemplateResponse(
                request,
                "error.html",
                {"message": "Já existe uma busca em andamento no PEC. Aguarde a conclusão."},
                status_code=409,
            )
        job_id = start_job(label, action, kind=kind)
    return RedirectResponse(f"/trabalho/{job_id}", status_code=303)


@router.get("/health")
def health(): return {"status":"ok"}


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    svc = services(request)
    pec_running = pec_job_running()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "default_competence": datetime.now().strftime("%Y%m"),
            "recent_batches": recent_batches(svc["settings"].outputs),
            "environment": environment_summary(svc["settings"].backups, pec_running),
            "pec_running": pec_running,
        },
    )


@router.get("/modelo.xlsx")
def model(request: Request):
    svc = services(request); target = svc["settings"].outputs / "modelo_controle_atendimentos.xlsx"
    create_template(target, [item.name for item in svc["professionals"].items])
    return FileResponse(target, filename="modelo_controle_atendimentos.xlsx")


@router.post("/importar")
async def import_file(request: Request, competence: str = Form(...), file: UploadFile = File(...)):
    svc = services(request)
    try:
        validate_competence(competence)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": str(exc)},
            status_code=400,
        )
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".xlsx",".csv",".tsv"}: raise HTTPException(400, "Use .xlsx, .csv ou .tsv")
    upload_dir = svc["settings"].outputs / "uploads"; upload_dir.mkdir(exist_ok=True)
    destination = upload_dir / f"{uuid.uuid4().hex}{suffix}"
    with destination.open("wb") as stream: shutil.copyfileobj(file.file, stream)
    try:
        batch_id, _ = svc["batch"].import_spreadsheet(destination, competence)
    except (ValueError, OSError) as exc:
        destination.unlink(missing_ok=True)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": str(exc)},
            status_code=400,
        )
    svc["audit"].record(batch_id, "SPREADSHEET_IMPORTED", {"filename":Path(file.filename or "planilha").name})
    return RedirectResponse(f"/lote/{batch_id}", status_code=303)


@router.post("/pec/lista-atendimentos")
def import_from_pec_list(
    request: Request,
    competence: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
):
    svc = services(request)
    try:
        validate_competence(competence)
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        if end < start:
            raise ValueError("A data final não pode ser anterior à data inicial.")
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": str(exc)},
            status_code=400,
        )
    def action(progress):
        records = svc["pec"].discover(start_date, end_date, progress)
        batch_id, payload = svc["batch"].import_pec_attendances(
            records, competence, start_date, end_date
        )
        svc["audit"].record(
            batch_id,
            "PEC_ATTENDANCE_LIST_IMPORTED",
            {"items": len(payload["items"]), "period_start": start_date, "period_end": end_date},
        )
        return {"redirect": f"/lote/{batch_id}"}

    return start_pec_job(request, "Buscando atendimentos diretamente no PEC", action, "PEC_DISCOVERY")


@router.get("/lote/{batch_id}", response_class=HTMLResponse)
def batch_view(request: Request, batch_id: str):
    svc = services(request)
    filenames = ["revisao_final.json","revisao_planilha.json"]
    payload = None
    for filename in filenames:
        try: payload = svc["reviews"].read(batch_id, filename); break
        except FileNotFoundError: pass
    if payload is None: raise HTTPException(404, "Lote não encontrado")
    preview = None; result = None
    try: preview = svc["reviews"].read(batch_id, "previa_firebird.json")
    except FileNotFoundError: pass
    if preview and preview.get("based_on_review_sha256") != payload.get("review_sha256"):
        preview = None
    try: result = svc["reviews"].read(batch_id, "resultado_gravacao.json")
    except FileNotFoundError: pass
    invalid_cpf_rows = {
        int(item.get("row", 0))
        for item in payload.get("items", [])
        if not valid_cpf(item.get("patient", {}).get("cpf", ""))
    }
    return templates.TemplateResponse(
        request,
        "batch.html",
        {
            "payload": payload,
            "preview": preview,
            "result": result,
            "invalid_cpf_rows": invalid_cpf_rows,
            "missing_address_rows": rows_missing_address(payload),
            "pec_retry_count": sum(
                item.get("status") == Status.ERRO_TECNICO for item in payload.get("items", [])
            ),
        },
    )


@router.post("/lote/{batch_id}/ignorar-sem-endereco")
def ignore_batch_missing_addresses(request: Request, batch_id: str, review_sha256: str = Form(...)):
    svc = services(request)
    try:
        with JOB_START_LOCK:
            if pec_job_running() or any(
                job.get("status") == "RUNNING" and job.get("kind") == "FIREBIRD_WRITE"
                for job in tuple(JOBS.values())
            ):
                raise ValueError("Aguarde a conclusão da consulta ao PEC ou da gravação em andamento.")
            rows = svc["batch"].ignore_missing_addresses(batch_id, review_sha256)
    except (FileNotFoundError, ValueError, OSError) as exc:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "title": "Não foi possível ignorar as linhas sem endereço",
                "message": str(exc),
                "return_url": f"/lote/{batch_id}",
                "return_label": "Voltar ao lote",
            },
            status_code=400,
        )
    if rows:
        svc["audit"].record(batch_id, "BATCH_MISSING_ADDRESSES_IGNORED", {"rows": sorted(rows)})
    return RedirectResponse(f"/lote/{batch_id}", status_code=303)


@router.post("/lote/{batch_id}/linha/{row}/cpf")
def correct_batch_item_cpf(
    request: Request,
    batch_id: str,
    row: int,
    cpf: str = Form(""),
    action: str = Form("correct"),
):
    svc = services(request)
    ignore = action == "ignore"
    try:
        result = svc["batch"].correct_item_cpf(
            batch_id,
            row,
            cpf,
            ignore=ignore,
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "title": "Não foi possível alterar a linha",
                "message": str(exc),
                "guidance": (
                    "Nenhum dado foi gravado no BPA. Confira o CPF ou volte ao lote."
                ),
                "return_url": f"/lote/{batch_id}",
                "return_label": "Voltar ao lote",
            },
            status_code=400,
        )
    svc["audit"].record(
        batch_id,
        "BATCH_ITEM_MANUALLY_REVIEWED",
        {"row": row, "action": result["action"]},
    )
    return RedirectResponse(f"/lote/{batch_id}", status_code=303)


@router.post("/lote/{batch_id}/cids")
async def import_batch_cids(
    request: Request,
    batch_id: str,
    file: UploadFile = File(...),
):
    svc = services(request)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".xlsx", ".csv", ".tsv"}:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "message": "Use uma planilha .xlsx, .csv ou .tsv",
                "return_url": f"/lote/{batch_id}",
                "return_label": "Voltar ao lote",
            },
            status_code=400,
        )

    upload_dir = svc["settings"].outputs / "uploads"
    upload_dir.mkdir(exist_ok=True)
    destination = upload_dir / f"{uuid.uuid4().hex}{suffix}"
    with destination.open("wb") as stream:
        shutil.copyfileobj(file.file, stream)
    try:
        summary = svc["batch"].import_cids_into_pec_batch(
            batch_id,
            destination,
            file.filename or "planilha-cids",
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "title": "Não foi possível cruzar os CIDs",
                "message": str(exc),
                "guidance": (
                    "Nenhum CID duvidoso foi atribuído. Confira CPF, data do atendimento, "
                    "profissional e CID na planilha."
                ),
                "return_url": f"/lote/{batch_id}",
                "return_label": "Voltar ao lote",
            },
            status_code=400,
        )
    finally:
        destination.unlink(missing_ok=True)

    svc["audit"].record(
        batch_id,
        "CIDS_MATCHED_FROM_SPREADSHEET",
        {
            "matched_keys": summary["matched_keys"],
            "matched_items": summary["matched_items"],
            "invalid_rows": len(summary["invalid_rows"]),
            "conflict_rows": len(summary["conflict_rows"]),
        },
    )
    return RedirectResponse(
        f"/lote/{batch_id}",
        status_code=303,
    )


@router.post("/lote/{batch_id}/pec")
def consult_pec(request: Request, batch_id: str):
    svc = services(request)
    def action(progress):
        reviews = svc["reviews"]
        base = reviews.read(batch_id, "revisao_planilha.json")
        reviews.verify_hash(base)
        try:
            previous = reviews.read(batch_id, "revisao_final.json")
        except FileNotFoundError:
            previous = None
        retry_rows = {
            item["row"]
            for item in (previous or {}).get("items", [])
            if item["status"] == Status.ERRO_TECNICO
        }
        resolved_professionals = 0
        if retry_rows:
            reviews.verify_hash(previous)
            if any(previous.get(key) != base.get(key) for key in ("batch_id", "competence", "source_sha256")):
                raise ValueError("A revisão anterior pertence a outra origem. Confira o lote antes de retomar.")
            if [item["row"] for item in previous["items"]] != [item["row"] for item in base["items"]]:
                raise ValueError("As linhas do lote mudaram. Confira a revisão antes de retomar.")
            payload = previous
            items = [
                ReviewItem.from_dict(item) for item in base["items"] if item["row"] in retry_rows
            ]
            progress(f"Retomando {len(items)} linhas que tiveram falha na consulta ao PEC.")
        else:
            payload, resolved_professionals = svc["batch"].resolve_newly_registered_professionals(base)
            if resolved_professionals:
                payload = svc["batch"].update_review(
                    batch_id,
                    payload,
                    filename="revisao_planilha.json",
                )
            items = [ReviewItem.from_dict(item) for item in payload["items"]]
        # A planilha (ou o lote descoberto) define os atendimentos. Aqui o PEC
        # é usado somente para completar/confirmar a identidade cadastral.
        svc["pec"].consult(items, progress, validate_attendance=False)
        for item in items:
            if item.status == Status.PEC_CONFIRMADO and item.original_cid and not item.final_cid:
                item.status = Status.CID_OMITIDO
        if retry_rows:
            current = reviews.read(batch_id, "revisao_final.json")
            current_base = reviews.read(batch_id, "revisao_planilha.json")
            if (
                current.get("review_sha256") != previous.get("review_sha256")
                or current_base.get("review_sha256") != base.get("review_sha256")
            ):
                raise ValueError("A revisão do lote mudou durante a consulta. Abra o lote e tente novamente.")
            retried = {item.row: item.to_dict() for item in items}
            payload["items"] = [retried.get(item["row"], item) for item in payload["items"]]
        else:
            payload["items"] = [item.to_dict() for item in items]
        updated = svc["batch"].update_review(batch_id, payload)
        svc["reviews"].write(batch_id, "lote_revisado.json", updated)
        svc["audit"].record(
            batch_id,
            "PEC_CONSULTED",
            {
                "items": len(payload["items"]),
                "newly_registered_professionals": resolved_professionals,
                "retried_rows": len(retry_rows),
            },
        )
        return {"redirect":f"/lote/{batch_id}"}
    return start_pec_job(request, "Consultando o PEC", action, "PEC_CONSULT")


@router.post("/lote/{batch_id}/relatorio")
def report(request: Request, batch_id: str):
    svc = services(request)
    try: payload = svc["reviews"].read(batch_id, "revisao_final.json")
    except FileNotFoundError: payload = svc["reviews"].read(batch_id, "revisao_planilha.json")
    directory = svc["reviews"].batch_dir(batch_id)
    generate_pdf(payload, directory / "relatorio_divergencias.pdf")
    generate_ignored_csv(payload, directory / "relatorio_ignorados.csv")
    return FileResponse(directory / "relatorio_divergencias.pdf", filename="relatorio_divergencias.pdf")


@router.post("/lote/{batch_id}/previa")
def preview(request: Request, batch_id: str):
    svc = services(request)
    try:
        svc["workflow"].preview(batch_id)
    except (ValueError, RuntimeError, OSError) as exc:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "title": "Não foi possível gerar a prévia do Firebird",
                "message": str(exc),
                "guidance": "Nenhum registro foi gravado. Volte ao lote, corrija a pendência indicada e tente novamente.",
                "return_url": f"/lote/{batch_id}",
                "return_label": "Voltar ao lote",
            },
            status_code=400,
        )
    return RedirectResponse(f"/lote/{batch_id}", status_code=303)


@router.post("/lote/{batch_id}/gravar")
def write(request: Request, batch_id: str, confirmation: str = Form(...)):
    svc = services(request)
    def action(progress):
        progress("Reabrindo e revalidando a base atual")
        progress("Verificando se o BPA está fechado")
        progress("Criando backup antes dos INSERTs")
        result = svc["workflow"].write(batch_id, confirmation)
        progress("Verificando cada registro após o COMMIT")
        return {"redirect":f"/lote/{batch_id}","write_result":result}
    with JOB_START_LOCK:
        job = start_job("Preparando gravação protegida", action, kind="FIREBIRD_WRITE")
    return RedirectResponse(f"/trabalho/{job}", status_code=303)


@router.get("/trabalho/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    if job_id not in JOBS: raise HTTPException(404, "Trabalho não encontrado")
    return templates.TemplateResponse(request, "job.html", {"job_id":job_id})


@router.get("/api/trabalho/{job_id}")
def job_status(job_id: str, after: int = 0):
    if job_id not in JOBS: raise HTTPException(404, "Trabalho não encontrado")
    job = JOBS[job_id].copy()
    job["progress"] = job["progress"][max(after, 0):]
    return JSONResponse(job)


@router.post("/credenciais/firebird")
def firebird_credential(request: Request, username: str = Form(...), password: str = Form(...)):
    write_credential(services(request)["settings"].credential_firebird, username, password)
    return RedirectResponse("/", status_code=303)


@router.post("/credenciais/pec")
def pec_credential(request: Request, username: str = Form(...), password: str = Form(...)):
    write_credential(services(request)["settings"].credential_pec, username, password)
    return RedirectResponse("/", status_code=303)


@router.post("/abrir-backups")
def open_backups(request: Request):
    os.startfile(services(request)["settings"].backups)
    return RedirectResponse("/", status_code=303)


def build_services(settings: Settings) -> dict[str, Any]:
    professionals = ProfessionalRegistry(settings.professionals_file, settings.ceae_cnes)
    reviews = ReviewRepository(settings.outputs); audit = AuditRepository(settings.outputs)
    from app.firebird.legacy_runner import LegacyRunner
    runner = LegacyRunner(settings.helper_query, settings.database, settings.credential_firebird)
    repository = FirebirdRepository(runner)
    return {"settings":settings,"professionals":professionals,"reviews":reviews,"audit":audit,
            "batch":BatchService(professionals,reviews),
            "pec":PecService(settings.pec_url,str(settings.profile),settings.credential_pec),
            "workflow":FirebirdWorkflow(reviews,audit,runner,repository,settings.helper_backup,settings.backups,settings.credential_firebird,settings.database,settings.ceae_cnes)}
