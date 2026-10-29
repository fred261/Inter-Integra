import httpx
import pytest

from inter_integra import db, pagamentos as pg
from inter_integra.client import InterClient
from inter_integra.config import Settings, ensure_home

CODIGO = "00020126580014br.gov.bcb.pix0136abcdef1234567890"


@pytest.fixture
def env(tmp_path):
    s = Settings(home=tmp_path / "p")
    ensure_home(s)
    return s, db.init_db(s.db_path)


class Fake:
    """Simula o Inter e guarda as chamadas Pix."""

    def __init__(self, s, pix_resp=None):
        self.pix, self.consultas = [], []
        self.pix_resp = pix_resp

        def h(req: httpx.Request):
            if req.url.path == "/oauth/v2/token":
                return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
            if req.method == "POST":
                import json
                self.pix.append((req.headers.get("x-id-idempotente"), json.loads(req.content)))
                if self.pix_resp:
                    return self.pix_resp(len(self.pix))
                return httpx.Response(200, json={"tipoRetorno": "APROVACAO",
                                                 "codigoSolicitacao": f"cod-{len(self.pix)}"})
            self.consultas.append(req.url.path)
            return httpx.Response(200, json={"transacaoPix": {"status": "REALIZADO"}})

        self.client = InterClient(s, "i", "s", http=httpx.Client(
            base_url=s.base_url, transport=httpx.MockTransport(h)))


def benef(conn, chave="val@exemplo.com", tipo="faxina"):
    return pg.add_beneficiario(conn, "Val", chave, tipo)


def test_parse_valor():
    assert pg.parse_valor("1.234,56") == 123456
    assert pg.parse_valor("R$ 50") == 5000
    assert pg.parse_valor("1234.5") == 123450
    with pytest.raises(pg.PagamentoInvalido):
        pg.parse_valor("1,234")  # 3 casas
    with pytest.raises(pg.PagamentoInvalido):
        pg.parse_valor("abc")


def test_teto_e_validacoes(env):
    s, conn = env
    b = benef(conn)
    with pytest.raises(pg.PagamentoInvalido, match="teto"):
        pg.criar_pix_chave(conn, b, 500_001)
    with pytest.raises(pg.PagamentoInvalido):
        pg.criar_pix_chave(conn, b, 0)
    with pytest.raises(pg.PagamentoInvalido, match="140"):
        pg.criar_pix_chave(conn, b, 100, descricao="x" * 141)
    with pytest.raises(pg.PagamentoInvalido, match="passado"):
        pg.criar_pix_chave(conn, b, 100, data_pagamento="2020-01-01")
    with pytest.raises(pg.PagamentoInvalido):
        pg.criar_pix_chave(conn, 999, 100)


def test_envio_exige_confirmacao_e_monta_payload(env):
    s, conn = env
    b = benef(conn)
    pid = pg.criar_pix_chave(conn, b, 15050, descricao="Faxina semanal")
    f = Fake(s)
    with pytest.raises(pg.PagamentoInvalido, match="confirmação"):
        pg.enviar(conn, f.client, pid)
    assert f.pix == []
    p = pg.enviar(conn, f.client, pid, confirmado=True)
    idem, body = f.pix[0]
    assert idem == p["id_idempotente"]
    assert body == {"valor": 150.5, "descricao": "Faxina semanal",
                    "destinatario": {"tipo": "CHAVE", "chave": "val@exemplo.com"}}
    assert (p["status"], p["codigo_solicitacao"]) == ("aguardando_aprovacao", "cod-1")
    with pytest.raises(pg.PagamentoInvalido, match="já enviado"):
        pg.enviar(conn, f.client, pid, confirmado=True)
    assert len(f.pix) == 1


def test_copia_e_cola(env):
    s, conn = env
    pid = pg.criar_pix_copia_e_cola(conn, CODIGO[:20] + "\n " + CODIGO[20:], 20000, "Caju")
    f = Fake(s)
    pg.enviar(conn, f.client, pid, confirmado=True)
    assert f.pix[0][1]["destinatario"] == {"tipo": "PIX_COPIA_E_COLA", "pixCopiaECola": CODIGO}
    with pytest.raises(pg.PagamentoInvalido):
        pg.criar_pix_copia_e_cola(conn, "curto", 100)


def test_erro_http_permite_reenvio_com_mesmo_id(env):
    s, conn = env
    pid = pg.criar_pix_chave(conn, benef(conn), 1000)
    f = Fake(s, lambda n: httpx.Response(400, json={"title": "chave inválida"}) if n == 1
             else httpx.Response(200, json={"tipoRetorno": "APROVACAO", "codigoSolicitacao": "c"}))
    p = pg.enviar(conn, f.client, pid, confirmado=True)
    assert p["status"] == "erro" and "400" in p["erro"]
    p = pg.enviar(conn, f.client, pid, confirmado=True)
    assert p["status"] == "aguardando_aprovacao"
    assert f.pix[0][0] == f.pix[1][0]  # mesmo x-id-idempotente


def test_falha_de_rede_deixa_enviando_e_reenvia_mesmo_id(env):
    s, conn = env
    pid = pg.criar_pix_chave(conn, benef(conn), 1000)
    f = Fake(s, lambda n: (_ for _ in ()).throw(httpx.ReadTimeout("t")) if n == 1
             else httpx.Response(200, json={"tipoRetorno": "APROVACAO", "codigoSolicitacao": "c"}))
    with pytest.raises(httpx.ReadTimeout):
        pg.enviar(conn, f.client, pid, confirmado=True)
    assert conn.execute("SELECT status FROM pagamentos").fetchone()[0] == "enviando"
    pg.enviar(conn, f.client, pid, confirmado=True)
    assert f.pix[0][0] == f.pix[1][0]


def test_atualizar_status(env):
    s, conn = env
    pid = pg.criar_pix_chave(conn, benef(conn), 1000)
    f = Fake(s)
    pg.enviar(conn, f.client, pid, confirmado=True)
    assert pg.atualizar_status(conn, f.client, sleep=lambda _: None) == 1
    assert conn.execute("SELECT status FROM pagamentos").fetchone()[0] == "REALIZADO"
    assert pg.atualizar_status(conn, f.client, sleep=lambda _: None) == 0  # final: não reconsulta


def test_folha(env):
    s, conn = env
    a = pg.add_beneficiario(conn, "A", "a@x.com", "colaborador")
    b = pg.add_beneficiario(conn, "B", "b@x.com", "colaborador")
    fid = pg.criar_folha(conn, "2026-10", "2099-11-05", [(a, 300000), (b, 250000)])
    f = Fake(s)
    with pytest.raises(pg.PagamentoInvalido, match="total"):
        pg.enviar_folha(conn, f.client, fid, 123)
    assert f.pix == []
    pausas = []
    r = pg.enviar_folha(conn, f.client, fid, 550000, sleep=pausas.append)
    assert r == {"enviados": 2, "pendentes_de_reenvio": 0}
    assert pausas == [pg.INTERVALO_ENVIO_S] and len(f.pix) == 2
    assert conn.execute("SELECT status FROM folhas").fetchone()[0] == "enviada"
    pg.enviar_folha(conn, f.client, fid, 550000, sleep=pausas.append)
    assert len(f.pix) == 2  # nada é reenviado


def test_folha_parcial_e_tudo_ou_nada(env):
    s, conn = env
    a = pg.add_beneficiario(conn, "A", "a@x.com")
    b = pg.add_beneficiario(conn, "B", "b@x.com")
    with pytest.raises(pg.PagamentoInvalido):
        pg.criar_folha(conn, "2026-10", "2099-11-05", [(a, 100), (b, 9_999_999)])
    assert conn.execute("SELECT count(*) FROM folhas").fetchone()[0] == 0  # rollback
    assert conn.execute("SELECT count(*) FROM pagamentos").fetchone()[0] == 0
    with pytest.raises(pg.PagamentoInvalido):
        pg.criar_folha(conn, "2026-10", "2099-11-05", [(a, 100), (a, 200)])
    fid = pg.criar_folha(conn, "2026-10", "2099-11-05", [(a, 100), (b, 200)])
    f = Fake(s, lambda n: httpx.Response(400, text="x") if n == 2 else
             httpx.Response(200, json={"tipoRetorno": "APROVACAO", "codigoSolicitacao": "c"}))
    r = pg.enviar_folha(conn, f.client, fid, 300, sleep=lambda _: None)
    assert r == {"enviados": 1, "pendentes_de_reenvio": 1}
    assert conn.execute("SELECT status FROM folhas").fetchone()[0] == "parcial"
