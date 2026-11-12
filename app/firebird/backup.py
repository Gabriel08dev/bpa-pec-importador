from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from app.credentials import read_credential
from app.firebird.legacy_runner import FirebirdError


BACKUP_NAME = re.compile(r"\ABPAMAG_ANTES_IMPORTACAO_\d{8}_\d{6}\.fbk\Z")
DEFAULT_BACKUP_KEEP = 5


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


def list_backups(directory: Path) -> list[Path]:
    """Backups criados pelo importador, do mais antigo para o mais recente.

    Somente arquivos com o nome exato gerado por create_backup são considerados;
    qualquer outro arquivo da pasta é ignorado e nunca apagado.
    """
    if not directory.is_dir():
        return []
    return sorted(
        (path for path in directory.iterdir() if path.is_file() and BACKUP_NAME.fullmatch(path.name)),
        key=lambda path: path.name,
    )


def prune_backups(directory: Path, keep: int, protect: Path) -> tuple[list[str], list[str]]:
    """Apaga os backups mais antigos, mantendo os `keep` mais recentes.

    O backup recém-criado (`protect`) nunca é apagado. Retorna (removidos, avisos);
    falha ao apagar um arquivo vira aviso e não interrompe a gravação já concluída.
    """
    if keep < 1:
        raise ValueError("A retenção de backups deve manter pelo menos 1 arquivo")
    backups = list_backups(directory)
    excess = backups[:-keep] if len(backups) > keep else []
    removed: list[str] = []
    warnings: list[str] = []
    protected = protect.resolve()
    for path in excess:
        if path.resolve() == protected:
            continue
        try:
            path.unlink()
            removed.append(path.name)
        except OSError as error:
            warnings.append(f"Não foi possível apagar o backup antigo {path.name}: {error}")
    return removed, warnings
