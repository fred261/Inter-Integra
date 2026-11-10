import pytest

from inter_integra import db, pixcode as pc


def test_crc_de_referencia():
    assert pc.crc16("123456789") == "29B1"  # CRC-16/CCITT-FALSE, valor de referência do padrão


def test_le_valor_recebedor_e_chave():
    p = pc.parse(pc.monta("a@x.com", "150.50", nome="JOSE DA SILVA", cidade="RIO DE JANEIRO"))
    assert (p.valor_centavos, p.recebedor, p.cidade, p.chave, p.dinamico) == (
        15050, "JOSE DA SILVA", "RIO DE JANEIRO", "a@x.com", False)


def test_sem_valor_e_dinamico():
    assert pc.parse(pc.monta("a@x.com")).valor_centavos is None
    d = pc.parse(pc.monta(url="pix.exemplo.com/qr/v2/x"))
    assert d.dinamico and d.url == "pix.exemplo.com/qr/v2/x" and d.valor_centavos is None


def test_quebra_de_linha_na_colagem_e_espacos_removidos():
    c = pc.monta("a@x.com", "10.00", nome="ANA PAULA")
    assert pc.parse(c[:30] + "\n" + c[30:]).codigo == c
    assert pc.parse("  " + c + "\n").codigo == c
    with pytest.raises(pc.PixCodeInvalido, match="CRC"):
        pc.parse(c.replace("ANA PAULA", "ANAPAULA"))  # espaço perdido altera o código: recusa, não "conserta"


@pytest.mark.parametrize("ruim", ["", "abc", "00020126", "0002" + "9" * 40])
def test_recusas(ruim):
    with pytest.raises(pc.PixCodeInvalido):
        pc.parse(ruim)


def test_valor_malformado_recusado():
    base = pc.monta("a@x.com", "10.001")
    with pytest.raises(pc.PixCodeInvalido, match="valor"):
        pc.parse(base)


def test_migracao_de_banco_antigo(tmp_path):
    import sqlite3
    p = tmp_path / "velho.db"
    antigo = "\n".join(l for l in db.SCHEMA.splitlines() if "recebedor_texto" not in l)
    c = sqlite3.connect(p)
    c.executescript(antigo)
    c.commit()
    c.close()
    conn = db.init_db(p)  # banco criado antes da coluna: não pode quebrar e deve adicioná-la
    assert "recebedor_texto" in {r["name"] for r in conn.execute("PRAGMA table_info(pagamentos)")}
    db.init_db(p).close()  # idempotente
