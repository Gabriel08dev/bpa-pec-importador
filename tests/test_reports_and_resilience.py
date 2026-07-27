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
    from app.services.pec_browser import consult_pec
    source=inspect.getsource(consult_pec)
    assert "Falha individual" in source and "for index" in source


def test_pec_search_matches_current_button_and_waits_for_form():
    import inspect
    from app.services.pec_browser import _local_result_count, consult_pec
    source=inspect.getsource(consult_pec)
    assert "cadastro|cidad" in source
    assert "wait_for_search_field" in source
    assert "A data de nascimento é" in source
    assert "birth.fill" not in source
    assert "_local_result_count(search_result, visible_actions)" in source
    assert "reconcile_birth_date(" in source
    assert 'item.sources["birth_date_planilha_original"]' in source
    # O cartão que o operador vê no PEC não pode ser apagado por uma resposta
    # GraphQL vazia que chegou depois.
    assert _local_result_count({"content": []}, 1) == 1
    assert _local_result_count({"content": [{"id": 1}]}, 0) == 1
    assert _local_result_count({"content": []}, 0) == 0


def test_closed_pec_window_aborts_batch_instead_of_marking_every_row():
    import inspect
    from app.services.pec_browser import consult_pec
    source=inspect.getsource(consult_pec)
    assert "page.is_closed()" in source
    assert "mantenha essa janela aberta" in source


def test_attendance_list_uses_only_the_read_only_action():
    import inspect
    from app.services.pec_browser import consult_pec
    source=inspect.getsource(consult_pec)
    assert '"Visualizar atendimentos do dia", exact=True' in source
    for forbidden in ('get_by_text("Editar"', 'get_by_text("Excluir"', 'Cidadão não aguardou'):
        assert forbidden not in source


def test_attendance_list_has_controlled_individual_fallback_and_source_history():
    import inspect
    from app.services.pec_browser import consult_pec
    source=inspect.getsource(consult_pec)
    assert "consult_individual" in source
    assert 'item.sources["pec_lookup"]' in source
    assert "PEC_CADASTRO_INDIVIDUAL" in source


def test_standalone_pec_discovery_is_read_only():
    import inspect
    from app.services.pec_discovery import discover_pec_attendances
    source=inspect.getsource(discover_pec_attendances)
    assert '"Visualizar atendimentos do dia", exact=True' in source
    for forbidden in ('get_by_text("Editar"', 'get_by_text("Excluir"', 'Cidadão não aguardou'):
        assert forbidden not in source


def test_standalone_discovery_skips_rows_without_attendance_history():
    import inspect
    from app.services.pec_discovery import discover_pec_attendances

    source = inspect.getsource(discover_pec_attendances)
    assert 'safe_action.get_attribute("aria-disabled") == "true"' in source
    assert 'safe_action.get_attribute("disabled") is not None' in source
    assert 'page.keyboard.press("Escape")' in source
    assert "Seguindo para o próximo" in source
    assert "continue" in source


def test_attendance_validation_is_optional_and_disabled_by_default():
    import inspect
    from app.services.pec_browser import consult_pec
    from app.services.pec_service import PecService
    browser_source=inspect.getsource(consult_pec)
    service_source=inspect.getsource(PecService.consult)
    assert "validate_attendance: bool = False" in browser_source
    assert "if validate_attendance:" in browser_source
    assert "validate_attendance: bool = False" in service_source


def test_web_route_never_requires_attendance_list_validation():
    import inspect
    from app.web.routes import consult_pec as consult_pec_route
    source=inspect.getsource(consult_pec_route)
    assert "validate_attendance=False" in source


def test_login_page_is_not_reloaded_while_user_authenticates():
    import inspect
    from app.services.pec_browser import consult_pec
    from app.services.pec_discovery import discover_pec_attendances
    for function in (consult_pec, discover_pec_attendances):
        source=inspect.getsource(function)
        assert '"/agenda" in page.url.lower()' in source
        assert "last_navigation" not in source
        assert "não será recarregada durante a autenticação" in source


def test_pec_filter_supports_visible_angular_labels_and_date_values():
    import inspect
    from app.services import pec_filter_controls
    from app.services.pec_discovery import discover_pec_attendances
    controls=inspect.getsource(pec_filter_controls)
    discovery=inspect.getsource(discover_pec_attendances)
    assert "get_by_text(label, exact=True)" in controls
    assert "input_value()" in controls
    assert "click_action_by_text" in controls
    assert "preferred.click()" in controls and "leaf.click()" in controls
    assert '"Atendimento realizado": True' in discovery
    assert 'visible_inputs[0].fill(_display_date(start))' in discovery
    assert 'visible_inputs[1].fill(_display_date(end))' in discovery


def test_standalone_discovery_advances_until_last_results_page():
    import inspect
    from app.services.pec_discovery import discover_pec_attendances
    source=inspect.getsource(discover_pec_attendances)
    assert "advance_to_next_page" in source
    assert "paginator_state" in source
    assert "Página {page_number} de {total_pages} confirmada" in source
    assert "PAGINATOR NAVIGATION NEXT" in source
    assert "const pageInputs" in source
    assert "local[local.length - 1]" in source
    assert "bounding_box()" in source
    assert "current.input_value().strip().isdigit()" in source
    assert "def paginator_info" in source
    assert "page.mouse.click" in source
    assert "current_page_number = int(before)" in source
    assert "total_pages > current_page_number" in source
    assert "Ainda existem páginas de atendimentos" in source
    assert "known_total_pages" in source
    assert "while True:" in source
    assert "len(processed_rows) < 1000" not in source
    assert "result_page_signature" in source
    assert "require_paginator" in source
    assert "seen_page_signatures" in source
    assert "_page_transition_confirmed" in source
    assert "para não gerar lote incompleto" in source


def test_page_transition_requires_number_and_content_change():
    from app.services.pec_discovery import _page_transition_confirmed

    assert _page_transition_confirmed("8", "9", "pagina-oito", "pagina-nove", 9)
    assert not _page_transition_confirmed("8", "8", "pagina-oito", "pagina-nove", 9)
    assert not _page_transition_confirmed("8", "9", "pagina-oito", "pagina-oito", 9)
    assert not _page_transition_confirmed("", "9", "pagina-oito", "pagina-nove", 9)
    assert not _page_transition_confirmed("8", "9", "", "pagina-nove", 9)


def test_firebird_preview_skips_existing_any_origin_instead_of_aborting_batch():
    import inspect
    from app.services.firebird_workflow import FirebirdWorkflow
    source=inspect.getsource(FirebirdWorkflow.preview)
    assert "non_bpi_conflicts" not in source
    assert "existing_production" in source
