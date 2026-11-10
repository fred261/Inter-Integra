import os
import plistlib
import time
from datetime import date, timedelta

import pytest

from inter_integra import boletos as bs, db, entrada
from inter_integra.config import Settings, ensure_home
from tests.test_boletos import fator_de, formata, linha_de, make_barras, pdf_com


@pytest.fixture
def env(tmp_path):
    s = Settings(home=tmp_path / "p")
    ensure_home(s)
    conn = db.init_db(s.db_path)
    from inter_integra.config import ensure_entrada
    ensure_entrada(s)
    return s, conn


def pdf(dias=20, valor=15990):
    venc = date.today() + timedelta(days=dias)
    return pdf_com(f"Beneficiario: LOJA X CNPJ 12.345.678/0001-95\n{formata(linha_de(make_barras(fator_de(venc), valor)))}")


def solta(s, nome, conteudo, idade=60):
    p = s.entrada_dir / nome
    p.write_bytes(conteudo)
    os.utime(p, (time.time() - idade, time.time() - idade))
    return p


def test_pdf_vira_rascunho_e_arquivo_e_movido(env):
    s, conn = env
    solta(s, "conta luz.pdf", pdf())
    res = entrada.processar_pasta(s, conn)
    assert [(r.ok, r.detalhe) for r in res] == [(True, "1")]
    assert list(s.entrada_dir.iterdir()) == []
    assert [p.name for p in s.processados_dir.iterdir()] == ["1_conta_luz.pdf"]
    r = conn.execute("SELECT status, valor_centavos FROM boletos").fetchone()
    assert (r["status"], r["valor_centavos"]) == ("rascunho", 15990)  # nunca enviado sozinho


def test_nada_e_enviado_ao_inter(env, monkeypatch):
    s, conn = env

    def proibido(*a, **k):
        raise AssertionError("a pasta de entrada não pode chamar o Inter")
    monkeypatch.setattr(bs, "enviar", proibido)
    solta(s, "a.pdf", pdf())
    assert entrada.processar_pasta(s, conn)[0].ok


def test_problemas_vao_para_erros_com_motivo(env):
    s, conn = env
    solta(s, "foto.pdf", b"%PDF-1.4 lixo")
    solta(s, "nao_pdf.pdf", b"MZ....")
    solta(s, "vencido.pdf", pdf(dias=-3))
    res = entrada.processar_pasta(s, conn)
    assert [r.ok for r in res] == [False, False, False]
    nomes = sorted(p.name for p in s.erros_dir.iterdir())
    assert "vencido.pdf.erro.txt" in nomes and "foto.pdf" in nomes
    assert "vencido" in (s.erros_dir / "vencido.pdf.erro.txt").read_text()
    assert conn.execute("SELECT count(*) FROM boletos").fetchone()[0] == 0


def test_duplicado_vai_para_erros(env):
    s, conn = env
    conteudo = pdf()
    solta(s, "a.pdf", conteudo)
    solta(s, "b.pdf", conteudo)
    res = entrada.processar_pasta(s, conn)
    assert [r.ok for r in res] == [True, False]
    assert "já foi cadastrado" in res[1].detalhe
    assert conn.execute("SELECT count(*) FROM boletos").fetchone()[0] == 1


def test_ignora_arquivo_recente_symlink_e_nao_pdf(env, tmp_path):
    s, conn = env
    solta(s, "copiando.pdf", pdf(), idade=0)  # ainda sendo copiado
    solta(s, "notas.txt", b"x")
    alvo = tmp_path / "fora.pdf"
    alvo.write_bytes(pdf())
    os.utime(alvo, (time.time() - 60, time.time() - 60))
    (s.entrada_dir / "link.pdf").symlink_to(alvo)
    assert entrada.processar_pasta(s, conn) == []
    assert sorted(p.name for p in s.entrada_dir.iterdir()) == ["copiando.pdf", "link.pdf", "notas.txt"]
    assert alvo.exists()


def test_nomes_perigosos_e_colisao(env):
    s, conn = env
    solta(s, "../../x'; rm -rf.pdf".replace("/", "_"), b"%PDF lixo")
    solta(s, "dup.pdf", b"%PDF lixo")
    entrada.processar_pasta(s, conn)
    solta(s, "dup.pdf", b"%PDF lixo2")
    entrada.processar_pasta(s, conn)
    erros = [p.name for p in s.erros_dir.iterdir() if not p.name.endswith(".txt")]
    assert len(erros) == 3 and all("/" not in n and "'" not in n for n in erros)


def test_plist_do_agente():
    d = plistlib.loads(entrada.plist_agente("/u/.venv/bin/python", "/u/PagamentosInter", "producao", "/u/PagamentosInter/boletos/entrada"))
    assert d["ProgramArguments"] == ["/u/.venv/bin/python", "-m", "inter_integra", "entrada"]
    assert d["WatchPaths"] == ["/u/PagamentosInter/boletos/entrada"]
    assert d["EnvironmentVariables"]["INTER_AMBIENTE"] == "producao"
