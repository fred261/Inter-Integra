"""Atalho de Área de Trabalho (.command): duplo clique abre a interface, já no ambiente certo."""
from __future__ import annotations

import shlex

NOME = "Inter Integra.command"
URL = "http://localhost:8000"


def gerar_script(projeto: str, home: str, ambiente: str, python: str) -> str:
    """Script mínimo: a lógica (iniciar / só abrir / reiniciar se o código mudou) fica em
    `python -m inter_integra abrir`, então um `git pull` atualiza o comportamento do atalho."""
    q = shlex.quote
    return f"""#!/bin/bash
# Gerado por: python -m inter_integra atalho  (pode apagar este arquivo quando quiser)
cd {q(projeto)} || {{ echo "Pasta do projeto não encontrada: {projeto}"; read -r -p "Enter para fechar"; exit 1; }}
export INTER_AMBIENTE={q(ambiente)}
export INTER_HOME={q(home)}
echo "Inter Integra · ambiente: {ambiente}"
echo "Deixe esta janela aberta enquanto usa. Para encerrar, feche-a ou tecle Ctrl+C."
{q(python)} -m inter_integra abrir
status=$?
# se a página só foi aberta (sistema já rodando), a janela pode fechar sozinha
[ $status -eq 0 ] || read -r -p "Enter para fechar"
exit $status
"""
