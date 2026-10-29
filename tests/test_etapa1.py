import sqlite3
import stat

import keyring
import keyring.backend
import pytest

from inter_integra import credentials, db
from inter_integra.config import Settings, check_cert_permissions, ensure_home, load_settings


class MemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def __init__(self):
        self.d = {}

    def set_password(self, service, username, password):
        self.d[(service, username)] = password

    def get_password(self, service, username):
        return self.d.get((service, username))

    def delete_password(self, service, username):
        self.d.pop((service, username), None)


@pytest.fixture
def s(tmp_path):
    st = Settings(home=tmp_path / "PagamentosInter")
    ensure_home(st)
    return st


@pytest.fixture(autouse=True)
def fake_keyring():
    keyring.set_keyring(MemoryKeyring())


def test_home_permissao_700(s):
    assert stat.S_IMODE(s.home.stat().st_mode) == 0o700


def test_ambiente_invalido(monkeypatch):
    monkeypatch.setenv("INTER_AMBIENTE", "xx")
    with pytest.raises(ValueError):
        load_settings()


def test_init_db_idempotente_e_teto(s):
    db.init_db(s.db_path).close()
    conn = db.init_db(s.db_path)
    assert db.teto_centavos(conn) == 500_000
    assert stat.S_IMODE(s.db_path.stat().st_mode) == 0o600


def test_pagamento_exige_destino_e_idempotencia_unica(s):
    conn = db.init_db(s.db_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO pagamentos (valor_centavos, id_idempotente) VALUES (100, 'a')"
        )
    conn.execute(
        "INSERT INTO pagamentos (pix_copia_e_cola, valor_centavos, id_idempotente) "
        "VALUES ('x', 100, 'a')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO pagamentos (pix_copia_e_cola, valor_centavos, id_idempotente) "
            "VALUES ('y', 200, 'a')"
        )


def test_credenciais_roundtrip_e_separacao_por_ambiente(s):
    with pytest.raises(credentials.CredenciaisAusentes):
        credentials.carregar(s)
    credentials.salvar(s, "id", "segredo")
    assert credentials.carregar(s) == ("id", "segredo")
    prod = Settings(home=s.home, ambiente="producao")
    with pytest.raises(credentials.CredenciaisAusentes):
        credentials.carregar(prod)


def test_permissao_certificado(s):
    assert len(check_cert_permissions(s)) == 2
    s.cert_path.write_text("c")
    s.key_path.write_text("k")
    s.cert_path.chmod(0o644)
    s.key_path.chmod(0o600)
    assert check_cert_permissions(s) == [f"permissão deve ser 600: {s.cert_path}"]
    s.cert_path.chmod(0o600)
    assert check_cert_permissions(s) == []
