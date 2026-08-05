from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models import Patient, ReviewItem, Status
from app.repositories.review_repository import ReviewRepository, content_hash
from app.services.cid_service import normalize_cid
from app.services.patient_service import normalize_cpf, normalize_date, normalize_pec_patient, valid_cpf
from app.services.professional_service import ProfessionalRegistry
from app.services.spreadsheet_service import read_cid_rows, read_rows, sha256_file


CID_CROSS_SOURCE = "PLANILHA_CRUZADA_CPF_DATA_PROFISSIONAL"


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

    def import_pec_attendances(
        self,
        records: list[dict[str, str]],
        competence: str,
        start_date: str,
        end_date: str,
    ) -> tuple[str, dict[str, Any]]:
        competence = validate_competence(competence)
        start, end = normalize_date(start_date), normalize_date(end_date)
        if not start or not end or start > end:
            raise ValueError("Período inválido para consulta no PEC")
        if not records:
            raise ValueError("Nenhum atendimento foi encontrado no período informado")

        batch_id = datetime.now().strftime("%Y%m%d-%H%M%S-PEC-") + uuid.uuid4().hex[:8]
        items: list[ReviewItem] = []
        seen: set[tuple[str, str, str]] = set()
        for row, raw in enumerate(records, 1):
            professional_name = str(raw.get("professional", ""))
            service_date = str(raw.get("service_date", ""))
            patient = Patient(
                name=str(raw.get("name", "")),
                cpf=normalize_cpf(raw.get("cpf", "")),
                cns=str(raw.get("cns", "")),
                birth_date=str(raw.get("birth_date", "")),
            )
            try:
                patient, patient_warnings = normalize_pec_patient(raw)
                professional = self.professionals.match_name(professional_name)
                service_date = normalize_date(service_date)
                birth = normalize_date(raw.get("birth_date", ""))
                cpf = normalize_cpf(raw.get("cpf", ""))
                item = ReviewItem(
                    row=row,
                    original_identifier=cpf,
                    professional_name=professional.name,
                    professional_cns=professional.cns,
                    cbo=professional.cbo,
                    cnes=professional.cnes,
                    procedure=professional.procedure,
                    service_date=service_date,
                    informed_birth=birth,
                    original_cid="",
                    create_professional_if_missing=professional.create_if_missing,
                    patient=patient,
                    sources={
                        "cpf": "PEC_LISTA_ATENDIMENTOS",
                        "birth_date": "PEC_LISTA_ATENDIMENTOS",
                        "professional": "PEC_LISTA_ATENDIMENTOS",
                        "service_date": "PEC_LISTA_ATENDIMENTOS",
                        "pec_lookup": "PEC_LISTA_ATENDIMENTOS",
                        "competence": competence,
                    },
                )
                item.warnings.extend(patient_warnings)
                key = (cpf, professional.cns, service_date)
                if key in seen:
                    item.status = Status.IGNORADO_DUPLICIDADE
                    item.reason = (
                        "Duplicidade real: mesmo paciente, profissional e data; "
                        "não será incluído novamente."
                    )
                else:
                    seen.add(key)
                items.append(item)
            except Exception as exc:
                items.append(
                    ReviewItem(
                        row=row,
                        original_identifier=str(raw.get("cpf", "")),
                        professional_name=professional_name,
                        professional_cns="",
                        cbo="",
                        cnes="",
                        procedure="",
                        service_date=service_date,
                        informed_birth=str(raw.get("birth_date", "")),
                        original_cid="",
                        patient=patient,
                        status=Status.AGUARDANDO_CONFIRMACAO,
                        reason=str(exc),
                        sources={
                            "pec_lookup": "PEC_LISTA_ATENDIMENTOS",
                            "competence": competence,
                        },
                    )
                )

        source_manifest = {
            "method": "PEC_LISTA_ATENDIMENTOS",
            "period_start": start,
            "period_end": end,
            "records_sha256": content_hash(records),
        }
        payload = {
            "batch_id": batch_id,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "origin": "PEC_LISTA_ATENDIMENTOS",
            "source_manifest": source_manifest,
            "source_sha256": content_hash(source_manifest),
            "competence": competence,
            "items": [item.to_dict() for item in items],
        }
        payload["review_sha256"] = self.reviews.review_hash(payload)
        self.reviews.write(batch_id, "revisao_planilha.json", payload)
        return batch_id, payload

    def resolve_newly_registered_professionals(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], int]:
        """Reavalia somente linhas antes barradas por profissional desconhecido."""
        items = [ReviewItem.from_dict(raw) for raw in payload.get("items", [])]
        resolved = 0
        unknown_reason = "Profissional não identificado com segurança:"
        pec_origin = payload.get("origin") == "PEC_LISTA_ATENDIMENTOS"
        seen = {
            (item.patient.cpf, item.professional_cns, item.service_date)
            for item in items
            if item.professional_cns
            and item.patient.cpf
            and item.service_date
            and item.status != Status.IGNORADO_DUPLICIDADE
        }
        for item in items:
            if (
                item.professional_cns
                or item.status != Status.AGUARDANDO_CONFIRMACAO
                or not item.reason.startswith(unknown_reason)
            ):
                continue
            try:
                professional = self.professionals.match_name(item.professional_name)
            except ValueError:
                continue
            item.professional_name = professional.name
            item.professional_cns = professional.cns
            item.cbo = professional.cbo
            item.cnes = professional.cnes
            item.procedure = professional.procedure
            item.create_professional_if_missing = professional.create_if_missing

            # Na importação da planilha, o profissional é validado antes dos
            # demais campos. Se ele ainda não estava cadastrado, CPF, datas e CID
            # permaneceram no formato bruto. Normalize-os agora para que a linha
            # possa de fato prosseguir para a consulta no PEC.
            if not pec_origin:
                try:
                    item.service_date = normalize_date(item.service_date)
                    item.informed_birth = normalize_date(item.informed_birth)
                    item.original_identifier = normalize_cpf(item.original_identifier)
                    item.patient.cpf = item.original_identifier
                    item.patient.birth_date = item.informed_birth
                    item.final_cid, warning = normalize_cid(item.original_cid)
                    if warning and warning not in item.warnings:
                        item.warnings.append(warning)
                    item.sources.update(
                        {
                            "cpf": "PLANILHA",
                            "birth_date": "PLANILHA",
                            "service_date": "PLANILHA",
                            "competence": str(payload.get("competence", "")),
                        }
                    )
                except ValueError as exc:
                    item.reason = str(exc)
                    continue

            item.status = Status.REVISAO_INICIAL
            item.reason = ""
            item.sources["professional"] = (
                "PEC_LISTA_ATENDIMENTOS"
                if pec_origin
                else "LISTA_CEAE"
            )
            key = (item.patient.cpf, item.professional_cns, item.service_date)
            if all(key) and key in seen:
                item.status = Status.IGNORADO_DUPLICIDADE
                item.reason = (
                    "Duplicidade real: mesmo paciente, profissional e data; "
                    "não será incluído novamente."
                )
            elif all(key):
                seen.add(key)
            resolved += 1
        if not resolved:
            return payload, 0
        updated = dict(payload)
        updated["items"] = [item.to_dict() for item in items]
        return updated, resolved

    def correct_item_cpf(
        self,
        batch_id: str,
        row: int,
        cpf: str = "",
        *,
        ignore: bool = False,
    ) -> dict[str, Any]:
        """Corrige o CPF ou ignora uma linha ainda não gravada no BPA."""
        directory = self.reviews.batch_dir(batch_id)
        if (directory / "resultado_gravacao.json").exists():
            raise ValueError("Este lote já foi gravado e não pode mais ser alterado")
        normalized_cpf = ""
        if not ignore:
            normalized_cpf = normalize_cpf(cpf)
            if not valid_cpf(normalized_cpf):
                raise ValueError("Informe um CPF válido com 11 dígitos")

        filenames = (
            "revisao_planilha.json",
            "revisao_final.json",
            "lote_revisado.json",
        )
        prepared: list[tuple[str, dict[str, Any]]] = []
        found = False
        for filename in filenames:
            try:
                payload = self.reviews.read(batch_id, filename)
            except FileNotFoundError:
                continue
            self.reviews.verify_hash(payload)
            items = [ReviewItem.from_dict(raw) for raw in payload.get("items", [])]
            matches = [item for item in items if item.row == row]
            if len(matches) != 1:
                if matches:
                    raise ValueError(f"Linha {row} não possui identificação única")
                continue
            found = True
            item = matches[0]
            if ignore:
                item.status = Status.IGNORADO_PEC
                item.reason = "Linha ignorada manualmente pelo operador antes da prévia."
                item.sources["manual_review"] = "IGNORADO_PELO_OPERADOR"
            else:
                item.patient.cpf = normalized_cpf
                item.original_identifier = normalized_cpf
                item.sources["cpf"] = "CORRECAO_MANUAL"
                warning = "CPF corrigido manualmente antes da prévia"
                if warning not in item.warnings:
                    item.warnings.append(warning)
                if (
                    item.status == Status.AGUARDANDO_CONFIRMACAO
                    and "cpf" in item.reason.lower()
                ):
                    item.status = Status.REVISAO_INICIAL
                    item.reason = ""
            updated = dict(payload)
            updated["items"] = [current.to_dict() for current in items]
            prepared.append((filename, updated))

        if not found:
            raise ValueError(f"Linha {row} não encontrada no lote")
        for filename, payload in prepared:
            self.update_review(batch_id, payload, filename=filename)
        (directory / "previa_firebird.json").unlink(missing_ok=True)
        return {
            "row": row,
            "action": "IGNORED" if ignore else "CPF_CORRECTED",
        }

    def import_cids_into_pec_batch(
        self,
        batch_id: str,
        path: Path,
        source_filename: str,
    ) -> dict[str, Any]:
        """Cruza CIDs por CPF, data e profissional em um lote descoberto no PEC."""
        directory = self.reviews.batch_dir(batch_id)
        if (directory / "resultado_gravacao.json").exists():
            raise ValueError("Este lote já foi gravado no BPA e não pode receber novos CIDs")

        base = self.reviews.read(batch_id, "revisao_planilha.json")
        self.reviews.verify_hash(base)
        if base.get("origin") != "PEC_LISTA_ATENDIMENTOS":
            raise ValueError("O cruzamento de CIDs é permitido somente em lotes criados pela Lista de atendimentos do PEC")

        raw_rows = read_cid_rows(path)
        candidates: dict[tuple[str, str, str], dict[str, Any]] = {}
        invalid_rows: list[dict[str, Any]] = []
        blank_cid_rows: list[int] = []

        for raw in raw_rows:
            row_number = int(raw["row"])
            raw_cid = str(raw.get("cid", "")).strip()
            if not raw_cid:
                blank_cid_rows.append(row_number)
                continue
            try:
                cpf = normalize_cpf(raw.get("cpf", ""))
                if not valid_cpf(cpf):
                    raise ValueError("CPF inválido")
                service_date = normalize_date(raw.get("service_date", ""))
                professional = self.professionals.match_name(str(raw.get("professional", "")))
                cid, warning = normalize_cid(raw_cid)
                if warning or not cid:
                    raise ValueError(warning or "CID inválido")
            except ValueError as exc:
                invalid_rows.append({"row": row_number, "reason": str(exc)})
                continue

            key = (cpf, service_date, professional.cns)
            entry = candidates.setdefault(key, {"cids": set(), "rows": []})
            entry["cids"].add(cid)
            entry["rows"].append(row_number)

        conflicts = {
            key: entry
            for key, entry in candidates.items()
            if len(entry["cids"]) > 1
        }
        cid_by_key = {
            key: next(iter(entry["cids"]))
            for key, entry in candidates.items()
            if len(entry["cids"]) == 1
        }
        if not cid_by_key:
            raise ValueError("Nenhum CID válido e sem conflito foi encontrado na planilha")

        target_item_keys: list[tuple[str, str, str]] = []
        for item in base.get("items", []):
            key = (
                normalize_cpf(item.get("patient", {}).get("cpf", "")),
                str(item.get("service_date", "")),
                str(item.get("professional_cns", "")),
            )
            if valid_cpf(key[0]) and len(key[1]) == 8 and key[2]:
                target_item_keys.append(key)
        target_keys = set(target_item_keys)
        matched_keys = set(cid_by_key) & target_keys
        if not matched_keys:
            raise ValueError(
                "Nenhum CID encontrou correspondência exata no lote por CPF, data do atendimento e profissional"
            )

        unmatched_sheet_rows = sorted(
            row
            for key, entry in candidates.items()
            if key not in target_keys
            for row in entry["rows"]
        )
        conflict_rows = sorted(
            row
            for entry in conflicts.values()
            for row in entry["rows"]
        )
        summary = {
            "imported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "filename": Path(source_filename or path.name).name,
            "source_sha256": sha256_file(path),
            "spreadsheet_rows": len(raw_rows),
            "matched_keys": len(matched_keys),
            "matched_items": 0,
            "attendances_without_cid": sum(key not in matched_keys for key in target_item_keys),
            "unmatched_spreadsheet_rows": unmatched_sheet_rows,
            "blank_cid_rows": blank_cid_rows,
            "invalid_rows": invalid_rows,
            "conflict_rows": conflict_rows,
        }

        def apply_cids(payload: dict[str, Any]) -> dict[str, Any]:
            items = [ReviewItem.from_dict(raw) for raw in payload.get("items", [])]
            matched_items = 0
            for item in items:
                if item.sources.get("cid") == CID_CROSS_SOURCE:
                    item.original_cid = ""
                    item.final_cid = ""
                    item.sources.pop("cid", None)
                key = (normalize_cpf(item.patient.cpf), item.service_date, item.professional_cns)
                cid = cid_by_key.get(key)
                if not cid:
                    continue
                item.original_cid = cid
                item.final_cid = cid
                item.sources["cid"] = CID_CROSS_SOURCE
                if item.status == Status.CID_OMITIDO:
                    item.status = Status.PEC_CONFIRMADO
                matched_items += 1
            updated = dict(payload)
            updated["items"] = [item.to_dict() for item in items]
            updated["cid_import_summary"] = dict(summary, matched_items=matched_items)
            return updated

        try:
            final = self.reviews.read(batch_id, "revisao_final.json")
        except FileNotFoundError:
            final = None
        if final is not None:
            self.reviews.verify_hash(final)

        prepared_base = apply_cids(base)
        prepared_final = apply_cids(final) if final is not None else None
        updated_base = self.update_review(
            batch_id,
            prepared_base,
            filename="revisao_planilha.json",
        )
        summary["matched_items"] = updated_base["cid_import_summary"]["matched_items"]

        if prepared_final is not None:
            updated_final = self.update_review(batch_id, prepared_final)
            self.reviews.write(batch_id, "lote_revisado.json", updated_final)

        (directory / "previa_firebird.json").unlink(missing_ok=True)
        return summary

    def update_review(self, batch_id: str, payload: dict[str, Any], filename: str = "revisao_final.json") -> dict[str, Any]:
        payload = dict(payload)
        payload.pop("review_sha256", None)
        payload["review_sha256"] = self.reviews.review_hash(payload)
        self.reviews.write(batch_id, filename, payload)
        return payload

    @staticmethod
    def source_unchanged(payload: dict[str, Any]) -> bool:
        if payload.get("origin") == "PEC_LISTA_ATENDIMENTOS":
            manifest = payload.get("source_manifest")
            return isinstance(manifest, dict) and content_hash(manifest) == payload.get("source_sha256")
        source = Path(payload["origin"])
        return source.is_file() and sha256_file(source) == payload.get("source_sha256")
