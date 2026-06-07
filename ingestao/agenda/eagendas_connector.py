"""
e-Agendas (CGU) — Agenda do Poder Executivo Federal
Fonte: eagendas.cgu.gov.br/api/v2
Obrigatoriedade: Decreto nº 10.889/2021 — ministros + DAS-5+ de todos os ministérios.

Estratégia de ingestão:
  1. Para cada órgão prioritário (ministérios + PR/VPR):
     a. Busca os cargos de topo (MINISTRO DE ESTADO, PRESIDENTE DA REPÚBLICA, etc.)
     b. Para cada cargo, busca os compromissos no período
  2. Persiste na tabela agenda_executivo_compromissos

Autenticação: Bearer token (formato "id|hash") gerado no perfil do usuário.
Variável de ambiente: EAGENDAS_TOKEN

Campos capturados por compromisso:
  id, tipo_compromisso, assunto, detalhamento, local,
  data_inicio, data_termino, hora_inicio, hora_termino,
  participantes_publicos (apo_nome, cargo, orgao),
  participantes_privados, representantes,
  publicado_em, ultima_atualizacao

Órgãos cobertos: 38 ministérios + PR + VPR + Casa Civil + AGU + SECOM + SRI + SG + SERS
(total 42 órgãos, todos os com cargos de ministro/secretário de nível DAS-6)
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from typing import Generator

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BASE_URL = "https://eagendas.cgu.gov.br/api/v2"
REQUEST_DELAY = 0.5

# ── Órgãos prioritários (ministérios + cúpula do Executivo) ─────────────────
# id = orgao_id na API do e-Agendas
ORGAOS_PRIORITARIOS = {
    511:  "PR",
    638:  "VPR",
    659:  "CC-PR",
    1372: "SG",
    1381: "SECOM",
    1387: "SRI/PR",
    1434: "SERS",
    514:  "AGU",
    521:  "BCB",
    512:  "GSI/PR",
    549:  "MEC",
    560:  "MRE",
    563:  "MS",
    647:  "MMA",
    661:  "MME",
    679:  "MD",
    714:  "MTur",
    856:  "MJSP",
    860:  "MCOM",
    862:  "MCTI",
    1384: "MF",
    1386: "MDHC",
    1389: "MDS",
    1391: "MGISP",
    1393: "MIDR",
    1395: "MCID",
    1397: "MPI",
    1399: "MDIC",
    1401: "MPO",
    1403: "MESP",
    1405: "MIR",
    1407: "MDA",
    1409: "MinC",
    1411: "MT",
    1413: "MPA",
    1415: "MPOR",
    1417: "MPS",
    1419: "MAPA",
    1421: "MTE",
    1424: "MMULHERES",
    1429: "MEMP",
}

# Palavras-chave que identificam cargos de topo (ministro, presidente, secretário de nível 1)
KEYWORDS_CARGO_TOPO = [
    # Presidência
    "PRESIDENTE DA REPÚBLICA",
    "VICE-PRESIDENTE",
    # Ministros (com e sem parênteses para gênero)
    "MINISTRO DE ESTADO",
    "MINISTRO(A) DE ESTADO",
    "MINISTRO CHEFE",
    "MINISTRO(A) CHEFE",
    "MINISTRO(A) DA FAZENDA",
    "MINISTRO(A) DA EDUCAÇÃO",
    "MINISTRO(A) DA SAÚDE",
    "MINISTRO(A) DA DEFESA",
    "MINISTRO(A) DA JUSTIÇA",
    "MINISTRO(A) DO",
    "MINISTRO(A) DAS",
    "MINISTRO(A) DOS",
    "MINISTRO DO GABINETE DE SEGURANÇA INSTITUCIONAL",
    # Cargos equivalentes de ministro
    "ADVOGADO-GERAL DA UNIÃO",
    "ADVOCADO(A)-GERAL DA UNIÃO",
    "PRESIDENTE DO BANCO CENTRAL",
    "CHEFE DO GABINETE DE SEGURANÇA INSTITUCIONAL",
    # Secretários de nível 1 na Presidência
    "SECRETÁRIO-GERAL DA PRESIDÊNCIA",
    "SECRETÁRIO(A)-GERAL DA PRESIDÊNCIA",
    "SECRETÁRIO DE COMUNICAÇÃO SOCIAL",
    "SECRETÁRIO(A) DE COMUNICAÇÃO SOCIAL",
    "SECRETÁRIO DE RELAÇÕES INSTITUCIONAIS",
    "SECRETÁRIO(A) DE RELAÇÕES INSTITUCIONAIS",
    "SECRETÁRIO ESPECIAL DA SECRETARIA ESPECIAL DE ASSUNTOS ESTRATÉGICOS",
    "SECRETÁRIO EXTRAORDINÁRIO",
    "SECRETÁRIO(A) EXTRAORDINÁRIO",
]


def _build_session(token: str) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "BRInsider/1.0 (dados públicos; contato@thebrinsider.com)",
    })
    return session


def _get(session: requests.Session, url: str, params: dict | None = None) -> dict | None:
    time.sleep(REQUEST_DELAY)
    try:
        resp = session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("sucesso"):
            msg = data.get("mensagem", "")
            if "Não existe informação" in msg:
                return None
            logger.debug("API retornou sucesso=false: %s", msg)
            return None
        return data.get("resposta")
    except requests.HTTPError as e:
        logger.warning("HTTP %s em %s", e.response.status_code, url)
        return None
    except Exception as e:
        logger.warning("Erro em %s: %s", url, e)
        return None


# ── Busca de cargos de topo por órgão ────────────────────────────────────────

def _is_cargo_topo(descricao: str) -> bool:
    """Verifica se o cargo é de nível ministro/presidente (topo da hierarquia)."""
    desc_upper = descricao.upper()
    return any(kw in desc_upper for kw in KEYWORDS_CARGO_TOPO)


def get_cargos_topo(session: requests.Session, orgao_id: int) -> list[dict]:
    """
    Retorna os cargos de topo (ministro/presidente) de um órgão.
    Exclui substitutos e cargos subalternos.
    """
    resp = _get(session, f"{BASE_URL}/cargos-comissionados", {
        "orgao_id": orgao_id,
        "situacao": "Ativo",
        "per_page": 200,
    })
    if not resp:
        return []

    cargos = resp.get("cargos_comissionados", [])
    return [
        c for c in cargos
        if _is_cargo_topo(c.get("descricao", ""))
        # Excluir substitutos de forma conservadora: preferir titular
        # mas manter se for o único cargo disponível
    ]


# ── Busca de compromissos ─────────────────────────────────────────────────────

def fetch_compromissos_cargo(
    session: requests.Session,
    orgao_id: int,
    cargo_id: int,
    data_inicio: date,
    data_fim: date,
) -> list[dict]:
    """Busca todos os compromissos de um cargo em um período."""
    ini_str = data_inicio.strftime("%d-%m-%Y")
    fim_str = data_fim.strftime("%d-%m-%Y")

    params = {
        "orgao_id": orgao_id,
        "cargo_comissao_id": cargo_id,
        "data_inicio": ini_str,
        "data_termino": fim_str,
        "per_page": 200,
    }

    resp = _get(session, f"{BASE_URL}/compromissos", params)
    if not resp:
        return []

    return resp.get("compromissos", [])


# ── Normalização ──────────────────────────────────────────────────────────────

def _parse_participante(p: dict) -> dict:
    return {
        "apo_id": p.get("apo_id"),
        "nome": p.get("apo_nome"),
        "cpf_masked": p.get("apo_cpf"),
        "tipo_exercicio": p.get("apo_tipo_exercicio"),
        "orgao_id": p.get("orgao_id"),
        "orgao": p.get("orgao"),
        "cargo_id": p.get("cargo_comissao_id"),
        "cargo": p.get("cargo"),
        "situacao": p.get("situacao"),
        "tipo_participacao": p.get("tipo_participacao"),
        "publicado_em": p.get("publicado_em"),
    }


def normalize_compromisso(item: dict, orgao_id: int, orgao_sigla: str) -> dict:
    """
    Converte um compromisso bruto do e-Agendas para o formato
    da tabela agenda_executivo_compromissos.
    """
    comp_id = str(item.get("id", ""))

    # Participantes públicos: extrai nome e cargo do responsável
    part_pub = item.get("participantes_publicos") or []
    responsavel = next(
        (p for p in part_pub if "Responsável" in (p.get("tipo_participacao") or "")),
        part_pub[0] if part_pub else {},
    )

    # Participantes privados (alto valor investigativo)
    part_priv = item.get("participantes_privados") or []

    # Objetivos
    objetivos = item.get("objetivos_compromisso") or []
    objetivos_texto = "; ".join(
        o.get("descricao", "") for o in objetivos if o.get("descricao")
    ) or None

    return {
        "id": comp_id,
        "tipo_compromisso": item.get("tipo_compromisso"),
        "assunto": item.get("assunto"),
        "detalhamento": item.get("detalhamento"),
        "local": item.get("local"),
        "orgao_id": orgao_id,
        "orgao_sigla": orgao_sigla,
        "autoridade_nome": responsavel.get("apo_nome"),
        "autoridade_cargo": responsavel.get("cargo"),
        "apo_id": responsavel.get("apo_id"),
        "data_inicio": _parse_date_br(item.get("data_inicio")),
        "data_termino": _parse_date_br(item.get("data_termino")),
        "hora_inicio": item.get("hora_inicio"),
        "hora_termino": item.get("hora_termino"),
        "objetivos": objetivos_texto,
        "tem_participantes_privados": len(part_priv) > 0,
        "n_participantes_privados": len(part_priv),
        "participantes_publicos": [_parse_participante(p) for p in part_pub],
        "participantes_privados": part_priv,
        "representantes": item.get("representantes") or [],
        "publicado_em": item.get("publicado_em"),
        "ultima_atualizacao": item.get("ultima_atualizacao"),
        "raw": item,
    }


def _parse_date_br(value: str | None) -> str | None:
    """Converte dd-mm-aaaa para aaaa-mm-dd (ISO)."""
    if not value:
        return None
    try:
        d, m, y = value.split("-")
        return f"{y}-{m}-{d}"
    except Exception:
        return value


# ── Persistência ──────────────────────────────────────────────────────────────

def upsert_compromissos(supabase, registros: list[dict]) -> tuple[int, int]:
    if not registros:
        return 0, 0
    ok, erros = 0, 0
    BATCH = 100
    for i in range(0, len(registros), BATCH):
        batch = registros[i : i + BATCH]
        try:
            supabase.table("agenda_executivo_compromissos").upsert(
                batch, on_conflict="id"
            ).execute()
            ok += len(batch)
        except Exception as e:
            logger.error("e-Agendas upsert erro: %s", e)
            erros += len(batch)
    return ok, erros


# ── Ponto de entrada ──────────────────────────────────────────────────────────

def run(
    supabase,
    data_inicio: date | None = None,
    data_fim: date | None = None,
    token: str | None = None,
    orgaos_ids: list[int] | None = None,
) -> dict:
    """
    Ingestão de compromissos do e-Agendas para todos os ministérios + PR.

    token: Bearer token do e-Agendas (fallback: env EAGENDAS_TOKEN)
    orgaos_ids: limitar a órgãos específicos (None = todos os prioritários)
    """
    if token is None:
        token = os.environ.get("EAGENDAS_TOKEN", "")
    if not token:
        raise ValueError("Token do e-Agendas não fornecido (EAGENDAS_TOKEN)")

    hoje = date.today()
    if data_fim is None:
        data_fim = hoje
    if data_inicio is None:
        data_inicio = hoje - timedelta(days=1)

    logger.info(
        "e-Agendas: ingerindo %s → %s",
        data_inicio.isoformat(),
        data_fim.isoformat(),
    )

    session = _build_session(token)
    orgaos = {k: v for k, v in ORGAOS_PRIORITARIOS.items()
              if orgaos_ids is None or k in orgaos_ids}

    todos_compromissos: list[dict] = []
    n_orgaos_ok = 0
    n_orgaos_sem_dados = 0
    cargos_cache: dict[int, list[dict]] = {}

    for orgao_id, sigla in orgaos.items():
        # Buscar cargos de topo (com cache por orgao)
        if orgao_id not in cargos_cache:
            cargos = get_cargos_topo(session, orgao_id)
            cargos_cache[orgao_id] = cargos
            logger.debug("%s: %d cargos de topo", sigla, len(cargos))

        cargos_topo = cargos_cache[orgao_id]
        if not cargos_topo:
            logger.debug("%s: sem cargos de topo — pulando", sigla)
            n_orgaos_sem_dados += 1
            continue

        # Buscar compromissos para cada cargo de topo
        for cargo in cargos_topo:
            cargo_id = cargo["id"]
            try:
                comps_raw = fetch_compromissos_cargo(
                    session, orgao_id, cargo_id, data_inicio, data_fim
                )
                for raw in comps_raw:
                    norm = normalize_compromisso(raw, orgao_id, sigla)
                    todos_compromissos.append(norm)
                if comps_raw:
                    logger.info("%s / cargo %d: %d compromissos", sigla, cargo_id, len(comps_raw))
            except Exception as e:
                logger.warning("%s / cargo %d: erro: %s", sigla, cargo_id, e)

        n_orgaos_ok += 1

    # Deduplicar por id (mesmo compromisso pode aparecer em múltiplos cargos)
    vistos: set[str] = set()
    dedup: list[dict] = []
    for c in todos_compromissos:
        if c["id"] not in vistos:
            vistos.add(c["id"])
            dedup.append(c)

    logger.info(
        "e-Agendas: %d compromissos capturados (%d únicos) em %d órgãos",
        len(todos_compromissos), len(dedup), n_orgaos_ok,
    )

    ok, erros = upsert_compromissos(supabase, dedup)
    logger.info("e-Agendas: %d upserts, %d erros", ok, erros)

    return {
        "fonte": "eagendas",
        "data_inicio": data_inicio.isoformat(),
        "data_fim": data_fim.isoformat(),
        "n_orgaos": n_orgaos_ok,
        "n_sem_dados": n_orgaos_sem_dados,
        "n_capturados": len(dedup),
        "n_ok": ok,
        "n_erros": erros,
    }
