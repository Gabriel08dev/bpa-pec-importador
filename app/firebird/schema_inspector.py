from __future__ import annotations

from app.firebird.legacy_runner import LegacyRunner, parse_list_output


SCHEMA_SQL = """
SET LIST ON;
SET TRANSACTION READ ONLY;
SELECT RF.RDB$RELATION_NAME AS TABLE_NAME, RF.RDB$FIELD_POSITION AS FIELD_POSITION,
       RF.RDB$FIELD_NAME AS COLUMN_NAME, F.RDB$FIELD_LENGTH AS FIELD_LENGTH,
       RF.RDB$NULL_FLAG AS REQUIRED
FROM RDB$RELATION_FIELDS RF JOIN RDB$FIELDS F ON F.RDB$FIELD_NAME=RF.RDB$FIELD_SOURCE
WHERE RF.RDB$RELATION_NAME IN ('CADCNS','CADMED','CADMED_CBO_CNES','S_PRD','S_CID')
ORDER BY RF.RDB$RELATION_NAME, RF.RDB$FIELD_POSITION;
ROLLBACK;
QUIT;
"""


def inspect(runner: LegacyRunner) -> list[dict[str, str]]:
    rows = parse_list_output(runner.run(SCHEMA_SQL))
    required = {"CADCNS", "CADMED", "CADMED_CBO_CNES", "S_PRD", "S_CID"}
    found = {row.get("TABLE_NAME", "").strip() for row in rows}
    if not required <= found:
        raise RuntimeError("Esquema Firebird incompatível; tabelas ausentes: " + ", ".join(sorted(required - found)))
    return rows
