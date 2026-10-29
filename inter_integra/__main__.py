"""CLI: python -m inter_integra {init,credenciais,status}"""
import getpass
import sys

from . import credentials, db
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


COMANDOS = {"init": cmd_init, "credenciais": cmd_credenciais, "status": cmd_status}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in COMANDOS:
        print(f"Uso: python -m inter_integra {{{','.join(COMANDOS)}}}")
        return 2
    return COMANDOS[argv[1]](load_settings())


if __name__ == "__main__":
    sys.exit(main(sys.argv))
