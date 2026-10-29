# Inter-Integra

Integração local (macOS) com a API Banking do Inter Empresas: folha por Pix, recarga Caju (Pix Copia e Cola) e pagamento semanal da faxina. Todo envio exige aprovação manual no app do Inter.

## Etapa 1 — estrutura, banco e Keychain

```bash
pip install -r requirements.txt
python -m inter_integra init          # cria ~/PagamentosInter/ (700) e o banco SQLite (600)
python -m inter_integra credenciais   # grava Client ID/Secret no Keychain
python -m inter_integra status        # confere banco, certificado (chmod 600) e credenciais
pytest
```

- `INTER_AMBIENTE=sandbox` (padrão) ou `producao`; credenciais ficam em entradas separadas do Keychain.
- `INTER_HOME` muda a pasta (padrão `~/PagamentosInter`).
- Coloque `inter.crt` e `inter.key` na pasta, com `chmod 600`.
- Valores monetários são guardados em centavos; teto por pagamento em `config.teto_centavos` (padrão R$ 5.000,00).

## Etapa 2 — cliente HTTP (mTLS + token) e consultas

```bash
python -m inter_integra saldo                       # ou: saldo 2026-10-01
python -m inter_integra extrato 2026-10-01 2026-10-28   # máx. 90 dias
```

- Todas as chamadas usam `inter.crt`/`inter.key` (mTLS). O token (60 min) é reutilizado e guardado em `token_cache.json` (600) para não estourar o limite de 5/min do endpoint de token.
- Escopo padrão: `extrato.read`. Se a integração tiver mais de uma conta, defina `INTER_CONTA_CORRENTE`.
- Cada chamada vai para a tabela `log` (endpoint, status, resumo), sem token nem segredo.
