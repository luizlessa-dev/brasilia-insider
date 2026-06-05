"""
Cota Parlamentar (CEAP) — Câmara dos Deputados
Fonte: downloads CSV anuais em:
  https://www.camara.leg.br/cotas/Ano-{YYYY}.csv.zip

Formato CSV (2008-atual, encoding windows-1252, sep=";"):
  txNomeParlamentar, cpf, idDeputado, idCadastro, nuLegislatura,
  txNomeParlamentar, sgPartido, sgUF, nuDeputadoId, ideDocumento,
  datEmissao, vlrDocumento, vlrGlosa, vlrLiquido, numMes, numAno,
  numParcela, txtDescricao, numEspecificacaoSubCota, txtFornecedor,
  txtCNPJCPF, txtNumero, indTipoDocumento, urlDocumento, txtTrecho,
  numLote, numRessarcimento, ideDocumentoFiscal

Estratégia: download único por ano → CSV em memória → upsert Supabase.
Volume: ~500k linhas/ano. GHA: ~2min por ano.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import zipfile
from datetime import date
from typing import Optional

import requests

logger = logging.getLogger("cota_parlamentar")

BASE_URL = "https://www.camara.leg.br/cotas"
CHUNK = 500

# ── helpers de parsing ────────────────────────────────────────────────────

def _int(v: str) -> Optional[int]:
    try:
        return int(v.strip()) if v.strip() else None
    except ValueError:
        return None

def _float_br(v: str) -> float:
    """Converte "1.234,56" ou "1234.56" ou "1467" → float."""
    v = v.strip()
    if not v:
        return 0.0
    # Formato BR com vírgula decimal: "1.234,56"
    if "," in v:
        v = v.replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return 0.0

def _date(v: str) -> Optional[str]:
    """Normaliza datEmissao (pode ser vazio ou "yyyy-mm-ddThh:mm:ss")."""
    v = v.strip()
    if not v:
        return None
    return v[:10]   # retorna apenas "yyyy-mm-dd"

def _text(v: str) -> Optional[str]:
    v = v.strip()
    return v if v else None


# ── download + parse ──────────────────────────────────────────────────────

def _fetch_csv_rows(ano: int, timeout: int = 120) -> list[dict]:
    url = f"{BASE_URL}/Ano-{ano}.csv.zip"
    logger.info("Baixando %s …", url)
    resp = requests.get(url, timeout=timeout, headers={
        "User-Agent": "BRInsider/1.0 (contato@thebrinsider.com)"
    })
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        # Há exatamente 1 CSV por ZIP
        csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
        raw_bytes = zf.read(csv_name)
        # CSVs até ~2022 são windows-1252; a partir de ~2023 a Câmara migrou para UTF-8.
        # Tentamos UTF-8 primeiro; se falhar (erro real, não só substituição), usamos windows-1252.
        try:
            raw = raw_bytes.decode("utf-8-sig")   # utf-8-sig consome BOM se presente
        except UnicodeDecodeError:
            raw = raw_bytes.decode("windows-1252", errors="replace")

    reader = csv.DictReader(io.StringIO(raw), delimiter=";")
    return list(reader)


# ── normalização ──────────────────────────────────────────────────────────

def _nome_parlamentar(row: dict) -> str | None:
    """Lê txNomeParlamentar tolerando BOM UTF-8 no cabeçalho do CSV."""
    for k, v in row.items():
        if "txNomeParlamentar" in k:
            return _text(v)
    return None


def _to_deputado(row: dict) -> dict | None:
    """Extrai campos de dim deputado de uma linha do CSV."""
    id_dep = _int(row.get("nuDeputadoId") or "")
    nome = _nome_parlamentar(row)
    if not id_dep or not nome:
        return None
    return {
        "id_camara": id_dep,
        "nome": nome,
        "cpf": _text(row.get("cpf")),
        "partido": _text(row.get("sgPartido")),
        "uf": _text(row.get("sgUF")),
        "legislatura": _int(row.get("nuLegislatura") or ""),
    }


def _to_despesa(row: dict, ano: int) -> dict | None:
    """Normaliza uma linha do CSV para o schema cota_despesa."""
    id_doc = _int(row.get("ideDocumento") or "")
    id_dep = _int(row.get("nuDeputadoId") or "")
    tipo = _text(row.get("txtDescricao") or "")

    if not id_doc or not id_dep or not tipo:
        return None

    return {
        "id_documento": id_doc,
        "id_deputado": id_dep,
        "ano": _int(row.get("numAno") or "") or ano,
        "mes": _int(row.get("numMes") or "") or 0,
        "data_emissao": _date(row.get("datEmissao") or ""),
        "tipo_despesa": tipo,
        "sub_quotaid_cnt": _int(row.get("numEspecificacaoSubCota") or ""),
        "descricao": None,   # campo não existe no CSV (reservado pra API)
        "cnpj_cpf_fornecedor": _text(row.get("txtCNPJCPF") or ""),
        "nome_fornecedor": _text(row.get("txtFornecedor") or ""),
        "tipo_documento": _int(row.get("indTipoDocumento") or ""),
        "numero_documento": _text(row.get("txtNumero") or ""),
        "valor_documento": _float_br(row.get("vlrDocumento") or "0"),
        "valor_liquido": _float_br(row.get("vlrLiquido") or "0"),
        "valor_glosa": _float_br(row.get("vlrGlosa") or "0"),
        "num_sub_cota": _int(row.get("numParcela") or ""),
        "trecho": _text(row.get("txtTrecho") or ""),
        "ano_csv": ano,
    }


# ── persistência ──────────────────────────────────────────────────────────

def _upsert(writer, table: str, rows: list[dict], on_conflict: str) -> int:
    """Delega ao SupabaseWriter via PostgREST."""
    total = 0
    url = f"{writer.url}/rest/v1/{table}"
    headers = {**writer.session.headers, "Prefer": f"resolution=merge-duplicates,return=minimal"}
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i+CHUNK]
        resp = writer.session.post(
            url,
            json=chunk,
            headers={**headers, "Prefer": f"resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": on_conflict},
        )
        if resp.status_code not in (200, 201, 204):
            logger.error("upsert %s falhou: %s — %s", table, resp.status_code, resp.text[:300])
            resp.raise_for_status()
        total += len(chunk)
    return total


# ── entry point ───────────────────────────────────────────────────────────

def ingerir_ano(ano: int, writer=None) -> dict:
    """
    Ingere todas as despesas de um ano civil.
    Retorna resumo: {ano, n_deputados, n_despesas, erros}.
    """
    from .persistence import SupabaseWriter

    if writer is None:
        writer = SupabaseWriter.from_env()
        if writer is None:
            logger.warning("Sem writer — rodando em modo fetch-only.")

    rows = _fetch_csv_rows(ano)
    logger.info("Ano %d: %d linhas lidas do CSV", ano, len(rows))

    dep_map: dict[int, dict] = {}
    despesas: list[dict] = []
    erros = 0

    for row in rows:
        dep = _to_deputado(row)
        if dep:
            dep_map[dep["id_camara"]] = dep

        desp = _to_despesa(row, ano)
        if desp:
            despesas.append(desp)
        else:
            erros += 1

    # Deduplica por PK antes do upsert — CSV às vezes repete a mesma nota
    antes = len(despesas)
    despesas_map: dict[tuple, dict] = {}
    for d in despesas:
        despesas_map[(d["id_documento"], d["id_deputado"])] = d
    despesas = list(despesas_map.values())

    deputados = list(dep_map.values())
    logger.info("Ano %d: %d deputados únicos, %d despesas (%d duplicatas removidas), %d linhas ignoradas",
                ano, len(deputados), len(despesas), antes - len(despesas), erros)

    if writer:
        _upsert(writer, "cota_deputado", deputados, "id_camara")
        _upsert(writer, "cota_despesa", despesas, "id_documento,id_deputado")
        logger.info("Ano %d: persistência concluída.", ano)

    return {"ano": ano, "n_deputados": len(deputados), "n_despesas": len(despesas), "erros": erros}


def ingerir_anos(anos: list[int], writer=None) -> list[dict]:
    results = []
    for ano in sorted(anos):
        try:
            results.append(ingerir_ano(ano, writer=writer))
        except Exception as e:
            logger.error("Falha no ano %d: %s", ano, e)
            results.append({"ano": ano, "erro": str(e)})
    return results


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, json, sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Ingere Cota Parlamentar por ano(s).")
    parser.add_argument(
        "--anos",
        nargs="+",
        type=int,
        default=[date.today().year],
        help="Anos a ingerir (ex: 2022 2023 2024). Default: ano corrente.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Faz o download e parse, mas não persiste no Supabase.",
    )
    args = parser.parse_args()

    from ingestao.persistence import SupabaseWriter
    writer = None if args.dry_run else SupabaseWriter.from_env()

    results = ingerir_anos(args.anos, writer=writer)
    print(json.dumps(results, indent=2, ensure_ascii=False))

    failed = [r for r in results if "erro" in r]
    sys.exit(1 if failed else 0)
