"""Interface web local (somente 127.0.0.1). Todo envio passa por conferência + confirmação digitada."""
from __future__ import annotations

import csv
import hmac
import io
import secrets
import time
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from keyring.errors import KeyringError
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import credentials, db, pagamentos as pg
from .client import InterClient, InterError
from .config import Settings

BASE = Path(__file__).parent
HOSTS_PERMITIDOS = ["localhost", "127.0.0.1"]
SALDO_CACHE_S = 60  # o endpoint de saldo limita a 10 chamadas/min
STATUS_LABEL = {
    "rascunho": ("Rascunho", "neutro"), "enviando": ("Enviando", "aviso"),
    "erro": ("Erro", "erro"), "aguardando_aprovacao": ("Aguardando aprovação", "aviso"),
    "processando": ("Processando", "aviso"), "REALIZADO": ("Realizado", "ok"),
}


def brl(centavos) -> str:
    if centavos is None:
        return "—"
    s = f"{abs(int(centavos)) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if centavos < 0 else ''}R$ {s}"


def status_label(status: str) -> tuple[str, str]:
    if status in STATUS_LABEL:
        return STATUS_LABEL[status]
    return (status.replace("_", " ").capitalize(), "erro" if status in pg.STATUS_FINAIS else "aviso")


def _csv_seguro(v) -> str:
    """Evita injeção de fórmula ao abrir o CSV no Excel/Numbers."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def create_app(settings: Settings,
               client_factory: Callable[[Settings], InterClient] | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=HOSTS_PERMITIDOS)
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
    templates = Jinja2Templates(directory=BASE / "templates")
    templates.env.filters["brl"] = brl
    templates.env.filters["num"] = lambda c: f"{c / 100:.2f}".replace(".", ",")
    templates.env.globals["status_label"] = status_label
    csrf_token = secrets.token_urlsafe(32)  # novo a cada início do servidor
    templates.env.globals["csrf"] = csrf_token
    templates.env.globals["ambiente"] = settings.ambiente
    saldo_cache: dict = {"t": 0.0, "v": None}

    def novo_cliente() -> InterClient:
        if client_factory:
            return client_factory(settings)
        return InterClient(settings, *credentials.carregar(settings))

    @app.middleware("http")
    async def cabecalhos(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        return resp

    def get_conn():
        conn = db.connect(settings.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def checar_csrf(request: Request, csrf_token_form: str = Form("", alias="csrf_token")) -> None:
        origem = request.headers.get("origin")
        if origem and urlparse(origem).hostname not in HOSTS_PERMITIDOS:
            raise HTTPException(403, "origem não permitida")
        if not hmac.compare_digest(csrf_token_form, csrf_token):
            raise HTTPException(403, "token inválido; recarregue a página")

    def render(request: Request, nome: str, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, nome, {"pagina": nome.split(".")[0], **ctx})

    # --- painel --------------------------------------------------------
    def obter_saldo() -> tuple[str | None, str | None]:
        if saldo_cache["v"] is not None and time.time() - saldo_cache["t"] < SALDO_CACHE_S:
            return saldo_cache["v"], None
        try:
            j = novo_cliente().saldo()
        except KeyringError:
            return None, "Keychain indisponível. Rode: python -m inter_integra credenciais"
        except (InterError, credentials.CredenciaisAusentes, OSError) as e:
            return None, str(e)
        v = j.get("disponivel")  # campo a confirmar na resposta real do Inter
        if v is None:
            return None, "resposta do saldo sem o campo 'disponivel'"
        saldo_cache.update(t=time.time(), v=v)
        return v, None

    @app.get("/", response_class=HTMLResponse)
    def painel(request: Request, conn=Depends(get_conn)):
        saldo, erro_saldo = obter_saldo()
        pend = conn.execute(
            "SELECT p.*, b.nome FROM pagamentos p LEFT JOIN beneficiarios b ON b.id = p.beneficiario_id "
            "WHERE p.status IN ('aguardando_aprovacao','processando','enviando') ORDER BY p.id DESC").fetchall()
        ult = conn.execute(
            "SELECT p.*, b.nome FROM pagamentos p LEFT JOIN beneficiarios b ON b.id = p.beneficiario_id "
            "WHERE p.status NOT IN ('rascunho') ORDER BY p.id DESC LIMIT 10").fetchall()
        return render(request, "painel.html", saldo_centavos=None if saldo is None else round(saldo * 100),
                      erro_saldo=erro_saldo, pendentes=pend, ultimos=ult)

    @app.post("/atualizar", dependencies=[Depends(checar_csrf)])
    def atualizar(conn=Depends(get_conn)):
        try:
            pg.atualizar_status(conn, novo_cliente())
        except (InterError, credentials.CredenciaisAusentes):
            pass  # o painel mostra o estado atual; detalhes ficam no log
        return RedirectResponse("/", status_code=303)

    # --- beneficiários -------------------------------------------------
    def lista_benef(conn):
        return conn.execute("SELECT * FROM beneficiarios ORDER BY ativo DESC, nome").fetchall()

    @app.get("/beneficiarios", response_class=HTMLResponse)
    def beneficiarios(request: Request, conn=Depends(get_conn)):
        return render(request, "beneficiarios.html", itens=lista_benef(conn), tipos=pg.TIPOS, erro=None)

    @app.post("/beneficiarios", dependencies=[Depends(checar_csrf)])
    def benef_novo(request: Request, nome: str = Form(...), chave_pix: str = Form(...),
                   tipo: str = Form("colaborador"), cpf: str = Form(""), valor: str = Form("0"),
                   conn=Depends(get_conn)):
        try:
            pg.add_beneficiario(conn, nome, chave_pix, tipo, cpf or None, pg.parse_valor(valor or "0"))
        except pg.PagamentoInvalido as e:
            return render(request, "beneficiarios.html", itens=lista_benef(conn), tipos=pg.TIPOS,
                          erro=str(e))
        return RedirectResponse("/beneficiarios", status_code=303)

    @app.post("/beneficiarios/{bid}", dependencies=[Depends(checar_csrf)])
    def benef_editar(request: Request, bid: int, nome: str = Form(...), chave_pix: str = Form(...),
                     tipo: str = Form(...), cpf: str = Form(""), valor: str = Form("0"),
                     ativo: str = Form(""), conn=Depends(get_conn)):
        try:
            pg.update_beneficiario(conn, bid, nome, chave_pix, tipo, cpf or None,
                                   pg.parse_valor(valor or "0"), bool(ativo))
        except pg.PagamentoInvalido as e:
            return render(request, "beneficiarios.html", itens=lista_benef(conn), tipos=pg.TIPOS,
                          erro=str(e))
        return RedirectResponse("/beneficiarios", status_code=303)

    @app.post("/pix/conferir", dependencies=[Depends(checar_csrf)])
    def pix_avulso(request: Request, beneficiario_id: int = Form(...), valor: str = Form(...),
                   data: str = Form(""), descricao: str = Form(""), conn=Depends(get_conn)):
        try:
            pid = pg.criar_pix_chave(conn, beneficiario_id, pg.parse_valor(valor), data or None,
                                     descricao or None)
        except (pg.PagamentoInvalido, ValueError) as e:
            return render(request, "beneficiarios.html", itens=lista_benef(conn), tipos=pg.TIPOS,
                          erro=str(e))
        return RedirectResponse(f"/pagamento/{pid}", status_code=303)

    # --- Caju ----------------------------------------------------------
    @app.get("/caju", response_class=HTMLResponse)
    def caju(request: Request):
        return render(request, "caju.html", erro=None, codigo="", valor="")

    @app.post("/caju/conferir", dependencies=[Depends(checar_csrf)])
    def caju_conferir(request: Request, codigo: str = Form(...), valor: str = Form(...),
                      conn=Depends(get_conn)):
        try:
            pid = pg.criar_pix_copia_e_cola(conn, codigo, pg.parse_valor(valor),
                                            "Recarga benefícios Caju")
        except pg.PagamentoInvalido as e:
            return render(request, "caju.html", erro=str(e), codigo=codigo, valor=valor)
        return RedirectResponse(f"/pagamento/{pid}", status_code=303)

    # --- conferência e envio de um pagamento ---------------------------
    def buscar_pagamento(conn, pid: int):
        p = conn.execute(
            "SELECT p.*, b.nome, b.chave_pix FROM pagamentos p "
            "LEFT JOIN beneficiarios b ON b.id = p.beneficiario_id WHERE p.id = ?", (pid,)).fetchone()
        if p is None:
            raise HTTPException(404, "pagamento não encontrado")
        return p

    @app.get("/pagamento/{pid}", response_class=HTMLResponse)
    def pagamento(request: Request, pid: int, conn=Depends(get_conn)):
        return render(request, "pagamento.html", p=buscar_pagamento(conn, pid), erro=None,
                      enviaveis=pg.ENVIAVEIS)

    @app.post("/pagamento/{pid}/enviar", dependencies=[Depends(checar_csrf)])
    def pagamento_enviar(request: Request, pid: int, confirmacao: str = Form(""),
                         conn=Depends(get_conn)):
        erro = None
        if confirmacao.strip() != "ENVIAR":
            erro = "Digite ENVIAR para confirmar."
        else:
            try:
                pg.enviar(conn, novo_cliente(), pid, confirmado=True)
            except (pg.PagamentoInvalido, InterError, credentials.CredenciaisAusentes) as e:
                erro = str(e)
            except OSError as e:  # rede/timeout: fica 'enviando'; reenviar usa o mesmo id
                erro = f"Sem resposta do Inter ({e.__class__.__name__}). Reenvie: o mesmo código é usado."
        if erro:
            return render(request, "pagamento.html", p=buscar_pagamento(conn, pid), erro=erro,
                          enviaveis=pg.ENVIAVEIS)
        return RedirectResponse(f"/pagamento/{pid}", status_code=303)

    @app.post("/pagamento/{pid}/descartar", dependencies=[Depends(checar_csrf)])
    def pagamento_descartar(pid: int, conn=Depends(get_conn)):
        with conn:
            conn.execute("DELETE FROM pagamentos WHERE id = ? AND status = 'rascunho' "
                         "AND folha_id IS NULL", (pid,))
        return RedirectResponse("/", status_code=303)

    # --- folha ---------------------------------------------------------
    def colaboradores(conn):
        return conn.execute("SELECT * FROM beneficiarios WHERE ativo = 1 AND tipo = 'colaborador' "
                            "ORDER BY nome").fetchall()

    @app.get("/folha", response_class=HTMLResponse)
    def folha(request: Request, conn=Depends(get_conn)):
        hoje = date.today()
        return render(request, "folha.html", itens=colaboradores(conn), erro=None,
                      competencia=hoje.strftime("%Y-%m"), data=hoje.isoformat(),
                      folhas=conn.execute("SELECT * FROM folhas ORDER BY id DESC LIMIT 10").fetchall())

    @app.post("/folha/conferir", dependencies=[Depends(checar_csrf)])
    def folha_conferir(request: Request, competencia: str = Form(...), data: str = Form(...),
                       ids: list[int] = Form([]), valor: list[str] = Form([]),
                       sel: list[int] = Form([]), conn=Depends(get_conn)):
        try:
            if len(ids) != len(valor):
                raise pg.PagamentoInvalido("formulário inconsistente")
            itens = [(i, pg.parse_valor(v)) for i, v in zip(ids, valor) if i in set(sel)]
            fid = pg.criar_folha(conn, competencia.strip(), data, itens)
        except (pg.PagamentoInvalido, ValueError) as e:
            return render(request, "folha.html", itens=colaboradores(conn), erro=str(e),
                          competencia=competencia, data=data, folhas=[])
        return RedirectResponse(f"/folha/{fid}", status_code=303)

    def detalhe_folha(conn, fid: int):
        f = conn.execute("SELECT * FROM folhas WHERE id = ?", (fid,)).fetchone()
        if f is None:
            raise HTTPException(404, "folha não encontrada")
        ps = conn.execute(
            "SELECT p.*, b.nome, b.chave_pix FROM pagamentos p JOIN beneficiarios b "
            "ON b.id = p.beneficiario_id WHERE p.folha_id = ? ORDER BY b.nome", (fid,)).fetchall()
        return f, ps

    @app.get("/folha/{fid}", response_class=HTMLResponse)
    def folha_detalhe(request: Request, fid: int, conn=Depends(get_conn)):
        f, ps = detalhe_folha(conn, fid)
        return render(request, "folha_detalhe.html", f=f, ps=ps, erro=None, enviaveis=pg.ENVIAVEIS)

    @app.post("/folha/{fid}/enviar", dependencies=[Depends(checar_csrf)])
    def folha_enviar(request: Request, fid: int, total: str = Form(""), conn=Depends(get_conn)):
        erro = None
        try:
            pg.enviar_folha(conn, novo_cliente(), fid, pg.parse_valor(total))
        except (pg.PagamentoInvalido, InterError, credentials.CredenciaisAusentes) as e:
            erro = str(e)
        except OSError as e:
            erro = f"Sem resposta do Inter ({e.__class__.__name__}). Reenvie: os mesmos códigos são usados."
        if erro:
            f, ps = detalhe_folha(conn, fid)
            return render(request, "folha_detalhe.html", f=f, ps=ps, erro=erro, enviaveis=pg.ENVIAVEIS)
        return RedirectResponse(f"/folha/{fid}", status_code=303)

    @app.post("/folha/{fid}/descartar", dependencies=[Depends(checar_csrf)])
    def folha_descartar(request: Request, fid: int, conn=Depends(get_conn)):
        try:
            pg.descartar_folha(conn, fid)
        except pg.PagamentoInvalido as e:
            f, ps = detalhe_folha(conn, fid)
            return render(request, "folha_detalhe.html", f=f, ps=ps, erro=str(e), enviaveis=pg.ENVIAVEIS)
        return RedirectResponse("/folha", status_code=303)

    # --- histórico -----------------------------------------------------
    def consulta_historico(conn, de: str, ate: str, status: str):
        sql, args = ("SELECT p.*, b.nome FROM pagamentos p LEFT JOIN beneficiarios b "
                     "ON b.id = p.beneficiario_id WHERE 1=1"), []
        if de:
            sql += " AND date(p.criado_em) >= ?"
            args.append(date.fromisoformat(de).isoformat())
        if ate:
            sql += " AND date(p.criado_em) <= ?"
            args.append(date.fromisoformat(ate).isoformat())
        if status:
            sql += " AND p.status = ?"
            args.append(status)
        return conn.execute(sql + " ORDER BY p.id DESC", args).fetchall()

    @app.get("/historico", response_class=HTMLResponse)
    def historico(request: Request, de: str = "", ate: str = "", status: str = "",
                  conn=Depends(get_conn)):
        try:
            itens, erro = consulta_historico(conn, de, ate, status), None
        except ValueError:
            itens, erro = [], "Data inválida (use AAAA-MM-DD)."
        return render(request, "historico.html", itens=itens, erro=erro, de=de, ate=ate, status=status)

    @app.get("/historico.csv")
    def historico_csv(de: str = "", ate: str = "", status: str = "", conn=Depends(get_conn)):
        try:
            itens = consulta_historico(conn, de, ate, status)
        except ValueError:
            raise HTTPException(400, "data inválida")
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["id", "criado_em", "beneficiario", "destino", "valor", "data_pagamento",
                    "status", "codigo_solicitacao", "erro"])
        for p in itens:
            w.writerow([_csv_seguro(x) for x in (
                p["id"], p["criado_em"], p["nome"] or "Pix Copia e Cola",
                "copia_e_cola" if p["pix_copia_e_cola"] else "chave",
                f"{p['valor_centavos'] / 100:.2f}".replace(".", ","), p["data_pagamento"],
                p["status"], p["codigo_solicitacao"], p["erro"])])
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="historico.csv"'})

    return app
