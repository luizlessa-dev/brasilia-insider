"""
Câmara Federal — dados complementares via dadosabertos.camara.leg.br/api/v2

Módulos:
  ingerir_votacoes(data_inicio, data_fim)  → camara_votacao + camara_voto + camara_orientacao
  ingerir_frentes()                        → camara_frente + camara_frente_membro
  ingerir_ocupacoes(ids_deputados)         → camara_ocupacao

Estratégia por stream:
  - Votações: lista por período → para cada votação busca votos + orientações
  - Frentes: lista paginada → para cada frente busca membros
  - Ocupações: itera lista de deputados ativos → busca histórico individual

Limites conhecidos:
  - API retorna no máximo 100 itens por página
  - Rate-limit gentil: 0.3s entre requisições
  - Votos só disponíveis para votações com resultado registrado
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("camara_complementar")

API_BASE = "https://dadosabertos.camara.leg.br/api/v2"
CHUNK = 500
REQUEST_DELAY = 0.3


# ── Session ───────────────────────────────────────────────────────────────

def _build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({
        "User-Agent": "BRInsider/1.0 (contato@thebrinsider.com)",
        "Accept": "application/json",
    })
    return s


_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = _build_session()
    return _session


# ── HTTP helper ───────────────────────────────────────────────────────────

_last_req = 0.0


def _get(path: str, params: dict | None = None) -> dict:
    global _last_req
    elapsed = time.monotonic() - _last_req
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)
    _last_req = time.monotonic()

    url = f"{API_BASE}/{path}"
    try:
        r = _get_session().get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        raise RuntimeError(f"HTTP {e.response.status_code} em {url}") from e
    except requests.RequestException as e:
        raise RuntimeError(f"Falha de rede em {url}: {e}") from e


def _paginar(path: str, params: dict | None = None, max_paginas: int = 500) -> list[dict]:
    """Itera todas as páginas de um endpoint paginado."""
    params = {**(params or {}), "itens": 100, "pagina": 1}
    resultados = []
    for _ in range(max_paginas):
        data = _get(path, params)
        dados = data.get("dados", [])
        if not dados:
            break
        resultados.extend(dados)
        # verificar se há próxima página
        links = {l["rel"]: l["href"] for l in data.get("links", [])}
        if "next" not in links:
            break
        params["pagina"] += 1
    return resultados


# ── Persistência ──────────────────────────────────────────────────────────

def _upsert(writer, table: str, rows: list[dict], on_conflict: str) -> int:
    if not rows or writer is None:
        return 0
    from .persistence import _jsonable
    rows = [{k: _jsonable(v) for k, v in r.items()} for r in rows]
    url = f"{writer.url}/rest/v1/{table}"
    total = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i+CHUNK]
        resp = writer.session.post(
            url, json=chunk,
            headers={**writer.session.headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": on_conflict},
        )
        if resp.status_code not in (200, 201, 204):
            logger.error("upsert %s falhou: %s — %s", table, resp.status_code, resp.text[:300])
            resp.raise_for_status()
        total += len(chunk)
    return total


# ── 1. Votações ───────────────────────────────────────────────────────────

def _parse_votacao(v: dict) -> dict:
    return {
        "id": v["id"],
        "data": (v.get("data") or "")[:10] or None,
        "data_hora_registro": v.get("dataHoraRegistro"),
        "sigla_orgao": v.get("siglaOrgao"),
        "uri_evento": v.get("uriEvento"),
        "proposicao_objeto": v.get("proposicaoObjeto"),
        "tipo_votacao": v.get("tipoVotacao"),
        "descricao": v.get("descricao"),
        "aprovacao": v.get("aprovacao"),
        "votos_sim": v.get("votosSim") or v.get("sim"),
        "votos_nao": v.get("votosNao") or v.get("nao"),
        "votos_abstencao": v.get("votosAbstencao") or v.get("abstencao"),
        "total_votos": v.get("totalVotos"),
    }


def _parse_voto(id_votacao: str, v: dict) -> dict | None:
    dep = v.get("deputado_", v)
    id_dep = dep.get("id") or dep.get("nuDeputadoId")
    voto = v.get("voto") or v.get("tipoVoto")
    if not id_dep or not voto:
        return None
    return {
        "id_votacao": id_votacao,
        "id_deputado": int(id_dep),
        "nome_deputado": dep.get("nome"),
        "sigla_partido": dep.get("siglaPartido"),
        "sigla_uf": dep.get("siglaUf"),
        "voto": voto,
    }


def _parse_orientacao(id_votacao: str, o: dict) -> dict | None:
    partido = o.get("siglaPartidoBloco") or o.get("siglaOrgao")
    orientacao = o.get("orientacaoVoto")
    if not partido or not orientacao:
        return None
    return {
        "id_votacao": id_votacao,
        "sigla_partido": partido,
        "orientacao": orientacao,
        "cod_tipo_lideranca": o.get("codTipoLideranca"),
    }


def _janelas_mensais(data_inicio: date, data_fim: date):
    """Gera janelas mensais para não sobrecarregar a API da Câmara."""
    cur = data_inicio.replace(day=1)
    while cur <= data_fim:
        # fim do mês
        if cur.month == 12:
            fim_mes = cur.replace(year=cur.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            fim_mes = cur.replace(month=cur.month + 1, day=1) - timedelta(days=1)
        yield cur, min(fim_mes, data_fim)
        cur = fim_mes + timedelta(days=1)


def ingerir_votacoes(data_inicio: date, data_fim: date, writer=None) -> dict:
    """Ingere votações + votos + orientações de um período (por janelas mensais)."""
    logger.info("Buscando votações de %s a %s…", data_inicio, data_fim)

    votacoes_raw = []
    for ini_m, fim_m in _janelas_mensais(data_inicio, data_fim):
        logger.info("  janela %s–%s", ini_m, fim_m)
        try:
            batch = _paginar("votacoes", {
                "dataInicio": ini_m.isoformat(),
                "dataFim": fim_m.isoformat(),
                "ordem": "ASC",
                "ordenarPor": "dataHoraRegistro",
            })
            votacoes_raw.extend(batch)
        except Exception as e:
            logger.warning("votações %s–%s: %s", ini_m, fim_m, e)
    logger.info("%d votações encontradas", len(votacoes_raw))

    votacoes = [_parse_votacao(v) for v in votacoes_raw]
    votos: list[dict] = []
    orientacoes: list[dict] = []

    for i, v in enumerate(votacoes_raw):
        vid = v["id"]
        if (i + 1) % 20 == 0:
            logger.info("  %d/%d votações processadas…", i + 1, len(votacoes_raw))

        # votos individuais
        try:
            data_v = _get(f"votacoes/{vid}/votos")
            for item in data_v.get("dados", []):
                parsed = _parse_voto(vid, item)
                if parsed:
                    votos.append(parsed)
        except Exception as e:
            logger.warning("votos %s: %s", vid, e)

        # orientações de bancada
        try:
            data_o = _get(f"votacoes/{vid}/orientacoes")
            for item in data_o.get("dados", []):
                parsed = _parse_orientacao(vid, item)
                if parsed:
                    orientacoes.append(parsed)
        except Exception as e:
            logger.warning("orientações %s: %s", vid, e)

    logger.info("Total: %d votações, %d votos, %d orientações", len(votacoes), len(votos), len(orientacoes))

    if writer:
        _upsert(writer, "camara_votacao", votacoes, "id")
        _upsert(writer, "camara_voto", votos, "id_votacao,id_deputado")
        _upsert(writer, "camara_orientacao", orientacoes, "id_votacao,sigla_partido")
        logger.info("Persistência concluída.")

    return {"n_votacoes": len(votacoes), "n_votos": len(votos), "n_orientacoes": len(orientacoes)}


# ── 2. Frentes parlamentares ──────────────────────────────────────────────

def _parse_frente(f: dict) -> dict:
    return {
        "id": f["id"],
        "titulo": f.get("titulo", ""),
        "id_legislatura": f.get("idLegislatura"),
    }


def _parse_membro(id_frente: int, m: dict) -> dict | None:
    id_dep = m.get("id")
    if not id_dep:
        return None
    return {
        "id_frente": id_frente,
        "id_deputado": int(id_dep),
        "nome_deputado": m.get("nome"),
        "sigla_partido": m.get("siglaPartido"),
        "sigla_uf": m.get("siglaUf"),
        "titulo_na_frente": m.get("titulo"),
    }


def ingerir_frentes(writer=None) -> dict:
    """Ingere todas as frentes parlamentares e seus membros."""
    logger.info("Buscando frentes parlamentares…")

    frentes_raw = _paginar("frentes")
    logger.info("%d frentes encontradas", len(frentes_raw))

    frentes = [_parse_frente(f) for f in frentes_raw]
    membros: list[dict] = []

    for i, f in enumerate(frentes_raw):
        fid = f["id"]
        if (i + 1) % 50 == 0:
            logger.info("  %d/%d frentes processadas…", i + 1, len(frentes_raw))
        try:
            data = _get(f"frentes/{fid}/membros")
            for m in data.get("dados", []):
                parsed = _parse_membro(fid, m)
                if parsed:
                    membros.append(parsed)
        except Exception as e:
            logger.warning("membros frente %s: %s", fid, e)

    logger.info("Total: %d frentes, %d membros", len(frentes), len(membros))

    if writer:
        _upsert(writer, "camara_frente", frentes, "id")
        _upsert(writer, "camara_frente_membro", membros, "id_frente,id_deputado")
        logger.info("Persistência concluída.")

    return {"n_frentes": len(frentes), "n_membros": len(membros)}


# ── 3. Ocupações / histórico profissional ────────────────────────────────

def _buscar_deputados_ativos() -> list[int]:
    """Retorna lista de IDs de deputados em exercício."""
    dados = _paginar("deputados", {"ordem": "ASC", "ordenarPor": "nome"})
    return [int(d["id"]) for d in dados if d.get("id")]


def _parse_ocupacao(id_deputado: int, o: dict, tipo: str) -> dict | None:
    titulo = o.get("titulo") or o.get("codTipoProfissao")
    if not titulo:
        return None
    return {
        "id_deputado": id_deputado,
        "titulo": str(titulo),
        "entidade": o.get("entidade"),
        "entidade_uf": o.get("entidadeUF"),
        "entidade_pais": o.get("entidadePais"),
        "ano_inicio": o.get("anoInicio"),
        "ano_fim": o.get("anoFim"),
    }


def ingerir_ocupacoes(ids_deputados: list[int] | None = None, writer=None) -> dict:
    """Ingere histórico profissional de todos os deputados ativos."""
    if ids_deputados is None:
        logger.info("Buscando lista de deputados ativos…")
        ids_deputados = _buscar_deputados_ativos()
        logger.info("%d deputados encontrados", len(ids_deputados))

    ocupacoes: list[dict] = []

    for i, dep_id in enumerate(ids_deputados):
        if (i + 1) % 100 == 0:
            logger.info("  %d/%d deputados processados…", i + 1, len(ids_deputados))

        # ocupações (empregos anteriores)
        try:
            data = _get(f"deputados/{dep_id}/ocupacoes")
            for o in data.get("dados", []):
                parsed = _parse_ocupacao(dep_id, o, "ocupacao")
                if parsed:
                    ocupacoes.append(parsed)
        except Exception as e:
            logger.warning("ocupações dep %s: %s", dep_id, e)

        # profissões declaradas
        try:
            data = _get(f"deputados/{dep_id}/profissoes")
            for p in data.get("dados", []):
                parsed = _parse_ocupacao(dep_id, p, "profissao")
                if parsed:
                    # evita duplicata se mesma profissão já está em ocupações
                    key = (dep_id, parsed["titulo"])
                    if not any(o["id_deputado"] == dep_id and o["titulo"] == parsed["titulo"] for o in ocupacoes[-20:]):
                        ocupacoes.append(parsed)
        except Exception as e:
            logger.warning("profissões dep %s: %s", dep_id, e)

    logger.info("Total: %d registros de ocupação de %d deputados", len(ocupacoes), len(ids_deputados))

    if writer:
        # PK composta tem COALESCE — upsert manual via id_deputado + titulo
        _upsert(writer, "camara_ocupacao", ocupacoes, "id_deputado,titulo")
        logger.info("Persistência concluída.")

    return {"n_deputados": len(ids_deputados), "n_ocupacoes": len(ocupacoes)}


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, json, sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Ingere dados complementares da Câmara Federal.")
    parser.add_argument("--modulos", nargs="+",
        choices=["votacoes", "frentes", "ocupacoes"],
        default=["votacoes", "frentes", "ocupacoes"],
        help="Módulos a executar (default: todos)")
    parser.add_argument("--data-inicio", default=None, help="YYYY-MM-DD (votações)")
    parser.add_argument("--data-fim", default=None, help="YYYY-MM-DD (votações)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from ingestao.persistence import SupabaseWriter
    writer = None if args.dry_run else SupabaseWriter.from_env()

    resultados = {}

    if "votacoes" in args.modulos:
        hoje = date.today()
        ini = date.fromisoformat(args.data_inicio) if args.data_inicio else date(hoje.year, 1, 1)
        fim = date.fromisoformat(args.data_fim) if args.data_fim else hoje
        resultados["votacoes"] = ingerir_votacoes(ini, fim, writer=writer)

    if "frentes" in args.modulos:
        resultados["frentes"] = ingerir_frentes(writer=writer)

    if "ocupacoes" in args.modulos:
        resultados["ocupacoes"] = ingerir_ocupacoes(writer=writer)

    print(json.dumps(resultados, indent=2, ensure_ascii=False))
    sys.exit(0)
