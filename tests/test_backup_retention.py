import json
import os

import pytest

from app.config import Settings
from app.firebird.backup import list_backups, prune_backups
from app.services import firebird_workflow as workflow_module
from app.services.dashboard_service import environment_summary
from app.services.firebird_workflow import FirebirdWorkflow


def _backup(directory, stamp, size=10):
    path = directory / f"BPAMAG_ANTES_IMPORTACAO_{stamp}.fbk"
    path.write_bytes(b"x" * size)
    return path


def test_prune_keeps_most_recent_by_name_and_ignores_other_files(tmp_path):
    stamps = [
        "20260901_130914", "20260901_133017", "20260916_104101",
        "20260917_085417", "20261008_085201", "20261110_102615", "20261112_090000",
    ]
    created = [_backup(tmp_path, stamp) for stamp in stamps]
    # mtime invertido: a ordem deve vir do carimbo no nome, não da data do arquivo.
    for index, path in enumerate(created):
        os.utime(path, (1_000_000 - index, 1_000_000 - index))
    manual = tmp_path / "BPAMAG_copia_manual.fbk"
    manual.write_bytes(b"manual")
    other = tmp_path / "anotacoes.txt"
    other.write_text("não apagar", encoding="utf-8")

    removed, warnings = prune_backups(tmp_path, keep=5, protect=created[-1])

    assert removed == [created[0].name, created[1].name]
    assert warnings == []
    assert [path.name for path in list_backups(tmp_path)] == [path.name for path in created[2:]]
    assert manual.exists() and other.exists()


def test_prune_does_nothing_when_within_limit(tmp_path):
    created = [_backup(tmp_path, f"2026111{day}_080000") for day in range(3)]
    assert prune_backups(tmp_path, keep=5, protect=created[-1]) == ([], [])
    assert len(list_backups(tmp_path)) == 3


def test_prune_never_deletes_protected_backup(tmp_path):
    created = [_backup(tmp_path, f"2026111{day}_080000") for day in range(3)]
    removed, _ = prune_backups(tmp_path, keep=1, protect=created[0])
    assert created[0].exists()
    assert removed == [created[1].name]
    assert created[2].exists()


def test_prune_reports_failure_without_raising(tmp_path, monkeypatch):
    created = [_backup(tmp_path, f"2026111{day}_080000") for day in range(3)]
    original_unlink = type(created[0]).unlink

    def failing_unlink(self, *args, **kwargs):
        if self.name == created[0].name:
            raise PermissionError("arquivo em uso")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(type(created[0]), "unlink", failing_unlink)
    removed, warnings = prune_backups(tmp_path, keep=1, protect=created[-1])
    assert removed == [created[1].name]
    assert len(warnings) == 1 and created[0].name in warnings[0]


def test_prune_rejects_keep_below_one(tmp_path):
    with pytest.raises(ValueError):
        prune_backups(tmp_path, keep=0, protect=tmp_path / "x.fbk")


def test_list_backups_missing_directory(tmp_path):
    assert list_backups(tmp_path / "nao_existe") == []


@pytest.mark.parametrize("value", [0, 51, "5", True, 2.5])
def test_config_rejects_invalid_backup_keep(tmp_path, value):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"backups_manter": value}), encoding="utf-8")
    with pytest.raises(ValueError, match="backups_manter"):
        Settings.load(config)


def test_config_accepts_backup_keep_and_defaults_to_five(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"backups_manter": 8}), encoding="utf-8")
    assert Settings.load(config).backup_keep == 8
    assert Settings().backup_keep == 5


def test_dashboard_shows_backup_count_and_size(tmp_path):
    _backup(tmp_path, "20261110_080000", size=1024 * 1024)
    _backup(tmp_path, "20261111_080000", size=2 * 1024 * 1024)
    statuses = {item["label"]: item["value"] for item in environment_summary(tmp_path, pec_running=False)}
    assert statuses["Backups guardados"] == "2 · 3 MB"


class _Reviews:
    def __init__(self):
        self.written = {}

    def read(self, batch_id, name):
        return {"items": []}

    def verify_hash(self, payload):
        return None

    def write(self, batch_id, name, payload):
        self.written[name] = payload


class _Audit:
    def __init__(self):
        self.events = []

    def record(self, batch_id, event, data):
        self.events.append((event, data))


class _Repository:
    def __init__(self, totals):
        self.totals = list(totals)

    def manifest(self, competence, professionals):
        return {"total": self.totals.pop(0)}


class _Runner:
    def run(self, sql, **kwargs):
        return "ok"


def _workflow(tmp_path, monkeypatch, *, verify_failures):
    backups = tmp_path / "backups"
    backups.mkdir()
    for day in range(1, 7):
        _backup(backups, f"2026110{day}_080000")
    new_backup = backups / "BPAMAG_ANTES_IMPORTACAO_20261112_090000.fbk"

    def fake_create_backup(*args, **kwargs):
        new_backup.write_bytes(b"novo")
        return new_backup

    ready = type("Item", (), {"status": workflow_module.Status.PRONTO_PARA_ADICIONAR,
                              "professional_cns": "700000000000005",
                              "create_professional_if_missing": False})()
    monkeypatch.setattr(workflow_module, "ensure_bpa_closed", lambda: None)
    monkeypatch.setattr(workflow_module, "inspect", lambda runner: None)
    monkeypatch.setattr(workflow_module, "create_backup", fake_create_backup)
    monkeypatch.setattr(workflow_module, "build_write_script", lambda *a: "SQL")
    monkeypatch.setattr(workflow_module, "verify_write_output",
                        lambda *a: (verify_failures, []))
    monkeypatch.setattr(workflow_module, "_items", lambda payload: [ready])
    monkeypatch.setattr(FirebirdWorkflow, "_load_final",
                        lambda self, batch_id: {"competence": "202611", "review_sha256": "h"})
    monkeypatch.setattr(FirebirdWorkflow, "preview",
                        lambda self, batch_id: {"items": []})
    reviews = _Reviews()
    reviews.read = lambda batch_id, name: {"items": [], "based_on_review_sha256": "h"}
    audit = _Audit()
    workflow = FirebirdWorkflow(reviews, audit, _Runner(), _Repository([10, 11]),
                                tmp_path / "gbak.exe", backups, "alvo", "db", backup_keep=5)
    return workflow, reviews, audit, backups


def test_successful_write_prunes_old_backups(tmp_path, monkeypatch):
    workflow, reviews, audit, backups = _workflow(tmp_path, monkeypatch, verify_failures=[])
    result = workflow.write("lote", "ADICIONAR")
    assert result["status"] == "VERIFICADO"
    assert result["backups_removed"] == [
        "BPAMAG_ANTES_IMPORTACAO_20261101_080000.fbk",
        "BPAMAG_ANTES_IMPORTACAO_20261102_080000.fbk",
    ]
    assert len(list_backups(backups)) == 5
    assert (backups / "BPAMAG_ANTES_IMPORTACAO_20261112_090000.fbk").exists()
    assert reviews.written["resultado_gravacao.json"]["backups_removed"] == result["backups_removed"]
    assert audit.events[-1][1]["backups_removed"] == result["backups_removed"]


def test_write_with_failures_keeps_every_backup(tmp_path, monkeypatch):
    workflow, _, _, backups = _workflow(tmp_path, monkeypatch, verify_failures=["Inclusão 1: ausente"])
    result = workflow.write("lote", "ADICIONAR")
    assert result["status"] == "VERIFICACAO_COM_FALHAS"
    assert "backups_removed" not in result
    assert len(list_backups(backups)) == 7
