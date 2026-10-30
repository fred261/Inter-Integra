"""Pix por chave e por Copia e Cola, atualização de status e folha.

Regras: o id idempotente é gravado antes da chamada; nada é enviado sem confirmação
explícita; valores acima do teto são bloqueados.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from . import db
from .client import InterClient, InterError

MAX_DESCRICAO = 140
INTERVALO_ENVIO_S = 1.1  # a API permite no máximo 1 chamada Pix por segundo
# Status locais (anteriores à resposta do Inter). Os demais vêm da API.
ENVIAVEIS = ("rascunho", "erro", "enviando")  # 'enviando' = resposta perdida; reenvio usa o mesmo id
# TODO(etapa 6): confirmar a lista real de status finais com respostas do Inter.
STATUS_FINAIS = {"REALIZADO", "CANCELADO", "REPROVADO", "EXPIRADO", "FALHA", "DEVOLVIDO", "NAO_REALIZADO"}


class PagamentoInvalido(ValueError):
    pass


def parse_valor(txt: str) -> int:
    """'1.234,56' ou '1234.56' -> centavos."""
    s = txt.strip().replace("R$", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        v = Decimal(s)
    except InvalidOperation:
        raise PagamentoInvalido(f"valor inválido: {txt!r}") from None
    if v != v.quantize(Decimal("0.01")):
        raise PagamentoInvalido("valor com mais de duas casas decimais")
    return int(v * 100)


def _validar(conn, valor_centavos: int, descricao: str | None, data_pagamento: str | None) -> None:
    if valor_centavos <= 0:
        raise PagamentoInvalido("valor deve ser positivo")
    teto = db.teto_centavos(conn)
    if valor_centavos > teto:
        raise PagamentoInvalido(
            f"valor R$ {valor_centavos / 100:.2f} acima do teto de R$ {teto / 100:.2f}")
    if descricao and len(descricao) > MAX_DESCRICAO:
        raise PagamentoInvalido(f"descrição acima de {MAX_DESCRICAO} caracteres")
    if data_pagamento and date.fromisoformat(data_pagamento) < date.today():
        raise PagamentoInvalido("dataPagamento no passado")


# --- beneficiários ---------------------------------------------------
def add_beneficiario(conn, nome: str, chave_pix: str, tipo: str = "colaborador",
                     cpf: str | None = None, valor_padrao_centavos: int = 0) -> int:
    if not nome.strip() or not chave_pix.strip():
        raise PagamentoInvalido("nome e chave Pix são obrigatórios")
    if tipo not in TIPOS:
        raise PagamentoInvalido("tipo inválido")
    with conn:
        cur = conn.execute(
            "INSERT INTO beneficiarios (nome, cpf, chave_pix, tipo, valor_padrao_centavos) "
            "VALUES (?, ?, ?, ?, ?)", (nome.strip(), cpf, chave_pix.strip(), tipo, valor_padrao_centavos))
    return cur.lastrowid


TIPOS = ("colaborador", "faxina", "outro")


def update_beneficiario(conn, bid: int, nome: str, chave_pix: str, tipo: str,
                        cpf: str | None, valor_padrao_centavos: int, ativo: bool) -> None:
    if not nome.strip() or not chave_pix.strip():
        raise PagamentoInvalido("nome e chave Pix são obrigatórios")
    if tipo not in TIPOS:
        raise PagamentoInvalido("tipo inválido")
    if valor_padrao_centavos < 0:
        raise PagamentoInvalido("valor padrão não pode ser negativo")
    with conn:
        cur = conn.execute(
            "UPDATE beneficiarios SET nome=?, chave_pix=?, tipo=?, cpf=?, valor_padrao_centavos=?, "
            "ativo=? WHERE id=?",
            (nome.strip(), chave_pix.strip(), tipo, cpf or None, valor_padrao_centavos,
             int(ativo), bid))
    if cur.rowcount == 0:
        raise PagamentoInvalido("beneficiário inexistente")


# --- criação (rascunho) ----------------------------------------------
def _checar_pix_chave(conn, beneficiario_id, valor_centavos, data_pagamento, descricao) -> None:
    b = conn.execute("SELECT 1 FROM beneficiarios WHERE id = ? AND ativo = 1",
                     (beneficiario_id,)).fetchone()
    if b is None:
        raise PagamentoInvalido("beneficiário inexistente ou inativo")
    _validar(conn, valor_centavos, descricao, data_pagamento)


def _inserir_pix_chave(conn, beneficiario_id, valor_centavos, data_pagamento, descricao,
                       folha_id) -> int:
    """Sem transação própria: quem chama controla o commit."""
    return conn.execute(
        "INSERT INTO pagamentos (beneficiario_id, valor_centavos, data_pagamento, descricao, "
        "id_idempotente, folha_id) VALUES (?, ?, ?, ?, ?, ?)",
        (beneficiario_id, valor_centavos, data_pagamento, descricao, str(uuid.uuid4()),
         folha_id)).lastrowid


def criar_pix_chave(conn, beneficiario_id: int, valor_centavos: int,
                    data_pagamento: str | None = None, descricao: str | None = None,
                    folha_id: int | None = None) -> int:
    _checar_pix_chave(conn, beneficiario_id, valor_centavos, data_pagamento, descricao)
    with conn:
        return _inserir_pix_chave(conn, beneficiario_id, valor_centavos, data_pagamento,
                                  descricao, folha_id)


def criar_pix_copia_e_cola(conn, codigo: str, valor_centavos: int,
                           descricao: str | None = None) -> int:
    codigo = "".join(codigo.split())
    if len(codigo) < 20:
        raise PagamentoInvalido("código Copia e Cola parece incompleto")
    _validar(conn, valor_centavos, descricao, None)
    with conn:
        cur = conn.execute(
            "INSERT INTO pagamentos (pix_copia_e_cola, valor_centavos, descricao, id_idempotente) "
            "VALUES (?, ?, ?, ?)", (codigo, valor_centavos, descricao, str(uuid.uuid4())))
    return cur.lastrowid


def _payload(conn, p: sqlite3.Row) -> dict:
    body: dict = {"valor": round(p["valor_centavos"] / 100, 2)}
    if p["data_pagamento"]:
        body["dataPagamento"] = p["data_pagamento"]
    if p["descricao"]:
        body["descricao"] = p["descricao"]
    if p["pix_copia_e_cola"]:
        body["destinatario"] = {"tipo": "PIX_COPIA_E_COLA", "pixCopiaECola": p["pix_copia_e_cola"]}
    else:
        chave = conn.execute("SELECT chave_pix FROM beneficiarios WHERE id = ?",
                             (p["beneficiario_id"],)).fetchone()["chave_pix"]
        body["destinatario"] = {"tipo": "CHAVE", "chave": chave}
    return body


# --- envio -----------------------------------------------------------
def enviar(conn, client: InterClient, pagamento_id: int, confirmado: bool = False) -> sqlite3.Row:
    """Envia um pagamento ao Inter (que ficará aguardando aprovação no app)."""
    if confirmado is not True:
        raise PagamentoInvalido("envio exige confirmação explícita")
    p = conn.execute("SELECT * FROM pagamentos WHERE id = ?", (pagamento_id,)).fetchone()
    if p is None:
        raise PagamentoInvalido("pagamento inexistente")
    if p["status"] not in ENVIAVEIS:
        raise PagamentoInvalido(f"pagamento já enviado (status {p['status']})")
    _validar(conn, p["valor_centavos"], p["descricao"], p["data_pagamento"])  # teto pode ter mudado
    with conn:
        conn.execute("UPDATE pagamentos SET status='enviando', erro=NULL, "
                     "atualizado_em=datetime('now') WHERE id=?", (pagamento_id,))
    try:
        r = client.incluir_pix(_payload(conn, p), p["id_idempotente"])
    except InterError as e:
        with conn:
            conn.execute("UPDATE pagamentos SET status='erro', erro=?, "
                         "atualizado_em=datetime('now') WHERE id=?", (str(e)[:300], pagamento_id))
    # Timeout/rede (não-InterError) propaga: o Inter pode ter recebido, o pagamento fica
    # 'enviando' e o reenvio usa o mesmo id idempotente.
    else:
        tipo = r.get("tipoRetorno")
        status = "aguardando_aprovacao" if tipo == "APROVACAO" else "processando"
        with conn:
            conn.execute(
                "UPDATE pagamentos SET status=?, tipo_retorno=?, codigo_solicitacao=?, "
                "atualizado_em=datetime('now') WHERE id=?",
                (status, tipo, r.get("codigoSolicitacao"), pagamento_id))
    return conn.execute("SELECT * FROM pagamentos WHERE id = ?", (pagamento_id,)).fetchone()


def atualizar_status(conn, client: InterClient, sleep=time.sleep) -> int:
    """Consulta o Inter para cada pagamento pendente. Retorna quantos foram consultados."""
    pend = conn.execute(
        "SELECT id, codigo_solicitacao FROM pagamentos WHERE codigo_solicitacao IS NOT NULL "
        "AND status NOT IN ('rascunho','erro','enviando') "
        "AND status NOT IN (%s)" % ",".join("?" * len(STATUS_FINAIS)),
        tuple(STATUS_FINAIS)).fetchall()
    n = 0
    for row in pend:
        try:
            tx = client.consultar_pix(row["codigo_solicitacao"]).get("transacaoPix", {})
        except InterError:
            continue
        novo = tx.get("status")
        if novo:
            with conn:
                conn.execute("UPDATE pagamentos SET status=?, atualizado_em=datetime('now') "
                             "WHERE id=?", (str(novo).upper(), row["id"]))
        n += 1
        sleep(3.2)  # limite de 20 consultas/min
    return n


# --- folha -----------------------------------------------------------
def criar_folha(conn, competencia: str, data: str,
                itens: list[tuple[int, int]], descricao: str | None = None) -> int:
    """itens: [(beneficiario_id, valor_centavos)]. Tudo ou nada."""
    if not itens:
        raise PagamentoInvalido("folha vazia")
    if len({b for b, _ in itens}) != len(itens):
        raise PagamentoInvalido("beneficiário repetido na folha")
    desc = descricao or f"Folha {competencia}"
    for ben, valor in itens:  # valida tudo antes de gravar qualquer coisa
        _checar_pix_chave(conn, ben, valor, data, desc)
    with conn:  # uma única transação: tudo ou nada
        fid = conn.execute("INSERT INTO folhas (competencia, data, total_centavos) VALUES (?, ?, ?)",
                           (competencia, data, sum(v for _, v in itens))).lastrowid
        for ben, valor in itens:
            _inserir_pix_chave(conn, ben, valor, data, desc, fid)
    return fid


def enviar_folha(conn, client: InterClient, folha_id: int, total_confirmado_centavos: int,
                 sleep=time.sleep) -> dict:
    """Só envia se o total digitado pelo usuário bate com o total da folha."""
    f = conn.execute("SELECT * FROM folhas WHERE id = ?", (folha_id,)).fetchone()
    if f is None:
        raise PagamentoInvalido("folha inexistente")
    if total_confirmado_centavos != f["total_centavos"]:
        raise PagamentoInvalido("total confirmado diferente do total da folha")
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM pagamentos WHERE folha_id = ? AND status IN ('rascunho','erro','enviando') "
        "ORDER BY id", (folha_id,))]
    for i, pid in enumerate(ids):
        if i:
            sleep(INTERVALO_ENVIO_S)
        enviar(conn, client, pid, confirmado=True)
    st = [r["status"] for r in conn.execute("SELECT status FROM pagamentos WHERE folha_id = ?",
                                            (folha_id,))]
    ruins = sum(s in ENVIAVEIS for s in st)
    with conn:
        conn.execute("UPDATE folhas SET status = ? WHERE id = ?",
                     ("parcial" if ruins else "enviada", folha_id))
    return {"enviados": len(ids) - ruins, "pendentes_de_reenvio": ruins}


def descartar_folha(conn, folha_id: int) -> None:
    """Apaga uma folha que ainda não enviou nada."""
    n = conn.execute("SELECT count(*) FROM pagamentos WHERE folha_id = ? AND status != 'rascunho'",
                     (folha_id,)).fetchone()[0]
    if n:
        raise PagamentoInvalido("folha já tem pagamentos enviados; não pode ser descartada")
    with conn:
        conn.execute("DELETE FROM pagamentos WHERE folha_id = ?", (folha_id,))
        conn.execute("DELETE FROM folhas WHERE id = ?", (folha_id,))
