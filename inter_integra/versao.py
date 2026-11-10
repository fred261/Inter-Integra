"""Versão do código em execução: hash dos arquivos do pacote (funciona sem git e com edições locais)."""
from __future__ import annotations

import hashlib
from pathlib import Path

_EXT = {".py", ".html", ".css", ".js"}


def codigo_versao(raiz: Path | None = None) -> str:
    raiz = raiz or Path(__file__).parent
    h = hashlib.sha256()
    for arq in sorted(raiz.rglob("*")):
        if arq.is_file() and arq.suffix in _EXT and "__pycache__" not in arq.parts:
            h.update(arq.relative_to(raiz).as_posix().encode())
            h.update(arq.read_bytes())
    return h.hexdigest()[:12]
