"""
Senado Federal — CEAPS + Votações Nominais
Fontes:
  CEAPS:    https://adm.senado.gov.br/adm-dadosabertos/api/v1/senadores/despesas_ceaps/{ano}
  Votações: https://legis.senado.leg.br/dadosabertos/plenario/lista/votacao/{ini}/{fim}.json
  Orient.:  https://legis.senado.leg.br/dadosabertos/plenario/votacao/orientacaoBancada/{ini}/{fim}.json

Histórico disponível:
  CEAPS:    2008–atual (JSON, ~21k linhas/ano)
  Votações: 2019–atual (JSON aninhado, ~100 votações/mês)
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("senado")

CEAPS_BASE   = "https://adm.senado.gov.br/adm-dadosabertos/api/v1/senadores/despesas_ceaps"
VOTACAO_BASE = "https://legis.senado.leg.br/dadosabertos/plenario/lista/votacao"
ORIENT_BASE  = "https://legis.senado.leg.br/dadosabertos/plenario/votacao/orientacaoBancada"
CHUNK        = 500
REQUEST_DELAY = 0.4


# ── Session ───────────────────────────────────────────────────────────────

def _build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": "BRInsider/1.0 (contato@thebrinsider.com)"})
    return s


_session: requests.Session | None = None
_last_req: float = 0.0


def _sess() -> requests.Session:
    global _session
    if _session is None:
        _session = _build_session()
    return _session


def _get_json(url: str, params: dict | None = None, accept: str = "application/json") -> dict | list:
    global _last_req
    elapsed = time.monotonic() - _last_req
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)
    _last_req = time.monotonic()
    try:
        r = _sess().get(url, params=params, headers={"Accept": accept}, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        raise RuntimeError(f"HTTP {e.response.status_code} em {url}") from e
    except requests.RequestException as e:
        raise RuntimeError(f"Rede: {url}: {e}") from e


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
            logger.error("upsert %s: %s — %s", table, resp.status_code, resp.text[:300])
            resp.raise_for_status()
        total += len(chunk)
    return total


# ── helpers ───────────────────────────────────────────────────────────────

def _float_br(v) -> float:
    if v is None:
        return 0.0
    s = str(v).strip().replace(".", "").replace(",", ".")
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def _date(v: str | None) -> Optional[str]:
    if not v:
        return None
    return str(v)[:10]


# ═══════════════════════════════════════════════════════════════════
# A. CEAPS
# ═══════════════════════════════════════════════════════════════════

def _parse_ceaps(item: dict, ano: int) -> dict | None:
    id_ = item.get("id")
    cod = item.get("codSenador")
    nome = (item.get("nomeSenador") or "").strip()
    tipo = (item.get("tipoDespesa") or "").strip()
    if not id_ or not cod or not tipo:
        return None
    return {
        "id":                int(id_),
        "tipo_documento":    item.get("tipoDocumento"),
        "ano":               int(item.get("ano", ano)),
        "mes":               int(item.get("mes", 0)),
        "cod_senador":       int(cod),
        "nome_senador":      nome,
        "tipo_despesa":      tipo,
        "cpf_cnpj":          item.get("cpfCnpj") or item.get("CPF_CNPJ_FORNECEDOR"),
        "nome_fornecedor":   item.get("fornecedor") or item.get("NOME_FORNECEDOR"),
        "documento":         item.get("documento"),
        "data":              _date(item.get("data")),
        "detalhamento":      item.get("detalhamento"),
        "valor_reembolsado": _float_br(item.get("valorReembolsado") or item.get("VALOR_REEMBOLSADO")),
        "ano_csv":           ano,
    }


def ingerir_ceaps(anos: list[int], writer=None) -> list[dict]:
    results = []
    for ano in sorted(anos):
        try:
            logger.info("CEAPS %d: buscando…", ano)
            data = _get_json(f"{CEAPS_BASE}/{ano}")
            if isinstance(data, dict):
                # às vezes vem embrulhado
                for k, v in data.items():
                    if isinstance(v, list):
                        data = v
                        break

            rows = []
            erros = 0
            seen: set[int] = set()
            for item in data:
                parsed = _parse_ceaps(item, ano)
                if parsed and parsed["id"] not in seen:
                    seen.add(parsed["id"])
                    rows.append(parsed)
                else:
                    erros += 1

            logger.info("CEAPS %d: %d despesas (%d ignoradas)", ano, len(rows), erros)
            if writer:
                _upsert(writer, "senado_ceaps_despesa", rows, "id")
                logger.info("CEAPS %d: persistência concluída.", ano)
            results.append({"ano": ano, "n_despesas": len(rows), "erros": erros})
        except Exception as e:
            logger.error("CEAPS %d: %s", ano, e)
            results.append({"ano": ano, "erro": str(e)})
    return results


# ═══════════════════════════════════════════════════════════════════
# B. Votações nominais
# ═══════════════════════════════════════════════════════════════════

def _list_to_ensure(v) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _parse_votacao(v: dict) -> dict | None:
    sve = v.get("CodigoVotacaoSve")
    if not sve:
        return None
    votos = _list_to_ensure(v.get("Votos", {}).get("VotoParlamentar"))
    return {
        "id_sve":             int(sve),
        "cod_sessao":         _int(v.get("CodigoSessao")),
        "cod_sessao_votacao": _int(v.get("CodigoSessaoVotacao")),
        "data_sessao":        _date(v.get("DataSessao")),
        "hora_inicio":        v.get("HoraInicio"),
        "tipo_sessao":        v.get("TipoSessao"),
        "numero_sessao":      v.get("NumeroSessao"),
        "descricao":          v.get("DescricaoVotacao"),
        "resultado":          v.get("Resultado"),
        "cod_materia":        _int(v.get("CodigoMateria")),
        "sigla_materia":      v.get("SiglaMateria"),
        "numero_materia":     v.get("NumeroMateria"),
        "ano_materia":        _int(v.get("AnoMateria")),
        "secreta":            str(v.get("Secreta", "N")).upper() == "S",
        "votos_sim":          sum(1 for vt in votos if str(vt.get("Voto","")).lower() in ("sim","s")),
        "votos_nao":          sum(1 for vt in votos if str(vt.get("Voto","")).lower() in ("não","nao","n")),
        "votos_abstencao":    sum(1 for vt in votos if "absten" in str(vt.get("Voto","")).lower()),
    }


def _parse_voto(id_sve: int, vt: dict) -> dict | None:
    cod = vt.get("CodigoParlamentar")
    voto = vt.get("Voto")
    if not cod or not voto:
        return None
    return {
        "id_sve":           id_sve,
        "cod_parlamentar":  int(cod),
        "nome_parlamentar": vt.get("NomeParlamentar"),
        "sigla_partido":    vt.get("SiglaPartido"),
        "sigla_uf":         vt.get("SiglaUF"),
        "voto":             voto,
    }


def _int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def _janelas_60d(data_inicio: date, data_fim: date):
    """Gera janelas de até 60 dias (limite da API do Senado)."""
    cur = data_inicio
    while cur <= data_fim:
        fim = min(cur + timedelta(days=59), data_fim)
        yield cur, fim
        cur = fim + timedelta(days=1)


def ingerir_votacoes(data_inicio: date, data_fim: date, writer=None) -> dict:
    logger.info("Votações Senado: %s → %s", data_inicio, data_fim)
    votacoes, votos, orientacoes = [], [], []
    seen_sve: set[int] = set()

    for ini, fim in _janelas_60d(data_inicio, data_fim):
        ini_s = ini.strftime("%Y%m%d")
        fim_s = fim.strftime("%Y%m%d")
        logger.info("  janela %s–%s", ini_s, fim_s)

        # votações + votos
        try:
            data = _get_json(f"{VOTACAO_BASE}/{ini_s}/{fim_s}.json")
            lista = _list_to_ensure(
                data.get("ListaVotacoes", {}).get("Votacoes", {}).get("Votacao")
            )
            for v in lista:
                parsed_v = _parse_votacao(v)
                if not parsed_v or parsed_v["id_sve"] in seen_sve:
                    continue
                seen_sve.add(parsed_v["id_sve"])
                votacoes.append(parsed_v)
                for vt in _list_to_ensure(v.get("Votos", {}).get("VotoParlamentar")):
                    pv = _parse_voto(parsed_v["id_sve"], vt)
                    if pv:
                        votos.append(pv)
        except Exception as e:
            logger.warning("votações %s–%s: %s", ini_s, fim_s, e)

        # orientações
        try:
            data_o = _get_json(f"{ORIENT_BASE}/{ini_s}/{fim_s}.json")
            for item in data_o.get("votacoes", []):
                sve = _int(item.get("codigoVotacaoSve"))
                if not sve or sve not in seen_sve:
                    continue
                for p in _list_to_ensure(item.get("orientacoes")):
                    sigla = p.get("siglaPartido") or p.get("sigla")
                    ori   = p.get("orientacao") or p.get("descricao")
                    if sigla and ori:
                        orientacoes.append({"id_sve": sve, "sigla_partido": sigla, "orientacao": ori})
        except Exception as e:
            logger.warning("orientações %s–%s: %s", ini_s, fim_s, e)

    logger.info("Total: %d votações, %d votos, %d orientações", len(votacoes), len(votos), len(orientacoes))

    if writer:
        _upsert(writer, "senado_votacao",    votacoes,    "id_sve")
        _upsert(writer, "senado_voto",       votos,       "id_sve,cod_parlamentar")
        _upsert(writer, "senado_orientacao", orientacoes, "id_sve,sigla_partido")
        logger.info("Persistência concluída.")

    return {"n_votacoes": len(votacoes), "n_votos": len(votos), "n_orientacoes": len(orientacoes)}


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse, json, sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Ingere dados do Senado Federal.")
    parser.add_argument("--modulos", nargs="+",
        choices=["ceaps", "votacoes"],
        default=["ceaps", "votacoes"])
    parser.add_argument("--anos", nargs="+", type=int,
        default=[date.today().year],
        help="Anos para CEAPS (ex: 2022 2023 2024)")
    parser.add_argument("--data-inicio", default=None)
    parser.add_argument("--data-fim",    default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from ingestao.persistence import SupabaseWriter
    writer = None if args.dry_run else SupabaseWriter.from_env()

    resultados = {}

    if "ceaps" in args.modulos:
        resultados["ceaps"] = ingerir_ceaps(args.anos, writer=writer)

    if "votacoes" in args.modulos:
        hoje = date.today()
        ini = date.fromisoformat(args.data_inicio) if args.data_inicio else date(2019, 1, 1)
        fim = date.fromisoformat(args.data_fim)    if args.data_fim    else hoje
        resultados["votacoes"] = ingerir_votacoes(ini, fim, writer=writer)

    print(json.dumps(resultados, indent=2, ensure_ascii=False))
    sys.exit(0)
