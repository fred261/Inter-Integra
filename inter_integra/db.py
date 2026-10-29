"""SQLite: esquema e conexão. Valores monetários em centavos (inteiros)."""
import sqlite3
from pathlib import Path

from .config import DEFAULT_TETO_CENTAVOS

SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS beneficiarios (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    cpf TEXT,
    chave_pix TEXT NOT NULL,
    tipo TEXT NOT NULL CHECK (tipo IN ('colaborador', 'faxina', 'outro')),
    valor_padrao_centavos INTEGER NOT NULL DEFAULT 0 CHECK (valor_padrao_centavos >= 0),
    ativo INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS recorrencias (
    id INTEGER PRIMARY KEY,
    beneficiario_id INTEGER NOT NULL REFERENCES beneficiarios(id),
    valor_centavos INTEGER NOT NULL CHECK (valor_centavos > 0),
    periodicidade TEXT NOT NULL CHECK (periodicidade IN ('semanal', 'mensal')),
    dia INTEGER NOT NULL,  -- semanal: 0=segunda..6=domingo; mensal: 1..31
    ativa INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS folhas (
    id INTEGER PRIMARY KEY,
    competencia TEXT NOT NULL,  -- YYYY-MM
    data TEXT NOT NULL,         -- YYYY-MM-DD
    total_centavos INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'rascunho'
);

CREATE TABLE IF NOT EXISTS pagamentos (
    id INTEGER PRIMARY KEY,
    beneficiario_id INTEGER REFERENCES beneficiarios(id),
    pix_copia_e_cola TEXT,
    valor_centavos INTEGER NOT NULL CHECK (valor_centavos > 0),
    data_pagamento TEXT,
    descricao TEXT,
    id_idempotente TEXT NOT NULL UNIQUE,
    codigo_solicitacao TEXT,
    tipo_retorno TEXT,
    status TEXT NOT NULL DEFAULT 'rascunho',
    erro TEXT,
    folha_id INTEGER REFERENCES folhas(id),
    criado_em TEXT NOT NULL DEFAULT (datetime('now')),
    atualizado_em TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (beneficiario_id IS NOT NULL OR pix_copia_e_cola IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_pagamentos_status ON pagamentos(status);

-- Só endpoint, status HTTP e resumo. Nunca tokens ou segredos.
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY,
    quando TEXT NOT NULL DEFAULT (datetime('now')),
    endpoint TEXT NOT NULL,
    status_http INTEGER,
    resumo TEXT
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> sqlite3.Connection:
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO config (chave, valor) VALUES ('teto_centavos', ?)",
        (str(DEFAULT_TETO_CENTAVOS),),
    )
    conn.commit()
    db_path.chmod(0o600)
    return conn


def teto_centavos(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT valor FROM config WHERE chave = 'teto_centavos'").fetchone()
    return int(row["valor"])
