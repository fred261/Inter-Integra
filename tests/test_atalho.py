import subprocess

from inter_integra import atalho


def test_script_valido_e_com_aspas_seguras(tmp_path):
    projeto = "/Users/m 2/Inter Integra's"  # espaço e aspas simples
    texto = atalho.gerar_script(projeto, "/Users/m 2/PagamentosInter", "producao", "/Users/m 2/p/.venv/bin/python")
    f = tmp_path / "a.command"
    f.write_text(texto)
    assert subprocess.run(["bash", "-n", str(f)]).returncode == 0  # sintaxe ok
    assert "export INTER_AMBIENTE=producao" in texto
    assert "exec '/Users/m 2/p/.venv/bin/python' -m inter_integra web" in texto
    assert texto.startswith("#!/bin/bash")


def test_cmd_atalho_cria_arquivo_executavel(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    from inter_integra import __main__ as cli
    from inter_integra.config import Settings
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert cli.cmd_atalho(Settings(home=tmp_path / "p", ambiente="producao"), []) == 0
    arq = tmp_path / "Desktop" / atalho.NOME
    assert arq.exists() and os.access(arq, os.X_OK)
    assert "INTER_AMBIENTE=producao" in arq.read_text()
