from __future__ import annotations

from collections import defaultdict

from app.models import PREVIEW_CANDIDATE_STATUSES, ExistingProduction, ReviewItem, Status
from app.services.patient_service import normalize_cpf


def _identity(cpf: str, cns: str) -> set[str]:
    return ({"cpf:" + cpf} if cpf else set()) | ({"cns:" + cns} if cns else set())


def _reserve_slot(
    item: ReviewItem,
    occupied: dict[tuple[str, str, str, str, str], dict[int, set[int]]],
    used_sheets: dict[tuple[str, str, str, str], set[int]],
) -> tuple[str, str]:
    group = (
        item.professional_cns,
        item.cnes,
        item.cbo,
        item.sources.get("competence", ""),
    )
    date_group = group + (item.service_date,)
    for sheet in sorted(occupied[date_group]):
        used_sequences = occupied[date_group][sheet]
        free = next((seq for seq in range(1, 21) if seq not in used_sequences), 0)
        if free:
            occupied[date_group][sheet].add(free)
            return f"{sheet:03d}", f"{free:02d}"
    chosen = max(used_sheets[group], default=0) + 1
    used_sheets[group].add(chosen)
    occupied[date_group][chosen].add(1)
    return f"{chosen:03d}", "01"


def allocate(items: list[ReviewItem], existing: list[ExistingProduction]) -> list[ReviewItem]:
    occupied: dict[tuple[str, str, str, str, str], dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    used_sheets: dict[tuple[str, str, str, str], set[int]] = defaultdict(set)
    existing_identities: dict[tuple[str, str], set[str]] = defaultdict(set)
    existing_rows: dict[tuple[str, str], list[ExistingProduction]] = defaultdict(list)
    for row in existing:
        group = (row.professional_cns, row.cnes, row.cbo, row.competence)
        used_sheets[group].add(int(row.sheet))
        occupied[group + (row.service_date,)][int(row.sheet)].add(int(row.sequence))
        existing_identities[(row.professional_cns, row.service_date)] |= _identity(row.patient_cpf, row.patient_cns)
        existing_rows[(row.professional_cns, row.service_date)].append(row)

    seen: dict[tuple[str, str], set[str]] = defaultdict(set)
    planned_updates: set[tuple[str, str, str, str, str, str, str]] = set()
    candidates = [i for i in items if i.status in PREVIEW_CANDIDATE_STATUSES]
    candidates.sort(key=lambda i: (i.professional_cns, i.service_date, i.row))
    for item in candidates:
        ids = _identity(item.patient.cpf, item.patient.cns)
        duplicate_key = (item.professional_cns, item.service_date)
        if ids & existing_identities[duplicate_key]:
            exact_cpf_matches = [
                row
                for row in existing_rows[duplicate_key]
                if item.patient.cpf
                and normalize_cpf(row.patient_cpf) == normalize_cpf(item.patient.cpf)
            ]
            if len(exact_cpf_matches) == 1:
                target = exact_cpf_matches[0]
                if target.cid.strip():
                    item.status = Status.JA_EXISTE_NO_BPA
                    item.reason = (
                        "Atendimento já existe no BPA com CID preenchido; o valor "
                        "existente será preservado e nenhum campo será modificado."
                    )
                    continue
                needs_cnes = target.cnes != item.cnes
                needs_cid = bool(item.final_cid and not target.cid.strip())
                update_key = (
                    target.professional_cns,
                    target.competence,
                    target.service_date,
                    target.sheet,
                    target.sequence,
                    target.patient_cpf,
                    target.cnes,
                )
                if (needs_cnes or needs_cid) and update_key in planned_updates:
                    item.status = Status.IGNORADO_DUPLICIDADE
                    item.reason = (
                        "Correção já programada por outra linha do lote para o mesmo "
                        "paciente, profissional e data."
                    )
                    continue
                if needs_cnes or needs_cid:
                    planned_updates.add(update_key)
                    seen[duplicate_key] |= ids
                    item.sources.update(
                        {
                            "existing_origin": target.origin,
                            "existing_cnes": target.cnes,
                            "existing_sheet": target.sheet,
                            "existing_sequence": target.sequence,
                            "existing_cid": target.cid,
                        }
                    )
                    if needs_cnes:
                        item.sheet, item.sequence = _reserve_slot(
                            item, occupied, used_sheets
                        )
                        if needs_cid:
                            item.status = Status.PRONTO_PARA_CORRIGIR_CNES_E_CID
                            item.reason = (
                                f"Atendimento será movido do CNES {target.cnes} para o "
                                f"CEAE {item.cnes} e receberá o CID ausente."
                            )
                        else:
                            item.status = Status.PRONTO_PARA_CORRIGIR_CNES
                            item.reason = (
                                f"Atendimento será movido do CNES {target.cnes} para o "
                                f"CEAE {item.cnes}; o CID existente será preservado."
                            )
                    else:
                        item.sheet, item.sequence = target.sheet, target.sequence
                        item.status = Status.PRONTO_PARA_ATUALIZAR_CID
                        item.reason = (
                            "Atendimento já existe no BPA e está sem CID; somente o CID será preenchido."
                        )
                    continue
            item.status = Status.JA_EXISTE_NO_BPA
            if exact_cpf_matches and any(row.cid.strip() for row in exact_cpf_matches):
                item.reason = (
                    "Atendimento já existe no BPA com CID preenchido; o valor existente será preservado."
                )
            elif item.final_cid:
                item.reason = (
                    "Atendimento já existe, mas não há correspondência única por CPF para "
                    "atualizar o CID com segurança."
                )
            else:
                item.reason = (
                    "Duplicidade real: mesmo paciente, profissional e data; "
                    "não será incluído novamente."
                )
            continue
        if ids & seen[duplicate_key]:
            item.status = Status.IGNORADO_DUPLICIDADE
            item.reason = "Duplicidade real: mesmo paciente, profissional e data; não será incluído novamente."
            continue
        seen[duplicate_key] |= ids
        item.sheet, item.sequence = _reserve_slot(item, occupied, used_sheets)
        item.status = Status.PRONTO_PARA_ADICIONAR
    return items
