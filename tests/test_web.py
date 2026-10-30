import re

import httpx
import pytest
from fastapi.testclient import TestClient

from inter_integra import db, pagamentos as pg
from inter_integra.client import InterClient
from inter_integra.config import Settings, ensure_home
from inter_integra.web import create_app


@pytest.fixture
def ctx(tmp_path):
    s = Settings(home=tmp_path / "p")
    ensure_home(s)
    db.init_db(s.db_path).close()
    chamadas = []

    def h(req):
        if req.url.path == "/oauth/v2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if req.method == "POST":
            chamadas.append(req)
            return httpx.Response(200, json={"tipoRetorno": "APROVACAO",
                                             "codigoSolicitacao": f"c{len(chamadas)}"})
        if req.url.path.endswith("/saldo"):
            return httpx.Response(200, json={"disponivel": 1234.56})
        return httpx.Response(200, json={"transacaoPix": {"status": "REALIZADO"}})

    def fabrica(st):
        return InterClient(st, "i", "s", http=httpx.Client(
            base_url=st.base_url, transport=httpx.MockTransport(h)))

    c = TestClient(create_app(s, fabrica), base_url="http://localhost:8000")
    return c, s, chamadas


def token(c) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', c.get("/beneficiarios").text).group(1)


def post(c, url, **dados):
    return c.post(url, data={"csrf_token": token(c), **dados}, follow_redirects=False)


def test_paginas_abrem_e_painel_mostra_saldo(ctx):
    c, *_ = ctx
    for url in ("/", "/beneficiarios", "/folha", "/caju", "/historico"):
        assert c.get(url).status_code == 200, url
    assert "R$ 1.234,56" in c.get("/").text


def test_painel_sem_credenciais_nao_quebra(tmp_path, monkeypatch):
    from inter_integra import credentials
    s = Settings(home=tmp_path / "p")
    ensure_home(s)
    db.init_db(s.db_path).close()

    def sem(_s):
        raise credentials.CredenciaisAusentes("Credenciais não encontradas no Keychain")
    monkeypatch.setattr(credentials, "carregar", sem)
    r = TestClient(create_app(s), base_url="http://localhost:8000").get("/")
    assert r.status_code == 200 and "Credenciais não encontradas" in r.text


def test_host_estranho_bloqueado(ctx):
    c, *_ = ctx
    assert c.get("/", headers={"host": "evil.com"}).status_code == 400


def test_csrf_e_origem(ctx):
    c, *_ = ctx
    r = c.post("/beneficiarios", data={"nome": "A", "chave_pix": "a"}, follow_redirects=False)
    assert r.status_code in (403, 422)
    r = c.post("/beneficiarios", data={"csrf_token": "errado", "nome": "A", "chave_pix": "a"})
    assert r.status_code == 403
    r = c.post("/beneficiarios", data={"csrf_token": token(c), "nome": "A", "chave_pix": "a"},
               headers={"origin": "https://evil.com"})
    assert r.status_code == 403


def test_cabecalhos_de_seguranca(ctx):
    c, *_ = ctx
    r = c.get("/")
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_beneficiario_crud_e_xss_escapado(ctx):
    c, s, _ = ctx
    assert post(c, "/beneficiarios", nome="<script>x</script>", chave_pix="a@x.com",
                tipo="colaborador", valor="3.000,00").status_code == 303
    html = c.get("/beneficiarios").text
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html
    r = post(c, "/beneficiarios", nome="B", chave_pix="b", tipo="invalido", valor="1")
    assert "tipo inválido" in r.text
    assert post(c, "/beneficiarios/1", nome="Ana", chave_pix="a@x.com", tipo="colaborador",
                valor="3.500,00", ativo="1").status_code == 303
    assert "Ana" in c.get("/beneficiarios").text


def test_pix_avulso_fluxo_com_confirmacao(ctx):
    c, s, chamadas = ctx
    post(c, "/beneficiarios", nome="Val", chave_pix="val@x.com", tipo="faxina", valor="150,00")
    r = post(c, "/pix/conferir", beneficiario_id="1", valor="150,00")
    assert r.status_code == 303 and r.headers["location"] == "/pagamento/1"
    assert "val@x.com" in c.get("/pagamento/1").text
    assert "Digite ENVIAR" in post(c, "/pagamento/1/enviar", confirmacao="enviar").text
    assert chamadas == []
    assert post(c, "/pagamento/1/enviar", confirmacao="ENVIAR").status_code == 303
    assert len(chamadas) == 1
    assert "Aguardando aprovação" in c.get("/pagamento/1").text
    # não reenvia
    assert "já enviado" in post(c, "/pagamento/1/enviar", confirmacao="ENVIAR").text
    assert len(chamadas) == 1


def test_teto_aparece_na_tela(ctx):
    c, *_ = ctx
    post(c, "/beneficiarios", nome="Val", chave_pix="v", tipo="faxina", valor="1")
    assert "teto" in post(c, "/pix/conferir", beneficiario_id="1", valor="99.999,00").text


def test_caju(ctx):
    c, s, chamadas = ctx
    assert "incompleto" in post(c, "/caju/conferir", codigo="curto", valor="10").text
    r = post(c, "/caju/conferir", codigo="00020126580014br.gov.bcb.pix0136abcdef1234567890",
             valor="2.000,00")
    assert r.status_code == 303
    assert "Copia e Cola" in c.get(r.headers["location"]).text
    post(c, r.headers["location"] + "/enviar", confirmacao="ENVIAR")
    assert len(chamadas) == 1


def test_folha_ponta_a_ponta(ctx):
    c, s, chamadas = ctx
    post(c, "/beneficiarios", nome="Ana", chave_pix="a@x.com", tipo="colaborador", valor="3.000,00")
    post(c, "/beneficiarios", nome="Bia", chave_pix="b@x.com", tipo="colaborador", valor="2.500,00")
    r = post(c, "/folha/conferir", competencia="2026-10", data="2099-11-05",
             ids=["1", "2"], valor=["3.000,00", "2.500,00"], sel=["1", "2"])
    assert r.status_code == 303 and r.headers["location"] == "/folha/1"
    assert "R$ 5.500,00" in c.get("/folha/1").text
    assert "diferente" in post(c, "/folha/1/enviar", total="1,00").text
    assert chamadas == []
    import inter_integra.pagamentos as mod
    mod.INTERVALO_ENVIO_S = 0  # sem espera nos testes
    assert post(c, "/folha/1/enviar", total="5.500,00").status_code == 303
    assert len(chamadas) == 2
    assert "Tudo enviado" in c.get("/folha/1").text


def test_folha_so_selecionados_e_descarte(ctx):
    c, s, chamadas = ctx
    post(c, "/beneficiarios", nome="Ana", chave_pix="a@x.com", tipo="colaborador", valor="10,00")
    post(c, "/beneficiarios", nome="Bia", chave_pix="b@x.com", tipo="colaborador", valor="20,00")
    post(c, "/folha/conferir", competencia="2026-10", data="2099-11-05",
         ids=["1", "2"], valor=["10,00", "20,00"], sel=["2"])
    assert "R$ 20,00" in c.get("/folha/1").text
    assert post(c, "/folha/1/descartar").headers["location"] == "/folha"
    assert c.get("/folha/1").status_code == 404
    r = post(c, "/folha/conferir", competencia="2026-10", data="2099-11-05",
             ids=["1"], valor=["1"], sel=[])
    assert r.status_code == 200 and "folha vazia" in r.text


def test_historico_e_csv_sem_injecao(ctx):
    c, s, _ = ctx
    post(c, "/beneficiarios", nome="=CMD()", chave_pix="v", tipo="faxina", valor="1")
    post(c, "/pix/conferir", beneficiario_id="1", valor="10,00")
    r = c.get("/historico.csv")
    assert r.headers["content-type"].startswith("text/csv")
    assert "'=CMD()" in r.text and ";=CMD()" not in r.text
    assert c.get("/historico?de=xx").status_code == 200
    assert "inválida" in c.get("/historico?de=xx").text
    assert c.get("/historico.csv?de=xx").status_code == 400
