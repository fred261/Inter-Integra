"""Cliente HTTP da API Banking do Inter: mTLS, cache de token, saldo e extrato."""
import json
import os
import time
from datetime import date, datetime, timedelta

import httpx

from . import db
from .config import Settings

ESCOPOS_PADRAO = "extrato.read"
MARGEM_TOKEN_S = 120  # renova antes de expirar; o endpoint de token limita a 5/min
MAX_DIAS_EXTRATO = 90


class InterError(RuntimeError):
    def __init__(self, status: int | None, mensagem: str):
        super().__init__(f"HTTP {status}: {mensagem}" if status else mensagem)
        self.status = status


class InterClient:
    def __init__(self, settings: Settings, client_id: str, client_secret: str,
                 http: httpx.Client | None = None, conta_corrente: str | None = None):
        self.s = settings
        self._id, self._secret = client_id, client_secret
        self.conta = conta_corrente or os.environ.get("INTER_CONTA_CORRENTE")
        self.http = http or httpx.Client(
            base_url=settings.base_url,
            cert=(str(settings.cert_path), str(settings.key_path)),
            timeout=30,
        )
        self._cache_path = settings.home / "token_cache.json"

    # --- token -------------------------------------------------------
    def _ler_cache(self, escopos: str) -> str | None:
        try:
            c = json.loads(self._cache_path.read_text())
        except (OSError, ValueError):
            return None
        if (c.get("ambiente") == self.s.ambiente and c.get("escopos") == escopos
                and c.get("expira_em", 0) - MARGEM_TOKEN_S > time.time()):
            return c["access_token"]
        return None

    def _gravar_cache(self, escopos: str, token: str, expires_in: int) -> None:
        dados = {"ambiente": self.s.ambiente, "escopos": escopos, "access_token": token,
                 "expira_em": time.time() + expires_in}
        fd = os.open(self._cache_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(dados, f)

    def token(self, escopos: str = ESCOPOS_PADRAO, forcar: bool = False) -> str:
        if not forcar and (t := self._ler_cache(escopos)):
            return t
        r = self.http.post("/oauth/v2/token", data={
            "client_id": self._id, "client_secret": self._secret,
            "grant_type": "client_credentials", "scope": escopos})
        self._log("/oauth/v2/token", r.status_code, "token")
        if r.status_code != 200:
            raise InterError(r.status_code, _resumo(r))
        j = r.json()
        self._gravar_cache(escopos, j["access_token"], int(j["expires_in"]))
        return j["access_token"]

    # --- requisições -------------------------------------------------
    def _log(self, endpoint: str, status: int | None, resumo: str) -> None:
        if not self.s.db_path.exists():
            return
        conn = db.connect(self.s.db_path)
        with conn:
            conn.execute("INSERT INTO log (endpoint, status_http, resumo) VALUES (?, ?, ?)",
                         (endpoint, status, resumo[:200]))
        conn.close()

    def get(self, caminho: str, params: dict | None = None, escopos: str = ESCOPOS_PADRAO):
        for tentativa in (1, 2):
            h = {"Authorization": f"Bearer {self.token(escopos, forcar=tentativa == 2)}"}
            if self.conta:
                h["x-conta-corrente"] = self.conta
            r = self.http.get(caminho, params=params, headers=h)
            self._log(caminho, r.status_code, "" if r.is_success else _resumo(r))
            if r.status_code == 401 and tentativa == 1:
                continue  # token pode ter sido revogado: renova uma vez
            break
        if not r.is_success:
            raise InterError(r.status_code, _resumo(r))
        return r.json()

    # --- endpoints ---------------------------------------------------
    def saldo(self, data: date | None = None) -> dict:
        return self.get("/banking/v2/saldo", {"dataSaldo": data.isoformat()} if data else None)

    def extrato(self, inicio: date, fim: date) -> dict:
        _validar_periodo(inicio, fim)
        return self.get("/banking/v2/extrato",
                        {"dataInicio": inicio.isoformat(), "dataFim": fim.isoformat()})

    def extrato_completo(self, inicio: date, fim: date, tamanho: int = 50) -> list[dict]:
        """Todas as transações do período, percorrendo a paginação."""
        _validar_periodo(inicio, fim)
        itens, pagina = [], 0
        while True:
            j = self.get("/banking/v2/extrato/completo", {
                "dataInicio": inicio.isoformat(), "dataFim": fim.isoformat(),
                "pagina": pagina, "tamanhoPagina": tamanho})
            itens += j.get("transacoes", [])
            pagina += 1
            if j.get("ultimaPagina", True) or pagina >= j.get("totalPaginas", 1):
                return itens


def _validar_periodo(inicio: date, fim: date) -> None:
    if fim < inicio:
        raise ValueError("dataFim anterior a dataInicio")
    if (fim - inicio) > timedelta(days=MAX_DIAS_EXTRATO):
        raise ValueError(f"período máximo de {MAX_DIAS_EXTRATO} dias")


def _resumo(r: httpx.Response) -> str:
    return r.text[:200].replace("\n", " ")


def parse_data(txt: str) -> date:
    return datetime.strptime(txt, "%Y-%m-%d").date()
