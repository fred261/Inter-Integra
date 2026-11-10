"""CLI: python -m inter_integra {init,credenciais,status}"""
from __future__ import annotations
import getpass
import sys
import time
from datetime import timedelta

from datetime import date
from pathlib import Path

import httpx

from . import abrir, atalho, boleto as boleto_mod, boletos, credentials, db, entrada, pagamentos
from .client import InterClient, InterError, parse_data
from .config import check_cert_permissions, ensure_entrada, ensure_home, load_settings


def cmd_init(s) -> int:
    ensure_home(s)
    db.init_db(s.db_path).close()
    ensure_entrada(s)
    print(f"Pasta e banco prontos em {s.home} (ambiente: {s.ambiente})")
    print(f"Coloque o certificado em {s.cert_path} e a chave em {s.key_path} (chmod 600).")
    return 0


def cmd_credenciais(s) -> int:
    client_id = input("Client ID: ").strip()
    client_secret = getpass.getpass("Client Secret: ").strip()
    if not client_id or not client_secret:
        print("Client ID e Client Secret são obrigatórios.")
        return 1
    credentials.salvar(s, client_id, client_secret)
    print(f"Salvo no Keychain ({s.keyring_service}).")
    return 0


def cmd_status(s) -> int:
    ok = True
    print(f"Ambiente: {s.ambiente} ({s.base_url})")
    print(f"Pasta: {s.home}")
    if s.db_path.exists():
        conn = db.connect(s.db_path)
        print(f"Banco: ok (teto por pagamento: R$ {db.teto_centavos(conn) / 100:.2f})")
        conn.close()
    else:
        print("Banco: ausente (rode `init`)")
        ok = False
    for p in check_cert_permissions(s):
        print(f"Certificado: {p}")
        ok = False
    try:
        credentials.carregar(s)
        print("Credenciais no Keychain: ok")
    except credentials.CredenciaisAusentes as e:
        print(f"Credenciais: {e}")
        ok = False
    return 0 if ok else 1


def _cliente(s) -> InterClient:
    return InterClient(s, *credentials.carregar(s))


def cmd_saldo(s, args) -> int:
    j = _cliente(s).saldo(parse_data(args[0]) if args else None)
    print(j)
    return 0


def cmd_extrato(s, args) -> int:
    if len(args) != 2:
        print("Uso: extrato AAAA-MM-DD AAAA-MM-DD")
        return 2
    print(_cliente(s).extrato(parse_data(args[0]), parse_data(args[1])))
    return 0


def _abrir(s):
    if not s.db_path.exists():
        raise ValueError("banco ausente: rode `init`")
    return db.connect(s.db_path)


def _confirmar(resumo: str) -> bool:
    print(resumo)
    return input("Digite ENVIAR para confirmar: ").strip() == "ENVIAR"


def cmd_beneficiario_add(s, args) -> int:
    if len(args) < 2:
        print("Uso: beneficiario-add NOME CHAVE_PIX [colaborador|faxina|outro]")
        return 2
    conn = _abrir(s)
    bid = pagamentos.add_beneficiario(conn, args[0], args[1], *(args[2:3]))
    print(f"Beneficiário {bid} cadastrado.")
    return 0


def cmd_pix_chave(s, args) -> int:
    if len(args) < 2:
        print("Uso: pix-chave ID_BENEFICIARIO VALOR [AAAA-MM-DD] [DESCRICAO]")
        return 2
    conn = _abrir(s)
    valor = pagamentos.parse_valor(args[1])
    data = args[2] if len(args) > 2 else None
    desc = args[3] if len(args) > 3 else None
    pid = pagamentos.criar_pix_chave(conn, int(args[0]), valor, data, desc)  # valida beneficiário
    ben = conn.execute("SELECT nome, chave_pix FROM beneficiarios WHERE id = ?",
                       (int(args[0]),)).fetchone()
    resumo = (f"Pix de R$ {valor / 100:.2f} para {ben['nome']} (chave {ben['chave_pix']})"
              f" em {data or 'hoje'}")
    return _enviar(s, conn, pid, resumo)


def cmd_pix_caju(s, args) -> int:
    if len(args) > 2:
        print("Uso: pix-caju [VALOR|-] [DESCRICAO]   (o código é pedido em seguida; sem VALOR, usa o do código)")
        return 2
    conn = _abrir(s)
    valor = pagamentos.parse_valor(args[0]) if args and args[0] != "-" else None
    codigo = input("Cole o código Copia e Cola: ")
    pid = pagamentos.criar_pix_copia_e_cola(conn, codigo, valor,
                                            args[1] if len(args) > 1 else "Recarga benefícios Caju")
    p = conn.execute("SELECT valor_centavos, recebedor_texto FROM pagamentos WHERE id = ?", (pid,)).fetchone()
    return _enviar(s, conn, pid, f"Pix Copia e Cola de R$ {p['valor_centavos'] / 100:.2f}"
                                 f" para {p['recebedor_texto'] or '(recebedor não informado no código)'}")


def _enviar(s, conn, pid: int, resumo: str) -> int:
    if not _confirmar(resumo):
        print(f"Cancelado. O pagamento {pid} ficou como rascunho, nada foi enviado.")
        return 1
    p = pagamentos.enviar(conn, _cliente(s), pid, confirmado=True)
    if p["status"] == "erro":
        print(f"Erro do Inter: {p['erro']}")
        return 1
    print(f"Enviado. Status: {p['status']}. Aprove no app do Inter.")
    return 0


def cmd_atualizar(s, args) -> int:
    conn, cli = _abrir(s), _cliente(s)
    n = pagamentos.atualizar_status(conn, cli)
    n += boletos.atualizar_status(conn, cli, sleep=time.sleep)
    print(f"{n} pagamento(s) consultado(s).")
    return 0


def _previa_boleto(r) -> str:
    aviso = ("" if r["cnpj_beneficiario"] else
             "\n  ATENÇÃO: sem CNPJ/CPF do beneficiário no PDF; o Inter não vai conferir o destinatário.")
    return (f"Boleto de R$ {r['valor_centavos'] / 100:.2f}\n"
            f"  Beneficiário (texto do PDF, não verificado): {r['beneficiario_texto'] or '—'}\n"
            f"  CNPJ/CPF validado pelo Inter: {r['cnpj_beneficiario'] or 'não informado'}\n"
            f"  Vencimento: {r['data_vencimento']}  ·  Pagamento agendado para: {r['data_pagamento']}{aviso}")


def _fluxo_boleto(s, conn, b, arquivo, vencimento) -> int:
    bid = boletos.criar(conn, b, arquivo, vencimento)
    r = conn.execute("SELECT * FROM boletos WHERE id = ?", (bid,)).fetchone()
    if not _confirmar(_previa_boleto(r)):
        print(f"Cancelado. O boleto {bid} ficou como rascunho, nada foi enviado.")
        return 1
    r = boletos.enviar(conn, _cliente(s), bid, confirmado=True)
    if r["status"] == "erro":
        print(f"Erro do Inter: {r['erro']}")
        return 1
    print(f"Agendado no Inter ({r['status']}). Aprove no app do Inter, conferindo o destinatário.")
    return 0


def cmd_boleto(s, args) -> int:
    if not args or len(args) > 2:
        print("Uso: boleto ARQUIVO.pdf [VENCIMENTO AAAA-MM-DD, só se o PDF não trouxer]")
        return 2
    b = boleto_mod.parse_pdf(Path(args[0]).expanduser().read_bytes())
    return _fluxo_boleto(s, _abrir(s), b, Path(args[0]).name, parse_data(args[1]) if len(args) > 1 else None)


def cmd_boleto_diagnostico(s, args) -> int:
    """Mostra por que um PDF não foi lido, sem expor dados do boleto (dígitos viram #)."""
    if len(args) != 1:
        print("Uso: boleto-diagnostico ARQUIVO.pdf")
        return 2
    conteudo = Path(args[0]).expanduser().read_bytes()
    for i, texto in enumerate(boleto_mod.textos_do_pdf(conteudo), 1):
        print(f"===== extração {i} =====\n{boleto_mod.diagnostico(texto)}")
    return 0


def cmd_boleto_codigo(s, args) -> int:
    b = boleto_mod.parse_codigo(input("Linha digitável ou código de barras: "))
    return _fluxo_boleto(s, _abrir(s), b, None, parse_data(args[0]) if args else None)


def cmd_entrada(s, args=()) -> int:
    conn = _abrir(s)
    res = entrada.processar_pasta(s, conn)
    for r in res:
        print(f"{'OK ' if r.ok else 'ERRO'} {r.arquivo}: " + (f"boleto #{r.detalhe} criado (rascunho)" if r.ok else r.detalhe))
    entrada.notificar(sum(r.ok for r in res), sum(not r.ok for r in res))
    return 0


def cmd_agente(s, args=()) -> int:
    """Gera o LaunchAgent da pasta de entrada e mostra os comandos para ligá-lo."""
    ensure_entrada(s)
    destino = Path.home() / "Library" / "LaunchAgents" / "br.inter-integra.entrada.plist"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(entrada.plist_agente(sys.executable, str(s.home), s.ambiente, str(s.entrada_dir)))
    print(f"Agente gravado em {destino}\nPara LIGAR:\n  launchctl bootstrap gui/$(id -u) {destino}"
          f"\nPara DESLIGAR:\n  launchctl bootout gui/$(id -u) {destino}")
    return 0


def cmd_atalho(s, args=()) -> int:
    """Cria 'Inter Integra.command' na Área de Trabalho."""
    destino = Path.home() / "Desktop" / atalho.NOME
    destino.parent.mkdir(parents=True, exist_ok=True)
    projeto = str(Path(__file__).resolve().parent.parent)
    destino.write_text(atalho.gerar_script(projeto, str(s.home), s.ambiente, sys.executable), encoding="utf-8")
    destino.chmod(0o755)
    print(f"Atalho criado: {destino}\nAmbiente fixado no atalho: {s.ambiente}")
    if s.ambiente != "producao":
        print("ATENÇÃO: o ambiente não é 'producao'. Para o dinheiro real, rode antes: export INTER_AMBIENTE=producao")
    return 0


def cmd_boletos_inter(s, args) -> int:
    hoje = date.today()
    for i in _cliente(s).buscar_boletos(dataInicio=(hoje - timedelta(days=30)).isoformat(),
                                         dataFim=hoje.isoformat()):
        print(i.get("dataVencimentoTitulo"), i.get("valorNominal"), i.get("statusPagamento"),
              i.get("nomeBeneficiario"))
    return 0


def _subir_web(s):
    import uvicorn

    from .web import create_app
    db.init_db(s.db_path).close()  # cria tabelas novas (ex.: boletos) em bancos antigos
    ensure_entrada(s)  # pastas de boletos (entrada/processados/erros), mesmo em instalações antigas

    def subir(p: int) -> None:
        import threading
        threading.Thread(target=entrada.vigiar, args=(s, threading.Event()), daemon=True).start()
        print(f"Interface em http://localhost:{p} (somente neste computador). Ctrl+C para sair.")
        print(f"Pasta de boletos: {s.entrada_dir} (PDFs soltos ali aparecem em Boletos)")
        uvicorn.run(create_app(s), host="127.0.0.1", port=p, log_level="warning")
    return subir


def cmd_web(s) -> int:
    if not s.db_path.exists():
        print("banco ausente: rode `init`")
        return 1
    _subir_web(s)(abrir.porta())
    return 0


def cmd_abrir(s) -> int:
    """Usado pelo atalho: inicia, só abre o navegador ou reinicia se o código mudou."""
    if not s.db_path.exists():
        print("banco ausente: rode `init`")
        return 1
    return abrir.executar(_subir_web(s))


def cmd_versao(s) -> int:
    from .versao import codigo_versao
    print(codigo_versao())
    return 0


COMANDOS = {"init": cmd_init, "credenciais": cmd_credenciais, "status": cmd_status,
            "saldo": cmd_saldo, "extrato": cmd_extrato,
            "beneficiario-add": cmd_beneficiario_add, "pix-chave": cmd_pix_chave,
            "pix-caju": cmd_pix_caju, "atualizar": cmd_atualizar, "web": cmd_web,
            "abrir": cmd_abrir, "versao": cmd_versao, "entrada": cmd_entrada, "agente-entrada": cmd_agente, "atalho": cmd_atalho,
            "boleto": cmd_boleto, "boleto-codigo": cmd_boleto_codigo, "boleto-diagnostico": cmd_boleto_diagnostico, "boletos-inter": cmd_boletos_inter}
_COM_ARGS = set(COMANDOS) - {"init", "credenciais", "status", "web", "entrada", "agente-entrada", "atalho", "abrir", "versao"}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in COMANDOS or (len(argv) > 2 and argv[1] not in _COM_ARGS):
        print(f"Uso: python -m inter_integra {{{','.join(COMANDOS)}}}")
        return 2
    s = load_settings()
    try:
        if argv[1] in _COM_ARGS:
            return COMANDOS[argv[1]](s, argv[2:])
        return COMANDOS[argv[1]](s)
    except (InterError, credentials.CredenciaisAusentes, ValueError, OSError,
            httpx.HTTPError, pagamentos.PagamentoInvalido) as e:
        print(f"Erro: {e.__class__.__name__}: {e}" if isinstance(e, httpx.HTTPError) else f"Erro: {e}")
        if isinstance(e, httpx.HTTPError):
            print("Sem resposta do Inter. Se era um envio, NÃO repita o comando (criaria outro pagamento). Abra a interface web, vá no Histórico e reenvie pelo item, que usa o mesmo código sem duplicar.")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
