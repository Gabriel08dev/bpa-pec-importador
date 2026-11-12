from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path

from app.models import Professional
from app.services.patient_service import canonical, valid_cns


class ProfessionalRegistry:
    def __init__(self, path: Path, forced_cnes: str = ""):
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.items = [
            Professional(
                **{
                    **item,
                    **(
                        {"cnes": forced_cnes, "create_if_missing": True}
                        if forced_cnes
                        else {}
                    ),
                }
            )
            for item in raw["professionals"]
        ]
        for item in self.items:
            if not valid_cns(item.cns):
                raise ValueError(f"CNS profissional inválido: {item.name}")
            expected = "0301010072" if item.cbo.startswith("225") else "0301010048"
            if item.procedure != expected:
                raise ValueError(f"Procedimento incompatível: {item.name}")

    def by_cns(self, cns: str) -> Professional | None:
        return next((item for item in self.items if item.cns == cns), None)

    def match_name(self, name: str) -> Professional:
        key = canonical(name)
        exact = [item for item in self.items if canonical(item.name) == key]
        if len(exact) == 1:
            return exact[0]
        ranked = sorted(((SequenceMatcher(None, key, canonical(item.name)).ratio(), item) for item in self.items), reverse=True, key=lambda x: x[0])
        if not ranked or ranked[0][0] < 0.72 or (len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.08):
            raise ValueError(f"Profissional não identificado com segurança: {name}")
        return ranked[0][1]
