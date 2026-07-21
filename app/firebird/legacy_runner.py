from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from app.credentials import read_credential


class FirebirdError(RuntimeError):
    pass


FORBIDDEN = re.compile(r"\b(UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|RECREATE)\b", re.I)


def normalize_sql(sql: str) -> str:
    return sql.rstrip() + "\n"


class LegacyRunner:
    def __init__(self, executable: Path, database: str, credential_target: str):
        self.executable = executable
        self.database = database
        self.credential_target = credential_target

    def run(self, sql: str, *, allow_insert: bool = False, timeout: int = 180) -> str:
        if FORBIDDEN.search(sql):
            raise FirebirdError("SQL recusado: comandos destrutivos ou de alteração de esquema não são permitidos")
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
