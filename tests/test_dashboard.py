import json

from app.services.dashboard_service import environment_summary, recent_batches


def test_recent_batches_exposes_only_safe_operational_metadata(tmp_path):
    batch = tmp_path / "20260722-120000-PEC-synthetic"
    batch.mkdir()
    payload = {
        "batch_id": batch.name,
        "created_at": "2026-07-22T12:00:00-03:00",
        "origin": "PEC_LISTA_ATENDIMENTOS",
        "competence": "202607",
        "items": [
            {
                "status": "REVISAO_INICIAL",
                "patient": {"name": "PACIENTE SINTETICO", "cpf": "52998224725"},
            }
        ],
    }
    (batch / "revisao_planilha.json").write_text(json.dumps(payload), encoding="utf-8")

    summaries = recent_batches(tmp_path)

    assert summaries == [
        {
            "batch_id": batch.name,
            "origin": "PEC",
            "competence": "202607",
            "created_at": "22/07/2026 12:00",
            "items": 1,
            "state": "Aguardando consulta ao PEC",
            "action": "Continuar revisão",
            "incomplete": False,
        }
    ]
    serialized = json.dumps(summaries)
    assert "PACIENTE" not in serialized and "52998224725" not in serialized


def test_incomplete_batch_is_highlighted_without_being_modified(tmp_path):
    batch = tmp_path / "LOTE-INCOMPLETO"
    batch.mkdir()
    payload = {
        "created_at": "2026-07-22T12:00:00-03:00",
        "origin": "PEC_LISTA_ATENDIMENTOS",
        "competence": "202607",
        "incomplete": True,
        "items": [],
    }
    source = batch / "revisao_planilha.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    before = source.read_bytes()

    summary = recent_batches(tmp_path)[0]

    assert summary["state"] == "Incompleto"
    assert summary["action"] == "Ver erro"
    assert summary["incomplete"] is True
    assert source.read_bytes() == before


def test_environment_summary_reports_only_verified_local_state(tmp_path):
    statuses = environment_summary(tmp_path, pec_running=False)
    assert statuses == [
        {"label": "Aplicação local", "value": "Ativa", "tone": "active"},
        {"label": "Servidor local", "value": "Ativo", "tone": "active"},
        {"label": "PEC", "value": "Não iniciado", "tone": "neutral"},
    ]
