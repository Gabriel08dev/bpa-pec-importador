from __future__ import annotations

from app.firebird.legacy_runner import parse_list_output


def verify_output(output: str, expected: int) -> list[str]:
    combined: dict[str, str] = {}
    for row in parse_list_output(output): combined.update(row)
    failures = []
    for index in range(1, expected + 1):
        value = combined.get(f"VERIFY_{index}")
        if value != "1": failures.append(f"Item {index}: verificação retornou {value or 'nenhum resultado'}")
    return failures
