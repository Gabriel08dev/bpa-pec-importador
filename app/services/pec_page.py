from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


def select_pec_page(context: Any, base_url: str) -> Any:
    """Escolhe a aba do PEC e remove somente abas vazias excedentes."""
    pages = [page for page in context.pages if not page.is_closed()]
    expected_host = (urlsplit(base_url).hostname or "").lower()

    pec_pages = [
        page
        for page in pages
        if (urlsplit(page.url).hostname or "").lower() == expected_host
    ]
    if pec_pages:
        selected = pec_pages[-1]
    else:
        blank_pages = [page for page in pages if page.url.lower() == "about:blank"]
        selected = blank_pages[0] if blank_pages else context.new_page()

    for page in pages:
        if page is selected or page.url.lower() != "about:blank":
            continue
        try:
            page.close()
        except Exception:
            # Uma aba vazia que fechou sozinha nao compromete a aba escolhida.
            pass

    selected.bring_to_front()
    return selected


def navigate_pec(page: Any, destination: str) -> None:
    page.goto(destination, wait_until="domcontentloaded")
    page.bring_to_front()
