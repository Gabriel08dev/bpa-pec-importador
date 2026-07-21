from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models import Patient, ReviewItem, Status
from app.repositories.review_repository import ReviewRepository
from app.services.cid_service import normalize_cid
from app.services.patient_service import normalize_cpf, normalize_date
from app.services.professional_service import ProfessionalRegistry
from app.services.spreadsheet_service import read_rows, sha256_file


def validate_competence(value: str) -> str:
    if len(value) != 6 or not value.isdigit() or not 1 <= int(value[4:]) <= 12:
        raise ValueError("Competência deve estar no formato AAAAMM")
    return value


class BatchService:
    def __init__(self, professionals: ProfessionalRegistry, reviews: ReviewRepository):
        self.professionals = professionals
        self.reviews = reviews

    def import_spreadsheet(self, path: Path, competence: str) -> tuple[str, dict[str, Any]]:
        competence = validate_competence(competence)
        batch_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        items: list[ReviewItem] = []
        seen: set[tuple[str, str, str]] = set()
        for raw in read_rows(path):
            try:
                professional = self.professionals.match_name(raw["professional"])
                service_date = normalize_date(raw["service_date"])
                birth = normalize_date(raw["birth_date"])
                cpf = normalize_cpf(raw["cpf"])
                cid, warning = normalize_cid(raw.get("cid", ""))
                item = ReviewItem(
                    row=int(raw["row"]), original_identifier=str(raw["cpf"]), professional_name=professional.name,
                    professional_cns=professional.cns, cbo=professional.cbo, cnes=professional.cnes,
                    procedure=professional.procedure, service_date=service_date, informed_birth=birth,
                    original_cid=str(raw.get("cid", "")), create_professional_if_missing=professional.create_if_missing,
                    final_cid=cid, patient=Patient(cpf=cpf, birth_date=birth),
                    sources={"cpf":"PLANILHA","birth_date":"PLANILHA","professional":"LISTA_CEAE","service_date":"PLANILHA","competence":competence},
                )
                if warning: item.warnings.append(warning)
                key = (cpf, professional.cns, service_date)
                if key in seen:
                    item.status = Status.IGNORADO_DUPLICIDADE
                    item.reason = "Duplicidade real: mesmo paciente, profissional e data; não será incluído novamente."
                else:
                    seen.add(key)
                items.append(item)
            except Exception as exc:
                items.append(ReviewItem(row=int(raw["row"]), original_identifier=str(raw.get("cpf", "")), professional_name=str(raw.get("professional", "")),
                                        professional_cns="", cbo="", cnes="", procedure="", service_date=str(raw.get("service_date", "")),
                                        informed_birth=str(raw.get("birth_date", "")), original_cid=str(raw.get("cid", "")), status=Status.AGUARDANDO_CONFIRMACAO, reason=str(exc)))
        payload = {
            "batch_id": batch_id, "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "origin": str(path), "source_sha256": sha256_file(path), "competence": competence,
            "items": [item.to_dict() for item in items],
        }
        payload["review_sha256"] = self.reviews.review_hash(payload)
        self.reviews.write(batch_id, "revisao_planilha.json", payload)
        return batch_id, payload

    def update_review(self, batch_id: str, payload: dict[str, Any], filename: str = "revisao_final.json") -> dict[str, Any]:
        payload = dict(payload)
        payload.pop("review_sha256", None)
        payload["review_sha256"] = self.reviews.review_hash(payload)
        self.reviews.write(batch_id, filename, payload)
        return payload

    @staticmethod
    def source_unchanged(payload: dict[str, Any]) -> bool:
        source = Path(payload["origin"])
        return source.is_file() and sha256_file(source) == payload.get("source_sha256")
