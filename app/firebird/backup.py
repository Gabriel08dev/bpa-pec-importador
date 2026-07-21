from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

from app.credentials import read_credential
from app.firebird.legacy_runner import FirebirdError


def create_backup(executable: Path, database: str, destination_dir: Path, credential_target: str) -> Path:
    credential = read_credential(credential_target)
    if not credential: raise FirebirdError("Credencial Firebird não configurada")
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / f"BPAMAG_ANTES_IMPORTACAO_{datetime.now():%Y%m%d_%H%M%S}.fbk"
    env = os.environ.copy(); env["ISC_USER"] = credential[0]; env["ISC_PASSWORD"] = credential[1]
    completed = subprocess.run([str(executable), "-b", "-v", database, str(target)], capture_output=True, env=env, timeout=1800,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    stderr = completed.stderr.decode("cp1252", errors="replace")
    if completed.returncode != 0 or not target.is_file() or target.stat().st_size < 1024:
        if target.exists(): target.unlink()
        raise FirebirdError("Backup não foi concluído: " + stderr.strip())
    return target
