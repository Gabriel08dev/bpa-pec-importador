from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.repositories.review_repository import ReviewRepository


class AuditRepository:
    def __init__(self, outputs: Path):
        self.reviews = ReviewRepository(outputs)

    def record(self, batch_id: str, event: str, details: dict[str, Any]) -> Path:
        try:
            payload = self.reviews.read(batch_id, "auditoria_importacao.json")
        except FileNotFoundError:
            payload = {"batch_id": batch_id, "events": []}
        payload["events"].append({"at": datetime.now().astimezone().isoformat(timespec="seconds"), "event": event, "details": details})
        return self.reviews.write(batch_id, "auditoria_importacao.json", payload)
