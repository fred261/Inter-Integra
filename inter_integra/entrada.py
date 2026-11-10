"""Pasta de entrada de boletos: PDFs soltos em ~/PagamentosInter/boletos/entrada viram rascunhos.

Só LÊ e cadastra. Nunca envia nada ao Inter: o envio continua exigindo conferência e a confirmação
digitada na interface. Não usa credenciais nem rede, então roda bem em segundo plano (launchd).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date

from . import boleto as bl, boletos as bs
from .config import Settings, ensure_entrada

ESTABILIZA_S = 3  # ignora arquivo modificado há menos que isso (cópia ainda em andamento)


@dataclass
class Resultado:
    arquivo: str
    ok: bool
    detalhe: str  # id do boleto ou motivo da recusa


def _nome_seguro(nome: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", nome)[:100] or "arquivo.pdf"


def _destino(pasta, nome: str):
    d = pasta / _nome_seguro(nome)
    n = 1
    while d.exists():
        d = pasta / f"{n}_{_nome_seguro(nome)}"
        n += 1
    return d


def processar_pasta(s: Settings, conn, hoje: date | None = None, agora: float | None = None) -> list[Resultado]:
    ensure_entrada(s)
    agora = agora if agora is not None else time.time()
    saida: list[Resultado] = []
    for arq in sorted(s.entrada_dir.iterdir()):
        st = arq.lstat()
        if arq.is_symlink() or not arq.is_file() or arq.suffix.lower() != ".pdf":
            continue  # nunca segue link simbólico nem processa o que não é PDF
        if agora - st.st_mtime < ESTABILIZA_S:
            continue
        try:
            if st.st_size > bl.MAX_PDF_BYTES:
                raise bl.BoletoInvalido("arquivo maior que 5 MB")
            b = bl.parse_pdf(arq.read_bytes(), hoje)
            bid = bs.criar(conn, b, arq.name, hoje=hoje)
        except (bl.BoletoInvalido, bs.BoletoRecusado) as e:
            dest = _destino(s.erros_dir, arq.name)
            os.replace(arq, dest)
            dest.with_name(dest.name + ".erro.txt").write_text(f"{e}\n", encoding="utf-8")
            saida.append(Resultado(arq.name, False, str(e)))
        else:
            os.replace(arq, _destino(s.processados_dir, f"{bid}_{arq.name}"))
            saida.append(Resultado(arq.name, True, str(bid)))
    return saida


def vigiar(s: Settings, parar: threading.Event, intervalo: float = 15.0) -> None:
    """Roda em segundo plano enquanto a interface está aberta: lê a pasta de tempos em tempos."""
    from . import db
    while not parar.is_set():
        try:
            conn = db.connect(s.db_path)
            try:
                res = processar_pasta(s, conn)
            finally:
                conn.close()
            if res:
                notificar(sum(r.ok for r in res), sum(not r.ok for r in res))
        except Exception:  # um arquivo/banco problemático não pode derrubar a vigilância
            pass
        parar.wait(intervalo)


def notificar(ok: int, erros: int) -> None:
    """Notificação do macOS com texto FIXO (nada vindo do PDF entra no comando)."""
    if sys.platform != "darwin" or not (ok or erros):
        return
    partes = []
    if ok:
        partes.append(f"{ok} boleto(s) para conferir")
    if erros:
        partes.append(f"{erros} com problema")
    try:
        subprocess.run(["osascript", "-e", "on run argv", "-e",
                        'display notification (item 1 of argv) with title "Inter Integra"',
                        "-e", "end run", ", ".join(partes)], timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def plist_agente(python: str, home: str, ambiente: str, entrada: str) -> bytes:
    """LaunchAgent que roda `entrada` quando a pasta muda (e a cada 2 minutos, por garantia)."""
    import plistlib
    return plistlib.dumps({
        "Label": "br.inter-integra.entrada",
        "ProgramArguments": [python, "-m", "inter_integra", "entrada"],
        "WorkingDirectory": home,
        "EnvironmentVariables": {"INTER_AMBIENTE": ambiente, "INTER_HOME": home},
        "WatchPaths": [entrada],
        "StartInterval": 120,
        "RunAtLoad": True,
        "StandardOutPath": f"{home}/entrada.log",
        "StandardErrorPath": f"{home}/entrada.log",
    })
