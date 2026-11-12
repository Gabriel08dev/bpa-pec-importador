from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from app.credentials import read_credential


class FirebirdError(RuntimeError):
    pass


FORBIDDEN = re.compile(r"\b(DELETE|MERGE|DROP|ALTER|CREATE|RECREATE)\b", re.I)
UPDATE_STATEMENT = re.compile(r"\bUPDATE\b.*?;", re.I | re.S)
ALLOWED_PRODUCTION_UPDATE = re.compile(
    r"\A\s*UPDATE\s+S_PRD\s+SET\s+(?P<set>.*?)\s+WHERE\s+(?P<where>.*);\s*\Z",
    re.I | re.S,
)
ALLOWED_ASSIGNMENT = re.compile(
    r"\A\s*(PRD_UID|PRD_FLH|PRD_SEQ|PRD_CID|PRD_DTATEN)\s*=\s*'(?:[^']|'')+'\s*\Z",
    re.I,
)


def normalize_sql(sql: str) -> str:
    return sql.rstrip() + "\n"


class LegacyRunner:
    def __init__(self, executable: Path, database: str, credential_target: str):
        self.executable = executable
        self.database = database
        self.credential_target = credential_target

    @staticmethod
    def _validate_cid_updates(sql: str, allow_update: bool) -> None:
        if not re.search(r"\bUPDATE\b", sql, re.I):
            return
        if not allow_update:
            raise FirebirdError("UPDATE recusado em operação que não autorizou correções")
        statements = UPDATE_STATEMENT.findall(sql)
        if not statements:
            raise FirebirdError("UPDATE recusado: instrução incompleta")
        required = (
            "PRD_CNSMED=",
            "PRD_UID=",
            "PRD_CBO=",
            "PRD_CMP=",
            "PRD_DTATEN=",
            "PRD_FLH=",
            "PRD_SEQ=",
            "PRD_CPF_PCNTE=",
        )
        for statement in statements:
            compact = re.sub(r"\s+", " ", statement).upper()
            match = ALLOWED_PRODUCTION_UPDATE.fullmatch(statement)
            where_compact = (
                re.sub(r"\s+", " ", match.group("where")).upper()
                if match
                else ""
            )
            if not match or any(token not in where_compact for token in required):
                raise FirebirdError(
                    "UPDATE recusado: somente CNES/folha/sequência, CID e data podem ser "
                    "corrigidos com identificação exata do atendimento"
                )
            assignments = [part.strip() for part in match.group("set").split(",")]
            if not assignments or any(
                not ALLOWED_ASSIGNMENT.fullmatch(part) for part in assignments
            ):
                raise FirebirdError(
                    "UPDATE recusado: campo não autorizado na produção"
                )
            fields = [part.split("=", 1)[0].strip().upper() for part in assignments]
            if len(fields) != len(set(fields)):
                raise FirebirdError("UPDATE recusado: campo repetido")
            cnes_fields = {"PRD_UID", "PRD_FLH", "PRD_SEQ"}
            if cnes_fields & set(fields) and not cnes_fields <= set(fields):
                raise FirebirdError(
                    "UPDATE recusado: a correção de CNES exige nova folha e sequência"
                )
            if "PRD_CID" in fields and (
                "PRD_CID IS NULL" not in where_compact
                or "PRD_CID=''" not in where_compact
            ):
                raise FirebirdError(
                    "UPDATE recusado: CID existente nunca pode ser sobrescrito"
                )

    def run(
        self,
        sql: str,
        *,
        allow_insert: bool = False,
        allow_update: bool = False,
        timeout: int = 180,
    ) -> str:
        if FORBIDDEN.search(sql):
            raise FirebirdError("SQL recusado: comandos destrutivos ou de alteração de esquema não são permitidos")
        self._validate_cid_updates(sql, allow_update)
        if not allow_insert and re.search(r"\bINSERT\b", sql, re.I):
            raise FirebirdError("INSERT recusado em operação somente leitura")
        credential = read_credential(self.credential_target)
        if not credential:
            raise FirebirdError("Credencial Firebird não configurada no Gerenciador de Credenciais do Windows")
        env = os.environ.copy()
        env["ISC_USER"] = credential[0]
        env["ISC_PASSWORD"] = credential[1]
        fd, name = tempfile.mkstemp(suffix=".sql")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(normalize_sql(sql).encode("cp1252", errors="strict"))
            if not self.executable.is_file():
                raise FirebirdError(
                    "Cliente Firebird não encontrado. Instale o Firebird 1.5 "
                    "ou restaure firebird/bin/fb_query.exe."
                )
            completed = subprocess.run([str(self.executable), self.database, "-i", name], capture_output=True, env=env,
                                       timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            stdout = completed.stdout.decode("cp1252", errors="replace")
            stderr = completed.stderr.decode("cp1252", errors="replace")
            combined = stdout + "\n" + stderr
            if completed.returncode != 0 or "Statement failed" in combined or "SQLCODE" in combined or "Expected end of statement, encountered EOF" in combined:
                raise FirebirdError("Falha completa do Firebird: " + combined.strip())
            return stdout
        finally:
            try: os.unlink(name)
            except OSError: pass


def parse_list_output(output: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        if not line.strip():
            if current: rows.append(current); current = {}
            continue
        match = re.match(r"^([A-Z0-9_$]+)\s+(.*)$", line.rstrip(), re.I)
        if match: current[match.group(1).upper()] = "" if match.group(2).strip() == "<null>" else match.group(2).strip()
    if current: rows.append(current)
    return rows
