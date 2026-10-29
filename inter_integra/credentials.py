"""Client ID / Client Secret no Keychain do macOS (via keyring). Nunca em arquivo."""
from __future__ import annotations
import keyring

from .config import Settings

_CAMPOS = ("client_id", "client_secret")


class CredenciaisAusentes(RuntimeError):
    pass


def salvar(settings: Settings, client_id: str, client_secret: str) -> None:
    keyring.set_password(settings.keyring_service, "client_id", client_id)
    keyring.set_password(settings.keyring_service, "client_secret", client_secret)


def carregar(settings: Settings) -> tuple[str, str]:
    valores = [keyring.get_password(settings.keyring_service, c) for c in _CAMPOS]
    if not all(valores):
        raise CredenciaisAusentes(
            f"Credenciais não encontradas no Keychain ({settings.keyring_service}). "
            "Rode: python -m inter_integra credenciais"
        )
    return valores[0], valores[1]
