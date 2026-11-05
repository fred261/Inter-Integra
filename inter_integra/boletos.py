"""Pagamento de boletos: do PDF ao agendamento no Inter.

Como a API não tem idempotência para boleto, a proteção contra pagamento em duplicidade é local:
código de barras único no banco, estado 'enviando' gravado antes da chamada e, se a resposta se
perder, consulta ao Inter antes de qualquer reenvio.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from . import db
from .boleto import Boleto, BoletoInvalido
from .client import InterClient, InterError

ENVIAVEIS = ("rascunho", "erro")
# TODO(validação real): conferir todos os valores de statusPagamento devolvidos pelo Inter.
STATUS_FINAIS = {"PAGO", "CANCELADO", "REPROVADO", "EXPIRADO", "FALHA", "NAO_PAGO", "DEVOLVIDO"}


class BoletoRecusado(ValueError):
    pass


def data_de_pagamento(vencimento: date, hoje: date | None = None) -> date:
    """Paga no vencimento; se cair no fim de semana, na sexta anterior (nunca depois do vencimento).
    Feriados não são tratados: boleto que vence em feriado deve ser conferido manualmente."""
    hoje = hoje or date.today()
    d = vencimento
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return max(d, hoje)


def criar(conn: sqlite3.Connection, b: Boleto, arquivo: str | None = None,
          vencimento_manual: date | None = None, hoje: date | None = None) -> int:
    hoje = hoje or date.today()
    venc = b.vencimento or vencimento_manual
    if venc is None:
        raise BoletoRecusado("não consegui identificar o vencimento; informe a data manualmente")
    if b.valor_centavos is None:
        raise BoletoRecusado("boleto sem valor definido (valor livre) não é suportado")
    if venc < hoje:
        raise BoletoRecusado(f"boleto vencido em {venc.isoformat()}: o valor pode ter juros/multa; "
                             "pague manualmente ou gere uma nova via")
    teto = db.teto_centavos(conn)
    if b.valor_centavos > teto:
        raise BoletoRecusado(f"valor R$ {b.valor_centavos / 100:.2f} acima do teto de R$ {teto / 100:.2f}")
    ja = conn.execute("SELECT id, status FROM boletos WHERE codigo_barras = ?",
                      (b.codigo_barras,)).fetchone()
    if ja:
        raise BoletoRecusado(f"este boleto já foi cadastrado (#{ja['id']}, status {ja['status']})")
    with conn:
        return conn.execute(
            "INSERT INTO boletos (codigo_barras, linha_digitavel, tipo, valor_centavos, data_vencimento, "
            "data_pagamento, cnpj_beneficiario, beneficiario_texto, arquivo) VALUES (?,?,?,?,?,?,?,?,?)",
            (b.codigo_barras, b.linha_digitavel or None, b.tipo, b.valor_centavos, venc.isoformat(),
             data_de_pagamento(venc, hoje).isoformat(), b.cnpj_beneficiario, b.beneficiario_texto,
             (arquivo or "")[:120] or None)).lastrowid


def _payload(r: sqlite3.Row) -> dict:
    body = {"codBarraLinhaDigitavel": r["linha_digitavel"] or r["codigo_barras"],
            "valorPagar": f"{r['valor_centavos'] / 100:.2f}",
            "dataPagamento": r["data_pagamento"], "dataVencimento": r["data_vencimento"]}
    if r["cnpj_beneficiario"]:  # o Inter confere com o beneficiário real do boleto
        body["cpfCnpjBeneficiario"] = r["cnpj_beneficiario"]
    return body


def _salvar_resposta(conn, bid: int, r: dict) -> None:
    with conn:
        conn.execute(
            "UPDATE boletos SET status=?, codigo_transacao=?, aprovadores=?, erro=NULL, "
            "atualizado_em=datetime('now') WHERE id=?",
            (str(r.get("statusPagamento") or "EMPROCESSAMENTO").upper(), r.get("codigoTransacao"),
             r.get("quantidadeAprovadores"), bid))


def _ja_existe_no_inter(client: InterClient, row: sqlite3.Row) -> dict | None:
    hoje = date.today()
    itens = client.buscar_boletos(
        codBarraLinhaDigitavel=row["linha_digitavel"] or row["codigo_barras"],
        dataInicio=(hoje - timedelta(days=60)).isoformat(), dataFim=hoje.isoformat())
    ativos = [i for i in (itens or []) if str(i.get("statusPagamento", "")).upper()
              not in ("CANCELADO", "REPROVADO", "EXPIRADO", "FALHA")]
    return ativos[0] if ativos else None


def enviar(conn, client: InterClient, bid: int, confirmado: bool = False) -> sqlite3.Row:
    if confirmado is not True:
        raise BoletoRecusado("envio exige confirmação explícita")
    r = conn.execute("SELECT * FROM boletos WHERE id = ?", (bid,)).fetchone()
    if r is None:
        raise BoletoRecusado("boleto inexistente")
    if r["status"] not in ENVIAVEIS + ("enviando",):
        raise BoletoRecusado(f"boleto já enviado (status {r['status']})")
    if date.fromisoformat(r["data_pagamento"]) < date.today():
        raise BoletoRecusado("data de pagamento no passado; descarte e cadastre de novo")
    if r["valor_centavos"] > db.teto_centavos(conn):
        raise BoletoRecusado("valor acima do teto")
    if r["status"] == "enviando":
        # resposta anterior se perdeu: o Inter pode já ter o pagamento. Nunca reenviar às cegas.
        achado = _ja_existe_no_inter(client, r)
        if achado:
            _salvar_resposta(conn, bid, achado)
            return conn.execute("SELECT * FROM boletos WHERE id = ?", (bid,)).fetchone()
    with conn:
        conn.execute("UPDATE boletos SET status='enviando', erro=NULL, atualizado_em=datetime('now') "
                     "WHERE id=?", (bid,))
    try:
        resp = client.incluir_boleto(_payload(r))
    except InterError as e:
        with conn:
            conn.execute("UPDATE boletos SET status='erro', erro=?, atualizado_em=datetime('now') "
                         "WHERE id=?", (str(e)[:300], bid))
    else:
        _salvar_resposta(conn, bid, resp)
    return conn.execute("SELECT * FROM boletos WHERE id = ?", (bid,)).fetchone()


def atualizar_status(conn, client: InterClient, sleep=lambda s: None) -> int:
    pend = conn.execute(
        "SELECT id, codigo_transacao FROM boletos WHERE codigo_transacao IS NOT NULL AND status NOT IN (%s)"
        % ",".join("?" * len(STATUS_FINAIS)), tuple(STATUS_FINAIS)).fetchall()
    n = 0
    for row in pend:
        try:
            itens = client.buscar_boletos(codigoTransacao=row["codigo_transacao"])
        except InterError:
            continue
        if itens:
            with conn:
                conn.execute("UPDATE boletos SET status=?, atualizado_em=datetime('now') WHERE id=?",
                             (str(itens[0].get("statusPagamento", "")).upper() or "DESCONHECIDO", row["id"]))
        n += 1
        sleep(6.5)  # 10 consultas/min no pior caso
    return n


def cancelar(conn, client: InterClient, bid: int) -> None:
    """Cancela um pagamento agendado ainda não efetivado."""
    r = conn.execute("SELECT * FROM boletos WHERE id = ?", (bid,)).fetchone()
    if r is None or not r["codigo_transacao"]:
        raise BoletoRecusado("boleto não foi enviado ao Inter")
    client.cancelar_boleto(r["codigo_transacao"])
    with conn:
        conn.execute("UPDATE boletos SET status='CANCELADO', atualizado_em=datetime('now') WHERE id=?",
                     (bid,))


def descartar(conn, bid: int) -> None:
    with conn:
        n = conn.execute("DELETE FROM boletos WHERE id = ? AND status IN ('rascunho','erro')",
                         (bid,)).rowcount
    if not n:
        raise BoletoRecusado("só é possível descartar boleto ainda não enviado")
