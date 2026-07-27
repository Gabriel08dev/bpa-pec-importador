from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _read_payload(directory: Path) -> dict[str, Any] | None:
    for filename in ("revisao_final.json", "revisao_planilha.json"):
        path = directory / filename
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None
    return None


def _batch_state(directory: Path, payload: dict[str, Any]) -> tuple[str, str, bool]:
    files = {path.name for path in directory.iterdir() if path.is_file()}
    incomplete = bool(payload.get("incomplete")) or "incompleto.json" in files
    if incomplete:
        return "Incompleto", "Ver erro", True
    if "resultado_gravacao.json" in files:
        return "Concluído", "Visualizar lote", False
    if "previa_firebird.json" in files:
        return "Aguardando confirmação", "Continuar revisão", False
    if "lote_revisado.json" in files or "revisao_final.json" in files:
        return "Pronto para prévia", "Continuar revisão", False

    statuses = {
        str(item.get("status", ""))
        for item in payload.get("items", [])
        if isinstance(item, dict)
    }
    if statuses and statuses <= {"ERRO_TECNICO"}:
        return "Erro técnico", "Ver erro", False
    return "Aguardando consulta ao PEC", "Continuar revisão", False


def recent_batches(outputs: Path, limit: int = 8) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    if not outputs.is_dir():
        return summaries
    directories = sorted(
        (
            path
            for path in outputs.iterdir()
            if path.is_dir() and path.name != "uploads"
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for directory in directories:
        payload = _read_payload(directory)
        if payload is None:
            continue
        state, action, incomplete = _batch_state(directory, payload)
        created_at = str(payload.get("created_at") or "")
        try:
            created_label = datetime.fromisoformat(created_at).strftime("%d/%m/%Y %H:%M")
        except ValueError:
            created_label = datetime.fromtimestamp(directory.stat().st_mtime).strftime(
                "%d/%m/%Y %H:%M"
            )
        summaries.append(
            {
                "batch_id": directory.name,
                "origin": (
                    "PEC"
                    if payload.get("origin") == "PEC_LISTA_ATENDIMENTOS"
                    else "Planilha"
                ),
                "competence": str(payload.get("competence") or "—"),
                "created_at": created_label,
                "items": len(payload.get("items", [])),
                "state": state,
                "action": action,
                "incomplete": incomplete,
            }
        )
        if len(summaries) >= limit:
            break
    return summaries


def environment_summary(backups: Path, pec_running: bool) -> list[dict[str, str]]:
    statuses = [
        {"label": "Aplicação local", "value": "Ativa", "tone": "active"},
        {"label": "Servidor local", "value": "Ativo", "tone": "active"},
        {
            "label": "PEC",
            "value": "Busca em andamento" if pec_running else "Não iniciado",
            "tone": "busy" if pec_running else "neutral",
        },
    ]
    backup_files = sorted(
        backups.glob("*.fbk") if backups.is_dir() else [],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if backup_files:
        statuses.append(
            {
                "label": "Último backup",
                "value": datetime.fromtimestamp(backup_files[0].stat().st_mtime).strftime(
                    "%d/%m/%Y %H:%M"
                ),
                "tone": "neutral",
            }
        )
    return statuses
