from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import APP_HOST, APP_PORT, TOKEN_CREDENTIAL_TARGET, create_app, open_running_server
from app.security import SESSION_COOKIE, TOKEN
from app.web.routes import templates


def login(client: TestClient):
    response = client.get("/?token=" + TOKEN, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    return response


def test_server_endpoint_remains_fixed_for_cloudflared():
    assert APP_HOST == "127.0.0.1"
    assert APP_PORT == 51500


def test_shortcut_reuses_running_server_with_protected_token(monkeypatch):
    opened = []
    monkeypatch.setattr("app.main.server_is_running", lambda: True)
    monkeypatch.setattr(
        "app.main.read_credential",
        lambda target: ("BPA System", "protected-token") if target == TOKEN_CREDENTIAL_TARGET else None,
    )
    monkeypatch.setattr("app.main.webbrowser.open", opened.append)
    assert open_running_server() is True
    assert opened == ["http://localhost:51500/?token=protected-token"]


def test_shortcut_starts_server_when_port_is_free(monkeypatch):
    monkeypatch.setattr("app.main.server_is_running", lambda: False)
    assert open_running_server() is False


def test_local_interface_requires_token_and_serves_model():
    app=create_app()
    with TestClient(app,base_url="https://localhost:51500",client=("127.0.0.1",50000)) as client:
        assert client.get("/health").status_code==403
        assert client.get("/").status_code==403
        login(client)
        assert client.get("/health").status_code==200
        response=client.get("/")
        assert response.status_code==200 and "Importador CEAE" in response.text
        assert "Lotes recentes" in response.text
        assert "Credenciais protegidas" not in response.text and "Salvar no Windows" not in response.text
        model=client.get("/modelo.xlsx")
        assert model.status_code==200 and len(model.content)>5000


def test_token_exchange_creates_secure_cookie_and_removes_token_from_url():
    app = create_app()
    with TestClient(
        app,
        base_url="https://temporary-name.trycloudflare.com",
        client=("198.51.100.25", 43100),
    ) as client:
        response = login(client)
        cookie = response.headers["set-cookie"]
        assert f"{SESSION_COOKIE}=" in cookie
        assert "HttpOnly" in cookie and "Secure" in cookie
        assert "SameSite=strict" in cookie
        assert "Max-Age=" in cookie and "token" not in response.headers["location"]
        page = client.get("/")
        assert page.status_code == 200
        assert "Lotes recentes" not in page.text


def test_local_session_can_generate_reusable_external_link(monkeypatch):
    app = create_app()
    def credential(target):
        if target == "BPA_SYSTEM_CLOUDFLARE_URL":
            return "BPA System", "https://share-test.trycloudflare.com"
        if target == "BPA_SYSTEM_LOCAL_ACCESS":
            return "BPA System", "reusable-token"
        return None

    monkeypatch.setattr("app.web.routes.read_credential", credential)
    with TestClient(
        app,
        base_url="https://localhost:51500",
        client=("127.0.0.1", 50001),
    ) as client:
        login(client)
        response = client.get("/acesso-externo/gerar")
        assert response.status_code == 200
        assert "share-test.trycloudflare.com/?token=reusable-token" in response.text
        assert "quantas vezes" in response.text


def test_cloudflare_host_accepts_same_token_but_arbitrary_hosts_are_denied():
    app = create_app()
    with TestClient(
        app,
        base_url="https://random-subdomain.trycloudflare.com",
        client=("203.0.113.18", 50123),
    ) as tunnel:
        assert login(tunnel).status_code == 303
        assert tunnel.get("/").status_code == 200
    with TestClient(
        app,
        base_url="https://example.com",
        client=("127.0.0.1", 50124),
    ) as denied:
        response = denied.get("/?token=" + TOKEN)
        assert response.status_code == 403
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        assert "domínio não autorizado" in response.text


def test_same_protected_link_can_create_sessions_on_multiple_computers():
    app = create_app()
    for address in ("198.51.100.41", "198.51.100.42", "198.51.100.43"):
        with TestClient(
            app,
            base_url="https://shared-link.trycloudflare.com",
            client=(address, 50130),
        ) as client:
            response = client.get("/?token=" + TOKEN, follow_redirects=False)
            assert response.status_code == 303
            assert client.get("/").status_code == 200


def test_cross_site_post_is_denied_and_logout_invalidates_session():
    app = create_app()
    host = "secure-test.trycloudflare.com"
    with TestClient(
        app,
        base_url=f"https://{host}",
        client=("203.0.113.19", 50125),
    ) as client:
        login(client)
        blocked = client.post(
            "/sair",
            headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
        )
        assert blocked.status_code == 403
        logout = client.post(
            "/sair",
            headers={"Origin": f"https://{host}", "Referer": f"https://{host}/"},
        )
        assert logout.status_code == 200 and "Sessão encerrada" in logout.text
        assert client.get("/").status_code == 403


def test_localhost_and_loopback_are_equivalent_for_protected_posts(monkeypatch):
    app = create_app()
    def credential(target):
        if target == "BPA_SYSTEM_CLOUDFLARE_URL":
            return "BPA System", "https://shared-origin.trycloudflare.com"
        if target == "BPA_SYSTEM_LOCAL_ACCESS":
            return "BPA System", TOKEN
        return None

    monkeypatch.setattr("app.web.routes.read_credential", credential)
    with TestClient(
        app,
        base_url="https://localhost:51500",
        client=("127.0.0.1", 50126),
    ) as client:
        response = client.post(
            "/sair",
            headers={
                "X-Local-Token": TOKEN,
                "Origin": "https://127.0.0.1:51500",
                "Referer": "https://127.0.0.1:51500/",
                "Sec-Fetch-Site": "same-site",
            },
        )
        assert response.status_code == 200


def test_batch_template_renders_items_list():
    payload={"batch_id":"LOTE-TESTE","competence":"202607","items":[{
        "row":5,"patient":{"name":""},"professional_name":"Profissional Teste","service_date":"20260701",
        "final_cid":"","status":"REVISAO_INICIAL","reason":"","warnings":[],"procedure":"0301010072",
    }]}
    html=templates.env.get_template("batch.html").render(token="token-test",payload=payload,preview=None,result=None)
    assert "1 linhas" in html and "REVISAO_INICIAL" in html


def test_home_offers_pec_attendance_search_without_spreadsheet():
    html=templates.env.get_template("index.html").render(
        token="token-test",
        default_competence="202607",
        recent_batches=[],
        environment=[],
        pec_running=False,
        is_local=True,
    )
    assert "Buscar atendimentos no PEC" in html
    assert 'action="/pec/lista-atendimentos"' in html
    assert "?token=" not in html
    assert 'name="start_date"' in html and 'name="end_date"' in html


def test_home_uses_one_visible_competence_and_preserves_form_contracts():
    html = templates.env.get_template("index.html").render(
        token="token-test",
        default_competence="202607",
        recent_batches=[],
        environment=[],
        pec_running=False,
        is_local=True,
    )
    assert html.count('id="active-competence"') == 1
    assert html.count('type="hidden" name="competence" value="202607"') == 2
    assert html.count('name="competence"') == 2
    assert 'action="/importar"' in html
    assert 'action="/pec/lista-atendimentos"' in html
    assert html.index("Importar planilha") < html.index("Lotes recentes")
    assert html.index("Buscar atendimentos no PEC") < html.index("Lotes recentes")
    assert html.index("Lotes recentes") < html.index("Ferramentas e suporte")


def test_home_upload_and_accessible_validation_controls_are_present():
    html = templates.env.get_template("index.html").render(
        token="token-test",
        default_competence="202607",
        recent_batches=[],
        environment=[],
        pec_running=False,
    )
    assert 'id="upload-zone"' in html and 'id="file-selection"' in html
    assert 'accept=".xlsx,.csv,.tsv"' in html
    assert 'id="remove-file"' in html
    assert 'id="import-submit" type="submit" disabled' in html
    assert 'aria-live="polite"' in html
    assert "Origem" in html and "Revisão" in html and "Confirmação" in html


def test_pec_search_rejects_reverse_period_and_duplicate_running_job():
    from app.web.routes import JOBS

    app = create_app()
    with TestClient(app, base_url="https://localhost:51500", client=("127.0.0.1", 50000)) as client:
        login(client)
        reversed_period = client.post(
            "/pec/lista-atendimentos",
            data={"competence": "202607", "start_date": "2026-07-20", "end_date": "2026-07-01"},
        )
        assert reversed_period.status_code == 400
        assert "data final não pode ser anterior" in reversed_period.text

        JOBS["synthetic-running"] = {
            "status": "RUNNING",
            "message": "synthetic",
            "progress": [],
            "kind": "PEC_DISCOVERY",
        }
        try:
            duplicate = client.post(
                "/pec/lista-atendimentos",
                data={"competence": "202607", "start_date": "2026-07-01", "end_date": "2026-07-20"},
            )
            assert duplicate.status_code == 409
            assert "Já existe uma busca em andamento" in duplicate.text
        finally:
            JOBS.pop("synthetic-running", None)


def test_error_template_supports_firebird_preview_message():
    html = templates.env.get_template("error.html").render(
        token="token-test",
        title="Não foi possível gerar a prévia do Firebird",
        message="Nenhum item confirmado pelo PEC está apto à prévia",
        guidance="Nenhum registro foi gravado.",
        return_url="/lote/LOTE",
        return_label="Voltar ao lote",
    )
    assert "Nenhum item confirmado" in html
    assert "Voltar ao lote" in html


def test_new_layout_preserves_every_operational_batch_action():
    item={
        "row":5,"patient":{"name":"Paciente Sintético"},"professional_name":"Profissional Teste",
        "service_date":"20260701","final_cid":"","status":"PRONTO_PARA_ADICIONAR","reason":"",
        "warnings":[],"procedure":"0301010072","sheet":"001","sequence":"01",
    }
    payload={"batch_id":"LOTE-TESTE","competence":"202607","items":[item]}
    html=templates.env.get_template("batch.html").render(token="token-test",payload=payload,preview=payload,result=None)
    assert "/lote/LOTE-TESTE/pec" in html
    assert "/lote/LOTE-TESTE/relatorio" in html
    assert "/lote/LOTE-TESTE/previa" in html
    assert "/lote/LOTE-TESTE/gravar" in html
    assert "?token=" not in html
    assert 'name="confirmation"' in html and 'placeholder="ADICIONAR"' in html
    assert "corrigir CNES, completar CIDs e adicionar novos" in html


def test_pec_batch_offers_safe_cid_spreadsheet_crossing():
    payload={
        "batch_id":"LOTE-PEC",
        "origin":"PEC_LISTA_ATENDIMENTOS",
        "competence":"202607",
        "items":[],
        "cid_import_summary":{
            "matched_keys":3,
            "matched_items":3,
            "attendances_without_cid":1,
            "unmatched_spreadsheet_rows":[8],
            "invalid_rows":[],
            "conflict_rows":[],
        },
    }
    html=templates.env.get_template("batch.html").render(
        token="token-test",
        payload=payload,
        preview=None,
        result=None,
    )
    assert "/lote/LOTE-PEC/cids" in html
    assert 'enctype="multipart/form-data"' in html
    assert "CPF, data do atendimento e profissional" in html
    assert "Mesmo CPF com outro médico ou em outro dia" in html
    assert "Chaves encontradas" in html and ">3<" in html


def test_cid_upload_route_enriches_pec_batch_and_removes_temporary_upload(tmp_path):
    root=Path(__file__).parents[1]
    settings=Settings(
        root=root,
        outputs=tmp_path/"outputs",
        backups=tmp_path/"backups",
        profile=tmp_path/"profile",
        professionals_file=root/"data"/"profissionais.example.json",
        config_file=tmp_path/"config.json",
    )
    app=create_app(settings)
    records=[{
        "name":"Paciente Sintético",
        "cpf":"52998224725",
        "cns":"700000000000006",
        "birth_date":"01/02/2000",
        "service_date":"20260720",
        "professional":"Ana Profissional Exemplo",
    }]
    batch_id,_=app.state.services["batch"].import_pec_attendances(
        records,"202607","2026-07-01","2026-07-31",
    )
    content=(
        "Profissional;Dia do atendimento;CPF do paciente;CID\n"
        "Ana Profissional Exemplo;20/07/2026;52998224725;Z71.3\n"
    ).encode("utf-8")

    with TestClient(app,base_url="https://localhost:51500",client=("127.0.0.1",50000)) as client:
        login(client)
        response=client.post(
            f"/lote/{batch_id}/cids",
            files={"file":("cids.csv",content,"text/csv")},
            follow_redirects=False,
        )
    assert response.status_code==303
    updated=app.state.services["reviews"].read(batch_id,"revisao_planilha.json")
    assert updated["items"][0]["final_cid"]=="Z713"
    assert not any((settings.outputs/"uploads").iterdir())
