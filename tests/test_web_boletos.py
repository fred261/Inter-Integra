import re
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from inter_integra import db
from inter_integra.client import InterClient
from inter_integra.config import Settings, ensure_home
from inter_integra.web import create_app
from tests.test_boletos import fator_de, formata, linha_de, make_barras, pdf_com


@pytest.fixture
def ctx(tmp_path):
    s = Settings(home=tmp_path / "p")
    ensure_home(s)
    db.init_db(s.db_path).close()
    posts, estado = [], {"falha": False}

    def h(req):
        if req.url.path == "/oauth/v2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if estado["falha"]:
            raise httpx.ConnectTimeout("t")
        if req.method == "POST":
            posts.append(req)
            return httpx.Response(200, json={"quantidadeAprovadores": 1, "statusPagamento": "EMPROCESSAMENTO",
                                             "codigoTransacao": "tx1"})
        if req.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"disponivel": 10.0} if req.url.path.endswith("saldo") else [])

    fab = lambda st: InterClient(st, "i", "s", http=httpx.Client(
        base_url=st.base_url, transport=httpx.MockTransport(h)))
    return TestClient(create_app(s, fab), base_url="http://localhost:8000"), posts, estado


def tok(c):
    return re.search(r'name="csrf_token" value="([^"]+)"', c.get("/boletos").text).group(1)


def pdf_valido(dias=20, valor=15990):
    venc = date.today() + timedelta(days=dias)
    linha = formata(linha_de(make_barras(fator_de(venc), valor)))
    return pdf_com(f"Beneficiario: LOJA X CNPJ 12.345.678/0001-95\nVencimento {venc:%d/%m/%Y}\n{linha}")


def test_upload_pdf_confere_e_envia(ctx):
    c, posts, _ = ctx
    r = c.post("/boletos/conferir", data={"csrf_token": tok(c)}, follow_redirects=False,
               files={"arquivo": ("conta.pdf", pdf_valido(), "application/pdf")})
    assert r.status_code == 303 and r.headers["location"] == "/boleto/1"
    pag = c.get("/boleto/1").text
    assert "R$ 159,90" in pag and "12345678000195" in pag and "Digite" not in pag
    assert "Digite ENVIAR" in c.post("/boleto/1/enviar", data={"csrf_token": tok(c), "confirmacao": "x"}).text
    assert posts == []
    assert c.post("/boleto/1/enviar", data={"csrf_token": tok(c), "confirmacao": "ENVIAR"},
                  follow_redirects=False).status_code == 303
    assert len(posts) == 1
    assert "Em processamento" in c.get("/boleto/1").text
    assert "Cancelar agendamento" in c.get("/boleto/1").text
    # mesmo PDF de novo: recusado como duplicado
    r = c.post("/boletos/conferir", data={"csrf_token": tok(c)},
               files={"arquivo": ("conta.pdf", pdf_valido(), "application/pdf")})
    assert "já foi cadastrado" in r.text


def test_codigo_colado_e_erros_amigaveis(ctx):
    c, _, _ = ctx
    venc = date.today() + timedelta(days=10)
    linha = formata(linha_de(make_barras(fator_de(venc), 5000)))
    r = c.post("/boletos/conferir", data={"csrf_token": tok(c), "codigo": linha}, follow_redirects=False)
    assert r.status_code == 303
    assert "sem CNPJ" in c.get("/boleto/1").text or "não vai conferir" in c.get("/boleto/1").text
    for dados, msg in [({"codigo": "123"}, "dígitos"), ({}, "envie o PDF"),
                       ({"codigo": linha[:12] + "9" + linha[13:]}, "inválid")]:
        assert msg in c.post("/boletos/conferir", data={"csrf_token": tok(c), **dados}).text
    r = c.post("/boletos/conferir", data={"csrf_token": tok(c)},
               files={"arquivo": ("x.pdf", b"nao sou pdf", "application/pdf")})
    assert "não é um PDF" in r.text


def test_rede_fora_nao_gera_500(ctx):
    c, posts, estado = ctx
    venc = date.today() + timedelta(days=10)
    c.post("/boletos/conferir", data={"csrf_token": tok(c), "codigo": formata(linha_de(make_barras(fator_de(venc), 5000)))})
    estado["falha"] = True
    assert "Sem resposta do Inter" in c.get("/").text  # painel
    r = c.post("/boleto/1/enviar", data={"csrf_token": tok(c), "confirmacao": "ENVIAR"})
    assert r.status_code == 200 and "Sem resposta do Inter" in r.text
    assert "Enviando" in c.get("/boleto/1").text  # fica reenviável, sem duplicar
    assert c.post("/atualizar", data={"csrf_token": tok(c)}, follow_redirects=False).status_code == 303


def test_csrf_vale_para_boletos(ctx):
    c, _, _ = ctx
    assert c.post("/boletos/conferir", data={"codigo": "1"}).status_code in (403, 422)
    assert c.post("/boleto/1/enviar", data={"csrf_token": "x", "confirmacao": "ENVIAR"}).status_code == 403
