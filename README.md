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
