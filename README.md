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

## Etapa 3 — Pix por chave, Copia e Cola e folha

```bash
python -m inter_integra beneficiario-add "Val" chave@pix.com faxina
python -m inter_integra pix-chave 1 "150,00" 2026-11-06 "Faxina semanal"
python -m inter_integra pix-caju "2.000,00"      # pede o código Copia e Cola
python -m inter_integra atualizar                # consulta o status dos pendentes
```

Proteções (no código, testadas): confirmação digitada (`ENVIAR`) antes de cada envio; teto por pagamento; `x-id-idempotente` gravado antes da chamada (reenvio após erro ou timeout usa o mesmo id, sem duplicar); folha só é enviada se o total confirmado bater, com 1 Pix por segundo e tudo-ou-nada na criação.

Requer o escopo `pagamento-pix.write` (envio) e `pagamento-pix.read` (consulta) na integração do Inter. A folha (`criar_folha`/`enviar_folha`) ganha tela na etapa 4.

Ainda não validado contra a API real: nomes dos status finais do Pix (lista em `pagamentos.STATUS_FINAIS`) e campos de resposta.
