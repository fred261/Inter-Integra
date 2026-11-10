"""Atalho de Área de Trabalho (.command): duplo clique abre a interface, já no ambiente certo."""
from __future__ import annotations

import shlex

NOME = "Inter Integra.command"
URL = "http://localhost:8000"


def gerar_script(projeto: str, home: str, ambiente: str, python: str) -> str:
    q = shlex.quote
    return f"""#!/bin/bash
# Gerado por: python -m inter_integra atalho  (pode apagar este arquivo quando quiser)
cd {q(projeto)} || {{ echo "Pasta do projeto não encontrada: {projeto}"; read -r -p "Enter para fechar"; exit 1; }}
export INTER_AMBIENTE={q(ambiente)}
export INTER_HOME={q(home)}

# Já está rodando? Então só abre a página.
if curl -s {URL}/ 2>/dev/null | grep -q "Inter Integra"; then
  open {URL}
  exit 0
fi

echo "Inter Integra · ambiente: {ambiente}"
echo "Deixe esta janela aberta enquanto usa. Para encerrar, feche-a ou tecle Ctrl+C."
( sleep 2; open {URL} ) &
exec {q(python)} -m inter_integra web
"""
