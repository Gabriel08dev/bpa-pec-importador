from __future__ import annotations

from collections import Counter

from app.firebird.legacy_runner import LegacyRunner, parse_list_output
from app.firebird.sql_builder import literal
from app.models import ExistingProduction, ReviewItem
from app.services.patient_service import normalize_service_date


class FirebirdRepository:
    def __init__(self, runner: LegacyRunner):
        self.runner = runner

    def existing_production(self, competence: str, professional_cns: list[str]) -> list[ExistingProduction]:
        if not professional_cns: return []
        values = ",".join(literal(value) for value in sorted(set(professional_cns)))
        sql = f"""
SET LIST ON;
SET TRANSACTION READ ONLY;
SELECT PRD_CNSMED AS PROFESSIONAL_CNS, PRD_UID AS CNES, PRD_CBO AS CBO, PRD_CMP AS COMPETENCE,
       PRD_DTATEN AS SERVICE_DATE, PRD_FLH AS SHEET_NO, PRD_SEQ AS SEQUENCE_NO,
       PRD_CPF_PCNTE AS PATIENT_CPF, PRD_CNSPAC AS PATIENT_CNS,
       PRD_CID AS CID_VALUE, PRD_PA AS PROCEDURE_VALUE, PRD_ORG AS ORIGIN_VALUE
FROM S_PRD WHERE PRD_CMP={literal(competence)} AND PRD_CNSMED IN ({values});
ROLLBACK;
QUIT;
"""
        return [
            ExistingProduction(
                professional_cns=row.get("PROFESSIONAL_CNS", ""),
                cnes=row.get("CNES", ""),
                cbo=row.get("CBO", ""),
                competence=row.get("COMPETENCE", ""),
                service_date=row.get("SERVICE_DATE", ""),
                sheet=row.get("SHEET_NO", ""),
                sequence=row.get("SEQUENCE_NO", ""),
                patient_cpf=row.get("PATIENT_CPF", ""),
                patient_cns=row.get("PATIENT_CNS", ""),
                cid=row.get("CID_VALUE", ""),
                procedure=row.get("PROCEDURE_VALUE", ""),
                origin=row.get("ORIGIN_VALUE", ""),
            )
            for row in parse_list_output(self.runner.run(sql))
        ]

    def correct_swapped_service_dates(
        self, competence: str, rows: list[ExistingProduction]
    ) -> int:
        """Corrige somente linhas cuja inversão dia/mês coincide com a competência."""
        statements: list[str] = []
        for row in rows:
            corrected, warning = normalize_service_date(row.service_date, competence)
            if not warning:
                continue
            statements.append(
                "UPDATE S_PRD SET PRD_DTATEN={new_date} WHERE "
                "PRD_CNSMED={professional} AND PRD_UID={cnes} AND PRD_CBO={cbo} "
                "AND PRD_CMP={competence} AND PRD_DTATEN={old_date} "
                "AND PRD_FLH={sheet} AND PRD_SEQ={sequence} "
                "AND PRD_CPF_PCNTE={cpf} AND PRD_CNSPAC={cns};".format(
                    new_date=literal(corrected),
                    professional=literal(row.professional_cns),
                    cnes=literal(row.cnes),
                    cbo=literal(row.cbo),
                    competence=literal(competence),
                    old_date=literal(row.service_date),
                    sheet=literal(row.sheet),
                    sequence=literal(row.sequence),
                    cpf=literal(row.patient_cpf),
                    cns=literal(row.patient_cns),
                )
            )
        if not statements:
            return 0
        sql = "SET BAIL ON;\nSET TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;\nQUIT;\n"
        self.runner.run(sql, allow_update=True)
        return len(statements)

    def validate_cids(self, cids: list[str]) -> set[str]:
        values = sorted(set(value for value in cids if value))
        if not values: return set()
        # Firebird 1.5 pode emitir "Data type unknown" ao unir vários SELECTs
        # de literais. Consultar diretamente a tabela mantém o tipo de CD_COD.
        cid_list = ",".join(literal(value) for value in values)
        sql = f"SET LIST ON;\nSET TRANSACTION READ ONLY;\nSELECT DISTINCT CD_COD AS CID_VALUE FROM S_CID WHERE CD_COD IN ({cid_list});\nROLLBACK;\nQUIT;\n"
        rows = parse_list_output(self.runner.run(sql))
        return {row["CID_VALUE"] for row in rows if row.get("CID_VALUE")}

    def validate_professionals_and_procedures(self, items: list[ReviewItem], competence: str) -> list[str]:
        unique = {(i.professional_cns, i.cbo, i.cnes, i.procedure, i.create_professional_if_missing) for i in items}
        statements = []
        keys = []
        for index, (cns, cbo, cnes, procedure, allow_missing) in enumerate(sorted(unique), 1):
            keys.append((index, cns, cbo, cnes, procedure, allow_missing))
            statements.append(f"SELECT COUNT(*) AS BIND_{index} FROM CADMED M JOIN CADMED_CBO_CNES V ON V.MED_CNS=M.CADMED_CNS WHERE M.CADMED_CNS={literal(cns)} AND V.MED_CBO={literal(cbo)} AND V.MED_CNES={literal(cnes)};")
            # S_PACBO guarda os nove primeiros dígitos; S_PRD recebe o código SIGTAP completo com dígito verificador.
            statements.append(f"SELECT COUNT(*) AS PROC_{index} FROM S_PACBO WHERE PACBO_PA={literal(procedure[:9])} AND PACBO_CBO={literal(cbo)} AND (PACBO_CMP IS NULL OR PACBO_CMP='' OR PACBO_CMP<={literal(competence)});")
        output = self.runner.run("SET LIST ON;\nSET TRANSACTION READ ONLY;\n" + "\n".join(statements) + "\nROLLBACK;\nQUIT;\n")
        values: dict[str, str] = {}
        for row in parse_list_output(output): values.update(row)
        errors = []
        for index, cns, cbo, cnes, procedure, allow_missing in keys:
            if int(values.get(f"BIND_{index}", "0")) != 1 and not allow_missing:
                errors.append(f"Vínculo técnico ausente ou não único para CNS {cns}, CBO {cbo}, CNES {cnes}")
            if int(values.get(f"PROC_{index}", "0")) < 1:
                errors.append(f"Procedimento {procedure} não habilitado na tabela local para CBO {cbo}")
        return errors

    def patient_matches(self, items: list[ReviewItem]) -> dict[int, dict[str, str]]:
        statements = []
        for index, item in enumerate(items, 1):
            p = item.patient
            cns_filter = f"C.CNS={literal(p.cns)} OR " if p.cns else ""
            cns_priority = (
                f"CASE WHEN C.CNS={literal(p.cns)} THEN 0 ELSE 1 END,\n         " if p.cns else ""
            )
            statements.append(f"""SELECT FIRST 1 {index} AS ITEM_INDEX, C.ID_CADCNS AS DB_ID, C.CNS AS DB_CNS,
 C.NUM_CPF AS DB_CPF, C.DTNASC AS DB_BIRTH, C.NOME AS DB_NAME
FROM CADCNS C WHERE {cns_filter}C.NUM_CPF={literal(p.cpf)}
ORDER BY {cns_priority}CASE WHEN C.NUM_CPF={literal(p.cpf)} THEN 0 ELSE 1 END,
         CASE WHEN C.DTNASC={literal(p.birth_date)} THEN 0 ELSE 1 END, C.ID_CADCNS DESC;""")
        output = self.runner.run("SET LIST ON;\nSET TRANSACTION READ ONLY;\n" + "\n".join(statements) + "\nROLLBACK;\nQUIT;\n")
        return {int(row["ITEM_INDEX"]): row for row in parse_list_output(output) if row.get("ITEM_INDEX")}

    def manifest(self, competence: str, professional_cns: list[str]) -> dict:
        rows = self.existing_production(competence, professional_cns)
        counts = Counter((r.professional_cns, r.service_date, r.sheet) for r in rows)
        return {"total": len(rows), "groups": [{"professional_cns": key[0], "service_date": key[1], "sheet": key[2], "count": value,
                                                  "max_sequence": max(int(r.sequence) for r in rows if (r.professional_cns, r.service_date, r.sheet) == key)}
                                                 for key, value in sorted(counts.items())]}
