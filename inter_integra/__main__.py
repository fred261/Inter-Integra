"""CLI: python -m inter_integra {init,credenciais,status}"""
import getpass
import sys

from . import credentials, db
from .client import InterClient, InterError, parse_data
from .config import check_cert_permissions, ensure_home, load_settings


def cmd_init(s) -> int:
    ensure_home(s)
    db.init_db(s.db_path).close()
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


COMANDOS = {"init": cmd_init, "credenciais": cmd_credenciais, "status": cmd_status,
            "saldo": cmd_saldo, "extrato": cmd_extrato}
_COM_ARGS = {"saldo", "extrato"}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in COMANDOS or (len(argv) > 2 and argv[1] not in _COM_ARGS):
        print(f"Uso: python -m inter_integra {{{','.join(COMANDOS)}}}")
        return 2
    s = load_settings()
    try:
        if argv[1] in _COM_ARGS:
            return COMANDOS[argv[1]](s, argv[2:])
        return COMANDOS[argv[1]](s)
    except (InterError, credentials.CredenciaisAusentes, ValueError) as e:
        print(f"Erro: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
