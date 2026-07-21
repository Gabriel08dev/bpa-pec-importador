from __future__ import annotations

import re
from collections.abc import Callable


def normalize_cid(value: object) -> tuple[str, str]:
    text = str(value or "").strip().upper()
    if not text:
        return "", ""
    if any(separator in text for separator in (",", ";", "/", " E ")):
        return "", "Mais de um CID informado; produção seguirá sem CID"
    normalized = text.replace(".", "").replace(" ", "")
    if not re.fullmatch(r"[A-Z][0-9]{2}[A-Z0-9]?", normalized):
        return "", "CID ilegível/duvidoso; produção seguirá sem CID"
    return normalized, ""


def validate_cid(value: object, exists: Callable[[str], bool] | None = None) -> tuple[str, str]:
    normalized, warning = normalize_cid(value)
    if normalized and exists is not None and not exists(normalized):
        return "", "CID inexistente/duvidoso — produção seguirá sem CID"
    return normalized, warning
