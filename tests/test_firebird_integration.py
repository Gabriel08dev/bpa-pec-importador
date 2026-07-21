from __future__ import annotations

import os

import pytest

from app.config import Settings
from app.firebird.legacy_runner import LegacyRunner, parse_list_output
from app.services.firebird_workflow import ensure_bpa_closed


@pytest.mark.firebird
def test_temporary_insert_is_visible_then_rolled_back():
    if os.environ.get("RUN_FIREBIRD_ROLLBACK") != "ADICIONAR":
        pytest.skip("Defina RUN_FIREBIRD_ROLLBACK=ADICIONAR para o teste controlado")
    ensure_bpa_closed()
    settings=Settings.load(); runner=LegacyRunner(settings.helper_query,settings.database,settings.credential_firebird)
    synthetic="799999999999990"
    cleanup=f"SELECT COUNT(*) AS AFTER_COUNT FROM CADMED WHERE CADMED_CNS='{synthetic}'"
    before=parse_list_output(runner.run(f"SET LIST ON;\nSET TRANSACTION READ ONLY;\n{cleanup};\nROLLBACK;\nQUIT;\n"))
    assert before[0]["AFTER_COUNT"]=="0"
    sql=f"""SET LIST ON;
SET TRANSACTION;
INSERT INTO CADMED (CADMED_CNS,CADMED_NOME) SELECT '{synthetic}','TESTE SINTETICO ROLLBACK' FROM RDB$DATABASE
WHERE NOT EXISTS (SELECT 1 FROM CADMED WHERE CADMED_CNS='{synthetic}');
SELECT COUNT(*) AS INSIDE_COUNT FROM CADMED WHERE CADMED_CNS='{synthetic}';
ROLLBACK;
SET TRANSACTION READ ONLY;
{cleanup};
ROLLBACK;
QUIT;
"""
    rows=parse_list_output(runner.run(sql,allow_insert=True))
    values={key:value for row in rows for key,value in row.items()}
    assert values["INSIDE_COUNT"]=="1" and values["AFTER_COUNT"]=="0"
