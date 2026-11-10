import io
import json
from datetime import date, timedelta

import httpx
import pytest

from inter_integra import boleto as bl, boletos as bs, db
from inter_integra.client import InterClient
from inter_integra.config import Settings, ensure_home

HOJE = date(2026, 11, 5)  # quinta-feira
SANDBOX_BANCARIO = "03395988500000666539201493990000372830030102"
SANDBOX_CONVENIO = "82670000000653301602023123106000000002830894"


def make_barras(fator: int, valor_centavos: int, banco="341", livre="1234567890123456789012345") -> str:
    base = banco + "9" + f"{fator:04d}" + f"{valor_centavos:010d}" + livre
    return base[:4] + str(bl._mod11_bancario(base)) + base[4:]


def linha_de(barras: str) -> str:
    c = [barras[0:4] + barras[19:24], barras[24:34], barras[34:44]]
    campos = [x + str(bl._mod10(x)) for x in c]
    return campos[0] + campos[1] + campos[2] + barras[4] + barras[5:19]


def fator_de(d: date) -> int:
    return (d - date(2025, 2, 22)).days + 1000  # ciclo atual


def formata(linha: str) -> str:
    return f"{linha[:5]}.{linha[5:10]} {linha[10:15]}.{linha[15:21]} {linha[21:26]}.{linha[26:32]} {linha[32]} {linha[33:]}"


def test_codigos_de_exemplo_do_inter_sao_validos():
    b = bl.parse_codigo(SANDBOX_BANCARIO, HOJE)
    assert (b.tipo, b.valor_centavos) == ("bancario", 66653)
    c = bl.parse_codigo(SANDBOX_CONVENIO, HOJE)
    assert (c.tipo, c.valor_centavos, c.vencimento) == ("convenio", 6533, None)


def test_linha_digitavel_valor_e_vencimento_no_ciclo_novo():
    venc = date(2026, 12, 10)
    barras = make_barras(fator_de(venc), 123456)
    b = bl.parse_codigo(formata(linha_de(barras)), HOJE)
    assert (b.valor_centavos, b.vencimento, b.codigo_barras) == (123456, venc, barras)
    assert bl.parse_codigo(barras, HOJE).linha_digitavel == ""


def test_digito_errado_e_recusado():
    linha = linha_de(make_barras(fator_de(date(2026, 12, 10)), 5000))
    errada = linha[:12] + str((int(linha[12]) + 1) % 10) + linha[13:]
    with pytest.raises(bl.BoletoInvalido):
        bl.parse_codigo(errada, HOJE)
    barras = make_barras(fator_de(date(2026, 12, 10)), 5000)
    ruim = barras[:20] + str((int(barras[20]) + 1) % 10) + barras[21:]
    with pytest.raises(bl.BoletoInvalido):
        bl.parse_codigo(ruim, HOJE)
    with pytest.raises(bl.BoletoInvalido, match="dígitos"):
        bl.parse_codigo("123", HOJE)


def test_parse_texto_extrai_codigo_e_beneficiario():
    linha = formata(linha_de(make_barras(fator_de(date(2026, 12, 10)), 250000)))
    texto = f"Beneficiário: ACME COMERCIO LTDA CNPJ 12.345.678/0001-95\nVencimento 10/12/2026\n{linha}\n"
    b = bl.parse_texto(texto, HOJE)
    assert b.valor_centavos == 250000 and b.cnpj_beneficiario == "12345678000195"
    assert b.beneficiario_texto.startswith("ACME COMERCIO LTDA")


def test_texto_sem_codigo_ou_com_dois_codigos():
    with pytest.raises(bl.BoletoInvalido, match="nenhum"):
        bl.parse_texto("só texto", HOJE)
    a = formata(linha_de(make_barras(fator_de(date(2026, 12, 10)), 100)))
    b = formata(linha_de(make_barras(fator_de(date(2026, 12, 11)), 200)))
    with pytest.raises(bl.BoletoInvalido, match="mais de um"):
        bl.parse_texto(f"{a}\n{b}", HOJE)


def pdf_com(texto: str) -> bytes:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for linha in texto.splitlines():
        c.drawString(40, y, linha)
        y -= 18
    c.save()
    return buf.getvalue()


def test_pdf_ponta_a_ponta_e_arquivos_invalidos():
    linha = formata(linha_de(make_barras(fator_de(date(2026, 12, 10)), 7777)))
    b = bl.parse_pdf(pdf_com(f"Beneficiario: LOJA X CNPJ 12.345.678/0001-95\n{linha}"), HOJE)
    assert b.valor_centavos == 7777 and b.vencimento == date(2026, 12, 10)
    with pytest.raises(bl.BoletoInvalido, match="não é um PDF"):
        bl.parse_pdf(b"MZ" + b"x" * 100, HOJE)
    with pytest.raises(bl.BoletoInvalido, match="5 MB"):
        bl.parse_pdf(b"%PDF" + b"x" * (bl.MAX_PDF_BYTES + 1), HOJE)
    with pytest.raises(bl.BoletoInvalido):
        bl.parse_pdf(b"%PDF-1.4 lixo", HOJE)


# --- regras de negócio ----------------------------------------------------
@pytest.fixture
def env(tmp_path):
    s = Settings(home=tmp_path / "p")
    ensure_home(s)
    return s, db.init_db(s.db_path)


def boleto_em(venc: date, valor=10000, livre="1234567890123456789012345"):
    return bl.parse_codigo(make_barras(fator_de(venc), valor, livre=livre), HOJE)


def test_data_de_pagamento_fim_de_semana():
    assert bs.data_de_pagamento(date(2026, 11, 14), HOJE) == date(2026, 11, 13)  # sábado -> sexta
    assert bs.data_de_pagamento(date(2026, 11, 15), HOJE) == date(2026, 11, 13)  # domingo -> sexta
    assert bs.data_de_pagamento(date(2026, 11, 5), HOJE) == HOJE
    assert bs.data_de_pagamento(date(2026, 11, 7), HOJE) == date(2026, 11, 6)  # sábado -> sexta (amanhã)
    assert bs.data_de_pagamento(date(2026, 11, 7), date(2026, 11, 7)) == date(2026, 11, 7)  # sexta já passou -> hoje


def test_criar_recusas(env):
    s, conn = env
    with pytest.raises(bs.BoletoRecusado, match="vencido"):
        bs.criar(conn, boleto_em(date(2026, 11, 1)), hoje=HOJE)
    with pytest.raises(bs.BoletoRecusado, match="teto"):
        bs.criar(conn, boleto_em(date(2026, 12, 1), valor=600000), hoje=HOJE)
    livre = bl.parse_codigo(make_barras(fator_de(date(2026, 12, 1)), 0), HOJE)
    with pytest.raises(bs.BoletoRecusado, match="valor livre"):
        bs.criar(conn, livre, hoje=HOJE)
    b = boleto_em(date(2026, 12, 1))
    bs.criar(conn, b, hoje=HOJE)
    with pytest.raises(bs.BoletoRecusado, match="já foi cadastrado"):
        bs.criar(conn, b, hoje=HOJE)


def test_convenio_exige_vencimento_manual(env):
    s, conn = env
    c = bl.parse_codigo(SANDBOX_CONVENIO, HOJE)
    with pytest.raises(bs.BoletoRecusado, match="vencimento"):
        bs.criar(conn, c, hoje=HOJE)
    assert bs.criar(conn, c, vencimento_manual=date(2026, 12, 31), hoje=HOJE) == 1


class Inter:
    def __init__(self, s, post=None, busca=None):
        self.posts, self.buscas, self.dels = [], [], []

        def h(req: httpx.Request):
            if req.url.path == "/oauth/v2/token":
                return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
            if req.method == "POST":
                self.posts.append(json.loads(req.content))
                if post:
                    return post(len(self.posts))
                return httpx.Response(200, json={"quantidadeAprovadores": 1, "statusPagamento": "EMPROCESSAMENTO",
                                                 "codigoTransacao": f"tx{len(self.posts)}"})
            if req.method == "DELETE":
                self.dels.append(req.url.path)
                return httpx.Response(204)
            self.buscas.append(dict(req.url.params))
            return busca(req) if busca else httpx.Response(200, json=[])

        self.client = InterClient(s, "i", "s", http=httpx.Client(
            base_url=s.base_url, transport=httpx.MockTransport(h)))


def novo(conn, n=0, cnpj=None):
    """Boleto com código válido de hoje, mas vencimento no futuro distante (a data de pagamento
    é validada contra a data real do relógio em enviar)."""
    import dataclasses
    b = boleto_em(date(2026, 12, 1), livre=f"{n:025d}")
    b = dataclasses.replace(b, vencimento=date(2099, 12, 1) + timedelta(days=n), cnpj_beneficiario=cnpj)
    return bs.criar(conn, b, "conta.pdf", hoje=HOJE)


def test_envio_payload_confirmacao_e_sem_reenvio(env):
    s, conn = env
    bid = novo(conn, cnpj="12345678000195")
    inter = Inter(s)
    with pytest.raises(bs.BoletoRecusado, match="confirmação"):
        bs.enviar(conn, inter.client, bid)
    assert inter.posts == []
    r = bs.enviar(conn, inter.client, bid, confirmado=True)
    p = inter.posts[0]
    assert p["valorPagar"] == "100.00" and p["cpfCnpjBeneficiario"] == "12345678000195"
    assert p["dataVencimento"] == "2099-12-01" and p["codBarraLinhaDigitavel"].isdigit()
    assert (r["status"], r["codigo_transacao"], r["aprovadores"]) == ("EMPROCESSAMENTO", "tx1", 1)
    with pytest.raises(bs.BoletoRecusado, match="já enviado"):
        bs.enviar(conn, inter.client, bid, confirmado=True)
    assert len(inter.posts) == 1


def test_erro_http_permite_reenvio(env):
    s, conn = env
    bid = novo(conn)
    inter = Inter(s, post=lambda n: httpx.Response(422, json={"title": "beneficiário divergente"}) if n == 1
                  else httpx.Response(200, json={"statusPagamento": "EMPROCESSAMENTO", "codigoTransacao": "tx"}))
    assert bs.enviar(conn, inter.client, bid, confirmado=True)["status"] == "erro"
    assert bs.enviar(conn, inter.client, bid, confirmado=True)["status"] == "EMPROCESSAMENTO"


def test_timeout_nao_duplica_pagamento(env):
    """Resposta perdida: o Inter pode ter registrado. Reenvio consulta antes e adota o existente."""
    s, conn = env
    bid = novo(conn)

    def post(n):
        raise httpx.ReadTimeout("t")
    inter = Inter(s, post=post)
    with pytest.raises(httpx.ReadTimeout):
        bs.enviar(conn, inter.client, bid, confirmado=True)
    assert conn.execute("SELECT status FROM boletos").fetchone()[0] == "enviando"

    existente = Inter(s, busca=lambda req: httpx.Response(200, json=[
        {"codigoTransacao": "tx-no-inter", "statusPagamento": "EMPROCESSAMENTO", "aprovacoesNecessarias": 1}]))
    r = bs.enviar(conn, existente.client, bid, confirmado=True)
    assert existente.posts == [] and r["codigo_transacao"] == "tx-no-inter"

    # se o Inter NÃO tem o pagamento, aí sim reenvia
    bid2 = novo(conn, n=1)
    with conn:
        conn.execute("UPDATE boletos SET status='enviando' WHERE id=?", (bid2,))
    vazio = Inter(s)
    bs.enviar(conn, vazio.client, bid2, confirmado=True)
    assert len(vazio.posts) == 1


def test_status_e_cancelamento(env):
    s, conn = env
    bid = novo(conn)
    inter = Inter(s, busca=lambda req: httpx.Response(200, json=[{"statusPagamento": "pago"}]))
    bs.enviar(conn, inter.client, bid, confirmado=True)
    assert bs.atualizar_status(conn, inter.client) == 1
    assert conn.execute("SELECT status FROM boletos").fetchone()[0] == "PAGO"
    assert bs.atualizar_status(conn, inter.client) == 0  # final: não reconsulta
    bid2 = novo(conn, n=1)
    bs.enviar(conn, inter.client, bid2, confirmado=True)
    bs.cancelar(conn, inter.client, bid2)
    assert inter.dels == ["/banking/v2/pagamento/tx2"]
    assert conn.execute("SELECT status FROM boletos WHERE id=?", (bid2,)).fetchone()[0] == "CANCELADO"


def test_descartar(env):
    s, conn = env
    bid = novo(conn)
    bs.descartar(conn, bid)
    assert conn.execute("SELECT count(*) FROM boletos").fetchone()[0] == 0


# --- formatos reais variados de PDF ---------------------------------------
def _linha(dias=20, valor=34567):
    return linha_de(make_barras(fator_de(date.today() + timedelta(days=dias)), valor))


@pytest.mark.parametrize("fmt", [
    lambda l: f"{l[:5]}.{l[5:10]} {l[10:15]}.{l[15:21]} {l[21:26]}.{l[26:32]} {l[32]} {l[33:]}",   # padrão
    lambda l: f"{l[:5]}.{l[5:10]}  {l[10:15]}.{l[15:21]}  {l[21:26]}.{l[26:32]}  {l[32]}  {l[33:]}",  # espaço duplo
    lambda l: f"{l[:5]}.{l[5:10]}\n{l[10:15]}.{l[15:21]}\n{l[21:26]}.{l[26:32]}\n{l[32]}\n{l[33:]}",  # um campo por linha
    lambda l: f"{l[:10]} {l[10:21]} {l[21:32]} {l[32]} {l[33:]}",                                   # sem pontos
    lambda l: f"{l[:5]}-{l[5:10]} {l[10:15]}-{l[15:21]} {l[21:26]}-{l[26:32]} {l[32]} {l[33:]}",      # traços
    lambda l: l,                                                                                     # tudo colado
    lambda l: f"{l[:5]} {l[5:10]} {l[10:15]} {l[15:21]} {l[21:26]} {l[26:32]} {l[32]} {l[33:]}",      # grupos soltos
])
def test_formatos_de_linha_digitavel(fmt):
    linha = _linha()
    texto = f"Pagador: JOAO\nNosso numero 12345678-9  Agencia 1234/56789-0\nVencimento 10/12/2026\n{fmt(linha)}\nAutenticacao mecanica"
    b = bl.parse_texto(texto, date.today())
    assert b.valor_centavos == 34567


def test_codigo_de_barras_44_e_linha_do_mesmo_boleto_nao_contam_como_dois():
    barras = make_barras(fator_de(date.today() + timedelta(days=20)), 9900)
    texto = f"{formata(linha_de(barras))}\nCodigo de barras: {barras}"
    assert bl.parse_texto(texto, date.today()).valor_centavos == 9900


def test_numeros_vizinhos_nao_atrapalham():
    linha = formata(linha_de(make_barras(fator_de(date.today() + timedelta(days=20)), 1500)))
    texto = f"CNPJ 12.345.678/0001-95 Valor 15,00 Doc 000123 456789 {linha} 99999 11111"
    assert bl.parse_texto(texto, date.today()).valor_centavos == 1500


def test_convenio_48_com_tracos_e_espacos():
    l = "".join(SANDBOX_CONVENIO)  # 44 dígitos (código de barras): monta a linha de 48 com DV mod 11
    blocos = [l[i:i + 11] for i in range(0, 44, 11)]
    calc = bl._mod10 if int(l[2]) in (6, 7) else bl._mod11_convenio
    linha = [b + str(calc(b)) for b in blocos]
    texto = "Pague ate o vencimento 31/12/2026\n" + " ".join(f"{x[:11]}-{x[11]}" for x in linha)
    b = bl.parse_texto(texto, HOJE)
    assert b.tipo == "convenio" and b.valor_centavos == 6533 and b.vencimento == date(2026, 12, 31)


def test_diagnostico_nao_vaza_digitos_nem_nomes():
    linha = formata(_linha())
    texto = f"Beneficiario: EMPRESA SECRETA LTDA CNPJ 12.345.678/0001-95\n{linha}"
    rel = bl.diagnostico(texto, date.today())
    assert "códigos válidos: 1" in rel
    assert not any(c.isdigit() for c in rel.split("estrutura")[1])  # nenhum dígito na estrutura
    assert "SECRETA" not in rel and "12.345" not in rel


def test_erro_informa_motivo_do_candidato_recusado():
    ruim = formata(_linha())
    ruim = ruim[:20] + ("1" if ruim[20] != "1" else "2") + ruim[21:]
    with pytest.raises(bl.BoletoInvalido, match="recusados"):
        bl.parse_texto(f"linha {ruim}", date.today())
    with pytest.raises(bl.BoletoInvalido, match="nenhuma sequência"):
        bl.parse_texto("Vencimento 10/12/2026 valor 15,00", date.today())


def test_pdf_com_linha_quebrada_em_varias_linhas():
    linha = _linha()
    pdf = pdf_com("Beneficiario: LOJA CNPJ 12.345.678/0001-95\n" + f"{linha[:5]}.{linha[5:10]}\n{linha[10:15]}.{linha[15:21]}\n"
                  f"{linha[21:26]}.{linha[26:32]}\n{linha[32]}\n{linha[33:]}")
    assert bl.parse_pdf(pdf, date.today()).valor_centavos == 34567


def test_diagnostico_resume_leitura_sem_valores():
    linha = formata(_linha(valor=34567))
    rel = bl.diagnostico(f"Beneficiario: LOJA CNPJ 12.345.678/0001-95\n{linha}", date.today())
    assert "valor: lido" in rel and "vencimento: lido" in rel and "CNPJ/CPF do beneficiário: lido" in rel
    assert "345,67" not in rel and "34567" not in rel


def test_numero_de_48_digitos_que_nao_e_convenio_tem_mensagem_clara():
    with pytest.raises(bl.BoletoInvalido, match="não começam com 8"):
        bl.parse_codigo("1" * 48)


def test_leitura_de_pdf_nao_imprime_avisos_de_fonte(capfd):
    import logging
    bl.parse_pdf(pdf_com(f"{formata(_linha())}"), date.today())
    assert logging.getLogger("pypdf").level == logging.ERROR
