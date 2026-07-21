from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    database: str = r"localhost:C:\Program Files (x86)\Datasus\BPA\BPAMAG.GDB"
    bpa_executable: Path = Path(r"C:\Program Files (x86)\Datasus\BPA\bpamag.exe")
    pec_url: str = "https://pec.exemplo.gov.br"
    credential_firebird: str = "BPA_PEC_IMPORTADOR_FIREBIRD"
    credential_pec: str = "BPA_PEC_IMPORTADOR_PEC"
    outputs: Path = ROOT / "outputs"
    backups: Path = ROOT / "backups"
    profile: Path = ROOT / ".pec_profile"
    helper_query: Path = ROOT / "firebird" / "bin" / "fb_query.exe"
    helper_backup: Path = ROOT / "firebird" / "bin" / "fb_backup.exe"
    professionals_file: Path = ROOT / "data" / "profissionais.json"
    config_file: Path = ROOT / "data" / "config.json"

    @classmethod
    def load(cls) -> "Settings":
        base = cls()
        if not base.config_file.exists():
            return base
        raw = json.loads(base.config_file.read_text(encoding="utf-8"))
        allowed = {"database", "bpa_executable", "pec_url"}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError("Configuração contém chaves não permitidas: " + ", ".join(sorted(unknown)))
        if any("senha" in key.lower() or "password" in key.lower() for key in raw):
            raise ValueError("Senhas não podem ser armazenadas no arquivo de configuração")
        return cls(
            database=str(raw.get("database", base.database)),
            bpa_executable=Path(raw.get("bpa_executable", base.bpa_executable)),
            pec_url=str(raw.get("pec_url", base.pec_url)).rstrip("/"),
        )

    def prepare(self) -> None:
        for directory in (self.outputs, self.backups, self.profile):
            directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.outputs, 0o700)
