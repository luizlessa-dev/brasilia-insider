"""
Câmara dos Deputados — Agenda / Eventos
Fonte: dadosabertos.camara.leg.br/api/v2/eventos
Sem autenticação. Histórico desde 2013.

Endpoints utilizados:
  GET /api/v2/eventos          → listagem paginada por período
  GET /api/v2/eventos/{id}     → detalhe com deputados e convidados

Campos capturados:
  id, dataHoraInicio, dataHoraFim, situacao, descricaoTipo,
  descricao, localCamara, localExterno, orgaos, requerimentos,
  urlDocumentoPauta, urlRegistro, urlConvite

Paginação: 100 itens/página via parâmetros `itens` + `pagina`.
Limite de segurança: 500 páginas por chamada (evita loop infinito).
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import date, timedelta
from typing import Generator

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"
PAGE_SIZE = 100
MAX_PAGES = 500
REQUEST_DELAY = 0.4  # segundos entre requisições


def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": "BRInsider/1.0 (dados públicos; contato@thebrinsider.com)",
        "Accept": "application/json",
    })
    return session


def _get(session: requests.Session, url: str, params: dict) -> dict:
    resp = session.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_eventos(
    data_inicio: date,
    data_fim: date,
    session: requests.Session | None = None,
) -> Generator[dict, None, None]:
    """
    Gerador que produz eventos brutos da API da Câmara no período dado.
    Cuida da paginação automaticamente.
    """
    if session is None:
        session = _build_session()

    params = {
        "dataInicio": data_inicio.isoformat(),
        "dataFim": data_fim.isoformat(),
        "itens": PAGE_SIZE,
        "pagina": 1,
        "ordem": "ASC",
        "ordenarPor": "dataHoraInicio",
    }

    for page in range(1, MAX_PAGES + 1):
        params["pagina"] = page
        try:
            data = _get(session, f"{BASE_URL}/eventos", params)
        except requests.HTTPError as e:
            logger.error("Câmara eventos HTTP %s na página %d", e.response.status_code, page)
            break
        except Exception as e:
            logger.error("Câmara eventos erro na página %d: %s", page, e)
            break

        items = data.get("dados", [])
        if not items:
            logger.debug("Câmara: fim da paginação na página %d", page)
            break

        for item in items:
            yield item

        # Verifica se há próxima página
        links = data.get("links", [])
        has_next = any(lk.get("rel") == "next" for lk in links)
        if not has_next:
            break

        time.sleep(REQUEST_DELAY)

    else:
        logger.warning("Câmara: atingiu limite de %d páginas — pode haver dados truncados", MAX_PAGES)


def _extract_orgaos(item: dict) -> tuple[list[dict], list[str]]:
    """Extrai lista de órgãos e suas siglas."""
    orgaos_raw = item.get("orgaos") or []
    orgaos = [
        {
            "id": o.get("id"),
            "sigla": o.get("sigla"),
            "nome": o.get("nome"),
            "tipoOrgao": o.get("tipoOrgao"),
            "nomePublicacao": o.get("nomePublicacao"),
        }
        for o in orgaos_raw
    ]
    siglas = [o["sigla"] for o in orgaos if o.get("sigla")]
    return orgaos, siglas


def _extract_local(item: dict) -> dict:
    """Extrai campos de local (interno e externo)."""
    lc = item.get("localCamara") or {}
    le = item.get("localExterno") or {}
    if not isinstance(lc, dict):
        lc = {}
    if not isinstance(le, dict):
        # Às vezes vem como string direta
        local_ext = str(le) if le else None
        return {
            "local_nome": None,
            "local_predio": None,
            "local_sala": None,
            "local_andar": None,
            "local_externo": local_ext,
        }
    return {
        "local_nome": lc.get("nome"),
        "local_predio": lc.get("predio"),
        "local_sala": lc.get("sala"),
        "local_andar": lc.get("andar"),
        "local_externo": le.get("endereco") or le.get("local") or le.get("nome"),
    }


def normalize_evento(item: dict) -> dict:
    """
    Converte um evento bruto da API da Câmara para o formato
    da tabela agenda_camara_eventos.
    """
    evento_id = str(item.get("id", ""))
    orgaos, siglas = _extract_orgaos(item)
    local = _extract_local(item)

    dhi = item.get("dataHoraInicio")
    data_inicio_date = dhi[:10] if dhi else None

    return {
        "id": evento_id,
        "data_hora_inicio": dhi,
        "data_hora_fim": item.get("dataHoraFim"),
        "data_inicio_date": data_inicio_date,
        "tipo_evento_cod": item.get("codTipoEvento"),
        "tipo_evento": item.get("descricaoTipo"),
        "situacao": item.get("descricaoSituacao"),
        "descricao": item.get("descricao"),
        **local,
        "orgaos": orgaos,
        "orgaos_siglas": siglas,
        "url_documento_pauta": item.get("urlDocumentoPauta"),
        "url_registro": item.get("urlRegistro"),
        "url_convite": item.get("urlConvite"),
        "requerimentos": item.get("requerimentos"),
        "raw": item,
    }


def upsert_eventos(supabase, eventos: list[dict]) -> tuple[int, int]:
    """
    Faz upsert dos eventos na tabela agenda_camara_eventos.
    Retorna (n_inseridos_ou_atualizados, n_erros).
    """
    if not eventos:
        return 0, 0

    ok = 0
    erros = 0
    BATCH = 200

    for i in range(0, len(eventos), BATCH):
        batch = eventos[i : i + BATCH]
        try:
            supabase.table("agenda_camara_eventos").upsert(
                batch,
                on_conflict="id",
            ).execute()
            ok += len(batch)
        except Exception as e:
            logger.error("Câmara upsert erro (batch %d): %s", i // BATCH, e)
            erros += len(batch)

    return ok, erros


def run(
    supabase,
    data_inicio: date | None = None,
    data_fim: date | None = None,
) -> dict:
    """
    Ponto de entrada principal: busca e persiste eventos da Câmara.

    data_inicio / data_fim: janela a ingerir (padrão: ontem + hoje).
    """
    hoje = date.today()
    if data_fim is None:
        data_fim = hoje
    if data_inicio is None:
        data_inicio = hoje - timedelta(days=1)

    logger.info(
        "Câmara agenda: ingerindo %s → %s",
        data_inicio.isoformat(),
        data_fim.isoformat(),
    )

    session = _build_session()
    eventos_norm = []

    for raw in fetch_eventos(data_inicio, data_fim, session=session):
        try:
            eventos_norm.append(normalize_evento(raw))
        except Exception as e:
            logger.warning("Câmara: erro ao normalizar evento %s: %s", raw.get("id"), e)

    logger.info("Câmara: %d eventos capturados", len(eventos_norm))

    ok, erros = upsert_eventos(supabase, eventos_norm)
    logger.info("Câmara: %d upserts, %d erros", ok, erros)

    return {
        "fonte": "camara",
        "data_inicio": data_inicio.isoformat(),
        "data_fim": data_fim.isoformat(),
        "n_capturados": len(eventos_norm),
        "n_ok": ok,
        "n_erros": erros,
    }
