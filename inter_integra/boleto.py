"""Leitura e validação de boletos (47/48 dígitos) a partir de texto ou PDF.

Tudo local: extrai a linha digitável do texto do PDF, valida os dígitos verificadores e
calcula valor e vencimento a partir do próprio código. O conteúdo do PDF é dado não confiável:
só se extrai texto e se confere matematicamente; nada é executado.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, timedelta

MAX_PDF_BYTES = 5 * 1024 * 1024
_BASE_FATOR = date(1997, 10, 7)
_CICLO_FATOR = 9000  # o fator volta a 1000 depois de 9999 (a partir de 22/02/2025)


class BoletoInvalido(ValueError):
    pass


@dataclass(frozen=True)
class Boleto:
    tipo: str  # "bancario" (47 dígitos) ou "convenio" (48 dígitos)
    linha_digitavel: str  # só dígitos
    codigo_barras: str  # 44 dígitos
    valor_centavos: int | None  # None = valor livre (sem valor no código)
    vencimento: date | None
    cnpj_beneficiario: str | None = None
    beneficiario_texto: str | None = None


# --- dígitos verificadores ---------------------------------------------
def _mod10(s: str) -> int:
    soma, peso = 0, 2
    for ch in reversed(s):
        p = int(ch) * peso
        soma += p // 10 + p % 10
        peso = 1 if peso == 2 else 2
    return (10 - soma % 10) % 10


def _mod11_bancario(s: str) -> int:
    soma, peso = 0, 2
    for ch in reversed(s):
        soma += int(ch) * peso
        peso = 2 if peso == 9 else peso + 1
    dv = 11 - soma % 11
    return 1 if dv in (0, 10, 11) else dv


def _mod11_convenio(s: str) -> int:
    soma, peso = 0, 2
    for ch in reversed(s):
        soma += int(ch) * peso
        peso = 2 if peso == 9 else peso + 1
    r = soma % 11
    if r in (0, 1):
        return 0
    return 1 if r == 10 else 11 - r


def _data_do_fator(fator: int, hoje: date) -> date:
    d = _BASE_FATOR + timedelta(days=fator)
    # escolhe o ciclo mais próximo de hoje
    while d < hoje - timedelta(days=_CICLO_FATOR // 2):
        d += timedelta(days=_CICLO_FATOR)
    while d > hoje + timedelta(days=_CICLO_FATOR // 2):
        d -= timedelta(days=_CICLO_FATOR)
    return d


# --- interpretação ------------------------------------------------------
def _bancario_47(linha: str, hoje: date) -> Boleto:
    campos = [linha[0:10], linha[10:21], linha[21:32]]
    for c in campos:
        if _mod10(c[:-1]) != int(c[-1]):
            raise BoletoInvalido("dígito verificador de campo inválido (linha digitável com erro)")
    barras = linha[0:4] + linha[32] + linha[33:47] + linha[4:9] + linha[10:20] + linha[21:31]
    return _bancario_44(barras, hoje, linha)


def _bancario_44(barras: str, hoje: date, linha: str | None = None) -> Boleto:
    dv = int(barras[4])
    if _mod11_bancario(barras[:4] + barras[5:]) != dv:
        raise BoletoInvalido("dígito verificador geral inválido")
    if barras[3] != "9":
        raise BoletoInvalido("moeda diferente de real")
    fator = int(barras[5:9])
    valor = int(barras[9:19])
    return Boleto(
        tipo="bancario", linha_digitavel=linha or "", codigo_barras=barras,
        valor_centavos=valor or None,
        vencimento=_data_do_fator(fator, hoje) if fator else None)


def _convenio(digitos: str) -> Boleto:
    """48 dígitos (4 blocos de 11 + DV) ou 44 (só o código de barras)."""
    if len(digitos) == 48:
        blocos = [digitos[i:i + 12] for i in range(0, 48, 12)]
        ident = int(digitos[2])
        calc = _mod10 if ident in (6, 7) else _mod11_convenio
        for b in blocos:
            if calc(b[:11]) != int(b[11]):
                raise BoletoInvalido("dígito verificador de bloco inválido (linha digitável com erro)")
        barras, linha = "".join(b[:11] for b in blocos), digitos
    else:
        barras, linha = digitos, ""
    ident = int(barras[2])
    if ident not in (6, 7, 8, 9):
        raise BoletoInvalido("identificador de valor desconhecido")
    calc = _mod10 if ident in (6, 7) else _mod11_convenio
    if calc(barras[:3] + barras[4:]) != int(barras[3]):
        raise BoletoInvalido("dígito verificador geral inválido")
    valor = int(barras[4:15])
    return Boleto(tipo="convenio", linha_digitavel=linha, codigo_barras=barras,
                  valor_centavos=valor or None, vencimento=None)


def parse_codigo(codigo: str, hoje: date | None = None) -> Boleto:
    """Aceita linha digitável (47/48 dígitos, com ou sem pontuação) ou código de barras (44)."""
    hoje = hoje or date.today()
    d = re.sub(r"\D", "", codigo)
    if len(d) == 47:
        return _bancario_47(d, hoje)
    if len(d) == 44 and d[0] != "8":
        return _bancario_44(d, hoje)
    if len(d) in (44, 48) and d[0] == "8":
        return _convenio(d)
    raise BoletoInvalido(f"código com {len(d)} dígitos; esperado 44, 47 ou 48")


# --- texto / PDF --------------------------------------------------------
_RE_47 = re.compile(r"\b\d{5}[.\s]?\d{5}\s+\d{5}[.\s]?\d{6}\s+\d{5}[.\s]?\d{6}\s+\d\s+\d{14}\b")
_RE_48 = re.compile(r"\b(?:\d{11}[-\s]?\d\s+){3}\d{11}[-\s]?\d\b")
_RE_44 = re.compile(r"\b\d{44}\b")
_RE_CNPJ = re.compile(r"\b(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})\b")
_RE_CPF = re.compile(r"\b(\d{3}\.?\d{3}\.?\d{3}-?\d{2})\b")
_RE_DATA = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")


def _benef(texto: str) -> tuple[str | None, str | None]:
    m = re.search(r"(?:benefici[aá]rio|cedente)\b(.{0,160})", texto, re.I | re.S)
    if not m:
        return None, None
    trecho = " ".join(m.group(1).split())
    doc = _RE_CNPJ.search(trecho) or _RE_CPF.search(trecho)
    nome = re.split(r"\s*(?:CNPJ|CPF|\d{2}\.\d{3}|ag[eê]ncia|\d{3}\.\d{3})", trecho, flags=re.I)[0]
    nome = nome.strip(" :-/")[:80] or None
    return (re.sub(r"\D", "", doc.group(1)) if doc else None), nome


def _vencimento_texto(texto: str) -> date | None:
    m = re.search(r"vencimento\D{0,40}?" + _RE_DATA.pattern, texto, re.I | re.S)
    if not m:
        return None
    d, mth, a = (int(x) for x in m.groups())
    try:
        return date(a, mth, d)
    except ValueError:
        return None


def parse_texto(texto: str, hoje: date | None = None) -> Boleto:
    """Procura o código no texto de um boleto. Se houver mais de um código distinto, recusa."""
    hoje = hoje or date.today()
    achados: dict[str, Boleto] = {}
    erros: list[str] = []
    for rx in (_RE_47, _RE_48, _RE_44):
        for m in rx.finditer(texto):
            try:
                b = parse_codigo(m.group(0), hoje)
            except BoletoInvalido as e:
                erros.append(str(e))
                continue
            achados.setdefault(b.codigo_barras, b)
    if not achados:
        raise BoletoInvalido("nenhum código de boleto válido encontrado"
                             + (f" ({erros[0]})" if erros else ""))
    if len(achados) > 1:
        raise BoletoInvalido("mais de um código de boleto no documento; envie um por arquivo")
    b = next(iter(achados.values()))
    cnpj, nome = _benef(texto)
    venc = b.vencimento or _vencimento_texto(texto)
    return Boleto(b.tipo, b.linha_digitavel, b.codigo_barras, b.valor_centavos, venc, cnpj, nome)


def texto_do_pdf(conteudo: bytes) -> str:
    if len(conteudo) > MAX_PDF_BYTES:
        raise BoletoInvalido("arquivo maior que 5 MB")
    if not conteudo.startswith(b"%PDF"):
        raise BoletoInvalido("o arquivo não é um PDF")
    from pypdf import PdfReader
    try:
        leitor = PdfReader(io.BytesIO(conteudo))
        if leitor.is_encrypted:
            raise BoletoInvalido("PDF protegido por senha")
        return "\n".join((p.extract_text() or "") for p in leitor.pages[:5])
    except BoletoInvalido:
        raise
    except Exception as e:  # pypdf lança vários tipos em PDFs malformados
        raise BoletoInvalido(f"não consegui ler o PDF ({e.__class__.__name__})") from None


def parse_pdf(conteudo: bytes, hoje: date | None = None) -> Boleto:
    texto = texto_do_pdf(conteudo)
    if not texto.strip():
        raise BoletoInvalido("PDF sem texto (provavelmente imagem/foto); use o código digitado")
    return parse_texto(texto, hoje)
