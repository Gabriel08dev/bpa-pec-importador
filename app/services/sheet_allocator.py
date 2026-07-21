from __future__ import annotations

from collections import defaultdict

from app.models import ExistingProduction, ReviewItem, Status


def _identity(cpf: str, cns: str) -> set[str]:
    return ({"cpf:" + cpf} if cpf else set()) | ({"cns:" + cns} if cns else set())


def allocate(items: list[ReviewItem], existing: list[ExistingProduction]) -> list[ReviewItem]:
    occupied: dict[tuple[str, str, str, str, str], dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    used_sheets: dict[tuple[str, str, str, str], set[int]] = defaultdict(set)
    existing_identities: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in existing:
        group = (row.professional_cns, row.cnes, row.cbo, row.competence)
        used_sheets[group].add(int(row.sheet))
        occupied[group + (row.service_date,)][int(row.sheet)].add(int(row.sequence))
        existing_identities[(row.professional_cns, row.service_date)] |= _identity(row.patient_cpf, row.patient_cns)

    seen: dict[tuple[str, str], set[str]] = defaultdict(set)
    candidates = [i for i in items if i.status in {Status.PEC_CONFIRMADO, Status.PRONTO_PARA_ADICIONAR, Status.CID_OMITIDO}]
    candidates.sort(key=lambda i: (i.professional_cns, i.service_date, i.row))
    for item in candidates:
        ids = _identity(item.patient.cpf, item.patient.cns)
        duplicate_key = (item.professional_cns, item.service_date)
        if ids & existing_identities[duplicate_key]:
            item.status = Status.JA_EXISTE_NO_BPA
            item.reason = "Duplicidade real: mesmo paciente, profissional e data; não será incluído novamente."
            continue
        if ids & seen[duplicate_key]:
            item.status = Status.IGNORADO_DUPLICIDADE
            item.reason = "Duplicidade real: mesmo paciente, profissional e data; não será incluído novamente."
            continue
        seen[duplicate_key] |= ids
        group = (item.professional_cns, item.cnes, item.cbo, item.sources.get("competence", ""))
        date_group = group + (item.service_date,)
        date_sheets = sorted(occupied[date_group])
        chosen = 0
        sequence = 0
        for sheet in date_sheets:
            used_sequences = occupied[date_group][sheet]
            free = next((seq for seq in range(1, 21) if seq not in used_sequences), 0)
            if free:
                chosen, sequence = sheet, free
                break
        if not chosen:
            chosen = max(used_sheets[group], default=0) + 1
            sequence = 1
            used_sheets[group].add(chosen)
        item.sheet, item.sequence = f"{chosen:03d}", f"{sequence:02d}"
        occupied[date_group][chosen].add(sequence)
        item.status = Status.PRONTO_PARA_ADICIONAR
    return items
