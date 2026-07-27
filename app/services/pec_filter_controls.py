from __future__ import annotations

import re
from typing import Any

from app.services.patient_service import canonical


def set_checked(page: Any, label: str, wanted: bool, visible) -> bool:
    """Altera um checkbox/switch pelo rótulo, inclusive no Angular Material antigo."""
    for role in ("checkbox", "switch"):
        control = page.get_by_role(role, name=label, exact=True).first
        if not visible(control):
            continue
        changed = control.evaluate(
            """(element, wanted) => {
                const isInput = element.matches?.('input[type="checkbox"]');
                const checked = isInput
                    ? Boolean(element.checked)
                    : element.getAttribute('aria-checked') === 'true';
                if (checked !== wanted) element.click();
                return isInput
                    ? Boolean(element.checked) === wanted
                    : (element.getAttribute('aria-checked') === 'true') === wanted;
            }""",
            wanted,
        )
        if changed:
            return True

    labels = page.get_by_text(label, exact=True)
    for index in range(labels.count()):
        text = labels.nth(index)
        if not text.is_visible():
            continue
        changed = text.evaluate(
            """(element, wanted) => {
                let node = element;
                for (let depth = 0; node && depth < 10; depth++, node = node.parentElement) {
                    const inputs = [];
                    if (node.matches?.('input[type="checkbox"]')) inputs.push(node);
                    for (const input of node.querySelectorAll?.('input[type="checkbox"]') || []) {
                        if (!inputs.includes(input)) inputs.push(input);
                    }
                    if (inputs.length === 1) {
                        if (Boolean(inputs[0].checked) !== wanted) inputs[0].click();
                        return Boolean(inputs[0].checked) === wanted;
                    }
                    const roles = [];
                    if (node.matches?.('[role="checkbox"],[role="switch"]')) roles.push(node);
                    for (const role of node.querySelectorAll?.('[role="checkbox"],[role="switch"]') || []) {
                        if (!roles.includes(role)) roles.push(role);
                    }
                    if (roles.length === 1) {
                        const checked = roles[0].getAttribute('aria-checked') === 'true';
                        if (checked !== wanted) roles[0].click();
                        return (roles[0].getAttribute('aria-checked') === 'true') === wanted;
                    }
                }
                return false;
            }""",
            wanted,
        )
        if changed:
            return True
    return False


def date_range_inputs(page: Any, visible) -> list[Any]:
    """Localiza somente os dois campos do período dentro do painel de filtros."""
    heading = page.get_by_text("Status do atendimento", exact=True).first
    if not visible(heading):
        return []
    panel = heading.locator(
        "xpath=ancestor::*[.//button[normalize-space()='Filtrar']][1]"
    )
    if not visible(panel):
        return []
    candidates: list[Any] = []
    inputs = panel.locator("input")
    for index in range(inputs.count()):
        field = inputs.nth(index)
        if not field.is_visible():
            continue
        value = field.input_value()
        placeholder = field.get_attribute("placeholder") or ""
        aria_label = field.get_attribute("aria-label") or ""
        descriptor = canonical(f"{placeholder} {aria_label}")
        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", value) or "DD MM" in descriptor or "DATA" in descriptor:
            candidates.append(field)
    return candidates if len(candidates) == 2 else []


def click_action_by_text(page: Any, text_value: str) -> bool:
    """Aciona controles que parecem botões, mas não expõem role acessível."""
    return bool(
        page.evaluate(
            r"""(expected) => {
                const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim().toUpperCase();
                const wanted = normalize(expected);
                const preferred = [...document.querySelectorAll('button, [role="button"], a')]
                    .find((element) => normalize(element.innerText || element.textContent) === wanted);
                if (preferred) {
                    preferred.click();
                    return true;
                }
                const leaf = [...document.querySelectorAll('span, div')]
                    .find((element) => normalize(element.innerText || element.textContent) === wanted);
                if (leaf) {
                    leaf.click();
                    return true;
                }
                return false;
            }""",
            text_value,
        )
    )
