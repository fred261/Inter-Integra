import httpx
import pytest

from inter_integra import __main__ as cli, db, pagamentos as pg
from inter_integra.client import InterClient
from inter_integra.config import Settings, ensure_home


@pytest.fixture
def s(tmp_path, monkeypatch):
    st = Settings(home=tmp_path / "p")
    ensure_home(st)
    db.init_db(st.db_path).close()
    monkeypatch.setattr(cli, "load_settings", lambda: st)
    return st


def instalar_cliente(monkeypatch, s, chamadas):
    def h(req):
        if req.url.path == "/oauth/v2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        chamadas.append(req)
        return httpx.Response(200, json={"tipoRetorno": "APROVACAO", "codigoSolicitacao": "c1"})
    monkeypatch.setattr(cli, "_cliente", lambda _s: InterClient(
        s, "i", "s", http=httpx.Client(base_url=s.base_url, transport=httpx.MockTransport(h))))


def test_pix_chave_confirmado_e_cancelado(s, monkeypatch, capsys):
    chamadas = []
    instalar_cliente(monkeypatch, s, chamadas)
    assert cli.main(["x", "beneficiario-add", "Val", "val@x.com", "faxina"]) == 0
    monkeypatch.setattr("builtins.input", lambda _: "nao")
    assert cli.main(["x", "pix-chave", "1", "150,00"]) == 1
    assert chamadas == []
    monkeypatch.setattr("builtins.input", lambda _: "ENVIAR")
    assert cli.main(["x", "pix-chave", "1", "150,00"]) == 0
    assert len(chamadas) == 1
    assert "Aprove no app" in capsys.readouterr().out


def test_pix_acima_do_teto_nao_chega_na_api(s, monkeypatch, capsys):
    chamadas = []
    instalar_cliente(monkeypatch, s, chamadas)
    cli.main(["x", "beneficiario-add", "Val", "val@x.com"])
    assert cli.main(["x", "pix-chave", "1", "99999,00"]) == 1
    assert "teto" in capsys.readouterr().out and chamadas == []


def test_pix_beneficiario_inexistente(s, monkeypatch, capsys):
    assert cli.main(["x", "pix-chave", "42", "10"]) == 1
    assert "inexistente" in capsys.readouterr().out
