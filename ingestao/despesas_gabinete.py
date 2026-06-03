"""
Despesas de Gabinete — Câmara dos Deputados
Fonte: https://dadosabertos.camara.leg.br/api/v2/deputados/{id}/despesas
Tabela alvo: public.despesas_gabinete_raw (pipeline TS legado, dados-civicos)

Diferença da Cota Parlamentar (cota_despesa):
  - Cota = despesas pessoais reembolsadas pelo deputado
  - Gabinete = despesas administrativas do gabinete (contratos, serviços, pessoal)
  Fonte é a mesma API, endpoint diferente por deputado.

Estratégia: lista todos os deputados → para cada um itera anos → pagina despesas.
Volume: ~500k linhas/ano. Mais pesado que CEAPS, mas mesmo padrão da cota.
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("despesas_gabinete")

API_BASE      = "https://dadosabertos.camara.leg.br/api/v2"
CHUNK         = 500
REQUEST_DELAY = 0.35


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
_last_req: float = 0.0


def _sess() -> requests.Session:
    global _session
    if _session is None:
        _session = _build_session()
    return _session


def _get(path: str, params: dict | None = None) -> dict:
    global _last_req
    elapsed = time.monotonic() - _last_req
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)
    _last_req = time.monotonic()
    url = f"{API_BASE}/{path}"
    try:
        r = _sess().get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        raise RuntimeError(f"HTTP {e.response.status_code} em {url}") from e
    except requests.RequestException as e:
        raise RuntimeError(f"Rede: {e}") from e


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


# ── Helpers ───────────────────────────────────────────────────────────────

def _float(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (ValueError, TypeError):
        return 0.0


def _int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None


# ── Lógica principal ──────────────────────────────────────────────────────

def _buscar_deputados() -> list[dict]:
    """Retorna id + nome de todos os deputados em exercício."""
    dados = []
    pagina = 1
    while True:
        d = _get("deputados", {"ordem": "ASC", "ordenarPor": "nome", "itens": 100, "pagina": pagina})
        items = d.get("dados", [])
        if not items:
            break
        dados.extend(items)
        links = {l["rel"]: l["href"] for l in d.get("links", [])}
        if "next" not in links:
            break
        pagina += 1
    return dados


def _despesas_deputado_ano(id_deputado: int, ano: int) -> list[dict]:
    """Busca todas as despesas de gabinete de um deputado em um ano."""
    rows = []
    pagina = 1
    while True:
        try:
            d = _get(f"deputados/{id_deputado}/despesas",
                     {"ano": ano, "itens": 100, "pagina": pagina})
        except Exception as e:
            logger.warning("dep %s ano %s p%s: %s", id_deputado, ano, pagina, e)
            break

        items = d.get("dados", [])
        if not items:
            break

        for item in items:
            tipo = (item.get("tipoDespesa") or "").strip()
            num  = item.get("numDocumento") or item.get("codDocumento") or ""
            if not tipo:
                continue
            rows.append({
                "deputado_id":      str(id_deputado),
                "id_camara_deputado": int(id_deputado),
                "ano":              int(item.get("ano") or ano),
                "mes":              _int(item.get("mes")) or 0,
                "tipo_despesa":     tipo,
                "data_documento":   str(item.get("dataDocumento") or "")[:10] or None,
                "num_documento":    str(num)[:100] if num else None,
                "valor":            _float(item.get("valorDocumento")),
                "valor_liquido":    _float(item.get("valorLiquido")),
                "valor_glosa":      _float(item.get("valorGlosa")),
                "fornecedor":       item.get("nomeFornecedor"),
                "cnpj_cpf":         item.get("cnpjCpfFornecedor"),
                "url_documento":    item.get("urlDocumento"),
            })

        links = {l["rel"]: l["href"] for l in d.get("links", [])}
        if "next" not in links:
            break
        pagina += 1

    return rows


def ingerir_gabinete(anos: list[int], writer=None) -> list[dict]:
    """
    Ingere despesas de gabinete para todos os deputados em exercício,
    nos anos fornecidos.
    """
    logger.info("Buscando lista de deputados em exercício…")
    deputados = _buscar_deputados()
    logger.info("%d deputados encontrados", len(deputados))

    resultados = []
    for ano in sorted(anos):
        total_rows = 0
        erros = 0
        logger.info("Ano %d: iniciando ingestão de %d deputados…", ano, len(deputados))

        for i, dep in enumerate(deputados):
            dep_id = _int(dep.get("id"))
            if not dep_id:
                continue
            if (i + 1) % 50 == 0:
                logger.info("  %d/%d deputados processados (ano %d)…", i + 1, len(deputados), ano)

            try:
                rows = _despesas_deputado_ano(dep_id, ano)
                if rows:
                    # deduplicar por constraint natural
                    seen = set()
                    dedup = []
                    for r in rows:
                        key = (r["id_camara_deputado"], r["ano"], r["mes"],
                               r.get("num_documento") or "", r["tipo_despesa"])
                        if key not in seen:
                            seen.add(key)
                            dedup.append(r)
                    if writer:
                        _upsert(writer, "despesas_gabinete_raw", dedup,
                                "deputado_id,ano,mes,num_documento,tipo_despesa")
                    total_rows += len(dedup)
            except Exception as e:
                logger.warning("dep %s ano %s: %s", dep_id, ano, e)
                erros += 1

        logger.info("Ano %d: %d despesas persistidas (%d deputados com erro)", ano, total_rows, erros)
        resultados.append({"ano": ano, "n_despesas": total_rows, "erros": erros})

    return resultados


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, json, sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Ingere despesas de gabinete da Câmara.")
    parser.add_argument("--anos", nargs="+", type=int,
        default=[date.today().year],
        help="Anos a ingerir (ex: 2023 2024 2025)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from ingestao.persistence import SupabaseWriter
    writer = None if args.dry_run else SupabaseWriter.from_env()

    resultados = ingerir_gabinete(args.anos, writer=writer)
    print(json.dumps(resultados, indent=2, ensure_ascii=False))
    failed = [r for r in resultados if "erro" in r and r.get("n_despesas", 0) == 0]
    sys.exit(1 if failed else 0)
