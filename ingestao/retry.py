"""
retry.py — The Brasilia Insider

Retry com backoff exponencial + jitter para chamadas de rede frágeis.

Adaptado do padrão `with_retry` da lição s11 do shareAI-lab/learn-claude-code
(MIT): generalizado de "429/529 da API de LLM" para falhas transientes de
HTTP/rede em geral.

Onde usar:
  - Escritas no Supabase (persistence.py) — a Session do PostgREST NÃO tem o
    adapter de Retry que o BaseConnector monta na fonte, então um 503/429
    transiente do Supabase mata a ingestão inteira sem segunda chance.
  - Qualquer chamada a SDK/cliente que não seja uma `requests.Session` (ex.: o
    enriquecimento via Gemini planejado), onde não dá pra montar urllib3.Retry.

Onde NÃO usar:
  - O lado da FONTE (baixar das assembleias) já tem retry via urllib3.Retry
    montado em BaseConnector._build_session. Não precisa daqui.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Callable, Iterable, TypeVar

import requests

logger = logging.getLogger("retry")

T = TypeVar("T")

# Status HTTP que valem nova tentativa (transientes). 4xx de payload
# (400/401/403/404/409/422) são permanentes — repetir só queima cota e tempo.
RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})

DEFAULT_MAX_RETRIES = 5
BASE_DELAY_S = 0.5
MAX_DELAY_S = 32.0


class RetryExhausted(Exception):
    """Estourou o número máximo de tentativas sem sucesso."""


def retry_delay(attempt: int, retry_after: float | None = None) -> float:
    """Backoff exponencial com jitter. Retry-After tem prioridade.

    `attempt` é 0-based: 0.5s, 1s, 2s, 4s, 8s... (teto de 32s) + até 25% de
    jitter, pra não sincronizar várias casas batendo no Supabase ao mesmo tempo.
    """
    if retry_after is not None:
        return retry_after
    base = min(BASE_DELAY_S * (2 ** attempt), MAX_DELAY_S)
    return base + random.uniform(0, base * 0.25)


def _parse_retry_after(resp: requests.Response | None) -> float | None:
    """Lê o header Retry-After em segundos. Forma HTTP-date é ignorada (cai no
    backoff normal)."""
    if resp is None:
        return None
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def with_retry(
    fn: Callable[[], T],
    *,
    what: str = "requisição",
    max_retries: int = DEFAULT_MAX_RETRIES,
    retryable_status: Iterable[int] = RETRYABLE_STATUS,
) -> T:
    """Executa fn() com retry em falhas transientes. Retorna o que fn() retornar.

    Trata como transiente (tenta de novo, com backoff):
      - requests.ConnectionError / Timeout (rede instável, reset de conexão)
      - requests.HTTPError cujo status esteja em `retryable_status`
      - uma requests.Response devolvida por fn() com status em `retryable_status`
        (respeita o header Retry-After quando presente)

    Erros não-transientes (4xx de payload, JSON inválido, etc.) sobem na hora,
    sem retry. Na última tentativa, a exceção original propaga e uma Response
    transiente é devolvida ao chamador pra ele decidir o que fazer.
    """
    retryable = frozenset(retryable_status)

    for attempt in range(max_retries):
        last = attempt + 1 >= max_retries
        try:
            result = fn()
        except (requests.ConnectionError, requests.Timeout) as e:
            if last:
                raise
            delay = retry_delay(attempt)
            logger.warning(
                "[retry] %s: rede instável (%s) — tentativa %d/%d, aguardando %.1fs",
                what, type(e).__name__, attempt + 1, max_retries, delay,
            )
            time.sleep(delay)
            continue
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status not in retryable or last:
                raise
            delay = retry_delay(attempt, _parse_retry_after(e.response))
            logger.warning(
                "[retry] %s: HTTP %s — tentativa %d/%d, aguardando %.1fs",
                what, status, attempt + 1, max_retries, delay,
            )
            time.sleep(delay)
            continue

        # fn() devolveu uma Response: deixa o retry olhar o status antes do
        # chamador decidir levantar erro (persistence.py não usa raise_for_status).
        if (
            isinstance(result, requests.Response)
            and result.status_code in retryable
            and not last
        ):
            delay = retry_delay(attempt, _parse_retry_after(result))
            logger.warning(
                "[retry] %s: HTTP %s — tentativa %d/%d, aguardando %.1fs",
                what, result.status_code, attempt + 1, max_retries, delay,
            )
            time.sleep(delay)
            continue

        return result

    # Inalcançável (o loop sempre retorna ou levanta na última volta), mas
    # mantém o type-checker e o invariante explícitos.
    raise RetryExhausted(f"{what}: esgotou {max_retries} tentativas")
