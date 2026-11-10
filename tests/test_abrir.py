import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from inter_integra import abrir, db
from inter_integra.config import Settings, ensure_home
from inter_integra.versao import codigo_versao

RAIZ = Path(__file__).resolve().parent.parent


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def espera(cond, s=10.0):
    fim = time.time() + s
    while time.time() < fim:
        if cond():
            return True
        time.sleep(0.2)
    return False


@pytest.fixture
def porta(monkeypatch):
    p = porta_livre()
    monkeypatch.setenv("INTER_PORTA", str(p))
    return p


@pytest.fixture
def procs():
    lista = []
    yield lista
    for pr in lista:
        if pr.poll() is None:
            pr.kill()
        pr.wait()


@pytest.fixture
def home(tmp_path):
    s = Settings(home=tmp_path / "p")
    ensure_home(s)
    db.init_db(s.db_path).close()
    return s.home


def servidor_do_projeto(home, p, procs):
    pr = subprocess.Popen([sys.executable, "-m", "inter_integra", "web"], cwd=RAIZ, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, env={**os.environ, "INTER_HOME": str(home), "INTER_PORTA": str(p)})
    procs.append(pr)
    assert espera(lambda: abrir.estado_porta(p)[0] == "nosso"), "servidor não subiu"
    return pr


def servidor_falso(p, procs, marcador: str, pagina: str):
    codigo = (f"# {marcador}\nimport http.server\n"
              "class H(http.server.BaseHTTPRequestHandler):\n"
              "    def do_GET(self):\n"
              f"        self.send_response(404 if self.path=='/versao' else 200); self.end_headers(); self.wfile.write({pagina!r}.encode())\n"
              "    def log_message(self,*a): pass\n"
              f"http.server.HTTPServer(('127.0.0.1',{p}),H).serve_forever()\n")
    pr = subprocess.Popen([sys.executable, "-c", codigo], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(pr)
    assert espera(lambda: abrir._get(f"http://127.0.0.1:{p}/") is not None)
    return pr


def test_versao_muda_quando_o_codigo_muda(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    v1 = codigo_versao(tmp_path)
    assert codigo_versao(tmp_path) == v1
    (tmp_path / "a.py").write_text("x = 2")
    assert codigo_versao(tmp_path) != v1
    (tmp_path / "nota.txt").write_text("ignorado")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.py").write_text("lixo")
    assert codigo_versao(tmp_path) == codigo_versao(tmp_path)


@pytest.mark.parametrize("estado,rodando,esperado", [
    ("livre", None, "iniciar"), ("outro", None, "ocupada"),
    ("nosso", "abc", "abrir"), ("nosso", "velha", "reiniciar"), ("nosso", None, "reiniciar")])
def test_decisao(estado, rodando, esperado):
    assert abrir.decidir(estado, rodando, "abc") == esperado


def test_porta_livre_inicia_e_abre_navegador(porta, monkeypatch):
    abertos, subidos = [], []
    monkeypatch.setattr(abrir.webbrowser, "open", abertos.append)
    monkeypatch.setattr(abrir, "_abrir_navegador", lambda p: abertos.append(p))
    assert abrir.executar(subidos.append) == 0
    assert subidos == [porta]


def test_mesma_versao_so_abre_a_pagina(porta, home, procs, monkeypatch):
    pr = servidor_do_projeto(home, porta, procs)
    assert abrir.estado_porta(porta) == ("nosso", codigo_versao())
    abertos, subidos = [], []
    monkeypatch.setattr(abrir.webbrowser, "open", abertos.append)
    assert abrir.executar(subidos.append) == 0
    assert abertos == [f"http://localhost:{porta}"] and subidos == [] and pr.poll() is None  # não reiniciou


def test_codigo_novo_reinicia_o_servidor(porta, home, procs, monkeypatch):
    pr = servidor_do_projeto(home, porta, procs)
    monkeypatch.setattr(abrir, "codigo_versao", lambda: "versao-nova")
    subidos = []
    monkeypatch.setattr(abrir, "_abrir_navegador", lambda p: None)
    assert abrir.executar(subidos.append) == 0
    assert pr.wait(timeout=10) is not None  # servidor antigo encerrado
    assert subidos == [porta] and abrir.estado_porta(porta)[0] == "livre"


def test_servidor_de_versao_anterior_sem_endpoint_tambem_reinicia(porta, procs, monkeypatch):
    pr = servidor_falso(porta, procs, "inter_integra web (versao antiga)", "<html>Inter Integra</html>")
    assert abrir.estado_porta(porta) == ("nosso", None)
    monkeypatch.setattr(abrir, "_abrir_navegador", lambda p: None)
    subidos = []
    assert abrir.executar(subidos.append) == 0
    assert pr.wait(timeout=10) is not None and subidos == [porta]


def test_nunca_encerra_programa_de_terceiros(porta, procs, monkeypatch, capsys):
    pr = servidor_falso(porta, procs, "outro programa qualquer", "<html>Outro app</html>")
    assert abrir.estado_porta(porta)[0] == "outro"
    subidos = []
    assert abrir.executar(subidos.append) == 1
    assert "ocupada por outro programa" in capsys.readouterr().out
    assert pr.poll() is None and subidos == []  # continua vivo, nada foi iniciado
    assert abrir.encerrar_servidor(porta) is False and pr.poll() is None


def test_processo_com_pagina_nossa_mas_comando_alheio_nao_e_encerrado(porta, procs):
    """Página diz 'Inter Integra' mas o processo não é do projeto: não pode ser morto."""
    pr = servidor_falso(porta, procs, "programa-sem-o-nome-do-projeto", "<html>Inter Integra</html>")
    assert abrir.encerrar_servidor(porta) is False and pr.poll() is None
