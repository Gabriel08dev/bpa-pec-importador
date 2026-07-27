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


def verify_write_output(
    output: str,
    expected_inserts: int,
    expected_updates: int,
) -> tuple[list[str], list[str]]:
    combined: dict[str, str] = {}
    for row in parse_list_output(output):
        combined.update(row)
    insert_failures = []
    update_failures = []
    for index in range(1, expected_inserts + 1):
        value = combined.get(f"VERIFY_{index}")
        if value != "1":
            insert_failures.append(
                f"Inclusão {index}: verificação retornou {value or 'nenhum resultado'}"
            )
    for index in range(1, expected_updates + 1):
        value = combined.get(f"VERIFY_UPDATE_{index}")
        if value != "1":
            update_failures.append(
                f"Atualização {index}: verificação retornou {value or 'nenhum resultado'}"
            )
    return insert_failures, update_failures
