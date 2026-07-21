from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def content_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


class ReviewRepository:
    def __init__(self, outputs: Path):
        self.outputs = outputs

    def batch_dir(self, batch_id: str) -> Path:
        safe = "".join(c for c in batch_id if c.isalnum() or c in "-_")
        if not safe or safe != batch_id:
            raise ValueError("ID de lote inválido")
        path = self.outputs / safe
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write(self, batch_id: str, filename: str, payload: Any) -> Path:
        target = self.batch_dir(batch_id) / filename
        fd, temporary = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return target

    def read(self, batch_id: str, filename: str) -> dict[str, Any]:
        return json.loads((self.batch_dir(batch_id) / filename).read_text(encoding="utf-8"))

    def review_hash(self, payload: dict[str, Any]) -> str:
        clean = dict(payload)
        clean.pop("review_sha256", None)
        return content_hash(clean)

    def verify_hash(self, payload: dict[str, Any]) -> None:
        expected = payload.get("review_sha256", "")
        if not expected or expected != self.review_hash(payload):
            raise ValueError("Hash da revisão alterado; a prévia foi invalidada")
