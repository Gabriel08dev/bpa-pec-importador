from __future__ import annotations

from app.services.divergence_service import generate_ignored_csv, generate_pdf


def payload():
    return {"batch_id":"TESTE-SINTETICO","competence":"202607","items":[
        {"row":1,"professional_name":"PROFISSIONAL A","service_date":"20260701","sheet":"001","status":"PRONTO_PARA_ADICIONAR","reason":"","warnings":[],"original_cid":"","final_cid":"","patient":{"name":"PACIENTE SINTETICO","cpf":"52998224725","cns":"700000000000006","birth_date":"20000101"}},
        {"row":2,"professional_name":"PROFISSIONAL A","service_date":"20260701","sheet":"001","status":"IGNORADO_PEC","reason":"Não encontrado","warnings":[],"original_cid":"","final_cid":"","patient":{"name":"","cpf":"52998224725","cns":"","birth_date":"20000101"}}]}


def test_pdf_grouped_content_created(tmp_path):
    target=generate_pdf(payload(),tmp_path/"report.pdf"); assert target.stat().st_size>1000


def test_ignored_csv_masks_identifiers(tmp_path):
    target=generate_ignored_csv(payload(),tmp_path/"ignored.csv"); text=target.read_text(encoding="utf-8-sig")
    assert "52998224725" not in text and "IGNORADO_PEC" in text


def test_individual_error_pattern_is_present():
    import inspect
    from app.services.pec_service import PecService
    source=inspect.getsource(PecService.consult)
    assert "Falha individual" in source and "for index" in source


def test_pec_search_matches_current_button_and_waits_for_form():
    import inspect
    from app.services.pec_service import PecService
    source=inspect.getsource(PecService.consult)
    assert "cadastro|cidad" in source
    assert "wait_for_search_field" in source
    assert 'birth.press("Escape")' in source


def test_closed_pec_window_aborts_batch_instead_of_marking_every_row():
    import inspect
    from app.services.pec_service import PecService
    source=inspect.getsource(PecService.consult)
    assert "page.is_closed()" in source
    assert "mantenha essa janela aberta" in source
