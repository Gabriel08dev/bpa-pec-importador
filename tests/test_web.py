from fastapi.testclient import TestClient

from app.main import create_app
from app.security import TOKEN
from app.web.routes import templates


def test_local_interface_requires_token_and_serves_model():
    app=create_app()
    with TestClient(app,client=("127.0.0.1",50000)) as client:
        assert client.get("/health").status_code==200
        assert client.get("/").status_code==403
        response=client.get("/?token="+TOKEN)
        assert response.status_code==200 and "Importador CEAE" in response.text
        assert "Credenciais protegidas" not in response.text and "Salvar no Windows" not in response.text
        model=client.get("/modelo.xlsx?token="+TOKEN)
        assert model.status_code==200 and len(model.content)>5000


def test_batch_template_renders_items_list():
    payload={"batch_id":"LOTE-TESTE","competence":"202607","items":[{
        "row":5,"patient":{"name":""},"professional_name":"Profissional Teste","service_date":"20260701",
        "final_cid":"","status":"REVISAO_INICIAL","reason":"","warnings":[],"procedure":"0301010072",
    }]}
    html=templates.env.get_template("batch.html").render(token="token-test",payload=payload,preview=None,result=None)
    assert "1 linhas" in html and "REVISAO_INICIAL" in html


def test_error_template_supports_firebird_preview_message():
    html = templates.env.get_template("error.html").render(
        token="token-test",
        title="Não foi possível gerar a prévia do Firebird",
        message="Nenhum item confirmado pelo PEC está apto à prévia",
        guidance="Nenhum registro foi gravado.",
        return_url="/lote/LOTE?token=token-test",
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
    assert "/lote/LOTE-TESTE/pec?token=token-test" in html
    assert "/lote/LOTE-TESTE/relatorio?token=token-test" in html
    assert "/lote/LOTE-TESTE/previa?token=token-test" in html
    assert "/lote/LOTE-TESTE/gravar?token=token-test" in html
    assert 'name="confirmation"' in html and 'placeholder="ADICIONAR"' in html
