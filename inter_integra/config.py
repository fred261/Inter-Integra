"""Caminhos e configuração. Tudo fica em ~/PagamentosInter/ (ou INTER_HOME)."""
import os
import stat
from dataclasses import dataclass
from pathlib import Path

URLS = {
    "producao": "https://cdpj.partners.bancointer.com.br",
    "sandbox": "https://cdpj-sandbox.partners.uatinter.co",
}
KEYRING_SERVICE = "inter-integra"
DEFAULT_TETO_CENTAVOS = 500_000  # R$ 5.000,00 por pagamento; configurável no banco


@dataclass(frozen=True)
class Settings:
    home: Path
    ambiente: str = "sandbox"

    @property
    def db_path(self) -> Path:
        return self.home / "pagamentos.db"

    @property
    def cert_path(self) -> Path:
        return self.home / "inter.crt"

    @property
    def key_path(self) -> Path:
        return self.home / "inter.key"

    @property
    def base_url(self) -> str:
        return URLS[self.ambiente]

    @property
    def keyring_service(self) -> str:
        # credenciais de sandbox e produção ficam separadas no Keychain
        return f"{KEYRING_SERVICE}-{self.ambiente}"


def load_settings() -> Settings:
    ambiente = os.environ.get("INTER_AMBIENTE", "sandbox")
    if ambiente not in URLS:
        raise ValueError(f"INTER_AMBIENTE inválido: {ambiente!r} (use sandbox ou producao)")
    home = Path(os.environ.get("INTER_HOME", "~/PagamentosInter")).expanduser()
    return Settings(home=home, ambiente=ambiente)


def ensure_home(settings: Settings) -> None:
    settings.home.mkdir(parents=True, exist_ok=True)
    settings.home.chmod(0o700)


def check_cert_permissions(settings: Settings) -> list[str]:
    """Retorna problemas encontrados com certificado/chave (lista vazia = ok)."""
    problemas = []
    for p in (settings.cert_path, settings.key_path):
        if not p.exists():
            problemas.append(f"não encontrado: {p}")
        elif stat.S_IMODE(p.stat().st_mode) != 0o600:
            problemas.append(f"permissão deve ser 600: {p}")
    return problemas
