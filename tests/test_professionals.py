from pathlib import Path

import pytest

from app.services.professional_service import ProfessionalRegistry


REGISTRY = ProfessionalRegistry(Path(__file__).parents[1] / "data" / "profissionais.example.json")


def test_all_doctors_use_medical_procedure():
    assert all(item.procedure == "0301010072" for item in REGISTRY.items if item.cbo.startswith("225"))


def test_all_non_doctors_use_nonmedical_procedure():
    assert all(item.procedure == "0301010048" for item in REGISTRY.items if not item.cbo.startswith("225"))


def test_optional_binding_creation():
    item = REGISTRY.by_cns("700000000000013")
    assert (item.cbo, item.cnes, item.procedure, item.create_if_missing) == ("223710","0000000","0301010048",True)


@pytest.mark.parametrize("name", ["Ana Profissional Exemplo", "ANA PROFISSIONAL EXEMPLO", "Ana Prof. Exemplo"])
def test_professional_name_variations(name): assert REGISTRY.match_name(name).cns


def test_professional_is_found_by_cns_despite_name(): assert REGISTRY.by_cns("700000000000005").name.startswith("Ana")
