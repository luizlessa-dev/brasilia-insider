"""
HTTP client para o Portal da Transparência (CGU).

O portal está protegido por AWS WAF/captcha — qualquer User-Agent fora do
conjunto reconhecido cai em desafio (HTTP 405 com x-amzn-waf-action: captcha).
Único UA validado em produção (testado 2026-06-02): Chrome 92 (idem ao usado
pelo turicas/transparencia-gov-br).

URLs validadas:
  Mensal agregado:  https://portaldatransparencia.gov.br/download-de-dados/despesas-execucao/{YYYYMM}
  Snapshot diário:  https://portaldatransparencia.gov.br/download-de-dados/despesas/{YYYYMMDD}

Ambos redirecionam (HTTP 302) para CloudFront → S3 sa-east-1.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("siafi.client")

# UA fixo — alterar requer revalidação contra WAF.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/92.0.4515.93 Safari/537.36"
)

BASE_URL = "https://portaldatransparencia.gov.br/download-de-dados"


@dataclass
class RemoteFile:
    url: str                     # URL final (S3) após redirect
    request_url: str             # URL original solicitada
    content_length: Optional[int]
    last_modified: Optional[datetime]
    etag: Optional[str]

    @property
    def size_mb(self) -> float:
        return (self.content_length or 0) / (1024 * 1024)


class SiafiClient:
    """Cliente HTTP com WAF bypass e rate limit defensivo."""

    def __init__(self, request_delay: float = 3.0, timeout: int = 60) -> None:
        self.request_delay = request_delay
        self.timeout = timeout
        self.session = self._build_session()
        self._last_request: float = 0.0

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=2.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
        return session

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self._last_request = time.time()

    # ── URL builders ───────────────────────────────────────────────────────
    def url_execucao_mensal(self, year: int, month: int) -> str:
        return f"{BASE_URL}/despesas-execucao/{year:04d}{month:02d}"

    def url_snapshot_diario(self, year: int, month: int, day: int) -> str:
        return f"{BASE_URL}/despesas/{year:04d}{month:02d}{day:02d}"

    # ── Operações ─────────────────────────────────────────────────────────
    def head(self, url: str) -> RemoteFile:
        """HEAD seguindo redirects. Retorna metadata do arquivo final."""
        self._throttle()
        response = self.session.head(url, timeout=self.timeout, allow_redirects=True)
        response.raise_for_status()
        return RemoteFile(
            url=response.url,
            request_url=url,
            content_length=int(response.headers["Content-Length"])
            if "Content-Length" in response.headers
            else None,
            last_modified=self._parse_last_modified(response.headers.get("Last-Modified")),
            etag=response.headers.get("ETag"),
        )

    def download(self, url: str, dest_path: str) -> RemoteFile:
        """GET streaming pra arquivo local. Retorna metadata do arquivo final."""
        self._throttle()
        logger.info("Downloading %s", url)
        with self.session.get(url, timeout=self.timeout, stream=True) as response:
            response.raise_for_status()
            with open(dest_path, "wb") as fobj:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        fobj.write(chunk)
            return RemoteFile(
                url=response.url,
                request_url=url,
                content_length=int(response.headers["Content-Length"])
                if "Content-Length" in response.headers
                else None,
                last_modified=self._parse_last_modified(response.headers.get("Last-Modified")),
                etag=response.headers.get("ETag"),
            )

    @staticmethod
    def _parse_last_modified(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        # Last-Modified: Mon, 30 Mar 2026 04:14:21 GMT
        try:
            return datetime.strptime(value, "%a, %d %b %Y %H:%M:%S %Z").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None
