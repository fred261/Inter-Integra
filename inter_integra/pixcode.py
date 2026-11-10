"""Leitura do Pix Copia e Cola (BR Code / EMV): valor, recebedor e conferência do CRC.

Só interpreta o texto; não consulta nenhum servidor. Código dinâmico (com URL do PSP) geralmente
não traz o valor: nesse caso `valor_centavos` é None e o valor precisa ser informado.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class PixCodeInvalido(ValueError):
    pass


@dataclass(frozen=True)
class PixCode:
    codigo: str
    valor_centavos: int | None
    recebedor: str | None
    cidade: str | None
    chave: str | None
    url: str | None  # presente em código dinâmico
    dinamico: bool


def crc16(texto: str) -> str:
    crc = 0xFFFF
    for b in texto.encode("ascii"):
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def _tlv(s: str) -> dict[str, str]:
    campos, i = {}, 0
    while i < len(s):
        if i + 4 > len(s) or not s[i:i + 4].isdigit():
            raise PixCodeInvalido("código malformado (campo truncado)")
        tag, n = s[i:i + 2], int(s[i + 2:i + 4])
        if i + 4 + n > len(s):
            raise PixCodeInvalido("código malformado (campo truncado)")
        campos[tag] = s[i + 4:i + 4 + n]
        i += 4 + n
    return campos


def _candidatos(codigo: str) -> list[str]:
    """O nome do recebedor pode ter espaços, então primeiro só tiramos quebras de linha;
    se o CRC não fechar, tentamos sem nenhum espaço (colagem que quebrou linhas no meio)."""
    a = re.sub(r"[\r\n\t]", "", codigo.strip())
    b = "".join(codigo.split())
    return [a] if a == b else [a, b]


def parse(codigo: str) -> PixCode:
    cands = [c for c in _candidatos(codigo) if c.isascii() and c.startswith("0002")]
    if not cands:
        raise PixCodeInvalido("não parece um código Pix Copia e Cola")
    if all(len(c) < 30 for c in cands):
        raise PixCodeInvalido("código Pix incompleto")
    ok = [c for c in cands if c[-8:-4] == "6304" and crc16(c[:-4]) == c[-4:].upper()]
    if not ok:
        raise PixCodeInvalido("código Pix com erro ou cortado (CRC inválido): copie de novo")
    c = ok[0]
    campos = _tlv(c[:-8])
    conta = {}
    for tag in range(26, 52):
        sub = campos.get(f"{tag:02d}")
        if sub and sub.upper().startswith("0014BR.GOV.BCB.PIX"):
            conta = _tlv(sub)
            break
    if not conta:
        raise PixCodeInvalido("código sem dados Pix (br.gov.bcb.pix)")
    valor = None
    if "54" in campos:
        try:
            d = Decimal(campos["54"])
        except InvalidOperation:
            raise PixCodeInvalido("valor do código inválido") from None
        if d <= 0 or d != d.quantize(Decimal("0.01")):
            raise PixCodeInvalido("valor do código inválido")
        valor = int(d * 100)
    return PixCode(codigo=c, valor_centavos=valor, recebedor=campos.get("59"), cidade=campos.get("60"),
                   chave=conta.get("01"), url=conta.get("25"), dinamico="25" in conta)


def monta(chave: str = "", valor: str | None = None, nome: str = "LOJA TESTE", cidade: str = "SAO PAULO",
          url: str | None = None) -> str:
    """Gera um código válido (usado nos testes e para demonstração)."""
    def f(tag, v):
        return f"{tag}{len(v):02d}{v}"
    conta = f("00", "br.gov.bcb.pix") + (f("25", url) if url else f("01", chave))
    corpo = f("00", "01") + f("01", "12" if url else "11") + f("26", conta) + f("52", "0000") + f("53", "986")
    if valor:
        corpo += f("54", valor)
    corpo += f("58", "BR") + f("59", nome) + f("60", cidade) + f("62", f("05", "***")) + "6304"
    return corpo + crc16(corpo)
