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

## Etapa 4 — interface web local

```bash
python -m inter_integra web     # abre em http://localhost:8000 (somente neste computador)
```

Telas: **Painel** (saldo, pendentes, últimos), **Beneficiários** (cadastro, edição, Pix avulso), **Folha** (seleção, valores, conferência do total e envio), **Caju** (colar Copia e Cola, pré-visualização e envio) e **Histórico** (filtro por período e CSV).

Proteções: escuta só em 127.0.0.1; rejeita `Host` que não seja localhost (contra DNS rebinding); token CSRF e checagem de `Origin` em todo POST; cabeçalhos CSP/`X-Frame-Options`; CSV protegido contra injeção de fórmula; saldo em cache por 60 s (limite da API). Envio só com confirmação digitada: `ENVIAR` em pagamentos avulsos e Caju, e o total da folha na folha.

## Plano completo

1. Estrutura, banco e Keychain ✅
2. Cliente HTTP (mTLS, token), saldo e extrato ✅ (aguarda teste real)
3. Pix por chave, Copia e Cola e folha ✅ (aguarda teste real)
4. Interface web local ✅
5. LaunchAgents: Pix semanal da Val (segunda, para a sexta), atualização horária de status, extrato diário
6. Migração para produção (validar campos/status reais da API)
7. Boletos ✅ (código pronto e testado com respostas simuladas; aguarda teste real com o Inter)

## Etapa 7 — boletos

```bash
python -m inter_integra boleto ~/Downloads/conta.pdf        # lê, mostra a prévia, pede ENVIAR
python -m inter_integra boleto-codigo                       # para foto/PDF sem texto: cole a linha digitável
python -m inter_integra boletos-inter                       # lista o que o Inter tem (últimos 30 dias)
```
Ou na interface web, tela **Boletos**: envie o PDF, confira e digite `ENVIAR`.

Como funciona (conforme a documentação da API Banking do Inter): `POST /banking/v2/pagamento` com escopo `pagamento-boleto.write`, agendado para o vencimento; aprovação no app do Inter; consulta por `GET /banking/v2/pagamento`; cancelamento do agendamento por `DELETE /banking/v2/pagamento/{codigoTransacao}`.

- **Leitura local do PDF:** extrai a linha digitável, valida os dígitos verificadores (linha digitável com erro é recusada) e calcula valor e vencimento do próprio código. O PDF é tratado como dado não confiável: só se extrai texto.
- **Beneficiário:** o Inter não consulta o boleto antes de pagar. O CNPJ/CPF do beneficiário lido do PDF vai em `cpfCnpjBeneficiario`, e o Inter recusa se não bater com o real. Sem CNPJ no PDF, a tela avisa e a conferência fica por sua conta no app.
- **Sem duplicidade:** a API de boleto **não tem idempotência**. Por isso: código de barras único no banco; estado `enviando` gravado antes da chamada; se a resposta se perder, o reenvio consulta o Inter antes de mandar de novo.
- **Recusas:** boleto vencido (juros/multa), valor livre, valor acima do teto, PDF sem texto, mais de um código no mesmo PDF, PDF maior que 5 MB.
- **Data de pagamento:** o vencimento; se cair no fim de semana, a sexta anterior. Feriados não são tratados.
- **Contas de consumo/convênio (48 dígitos):** o valor vem do código, mas o vencimento é lido do texto do PDF ou informado por você.

Ainda não validado contra a API real: lista de status do boleto (`statusPagamento`; só `EMPROCESSAMENTO` aparece na documentação), comportamento do Inter com pagamento agendado e aprovação, e se a sua integração tem o escopo `pagamento-boleto`.

## Entrada por pasta e atalho

**Pasta de entrada de boletos.** PDFs soltos em `~/PagamentosInter/boletos/entrada` viram rascunhos para conferir (nada é enviado ao Inter sem a sua confirmação). Quem foi lido vai para `processados/`; quem teve problema vai para `erros/` com um `.erro.txt` explicando o motivo.
```bash
python -m inter_integra entrada            # processa a pasta uma vez
python -m inter_integra agente-entrada     # gera o LaunchAgent e mostra os comandos para ligar/desligar
```
O agente roda `entrada` quando a pasta muda (e a cada 2 minutos) e mostra uma notificação do macOS. Ele não usa credenciais nem rede.

**Atalho na Área de Trabalho.**
```bash
export INTER_AMBIENTE=producao
python -m inter_integra atalho             # cria ~/Desktop/"Inter Integra.command"
```
Duplo clique: inicia a interface no ambiente gravado no atalho e abre o navegador (se já estiver rodando, só abre a página).

A tela **Copia e Cola** (antes "Caju") serve para qualquer código Pix Copia e Cola, com descrição editável. O sistema **lê o valor e o recebedor do próprio código** (padrão EMV/BR Code), confere o CRC para pegar colagens cortadas e recusa se o valor digitado divergir do código. Pix dinâmico (com URL) normalmente não traz o valor: nesse caso ele precisa ser informado. Nome e cidade do recebedor mostrados vêm do código e não são verificados; confirme o destinatário real no app do Inter.

## Diagnóstico de boleto e pasta de entrada (atualização)

- A leitura do PDF agora procura **qualquer sequência de 44/47/48 dígitos** com separadores variados (espaço, quebra de linha, ponto, traço) e valida pelos dígitos verificadores; tenta também a extração em modo *layout*. A mensagem de erro informa o motivo do candidato recusado.
- `python -m inter_integra boleto-diagnostico ARQUIVO.pdf` mostra por que um PDF não foi lido **sem expor dados** (dígitos viram `#`, letras viram `x`): dá para colar a saída num chamado/conversa com segurança.
- Ao abrir a interface (`web`, `abrir` ou o atalho), as pastas `~/PagamentosInter/boletos/{entrada,processados,erros}` são criadas sozinhas, e a pasta de entrada é vigiada a cada 15 s enquanto a interface estiver aberta. A tela **Boletos** tem o botão "Ler a pasta agora".
