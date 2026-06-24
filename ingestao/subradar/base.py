"""
Base para conectores de fontes do Subradar.
Mais simples que o BaseConnector de assembleias — sem abstração de deputados/votações.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import date, datetime
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("INTERNAL_SUPABASE_SERVICE_ROLE_KEY")
    or ""
)


def _ciclo_atual() -> str:
    return datetime.utcnow().strftime("%Y-%m")


def _jsonable(v: Any) -> Any:
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def _hash(data: Any) -> str:
    raw = json.dumps(_jsonable(data), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _supabase_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }


def upsert(table: str, rows: list[dict]) -> None:
    if not rows:
        return
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("SUPABASE_URL/KEY ausentes — pulando persistência")
        return
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    chunk = 500
    for i in range(0, len(rows), chunk):
        batch = [_jsonable(r) for r in rows[i : i + chunk]]
        resp = requests.post(url, json=batch, headers=_supabase_headers(), timeout=30)
        if not resp.ok:
            logger.error("upsert %s falhou: %s %s", table, resp.status_code, resp.text[:300])
            resp.raise_for_status()
    logger.info("upsert %s: %d linhas", table, len(rows))


def snapshot_changed(cnpj: str, fonte: str, ciclo: str, dados: Any) -> tuple[bool, str]:
    """Retorna (mudou, hash_novo). Consulta sub_snapshots para comparar."""
    h = _hash(dados)
    if not SUPABASE_URL or not SUPABASE_KEY:
        return True, h
    url = f"{SUPABASE_URL}/rest/v1/sub_snapshots"
    params = {"cnpj": f"eq.{cnpj}", "fonte": f"eq.{fonte}", "ciclo": f"eq.{ciclo}"}
    resp = requests.get(url, params=params, headers=_supabase_headers(), timeout=15)
    rows = resp.json() if resp.ok else []
    if not rows:
        return True, h
    return rows[0].get("hash_dados") != h, h


class SubradarSource:
    """Classe base para fontes do Subradar."""
    fonte: str = ""
    base_url: str = ""
    request_delay: float = 0.5
    timeout: int = 30

    def __init__(self) -> None:
        self.log = logging.getLogger(f"subradar.{self.fonte}")
        self._session = self._build_session()
        self._last: float = 0.0

    def _build_session(self) -> requests.Session:
        s = requests.Session()
        retry = Retry(total=3, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503, 504])
        s.mount("https://", HTTPAdapter(max_retries=retry))
        s.headers.update({
            "User-Agent": "Subradar/1.0 (dados-publicos; contato@subradar.com.br)",
            "Accept": "application/json",
        })
        return s

    def _get(self, url: str, params: dict | None = None, **kw) -> Any:
        elapsed = time.monotonic() - self._last
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self._last = time.monotonic()
        self.log.debug("GET %s", url)
        r = self._session.get(url, params=params, timeout=self.timeout, **kw)
        r.raise_for_status()
        return r.json()

    def consultar_cnpj(self, cnpj: str) -> list[dict]:
        """Retorna lista de alertas para o CNPJ. Implementar no subclasse."""
        raise NotImplementedError
