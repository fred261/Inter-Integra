import json
import stat
from datetime import date

import httpx
import pytest

from inter_integra import db
from inter_integra.client import InterClient, InterError
from inter_integra.config import Settings, ensure_home


@pytest.fixture
def s(tmp_path):
    st = Settings(home=tmp_path / "p")
    ensure_home(st)
    db.init_db(st.db_path).close()
    return st


def cliente(s, handler):
    http = httpx.Client(base_url=s.base_url, transport=httpx.MockTransport(handler))
    return InterClient(s, "cid", "csecret", http=http)


def handler_ok(chamadas):
    def h(req: httpx.Request):
        chamadas.append(req)
        if req.url.path == "/oauth/v2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(200, json={"disponivel": 10.5})
    return h


def test_token_form_e_cache_em_disco(s):
    ch = []
    c = cliente(s, handler_ok(ch))
    assert c.saldo() == {"disponivel": 10.5}
    body = ch[0].content.decode()
    assert "grant_type=client_credentials" in body and "scope=extrato.read" in body
    assert ch[1].headers["authorization"] == "Bearer tok"
    # novo cliente (outro processo) reutiliza o token
    ch2 = []
    cliente(s, handler_ok(ch2)).saldo()
    assert [r.url.path for r in ch2] == ["/banking/v2/saldo"]
    assert stat.S_IMODE((s.home / "token_cache.json").stat().st_mode) == 0o600


def test_401_renova_token_uma_vez(s):
    estado = {"n": 0}

    def h(req):
        if req.url.path == "/oauth/v2/token":
            estado["n"] += 1
            return httpx.Response(200, json={"access_token": f"t{estado['n']}", "expires_in": 3600})
        if req.headers["authorization"] == "Bearer t1":
            return httpx.Response(401, text="expirado")
        return httpx.Response(200, json={"ok": 1})

    assert cliente(s, h).saldo() == {"ok": 1}
    assert estado["n"] == 2


def test_erro_http_e_log_sem_segredos(s):
    def h(req):
        if req.url.path == "/oauth/v2/token":
            return httpx.Response(200, json={"access_token": "SEGREDO-TOK", "expires_in": 3600})
        return httpx.Response(429, text="muitas")

    with pytest.raises(InterError) as e:
        cliente(s, h).saldo()
    assert e.value.status == 429
    conn = db.connect(s.db_path)
    log = json.dumps([tuple(r) for r in conn.execute("SELECT * FROM log")])
    assert "429" in log and "SEGREDO-TOK" not in log and "csecret" not in log


def test_conta_corrente_header(s):
    ch = []
    c = cliente(s, handler_ok(ch))
    c.conta = "123456"
    c.saldo(date(2026, 1, 2))
    assert ch[-1].headers["x-conta-corrente"] == "123456"
    assert ch[-1].url.params["dataSaldo"] == "2026-01-02"


def test_periodo_maximo_90_dias(s):
    c = cliente(s, handler_ok([]))
    with pytest.raises(ValueError):
        c.extrato(date(2026, 1, 1), date(2026, 6, 1))
    with pytest.raises(ValueError):
        c.extrato(date(2026, 2, 1), date(2026, 1, 1))


def test_extrato_completo_pagina(s):
    def h(req):
        if req.url.path == "/oauth/v2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        p = int(req.url.params["pagina"])
        return httpx.Response(200, json={"transacoes": [{"p": p}], "totalPaginas": 2,
                                         "ultimaPagina": p == 1})

    r = cliente(s, h).extrato_completo(date(2026, 1, 1), date(2026, 1, 31))
    assert r == [{"p": 0}, {"p": 1}]
