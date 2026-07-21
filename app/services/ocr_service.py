from __future__ import annotations

from pathlib import Path
from typing import Any


def render_pages(source: Path, output: Path, pages: list[int] | None = None, dpi: int = 220) -> list[Path]:
    import fitz
    output.mkdir(parents=True, exist_ok=True)
    document = fitz.open(source)
    selected = pages or list(range(1, len(document) + 1))
    rendered: list[Path] = []
    for page_number in selected:
        if not 1 <= page_number <= len(document):
            raise ValueError(f"Página inválida: {page_number}")
        pixmap = document[page_number - 1].get_pixmap(dpi=dpi, alpha=False)
        target = output / f"pagina_{page_number:03d}.png"
        pixmap.save(target)
        rendered.append(target)
    document.close()
    return rendered


def recognize_cells(image: Path) -> list[dict[str, Any]]:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError("OCR opcional não instalado; execute instalar.ps1 -ComOCR") from exc
    result, _ = RapidOCR()(str(image))
    return [{"text": row[1], "confidence": float(row[2]), "box": row[0], "status": "AGUARDANDO_CONFIRMACAO"} for row in (result or [])]
