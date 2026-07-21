from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
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
from app.services.batch_service import BatchService
from app.services.divergence_service import generate_ignored_csv, generate_pdf
from app.services.firebird_workflow import FirebirdWorkflow
from app.services.pec_service import PecService
from app.services.professional_service import ProfessionalRegistry
from app.services.spreadsheet_service import create_template


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
JOBS: dict[str, dict[str, Any]] = {}


def services(request: Request): return request.app.state.services


def start_job(label: str, action) -> str:
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status":"RUNNING","message":label,"progress":[]}
    def run():
        try:
            result = action(lambda message: JOBS[job_id]["progress"].append(message))
            JOBS[job_id].update(status="DONE", message="Concluído", result=result)
        except Exception as exc:
            JOBS[job_id].update(status="ERROR", message=str(exc))
    threading.Thread(target=run, daemon=True).start()
    return job_id


@router.get("/health")
def health(): return {"status":"ok"}


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    svc = services(request)
    batches = sorted([path.name for path in svc["settings"].outputs.iterdir() if path.is_dir() and path.name != "uploads"], reverse=True)
    return templates.TemplateResponse(request, "index.html", {"token":request.query_params["token"],"batches":batches})


@router.get("/modelo.xlsx")
def model(request: Request):
    svc = services(request); target = svc["settings"].outputs / "modelo_controle_atendimentos.xlsx"
    create_template(target, [item.name for item in svc["professionals"].items])
    return FileResponse(target, filename="modelo_controle_atendimentos.xlsx")


@router.post("/importar")
async def import_file(request: Request, competence: str = Form(...), file: UploadFile = File(...)):
    svc = services(request); suffix = Path(file.filename or "").suffix.lower()
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
            {"token": request.query_params["token"], "message": str(exc)},
            status_code=400,
        )
    svc["audit"].record(batch_id, "SPREADSHEET_IMPORTED", {"filename":Path(file.filename or "planilha").name})
    return RedirectResponse(f"/lote/{batch_id}?token={request.query_params['token']}", status_code=303)


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
    try: result = svc["reviews"].read(batch_id, "resultado_gravacao.json")
    except FileNotFoundError: pass
    return templates.TemplateResponse(request, "batch.html", {"token":request.query_params["token"],"payload":payload,"preview":preview,"result":result})


@router.post("/lote/{batch_id}/pec")
def consult_pec(request: Request, batch_id: str):
    svc = services(request)
    def action(progress):
        payload = svc["reviews"].read(batch_id, "revisao_planilha.json")
        svc["reviews"].verify_hash(payload)
        items = [ReviewItem.from_dict(item) for item in payload["items"]]
        svc["pec"].consult(items, progress)
        for item in items:
            if item.status == Status.PEC_CONFIRMADO and item.original_cid and not item.final_cid:
                item.status = Status.CID_OMITIDO
        payload["items"] = [item.to_dict() for item in items]
        updated = svc["batch"].update_review(batch_id, payload)
        svc["reviews"].write(batch_id, "lote_revisado.json", updated)
        svc["audit"].record(batch_id, "PEC_CONSULTED", {"items":len(items)})
        return {"redirect":f"/lote/{batch_id}?token={request.query_params['token']}"}
    job = start_job("Consultando o PEC", action)
    return RedirectResponse(f"/trabalho/{job}?token={request.query_params['token']}", status_code=303)


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
                "token": request.query_params["token"],
                "title": "Não foi possível gerar a prévia do Firebird",
                "message": str(exc),
                "guidance": "Nenhum registro foi gravado. Volte ao lote, corrija a pendência indicada e tente novamente.",
                "return_url": f"/lote/{batch_id}?token={request.query_params['token']}",
                "return_label": "Voltar ao lote",
            },
            status_code=400,
        )
    return RedirectResponse(f"/lote/{batch_id}?token={request.query_params['token']}", status_code=303)


@router.post("/lote/{batch_id}/gravar")
def write(request: Request, batch_id: str, confirmation: str = Form(...)):
    svc = services(request)
    def action(progress):
        progress("Reabrindo e revalidando a base atual")
        progress("Verificando se o BPA está fechado")
        progress("Criando backup antes dos INSERTs")
        result = svc["workflow"].write(batch_id, confirmation)
        progress("Verificando cada registro após o COMMIT")
        return {"redirect":f"/lote/{batch_id}?token={request.query_params['token']}","write_result":result}
    job = start_job("Preparando gravação protegida", action)
    return RedirectResponse(f"/trabalho/{job}?token={request.query_params['token']}", status_code=303)


@router.get("/trabalho/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    if job_id not in JOBS: raise HTTPException(404, "Trabalho não encontrado")
    return templates.TemplateResponse(request, "job.html", {"token":request.query_params["token"],"job_id":job_id})


@router.get("/api/trabalho/{job_id}")
def job_status(job_id: str):
    if job_id not in JOBS: raise HTTPException(404, "Trabalho não encontrado")
    return JSONResponse(JOBS[job_id])


@router.post("/credenciais/firebird")
def firebird_credential(request: Request, username: str = Form(...), password: str = Form(...)):
    write_credential(services(request)["settings"].credential_firebird, username, password)
    return RedirectResponse(f"/?token={request.query_params['token']}", status_code=303)


@router.post("/credenciais/pec")
def pec_credential(request: Request, username: str = Form(...), password: str = Form(...)):
    write_credential(services(request)["settings"].credential_pec, username, password)
    return RedirectResponse(f"/?token={request.query_params['token']}", status_code=303)


@router.post("/abrir-backups")
def open_backups(request: Request):
    os.startfile(services(request)["settings"].backups)
    return RedirectResponse(f"/?token={request.query_params['token']}", status_code=303)


def build_services(settings: Settings) -> dict[str, Any]:
    professionals = ProfessionalRegistry(settings.professionals_file)
    reviews = ReviewRepository(settings.outputs); audit = AuditRepository(settings.outputs)
    from app.firebird.legacy_runner import LegacyRunner
    runner = LegacyRunner(settings.helper_query, settings.database, settings.credential_firebird)
    repository = FirebirdRepository(runner)
    return {"settings":settings,"professionals":professionals,"reviews":reviews,"audit":audit,
            "batch":BatchService(professionals,reviews),
            "pec":PecService(settings.pec_url,str(settings.profile),settings.credential_pec),
            "workflow":FirebirdWorkflow(reviews,audit,runner,repository,settings.helper_backup,settings.backups,settings.credential_firebird,settings.database)}
