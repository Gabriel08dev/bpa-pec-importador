from __future__ import annotations

import re
from datetime import datetime

from app.models import ReviewItem


def literal(value: object) -> str:
    if value is None or value == "": return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def age_at(birth: str, service: str) -> int:
    born = datetime.strptime(birth, "%Y%m%d").date(); attended = datetime.strptime(service, "%Y%m%d").date()
    return attended.year - born.year - ((attended.month, attended.day) < (born.month, born.day))


def professional_inserts(item: ReviewItem) -> str:
    if not item.create_professional_if_missing: return ""
    professional_name = item.professional_name[:30]
    return f"""
INSERT INTO CADMED (CADMED_CNS,CADMED_NOME)
SELECT {literal(item.professional_cns)},{literal(professional_name)} FROM RDB$DATABASE
WHERE NOT EXISTS (SELECT 1 FROM CADMED WHERE CADMED_CNS={literal(item.professional_cns)});
INSERT INTO CADMED_CBO_CNES (MED_CNS,MED_CBO,MED_CNES)
SELECT {literal(item.professional_cns)},{literal(item.cbo)},{literal(item.cnes)} FROM RDB$DATABASE
WHERE NOT EXISTS (SELECT 1 FROM CADMED_CBO_CNES WHERE MED_CNS={literal(item.professional_cns)} AND MED_CBO={literal(item.cbo)} AND MED_CNES={literal(item.cnes)});
"""


def patient_insert(item: ReviewItem) -> str:
    p = item.patient
    return f"""
INSERT INTO CADCNS (CNS,NOME,DTNASC,SEXO,RACA,MAEPCN,LOGPCN,NUMPCN,CPLPCN,CEPPCN,NMRES,IBGE,ETNIA,NACIONALIDADE,ID_CADCNS,CO_LOGRAD,BAIRRO_PCNTE,DDTEL_PCNTE,TEL_PCNTE,EMAIL_PCNTE,CHK_SUM,NUM_CPF)
SELECT {literal(p.cns)},{literal(p.name)},{literal(p.birth_date)},{literal(p.sex)},{literal(p.race)},{literal(p.mother_name)},
       {literal(p.street)},{literal(p.number)},{literal(p.complement)},{literal(p.zip_code)},NULL,{literal(p.ibge)},NULL,{literal(p.nationality)},
       (SELECT COALESCE(MAX(ID_CADCNS),0)+1 FROM CADCNS),{literal(p.street_type)},{literal(p.neighborhood)},{literal(p.phone_area)},
       {literal(p.phone)},{literal(p.email)},NULL,{literal(p.cpf)} FROM RDB$DATABASE
WHERE NOT EXISTS (SELECT 1 FROM CADCNS WHERE CNS={literal(p.cns)} OR NUM_CPF={literal(p.cpf)});
"""


def production_insert(item: ReviewItem, competence: str) -> str:
    p = item.patient; age = f"{age_at(p.birth_date, item.service_date):03d}"
    identity = f"(P.PRD_CPF_PCNTE={literal(p.cpf)} OR P.PRD_CNSPAC={literal(p.cns)})"
    return f"""
INSERT INTO S_PRD (PRD_UID,PRD_CMP,PRD_CNSMED,PRD_CBO,PRD_FLH,PRD_SEQ,PRD_PA,PRD_CNSPAC,PRD_NMPAC,PRD_DTNASC,PRD_SEXO,PRD_IBGE,PRD_DTATEN,PRD_CID,PRD_IDADE,PRD_QT_P,PRD_CATEN,PRD_NAUT,PRD_ORG,PRD_MVM,PRD_FLPA,PRD_FLCBO,PRD_FLCA,PRD_FLIDA,PRD_FLQT,PRD_FLER,PRD_FLMUN,PRD_FLCID,PRD_RACA,PRD_SERVICO,PRD_CLASSIFICACAO,PRD_EQUIPE,PRD_ETNIA,PRD_NAC,PRD_ADVQT,PRD_CNPJ,PRD_EQP_AREA,PRD_EQP_SEQ,PRD_LOGRAD_PCNTE,PRD_CEP_PCNTE,PRD_END_PCNTE,PRD_COMPL_PCNTE,PRD_NUM_PCNTE,PRD_BAIRRO_PCNTE,PRD_DDTEL_PCNTE,PRD_TEL_PCNTE,PRD_EMAIL_PCNTE,PRD_INE,CHK_SUM,PRD_CPF_PCNTE,PRD_SITUACAO_RUA,PRD_SEM_CPF)
SELECT {literal(item.cnes)},{literal(competence)},M.CADMED_CNS,{literal(item.cbo)},{literal(item.sheet)},{literal(item.sequence)},
       {literal(item.procedure)},{literal(p.cns)},{literal(p.name)},{literal(p.birth_date)},{literal(p.sex)},{literal(p.ibge)},
       {literal(item.service_date)},{literal(item.final_cid)},{literal(age)},1,'01',NULL,'BPI',{literal(competence)},
       '0','0','0','0','0','0','0','0',{literal(p.race)},'','',NULL,NULL,{literal(p.nationality)},'0 ',NULL,NULL,NULL,
       {literal(p.street_type)},{literal(p.zip_code)},{literal(p.street)},{literal(p.complement)},{literal(p.number)},
       {literal(p.neighborhood)},{literal(p.phone_area)},{literal(p.phone)},{literal(p.email)},'',NULL,{literal(p.cpf)},'N','N'
FROM CADMED M JOIN CADMED_CBO_CNES V ON V.MED_CNS=M.CADMED_CNS
WHERE M.CADMED_CNS={literal(item.professional_cns)} AND V.MED_CBO={literal(item.cbo)} AND V.MED_CNES={literal(item.cnes)}
  AND NOT EXISTS (SELECT 1 FROM S_PRD P WHERE P.PRD_CNSMED={literal(item.professional_cns)} AND P.PRD_DTATEN={literal(item.service_date)} AND {identity});
"""


def verification_select(item: ReviewItem, competence: str, index: int) -> str:
    p = item.patient
    cid_condition = "PRD_CID IS NULL" if not item.final_cid else f"PRD_CID={literal(item.final_cid)}"
    return f"SELECT COUNT(*) AS VERIFY_{index} FROM S_PRD WHERE PRD_CNSMED={literal(item.professional_cns)} AND PRD_UID={literal(item.cnes)} AND PRD_CBO={literal(item.cbo)} AND PRD_CMP={literal(competence)} AND PRD_ORG='BPI' AND PRD_FLH={literal(item.sheet)} AND PRD_SEQ={literal(item.sequence)} AND PRD_DTATEN={literal(item.service_date)} AND PRD_PA={literal(item.procedure)} AND PRD_CPF_PCNTE={literal(p.cpf)} AND PRD_CNSPAC={literal(p.cns)} AND PRD_DTNASC={literal(p.birth_date)} AND {cid_condition};"


def build_insert_script(items: list[ReviewItem], competence: str) -> str:
    statements = ["SET LIST ON;", "SET TRANSACTION;"]
    added_professionals: set[str] = set()
    for item in items:
        if item.professional_cns not in added_professionals:
            statements.append(professional_inserts(item)); added_professionals.add(item.professional_cns)
        statements.append(patient_insert(item)); statements.append(production_insert(item, competence))
    statements.append("COMMIT;")
    statements.append("SET TRANSACTION READ ONLY;")
    statements.extend(verification_select(item, competence, index) for index, item in enumerate(items, 1))
    statements.extend(["ROLLBACK;", "QUIT;"])
    sql = "\n".join(filter(None, statements)).rstrip() + "\n"
    if re.search(r"\b(UPDATE|DELETE)\b", sql, re.I): raise AssertionError("SQL inseguro")
    return sql
