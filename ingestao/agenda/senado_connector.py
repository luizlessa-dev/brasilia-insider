"""
Senado Federal — Agenda: Comissões + Plenário
Fonte: legis.senado.leg.br/dadosabertos

Endpoints utilizados:
  1. Comissões:
     GET /dadosabertos/comissao/agenda/{YYYYMMDD}/{YYYYMMDD}.json
     Limite: máximo 1 mês por requisição.
     Campos: codigo, titulo, descricao, colegiadoCriador, dataInicio,
             confirmada, realizada, situacao, local, tipoPresenca,
             tipo, partes, urlUltimaPautaSimples, urlUltimaPautaCheia

  2. Plenário:
     GET /dadosabertos/plenario/agenda/dia/{YYYYMMDD}.json
     Uma requisição por dia.
     Campos: Data, Hora, TipoSessao, LocalSessao, Casa,
             SituacaoSessao, TipoPresenca, Evento, Oradores, PautaConfirmada

Sem autenticação. Limites: comissões → 1 mês/req; plenário → 1 dia/req.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BASE_URL = "https://legis.senado.leg.br/dadosabertos"
REQUEST_DELAY = 0.5


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


def _get_json(session: requests.Session, url: str) -> dict | list | None:
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code == 404:
            logger.debug("404 em %s — sem dados para o período", url)
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        logger.warning("HTTP %s em %s", e.response.status_code, url)
        return None
    except Exception as e:
        logger.warning("Erro em %s: %s", url, e)
        return None


# ── COMISSÕES ─────────────────────────────────────────────────────────────────

def fetch_comissoes(
    data_inicio: date,
    data_fim: date,
    session: requests.Session,
) -> list[dict]:
    """
    Busca reuniões de comissões do Senado.
    Divide automaticamente em janelas de 1 mês se necessário.
    """
    all_reunioes: list[dict] = []
    cursor = data_inicio

    while cursor <= data_fim:
        # Janela máxima de 28 dias para respeitar o limite da API
        fim_janela = min(cursor + timedelta(days=27), data_fim)
        ini_str = cursor.strftime("%Y%m%d")
        fim_str = fim_janela.strftime("%Y%m%d")

        url = f"{BASE_URL}/comissao/agenda/{ini_str}/{fim_str}.json"
        logger.debug("Senado comissões: GET %s", url)

        data = _get_json(session, url)
        if data:
            # Estrutura real: AgendaReuniao.reunioes.reuniao (lowercase)
            reunioes = (
                data.get("AgendaReuniao", {})
                    .get("reunioes", {})
                    .get("reuniao", [])
            )
            if isinstance(reunioes, dict):
                reunioes = [reunioes]
            all_reunioes.extend(reunioes)
            logger.debug("Senado comissões: %d reuniões em %s→%s", len(reunioes), ini_str, fim_str)

        cursor = fim_janela + timedelta(days=1)
        time.sleep(REQUEST_DELAY)

    return all_reunioes


def normalize_comissao(item: dict) -> dict:
    """Normaliza uma reunião de comissão para agenda_senado_comissoes."""
    codigo = str(item.get("Codigo") or item.get("codigo") or "")

    # Comissão criadora
    colegiado = item.get("ColegiadoCriador") or item.get("colegiadoCriador") or {}

    # Data de início
    data_inicio = (
        item.get("DataInicio")
        or item.get("dataInicio")
        or item.get("DataInicioFormatadaComObsHorario")
        or ""
    )

    # Tipo
    tipo = item.get("Tipo") or item.get("tipo") or {}
    tipo_cod = tipo.get("Codigo") or tipo.get("codigo") if isinstance(tipo, dict) else None
    tipo_desc = tipo.get("Descricao") or tipo.get("descricao") if isinstance(tipo, dict) else str(tipo)

    # Partes
    partes_raw = item.get("Partes") or item.get("partes") or []
    if isinstance(partes_raw, dict):
        partes_raw = [partes_raw]

    # Pautas
    url_simples = item.get("UrlUltimaPautaSimplesPublicada") or item.get("urlUltimaPautaSimplesPublicada")
    url_completa = item.get("UrlUltimaPautaCheiaPublicada") or item.get("urlUltimaPautaCheiaPublicada")

    # Tipo de presença
    tipo_presenca = item.get("TipoPresenca") or item.get("tipoPresenca")
    if isinstance(tipo_presenca, dict):
        tipo_presenca = tipo_presenca.get("Descricao") or tipo_presenca.get("descricao")

    # Situação
    situacao = item.get("Situacao") or item.get("situacao") or item.get("Status") or item.get("status")
    if isinstance(situacao, dict):
        situacao = situacao.get("Descricao") or situacao.get("descricao")

    # Casa
    casa = (
        colegiado.get("SiglaCasa")
        or colegiado.get("siglaCasa")
        or item.get("SessaoLegislativa", {}).get("SiglaCasa", "SF")
        if isinstance(colegiado, dict) else "SF"
    )

    data_inicio_date = data_inicio[:10] if data_inicio else None

    return {
        "id": codigo,
        "data_hora_inicio": data_inicio if data_inicio else None,
        "data_inicio_date": data_inicio_date,
        "titulo": item.get("Titulo") or item.get("titulo"),
        "descricao": item.get("Descricao") or item.get("descricao"),
        "tipo_cod": str(tipo_cod) if tipo_cod else None,
        "tipo_desc": str(tipo_desc) if tipo_desc else None,
        "comissao_codigo": str(colegiado.get("Codigo") or colegiado.get("codigo") or "") if isinstance(colegiado, dict) else None,
        "comissao_sigla": colegiado.get("Sigla") or colegiado.get("sigla") if isinstance(colegiado, dict) else None,
        "comissao_nome": colegiado.get("Nome") or colegiado.get("nome") if isinstance(colegiado, dict) else None,
        "casa": casa,
        "confirmada": item.get("Confirmada") or item.get("confirmada"),
        "realizada": item.get("Realizada") or item.get("realizada"),
        "situacao": str(situacao) if situacao else None,
        "local": item.get("Local") or item.get("local"),
        "tipo_presenca": tipo_presenca,
        "url_pauta_simples": url_simples,
        "url_pauta_completa": url_completa,
        "partes": partes_raw if partes_raw else None,
        "raw": item,
    }


# ── PLENÁRIO ─────────────────────────────────────────────────────────────────

def fetch_plenario_dia(data: date, session: requests.Session) -> list[dict]:
    """Busca sessões plenárias de um dia específico."""
    url = f"{BASE_URL}/plenario/agenda/dia/{data.strftime('%Y%m%d')}.json"
    logger.debug("Senado plenário: GET %s", url)

    data_raw = _get_json(session, url)
    if not data_raw:
        return []

    # Estrutura real: AgendaPlenario.Sessoes.Sessao
    sessoes = (
        data_raw.get("AgendaPlenario", {})
                .get("Sessoes", {})
                .get("Sessao", [])
    )
    if isinstance(sessoes, dict):
        sessoes = [sessoes]

    return sessoes


def normalize_plenario(item: dict, data: date, seq: int) -> dict:
    """
    Normaliza uma sessão plenária para agenda_senado_plenario.
    Estrutura real confirmada: campos CamelCase (CodigoSessao, TipoSessao, etc.)
    """
    # ID preferido: CodigoSessao (único); fallback determinístico por posição
    codigo_sessao = item.get("CodigoSessao")
    evento_id = f"sf_{codigo_sessao}" if codigo_sessao else f"sf_{data.isoformat()}_{seq:03d}"

    tipo_sessao = (item.get("TipoSessao") or "").strip()
    hora = item.get("Hora") or ""
    casa = item.get("Casa") or "SF"

    # Status
    situacao = item.get("SituacaoSessao")

    # Tipo de presença: "S" = Semipresencial; descrição completa em DescricaoTipoPresenca
    tipo_presenca = item.get("DescricaoTipoPresenca") or item.get("TipoPresenca")

    # Pauta confirmada
    pauta_confirmada_raw = item.get("PautaConfirmada")
    pauta_confirmada = str(pauta_confirmada_raw).lower() in ("sim", "s", "true", "1") if pauta_confirmada_raw else False

    # Evento associado
    evento = item.get("Evento") or {}
    if isinstance(evento, dict):
        evento_tipo = evento.get("DescricaoTipoEvento")
        evento_desc = evento.get("DescricaoEvento")
        # OrigemAutor pode conter Requerimento (dict) ou texto
        origem = evento.get("OrigemAutor") or {}
        req = origem.get("Requerimento") if isinstance(origem, dict) else None
        origem_autor = req.get("NomeAutor") if isinstance(req, dict) else None
        requerimento = req.get("Origem") if isinstance(req, dict) else None
    else:
        evento_tipo = evento_desc = origem_autor = requerimento = None

    # Oradores
    oradores_raw = item.get("Oradores")

    return {
        "id": evento_id,
        "data_sessao": data.isoformat(),
        "hora": hora,
        "tipo_sessao": tipo_sessao,
        "casa": casa,
        "local": item.get("LocalSessao"),
        "situacao": str(situacao).strip() if situacao else None,
        "pauta_confirmada": pauta_confirmada,
        "tipo_presenca": tipo_presenca,
        "evento_tipo": evento_tipo,
        "evento_desc": evento_desc,
        "origem_autor": origem_autor,
        "requerimento": requerimento,
        "oradores": oradores_raw if isinstance(oradores_raw, (list, dict)) else None,
        "raw": item,
    }


def fetch_plenario(
    data_inicio: date,
    data_fim: date,
    session: requests.Session,
) -> list[dict]:
    """Busca sessões plenárias para um intervalo de datas (uma req/dia)."""
    all_sessoes: list[dict] = []
    cursor = data_inicio

    while cursor <= data_fim:
        sessoes_raw = fetch_plenario_dia(cursor, session)
        for seq, s in enumerate(sessoes_raw):
            try:
                all_sessoes.append(normalize_plenario(s, cursor, seq))
            except Exception as e:
                logger.warning("Senado plenário: erro ao normalizar sessão %s: %s", cursor, e)
        cursor += timedelta(days=1)
        time.sleep(REQUEST_DELAY)

    return all_sessoes


# ── PERSISTÊNCIA ──────────────────────────────────────────────────────────────

def upsert_comissoes(supabase, registros: list[dict]) -> tuple[int, int]:
    if not registros:
        return 0, 0
    ok, erros = 0, 0
    BATCH = 200
    for i in range(0, len(registros), BATCH):
        batch = registros[i : i + BATCH]
        try:
            supabase.table("agenda_senado_comissoes").upsert(
                batch, on_conflict="id"
            ).execute()
            ok += len(batch)
        except Exception as e:
            logger.error("Senado comissões upsert erro: %s", e)
            erros += len(batch)
    return ok, erros


def upsert_plenario(supabase, registros: list[dict]) -> tuple[int, int]:
    if not registros:
        return 0, 0
    ok, erros = 0, 0
    BATCH = 200
    for i in range(0, len(registros), BATCH):
        batch = registros[i : i + BATCH]
        try:
            supabase.table("agenda_senado_plenario").upsert(
                batch, on_conflict="id"
            ).execute()
            ok += len(batch)
        except Exception as e:
            logger.error("Senado plenário upsert erro: %s", e)
            erros += len(batch)
    return ok, erros


# ── PONTO DE ENTRADA ──────────────────────────────────────────────────────────

def run(
    supabase,
    data_inicio: date | None = None,
    data_fim: date | None = None,
) -> dict:
    """
    Ingestão completa do Senado: comissões + plenário.
    Padrão: janela de ontem até hoje.
    """
    hoje = date.today()
    if data_fim is None:
        data_fim = hoje
    if data_inicio is None:
        data_inicio = hoje - timedelta(days=1)

    logger.info(
        "Senado agenda: ingerindo %s → %s",
        data_inicio.isoformat(),
        data_fim.isoformat(),
    )

    session = _build_session()
    resultado = {}

    # — Comissões —
    try:
        comissoes_raw = fetch_comissoes(data_inicio, data_fim, session)
        comissoes_norm = []
        for item in comissoes_raw:
            try:
                comissoes_norm.append(normalize_comissao(item))
            except Exception as e:
                logger.warning("Senado comissões: erro ao normalizar: %s", e)

        logger.info("Senado comissões: %d reuniões capturadas", len(comissoes_norm))
        ok_c, err_c = upsert_comissoes(supabase, comissoes_norm)
        resultado["senado_comissoes"] = {
            "n_capturados": len(comissoes_norm),
            "n_ok": ok_c,
            "n_erros": err_c,
        }
    except Exception as e:
        logger.error("Senado comissões: falha geral: %s", e)
        resultado["senado_comissoes"] = {"erro": str(e)}

    # — Plenário —
    try:
        plenario_norm = fetch_plenario(data_inicio, data_fim, session)
        logger.info("Senado plenário: %d sessões capturadas", len(plenario_norm))
        ok_p, err_p = upsert_plenario(supabase, plenario_norm)
        resultado["senado_plenario"] = {
            "n_capturados": len(plenario_norm),
            "n_ok": ok_p,
            "n_erros": err_p,
        }
    except Exception as e:
        logger.error("Senado plenário: falha geral: %s", e)
        resultado["senado_plenario"] = {"erro": str(e)}

    return resultado
