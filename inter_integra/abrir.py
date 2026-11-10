"""`abrir`: inicia a interface (ou só abre o navegador), reiniciando se o código mudou.

Chamado pelo atalho da Área de Trabalho. Só encerra um processo que for, comprovadamente, o servidor
deste projeto (escuta na porta E a linha de comando contém 'inter_integra').
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from .versao import codigo_versao

PORTA_PADRAO = 8000


def porta() -> int:
    return int(os.environ.get("INTER_PORTA", PORTA_PADRAO))


def _get(url: str, timeout: float = 1.5) -> tuple[int, str] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(200_000).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return None


def estado_porta(p: int) -> tuple[str, str | None]:
    """('livre'|'nosso'|'outro', versão_rodando). 'nosso' = é o servidor deste projeto."""
    r = _get(f"http://127.0.0.1:{p}/versao")
    if r is None:
        return "livre", None
    status, corpo = r
    if status == 200:
        try:
            j = json.loads(corpo)
            if j.get("app") == "inter-integra":
                return "nosso", j.get("versao")
        except ValueError:
            pass
    # servidor de uma versão anterior (sem /versao): reconhece pela página inicial
    home = _get(f"http://127.0.0.1:{p}/")
    if home and "Inter Integra" in home[1]:
        return "nosso", None
    return "outro", None


def decidir(estado: str, versao_rodando: str | None, versao_local: str) -> str:
    if estado == "livre":
        return "iniciar"
    if estado == "outro":
        return "ocupada"
    return "abrir" if versao_rodando == versao_local else "reiniciar"


def pid_escutando(p: int) -> int | None:
    try:
        out = subprocess.run(["lsof", "-ti", f"tcp:{p}", "-sTCP:LISTEN"], capture_output=True,
                             text=True, timeout=10).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    return int(out[0]) if len(out) == 1 and out[0].isdigit() else None


def e_servidor_do_projeto(pid: int) -> bool:
    try:
        cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "inter_integra" in cmd


def encerrar_servidor(p: int, espera_s: float = 8.0) -> bool:
    pid = pid_escutando(p)
    if pid is None or pid == os.getpid() or not e_servidor_do_projeto(pid):
        return False
    os.kill(pid, signal.SIGTERM)
    fim = time.time() + espera_s
    while time.time() < fim:
        if estado_porta(p)[0] == "livre":
            return True
        time.sleep(0.2)
    return False


def _abrir_navegador(p: int) -> None:
    for _ in range(50):  # espera o servidor subir (até ~10 s)
        if estado_porta(p)[0] == "nosso":
            break
        time.sleep(0.2)
    webbrowser.open(f"http://localhost:{p}")


def executar(subir_servidor) -> int:
    """`subir_servidor(p)` bloqueia rodando o servidor; é injetado para poder testar."""
    p, local = porta(), codigo_versao()
    estado, rodando = estado_porta(p)
    acao = decidir(estado, rodando, local)
    if acao == "ocupada":
        print(f"A porta {p} está ocupada por outro programa (não é o Inter Integra). Feche-o e tente de novo.")
        return 1
    if acao == "abrir":
        webbrowser.open(f"http://localhost:{p}")
        return 0
    if acao == "reiniciar":
        print("Código atualizado: reiniciando o sistema...")
        if not encerrar_servidor(p):
            print("Não consegui encerrar a versão anterior. Feche a janela antiga do Inter Integra e abra o atalho de novo.")
            return 1
    threading.Thread(target=_abrir_navegador, args=(p,), daemon=True).start()
    subir_servidor(p)
    return 0
