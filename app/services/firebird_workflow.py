from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict
from pathlib import Path

from app.firebird.backup import create_backup
from app.firebird.legacy_runner import LegacyRunner
from app.firebird.schema_inspector import inspect
from app.firebird.sql_builder import (
    CID_UPDATE_STATUSES,
    CNES_UPDATE_STATUSES,
    build_write_script,
)
from app.firebird.verifier import verify_write_output
from app.models import ReviewItem, Status
from app.repositories.audit_repository import AuditRepository
from app.repositories.firebird_repository import FirebirdRepository
from app.repositories.review_repository import ReviewRepository
from app.services.batch_service import BatchService
from app.services.patient_service import valid_cns, valid_cpf
from app.services.sheet_allocator import allocate


def ensure_bpa_closed() -> None:
    completed = subprocess.run(["tasklist.exe", "/FI", "IMAGENAME eq bpamag.exe", "/FO", "CSV", "/NH"], capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if "bpamag.exe" in completed.stdout.decode("cp1252", errors="replace").lower():
        raise RuntimeError("Feche o BPA Magnético antes de preparar ou gravar o lote")


def _items(payload: dict) -> list[ReviewItem]:
    return [ReviewItem.from_dict(item) for item in payload.get("items", [])]


class FirebirdWorkflow:
    def __init__(self, reviews: ReviewRepository, audit: AuditRepository, runner: LegacyRunner, repository: FirebirdRepository,
                 helper_backup: Path, backups: Path, credential_target: str, database: str,
                 target_cnes: str = ""):
        self.reviews = reviews; self.audit = audit; self.runner = runner; self.repository = repository
        self.helper_backup = helper_backup; self.backups = backups; self.credential_target = credential_target; self.database = database
        self.target_cnes = target_cnes

    def preview(self, batch_id: str) -> dict:
        payload = self._load_final(batch_id)
        items = _items(payload)
        candidates = [item for item in items if item.status in {Status.PEC_CONFIRMADO, Status.CID_OMITIDO, Status.PRONTO_PARA_ADICIONAR}]
        if not candidates: raise ValueError("Nenhum item confirmado pelo PEC está apto à prévia")
        if self.target_cnes:
            for item in candidates:
                item.cnes = self.target_cnes
                item.create_professional_if_missing = True
        inspect(self.runner)
        self._validate_items(candidates)
        valid_cids = self.repository.validate_cids([item.final_cid for item in candidates])
        for item in candidates:
            if item.final_cid and item.final_cid not in valid_cids:
                item.warnings.append("CID inexistente/duvidoso — produção seguirá sem CID"); item.final_cid = ""
        errors = self.repository.validate_professionals_and_procedures(candidates, payload["competence"])
        if errors: raise ValueError("; ".join(errors))
        matches = self.repository.patient_matches(candidates)
        for index, item in enumerate(candidates, 1):
            match = matches.get(index)
            if match:
                item.sources["cadcns_match"] = match.get("DB_ID", "")
                if match.get("DB_NAME", "").strip().upper() != item.patient.name.strip().upper():
                    item.warnings.append("Grafia do nome difere do CADCNS; identidade confirmada por CNS/CPF/nascimento e cadastro antigo preservado")
                if match.get("DB_BIRTH") and match.get("DB_BIRTH") != item.patient.birth_date:
                    item.warnings.append("Nascimento do CADCNS diverge do PEC; cadastro antigo preservado, dados PEC usados somente na nova produção")
        existing = self.repository.existing_production(payload["competence"], [item.professional_cns for item in candidates])
        allocate(items, existing)
        preview = dict(payload)
        preview["items"] = [item.to_dict() for item in items]
        preview["based_on_review_sha256"] = payload["review_sha256"]
        preview["manifest_before"] = self.repository.manifest(payload["competence"], [i.professional_cns for i in candidates])
        preview.pop("review_sha256", None)
        preview["review_sha256"] = self.reviews.review_hash(preview)
        self.reviews.write(batch_id, "previa_firebird.json", preview)
        self.audit.record(
            batch_id,
            "PREVIEW_CREATED",
            {
                "ready": sum(i.status == Status.PRONTO_PARA_ADICIONAR for i in items),
                "cid_updates": sum(
                    i.status in CID_UPDATE_STATUSES for i in items
                ),
                "cnes_corrections": sum(
                    i.status in CNES_UPDATE_STATUSES for i in items
                ),
            },
        )
        return preview

    def write(self, batch_id: str, confirmation: str) -> dict:
        if confirmation != "ADICIONAR": raise ValueError("Confirmação inválida; digite exatamente ADICIONAR")
        ensure_bpa_closed()
        final = self._load_final(batch_id)
        preview = self.reviews.read(batch_id, "previa_firebird.json")
        self.reviews.verify_hash(preview)
        if preview.get("based_on_review_sha256") != final.get("review_sha256"):
            raise ValueError("A revisão mudou depois da prévia; gere uma nova prévia")
        inspect(self.runner)
        previous_allocation = {
            item["row"]: (
                item.get("sheet"),
                item.get("sequence"),
                item.get("status"),
                item.get("final_cid"),
                item.get("cnes"),
                item.get("sources", {}).get("existing_cnes"),
                item.get("sources", {}).get("existing_sheet"),
                item.get("sources", {}).get("existing_sequence"),
            )
            for item in preview["items"]
        }
        refreshed = self.preview(batch_id)
        current_allocation = {
            item["row"]: (
                item.get("sheet"),
                item.get("sequence"),
                item.get("status"),
                item.get("final_cid"),
                item.get("cnes"),
                item.get("sources", {}).get("existing_cnes"),
                item.get("sources", {}).get("existing_sheet"),
                item.get("sources", {}).get("existing_sequence"),
            )
            for item in refreshed["items"]
        }
        if current_allocation != previous_allocation:
            raise ValueError("O banco mudou desde a prévia; a prévia foi recalculada. Revise e confirme novamente")
        ready = [item for item in _items(refreshed) if item.status == Status.PRONTO_PARA_ADICIONAR]
        update_items = [
            item
            for item in _items(refreshed)
            if item.status in CID_UPDATE_STATUSES | CNES_UPDATE_STATUSES
        ]
        if not ready and not update_items:
            return {
                "status": "SEM_PENDENCIAS",
                "inserted": 0,
                "updated_cids": 0,
                "corrected_cnes": 0,
                "expected": 0,
                "verified": 0,
                "failures": [],
            }
        affected = ready + update_items
        manifest_before = self.repository.manifest(
            final["competence"], [i.professional_cns for i in affected]
        )
        backup = create_backup(self.helper_backup, self.database, self.backups, self.credential_target)
        output = self.runner.run(
            build_write_script(ready, update_items, final["competence"]),
            allow_insert=bool(
                ready
                or any(item.create_professional_if_missing for item in update_items)
            ),
            allow_update=bool(update_items),
            timeout=600,
        )
        insert_failures, update_failures = verify_write_output(
            output, len(ready), len(update_items)
        )
        failures = insert_failures + update_failures
        manifest_after = self.repository.manifest(
            final["competence"], [i.professional_cns for i in affected]
        )
        inserted_by_manifest = manifest_after["total"] - manifest_before["total"]
        if inserted_by_manifest != len(ready):
            failures.append(f"Manifesto: diferença total {inserted_by_manifest}, esperados {len(ready)} INSERTs autorizados")
        inserted = len(ready) - len(insert_failures)
        failed_update_indexes = {
            int(match.group(1))
            for failure in update_failures
            if (match := re.match(r"Atualização\s+(\d+):", failure))
        }
        successful_updates = [
            item
            for index, item in enumerate(update_items, 1)
            if index not in failed_update_indexes
        ]
        updated_cids = sum(
            item.status in CID_UPDATE_STATUSES for item in successful_updates
        )
        corrected_cnes = sum(
            item.status in CNES_UPDATE_STATUSES for item in successful_updates
        )
        result = {
            "status": "VERIFICADO" if not failures else "VERIFICACAO_COM_FALHAS",
            "inserted": inserted,
            "updated_cids": updated_cids,
            "corrected_cnes": corrected_cnes,
            "updated_records": len(successful_updates),
            "expected": len(ready) + len(update_items),
            "verified": inserted + len(successful_updates),
            "failures": failures,
            "backup": str(backup),
            "manifest_before": manifest_before,
            "manifest_after": manifest_after,
        }
        self.reviews.write(batch_id, "resultado_gravacao.json", result)
        self.audit.record(
            batch_id,
            "WRITE_FINISHED",
            {
                "status": result["status"],
                "inserted": result["inserted"],
                "updated_cids": result["updated_cids"],
                "corrected_cnes": result["corrected_cnes"],
                "backup": backup.name,
            },
        )
        return result

    def _load_final(self, batch_id: str) -> dict:
        try: payload = self.reviews.read(batch_id, "revisao_final.json")
        except FileNotFoundError: payload = self.reviews.read(batch_id, "revisao_planilha.json")
        self.reviews.verify_hash(payload)
        if not BatchService.source_unchanged(payload):
            raise ValueError("A origem do lote foi alterada; gere o lote novamente")
        return payload

    @staticmethod
    def _validate_items(items: list[ReviewItem]) -> None:
        errors = []
        for item in items:
            if not valid_cpf(item.patient.cpf): errors.append(f"linha {item.row}: CPF não confirmado")
            if not valid_cns(item.patient.cns): errors.append(f"linha {item.row}: CNS não confirmado")
            if len(item.patient.birth_date) != 8: errors.append(f"linha {item.row}: nascimento ausente")
            if not item.patient.name: errors.append(f"linha {item.row}: nome PEC ausente")
        if errors: raise ValueError("; ".join(errors))
