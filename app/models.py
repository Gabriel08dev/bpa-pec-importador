from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    REVISAO_INICIAL = "REVISAO_INICIAL"
    PEC_CONFIRMADO = "PEC_CONFIRMADO"
    PRONTO_PARA_ADICIONAR = "PRONTO_PARA_ADICIONAR"
    IGNORADO_PEC = "IGNORADO_PEC"
    IGNORADO_DUPLICIDADE = "IGNORADO_DUPLICIDADE"
    AGUARDANDO_CONFIRMACAO = "AGUARDANDO_CONFIRMACAO"
    CID_OMITIDO = "CID_OMITIDO"
    JA_EXISTE_NO_BPA = "JA_EXISTE_NO_BPA"
    ERRO_TECNICO = "ERRO_TECNICO"


@dataclass
class Professional:
    name: str
    specialty: str
    cns: str
    cbo: str
    cnes: str
    procedure: str
    create_if_missing: bool = False


@dataclass
class Patient:
    name: str = ""
    cpf: str = ""
    cns: str = ""
    birth_date: str = ""
    sex: str = ""
    race: str = ""
    mother_name: str = ""
    father_name: str = ""
    nationality: str = ""
    birth_city: str = ""
    residence_city: str = ""
    ibge: str = ""
    zip_code: str = ""
    street_type: str = "081"
    street: str = ""
    number: str = ""
    complement: str = ""
    neighborhood: str = ""
    phone_area: str = ""
    phone: str = ""
    email: str = ""
    team: str = ""
    unit: str = ""


@dataclass
class ReviewItem:
    row: int
    original_identifier: str
    professional_name: str
    professional_cns: str
    cbo: str
    cnes: str
    procedure: str
    service_date: str
    informed_birth: str
    original_cid: str
    create_professional_if_missing: bool = False
    final_cid: str = ""
    patient: Patient = field(default_factory=Patient)
    sheet: str = ""
    sequence: str = ""
    status: Status = Status.REVISAO_INICIAL
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ReviewItem":
        data = dict(raw)
        data["patient"] = Patient(**data.get("patient", {}))
        data["status"] = Status(data.get("status", Status.REVISAO_INICIAL))
        return cls(**data)


@dataclass
class ExistingProduction:
    professional_cns: str
    cnes: str
    cbo: str
    competence: str
    service_date: str
    sheet: str
    sequence: str
    patient_cpf: str = ""
    patient_cns: str = ""


@dataclass
class Batch:
    batch_id: str
    created_at: str
    origin: str
    source_sha256: str
    review_sha256: str
    competence: str
    items: list[ReviewItem]

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.status.value] = counts.get(item.status.value, 0) + 1
        return {
            "batch_id": self.batch_id,
            "created_at": self.created_at,
            "origin": self.origin,
            "source_sha256": self.source_sha256,
            "review_sha256": self.review_sha256,
            "competence": self.competence,
            "total": len(self.items),
            "counts": counts,
            "professionals": sorted({item.professional_cns for item in self.items}),
            "dates": sorted({item.service_date for item in self.items}),
            "items": [item.to_dict() for item in self.items],
        }
