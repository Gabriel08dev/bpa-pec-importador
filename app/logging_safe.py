from __future__ import annotations

import logging
import re


_CPF = re.compile(r"(?<!\d)(\d{3})\.?\d{3}\.?\d{3}-?(\d{2})(?!\d)")
_CNS = re.compile(r"(?<!\d)(\d{3})\d{9}(\d{3})(?!\d)")


def mask_identifier(value: str | None) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11:
        return f"{digits[:3]}.***.***-{digits[-2:]}"
    if len(digits) == 15:
        return f"{digits[:3]}*********{digits[-3:]}"
    return "***" if digits else ""


def sanitize(message: object) -> str:
    text = str(message)
    text = _CPF.sub(lambda m: f"{m.group(1)}.***.***-{m.group(2)}", text)
    return _CNS.sub(lambda m: f"{m.group(1)}*********{m.group(2)}", text)


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = sanitize(record.getMessage())
        record.args = ()
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.addFilter(SensitiveDataFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
