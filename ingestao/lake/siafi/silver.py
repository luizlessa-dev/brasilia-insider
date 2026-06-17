"""
Carga incremental Parquet (bronze) → Supabase Postgres (silver).

Usa PostgREST REST API com UPSERT idempotente. Mesma estratégia da
persistence.py do pipeline ALE: chunks de 500 linhas, retry com backoff,
sem dependências extras (só `requests`).

Ordem de carga importa por causa das FKs:
  1. siafi_fornecedor  (dim) — extraída dos próprios fatos
  2. siafi_empenho, siafi_liquidacao, siafi_pagamento (paralelo possível)
  3. siafi_item_empenho (FK→empenho)
  4. siafi_pagamento_empenho (FK→empenho, →pagamento)
  5. siafi_pagamento_favorecido_final (FK→pagamento, →fornecedor)

Env vars:
  SUPABASE_URL                — https://xxxx.supabase.co
  SUPABASE_SERVICE_ROLE_KEY   — service_role JWT

Uso:
  python -m ingestao.lake.siafi.silver --execucao-mensal 2025-04
  python -m ingestao.lake.siafi.silver --snapshot 2025-04-30
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

import requests

logger = logging.getLogger("siafi.silver")

CHUNK_SIZE = 200
UPSERT_TIMEOUT_S = 180
UPSERT_MAX_RETRIES = 3
LAKE_ROOT = Path(os.getenv("LOCAL_LAKE_ROOT", "/tmp/brinsider-lake"))


# ─────────────────────────────────────────────────────────────────────────────
# DuckDB helpers — leitura do Parquet e transformação pra payload silver
# ─────────────────────────────────────────────────────────────────────────────

def _brl_to_numeric(expr: str) -> str:
    """SQL fragment: converte string BR ('1.234,56') em NUMERIC. NULL se falha."""
    return f"TRY_CAST(REPLACE(REPLACE({expr}, '.', ''), ',', '.') AS DOUBLE)"


def _br_date_to_iso(expr: str) -> str:
    """SQL fragment: 'DD/MM/AAAA' → 'AAAA-MM-DD'. NULL se vazio/inválido."""
    return (
        f"CASE WHEN {expr} IS NULL OR {expr} = '' THEN NULL "
        f"ELSE STRPTIME({expr}, '%d/%m/%Y')::DATE END"
    )


def _competencia_to_iso(expr: str) -> str:
    """SQL fragment: '2025/04' → '2025-04-01'. NULL se inválido (mantemos como string TEXT no silver)."""
    return expr  # silver mantém formato origem, não convertemos


def classify_favorecido(cnpj: str) -> str:
    """Classifica origem do código de favorecido (PJ/PF/EXTERIOR/ESPECIAL)."""
    if not cnpj or cnpj.strip() == "":
        return "ESPECIAL"
    s = cnpj.strip()
    # CNPJ = 14 dígitos; CPF = 11 dígitos com mascaramento ***.xxx.xxx-**
    if len(s) == 14 and s.isdigit():
        return "PJ"
    if "*" in s or (len(s) <= 14 and "." in s):
        return "PF"
    if s.startswith("EX") or "EXTERIOR" in s.upper():
        return "EXTERIOR"
    return "ESPECIAL"


# ─────────────────────────────────────────────────────────────────────────────
# Supabase REST writer
# ─────────────────────────────────────────────────────────────────────────────

class SupabaseUpsert:
    """Cliente PostgREST com UPSERT idempotente em chunks."""

    def __init__(self) -> None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise RuntimeError(
                "Variáveis SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY são obrigatórias."
            )
        self.base = url.rstrip("/") + "/rest/v1"
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }

    def upsert(self, table: str, rows: list[dict], on_conflict: str) -> int:
        """UPSERT em chunks com retry exponencial em timeout/5xx."""
        if not rows:
            return 0
        url = f"{self.base}/{table}?on_conflict={on_conflict}"
        sent = 0
        for i in range(0, len(rows), CHUNK_SIZE):
            chunk = rows[i : i + CHUNK_SIZE]
            for attempt in range(1, UPSERT_MAX_RETRIES + 1):
                try:
                    response = requests.post(
                        url, headers=self.headers, json=chunk, timeout=UPSERT_TIMEOUT_S
                    )
                except requests.exceptions.Timeout:
                    if attempt == UPSERT_MAX_RETRIES:
                        logger.error(
                            "UPSERT %s timeout final após %d tentativas (chunk i=%d, size=%d)",
                            table, UPSERT_MAX_RETRIES, i, len(chunk),
                        )
                        raise
                    backoff = 2 ** attempt
                    logger.warning(
                        "UPSERT %s timeout (chunk i=%d, tentativa %d/%d) — retry em %ds",
                        table, i, attempt, UPSERT_MAX_RETRIES, backoff,
                    )
                    time.sleep(backoff)
                    continue
                if response.status_code >= 500 and attempt < UPSERT_MAX_RETRIES:
                    backoff = 2 ** attempt
                    logger.warning(
                        "UPSERT %s %d (chunk i=%d, tentativa %d/%d) — retry em %ds: %s",
                        table, response.status_code, i, attempt,
                        UPSERT_MAX_RETRIES, backoff, response.text[:200],
                    )
                    time.sleep(backoff)
                    continue
                if response.status_code >= 400:
                    logger.error(
                        "UPSERT %s falhou (%d): %s",
                        table, response.status_code, response.text[:500],
                    )
                    response.raise_for_status()
                break
            sent += len(chunk)
        logger.info("Upserted %d rows in %s", sent, table)
        return sent


# ─────────────────────────────────────────────────────────────────────────────
# Loaders por tabela
# ─────────────────────────────────────────────────────────────────────────────

def _duckdb_rows(query: str) -> list[dict]:
    """Executa SQL no DuckDB e retorna lista de dicts (string-safe)."""
    import duckdb  # lazy import — só requerido pelo silver
    con = duckdb.connect()
    cursor = con.execute(query)
    columns = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    out = []
    for row in rows:
        record = {}
        for col, val in zip(columns, row):
            # DuckDB retorna Decimal/date/None — JSON-friendly:
            if val is None:
                record[col] = None
            elif isinstance(val, (int, float, str, bool)):
                record[col] = val
            else:
                record[col] = str(val)
        out.append(record)
    return out


def load_fornecedores_from_parquet(parquet_paths: list[Path]) -> list[dict]:
    """Extrai fornecedores únicos de uma lista de parquets de empenho/pagamento.

    O mesmo CNPJ pode aparecer com nomes ligeiramente diferentes entre tabelas.
    Deduplicamos por cnpj_cpf (PK), mantendo o nome mais frequente (ANY_VALUE
    após GROUP BY garante 1 linha por CNPJ).
    """
    if not parquet_paths:
        return []
    paths_sql = ", ".join(f"'{p}'" for p in parquet_paths)
    rows = _duckdb_rows(f"""
        SELECT
          cnpj_favorecido AS cnpj_cpf,
          ANY_VALUE(nome_favorecido) AS nome
        FROM read_parquet([{paths_sql}], union_by_name=true)
        WHERE cnpj_favorecido IS NOT NULL AND cnpj_favorecido <> ''
        GROUP BY cnpj_favorecido
    """)
    enriched = []
    for r in rows:
        cnpj = r["cnpj_cpf"]
        enriched.append({
            "cnpj_cpf": cnpj,
            "nome": r["nome"] or "",
            "tipo_pessoa": classify_favorecido(cnpj),
        })
    return enriched


def load_execucao_mensal(parquet_path: Path) -> list[dict]:
    """Lê parquet de execução mensal e prepara payload silver.

    O Portal da Transparência publica ~1k linhas duplicadas por mês (mesma PK
    composta, valores diferentes). Solução: agrupa por PK e SOMA os valores —
    comportamento correto pra dados de execução orçamentária acumulada.
    """
    pk_cols = (
        "competencia, cod_ug, cod_programa_orcamentario, cod_acao, "
        "cod_plano_orcamentario, cod_elemento_despesa, cod_modalidade_despesa, "
        "cod_autor_emenda, cod_subtitulo"
    )
    dim_cols = (
        "ANY_VALUE(cod_orgao_superior) AS cod_orgao_superior,"
        "ANY_VALUE(nome_orgao_superior) AS nome_orgao_superior,"
        "ANY_VALUE(cod_orgao_subordinado) AS cod_orgao_subordinado,"
        "ANY_VALUE(nome_orgao_subordinado) AS nome_orgao_subordinado,"
        "ANY_VALUE(nome_ug) AS nome_ug,"
        "ANY_VALUE(cod_gestao) AS cod_gestao,"
        "ANY_VALUE(nome_gestao) AS nome_gestao,"
        "ANY_VALUE(cod_unidade_orcamentaria) AS cod_unidade_orcamentaria,"
        "ANY_VALUE(nome_unidade_orcamentaria) AS nome_unidade_orcamentaria,"
        "ANY_VALUE(cod_funcao) AS cod_funcao,"
        "ANY_VALUE(nome_funcao) AS nome_funcao,"
        "ANY_VALUE(cod_subfuncao) AS cod_subfuncao,"
        "ANY_VALUE(nome_subfuncao) AS nome_subfuncao,"
        "ANY_VALUE(nome_programa_orcamentario) AS nome_programa_orcamentario,"
        "ANY_VALUE(nome_acao) AS nome_acao,"
        "ANY_VALUE(plano_orcamentario) AS plano_orcamentario,"
        "ANY_VALUE(cod_programa_governo) AS cod_programa_governo,"
        "ANY_VALUE(nome_programa_governo) AS nome_programa_governo,"
        "ANY_VALUE(uf) AS uf,"
        "ANY_VALUE(municipio) AS municipio,"
        "ANY_VALUE(nome_subtitulo) AS nome_subtitulo,"
        "ANY_VALUE(cod_localizador) AS cod_localizador,"
        "ANY_VALUE(nome_localizador) AS nome_localizador,"
        "ANY_VALUE(sigla_localizador) AS sigla_localizador,"
        "ANY_VALUE(descricao_complementar_localizador) AS descricao_complementar_localizador,"
        "ANY_VALUE(nome_autor_emenda) AS nome_autor_emenda,"
        "ANY_VALUE(cod_categoria_economica) AS cod_categoria_economica,"
        "ANY_VALUE(nome_categoria_economica) AS nome_categoria_economica,"
        "ANY_VALUE(cod_grupo_despesa) AS cod_grupo_despesa,"
        "ANY_VALUE(nome_grupo_despesa) AS nome_grupo_despesa,"
        "ANY_VALUE(nome_elemento_despesa) AS nome_elemento_despesa,"
        "ANY_VALUE(modalidade_despesa) AS modalidade_despesa"
    )
    return _duckdb_rows(f"""
        SELECT
          {pk_cols},
          {dim_cols},
          SUM({_brl_to_numeric('valor_empenhado')}) AS valor_empenhado,
          SUM({_brl_to_numeric('valor_liquidado')}) AS valor_liquidado,
          SUM({_brl_to_numeric('valor_pago')}) AS valor_pago,
          SUM({_brl_to_numeric('valor_restos_pagar_inscritos')}) AS valor_restos_pagar_inscritos,
          SUM({_brl_to_numeric('valor_restos_pagar_cancelado')}) AS valor_restos_pagar_cancelado,
          SUM({_brl_to_numeric('valor_restos_pagar_pagos')}) AS valor_restos_pagar_pagos,
          ANY_VALUE(NULLIF(_source_last_modified, '')) AS source_last_modified
        FROM read_parquet('{parquet_path}')
        GROUP BY {pk_cols}
    """)


def load_empenho(parquet_path: Path, snapshot_date: date) -> list[dict]:
    return _duckdb_rows(f"""
        SELECT
          id_empenho, codigo_empenho, codigo_empenho_resumido,
          '{snapshot_date.isoformat()}'::DATE AS snapshot_date,
          {_br_date_to_iso('data_emissao')} AS data_emissao,
          cod_tipo_documento, tipo_documento, tipo_empenho, especie_empenho,
          cod_orgao_superior, nome_orgao_superior,
          cod_orgao, nome_orgao,
          cod_ug, nome_ug,
          cod_gestao, nome_gestao,
          NULLIF(cnpj_favorecido, '') AS cnpj_favorecido,
          nome_favorecido, observacao,
          cod_esfera_orcamentaria, esfera_orcamentaria,
          cod_tipo_credito, tipo_credito,
          cod_grupo_fonte_recurso, nome_grupo_fonte_recurso,
          cod_fonte_recurso, nome_fonte_recurso,
          cod_unidade_orcamentaria, nome_unidade_orcamentaria,
          cod_funcao, nome_funcao,
          cod_subfuncao, nome_subfuncao,
          cod_programa, nome_programa,
          cod_acao, nome_acao,
          linguagem_cidada,
          cod_subtitulo, nome_subtitulo,
          cod_plano_orcamentario, plano_orcamentario,
          cod_programa_governo, nome_programa_governo,
          NULLIF(autor_emenda, '') AS autor_emenda,
          cod_categoria_despesa, categoria_despesa,
          cod_grupo_despesa, grupo_despesa,
          cod_modalidade_aplicacao, modalidade_aplicacao,
          cod_elemento_despesa, elemento_despesa,
          processo, modalidade_licitacao, inciso, amparo,
          ref_dispensa_inexigibilidade,
          NULLIF(cod_convenio, '') AS cod_convenio,
          NULLIF(contrato_repasse, '') AS contrato_repasse,
          {_brl_to_numeric('valor_original_empenho')} AS valor_original_empenho,
          {_brl_to_numeric('valor_empenho_brl')} AS valor_empenho_brl,
          {_brl_to_numeric('valor_utilizado_conversao')} AS valor_utilizado_conversao,
          NULLIF(_source_last_modified, '') AS source_last_modified
        FROM read_parquet('{parquet_path}')
    """)


def load_pagamento(parquet_path: Path, snapshot_date: date) -> list[dict]:
    return _duckdb_rows(f"""
        SELECT
          codigo_pagamento, codigo_pagamento_resumido,
          '{snapshot_date.isoformat()}'::DATE AS snapshot_date,
          {_br_date_to_iso('data_emissao')} AS data_emissao,
          cod_tipo_documento, tipo_documento, tipo_ob, extra_orcamentario,
          cod_orgao_superior, nome_orgao_superior,
          cod_orgao, nome_orgao,
          cod_ug, nome_ug,
          cod_gestao, nome_gestao,
          NULLIF(cnpj_favorecido, '') AS cnpj_favorecido,
          nome_favorecido, observacao, processo,
          cod_categoria_despesa, categoria_despesa,
          cod_grupo_despesa, grupo_despesa,
          cod_modalidade_aplicacao, modalidade_aplicacao,
          cod_elemento_despesa, elemento_despesa,
          cod_plano_orcamentario, plano_orcamentario,
          cod_programa_governo, nome_programa_governo,
          {_brl_to_numeric('valor_original_pagamento')} AS valor_original_pagamento,
          {_brl_to_numeric('valor_pagamento_brl')} AS valor_pagamento_brl,
          {_brl_to_numeric('valor_utilizado_conversao')} AS valor_utilizado_conversao,
          NULLIF(_source_last_modified, '') AS source_last_modified
        FROM read_parquet('{parquet_path}')
    """)


def load_liquidacao(parquet_path: Path, snapshot_date: date) -> list[dict]:
    return _duckdb_rows(f"""
        SELECT
          codigo_liquidacao, codigo_liquidacao_resumido,
          '{snapshot_date.isoformat()}'::DATE AS snapshot_date,
          {_br_date_to_iso('data_emissao')} AS data_emissao,
          cod_tipo_documento, tipo_documento,
          cod_orgao_superior, nome_orgao_superior,
          cod_orgao, nome_orgao,
          cod_ug, nome_ug,
          cod_gestao, nome_gestao,
          NULLIF(cnpj_favorecido, '') AS cnpj_favorecido,
          nome_favorecido, observacao,
          cod_categoria_despesa, categoria_despesa,
          cod_grupo_despesa, grupo_despesa,
          cod_modalidade_aplicacao, modalidade_aplicacao,
          cod_elemento_despesa, elemento_despesa,
          cod_plano_orcamentario, plano_orcamentario,
          cod_programa_governo, nome_programa_governo,
          NULLIF(_source_last_modified, '') AS source_last_modified
        FROM read_parquet('{parquet_path}')
    """)


def load_item_empenho(
    parquet_path: Path,
    snapshot_date: date,
    empenho_parquet: Optional[Path] = None,
) -> list[dict]:
    # Dedup: mesmo (id_empenho, sequencial) pode aparecer múltiplas vezes.
    # Mantemos a linha com maior valor_atual (estado mais recente do item).
    # Se empenho_parquet for fornecido, descartamos itens órfãos (id_empenho
    # ausente no parquet de empenho do mesmo snapshot) — protege a FK NOT NULL
    # `siafi_item_empenho.id_empenho → siafi_empenho.id_empenho`. O Portal
    # ocasionalmente publica itens cujo empenho-cabeçalho não vem no mesmo dia.
    fk_filter = (
        f"WHERE id_empenho IN (SELECT DISTINCT id_empenho FROM read_parquet('{empenho_parquet}'))"
        if empenho_parquet is not None
        else ""
    )
    rows = _duckdb_rows(f"""
        SELECT
          id_empenho, sequencial, codigo_empenho,
          cod_categoria_despesa, categoria_despesa,
          cod_grupo_despesa, grupo_despesa,
          cod_modalidade_aplicacao, modalidade_aplicacao,
          cod_elemento_despesa, elemento_despesa,
          cod_subelemento_despesa, subelemento_despesa,
          descricao,
          {_brl_to_numeric('quantidade')} AS quantidade,
          {_brl_to_numeric('valor_unitario')} AS valor_unitario,
          {_brl_to_numeric('valor_total')} AS valor_total,
          {_brl_to_numeric('valor_atual')} AS valor_atual,
          '{snapshot_date.isoformat()}'::DATE AS snapshot_date,
          NULLIF(_source_last_modified, '') AS source_last_modified
        FROM read_parquet('{parquet_path}')
        {fk_filter}
        QUALIFY ROW_NUMBER() OVER (
          PARTITION BY id_empenho, sequencial
          ORDER BY {_brl_to_numeric('valor_atual')} DESC NULLS LAST
        ) = 1
    """)
    if empenho_parquet is not None:
        total_in = _duckdb_rows(
            f"SELECT COUNT(*) AS n FROM read_parquet('{parquet_path}')"
        )[0]["n"]
        descartados = total_in - len(rows)
        if descartados > 0:
            logger.warning(
                "item_empenho: %d linhas descartadas por FK órfã em siafi_empenho (de %d totais)",
                descartados, total_in,
            )
    return rows


def load_pagamento_empenho(parquet_path: Path, snapshot_date: date) -> list[dict]:
    return _duckdb_rows(f"""
        SELECT
          codigo_pagamento, codigo_empenho, subitem,
          cod_natureza_despesa,
          {_brl_to_numeric('valor_pago')} AS valor_pago,
          {_brl_to_numeric('valor_restos_pagar_inscritos')} AS valor_restos_pagar_inscritos,
          {_brl_to_numeric('valor_restos_pagar_cancelado')} AS valor_restos_pagar_cancelado,
          {_brl_to_numeric('valor_restos_pagar_pagos')} AS valor_restos_pagar_pagos,
          '{snapshot_date.isoformat()}'::DATE AS snapshot_date,
          NULLIF(_source_last_modified, '') AS source_last_modified
        FROM read_parquet('{parquet_path}')
    """)


def load_pagamento_favorecido_final(parquet_path: Path, snapshot_date: date) -> list[dict]:
    # Dedup: mesmo (codigo_pagamento, codigo_lista, cnpj_favorecido_final) duplicado.
    # Mantemos a linha com maior valor (estado mais recente).
    return _duckdb_rows(f"""
        SELECT
          codigo_pagamento, codigo_lista, cnpj_favorecido_final,
          nome_favorecido_final,
          {_br_date_to_iso('data_emissao')} AS data_emissao,
          {_brl_to_numeric('valor_pagamento_brl')} AS valor_pagamento_brl,
          '{snapshot_date.isoformat()}'::DATE AS snapshot_date,
          NULLIF(_source_last_modified, '') AS source_last_modified
        FROM read_parquet('{parquet_path}')
        WHERE cnpj_favorecido_final IS NOT NULL AND cnpj_favorecido_final <> ''
        QUALIFY ROW_NUMBER() OVER (
          PARTITION BY codigo_pagamento, codigo_lista, cnpj_favorecido_final
          ORDER BY {_brl_to_numeric('valor_pagamento_brl')} DESC NULLS LAST
        ) = 1
    """)


# ─────────────────────────────────────────────────────────────────────────────
# Orquestração
# ─────────────────────────────────────────────────────────────────────────────

def silver_execucao_mensal(competencia: str, lake_root: Path = LAKE_ROOT) -> None:
    """Carrega 1 mês de execução agregada em siafi_execucao_mensal."""
    pq = lake_root / "siafi" / "execucao-mensal" / f"competencia={competencia}" / "data.parquet"
    if not pq.exists():
        raise FileNotFoundError(f"Bronze ausente: {pq}. Rode primeiro o run.py.")

    upserter = SupabaseUpsert()
    rows = load_execucao_mensal(pq)
    logger.info("execucao_mensal %s: %d rows lidos do parquet", competencia, len(rows))
    upserter.upsert(
        "siafi_execucao_mensal", rows,
        on_conflict=(
            "competencia,cod_ug,cod_programa_orcamentario,cod_acao,"
            "cod_plano_orcamentario,cod_elemento_despesa,cod_modalidade_despesa,"
            "cod_autor_emenda,cod_subtitulo"
        ),
    )


def silver_snapshot(snapshot_date: date, lake_root: Path = LAKE_ROOT) -> None:
    """Carrega 1 snapshot diário em todas as 6 tabelas + dim fornecedor."""
    snap_dir = lake_root / "siafi" / "snapshot" / f"snapshot_date={snapshot_date.isoformat()}"
    if not snap_dir.exists():
        raise FileNotFoundError(f"Bronze ausente: {snap_dir}. Rode primeiro o run.py.")

    pq_empenho = snap_dir / "empenho.parquet"
    pq_pagamento = snap_dir / "pagamento.parquet"
    pq_liquidacao = snap_dir / "liquidacao.parquet"
    pq_item_empenho = snap_dir / "item_empenho.parquet"
    pq_pag_emp = snap_dir / "pagamento_empenho.parquet"
    pq_pag_ff = snap_dir / "pagamento_favorecido_final.parquet"

    upserter = SupabaseUpsert()

    # 1. Fornecedores únicos primeiro (dim)
    # Empenho/pagamento/liquidacao usam cnpj_favorecido.
    # Pagamento_favorecido_final usa cnpj_favorecido_final — extrai separado.
    fornecedores = load_fornecedores_from_parquet([
        p for p in [pq_empenho, pq_pagamento, pq_liquidacao] if p.exists()
    ])
    # Adiciona favorecidos finais (coluna diferente)
    if pq_pag_ff.exists():
        import duckdb as _ddb
        _con = _ddb.connect()
        _rows = _con.execute(f"""
            SELECT cnpj_favorecido_final AS cnpj_cpf, ANY_VALUE(nome_favorecido_final) AS nome
            FROM read_parquet('{pq_pag_ff}')
            WHERE cnpj_favorecido_final IS NOT NULL AND cnpj_favorecido_final <> ''
            GROUP BY cnpj_favorecido_final
        """).fetchall()
        seen = {f["cnpj_cpf"] for f in fornecedores}
        for cnpj, nome in _rows:
            if cnpj not in seen:
                fornecedores.append({
                    "cnpj_cpf": cnpj,
                    "nome": nome or "",
                    "tipo_pessoa": classify_favorecido(cnpj),
                })
                seen.add(cnpj)
    logger.info("Fornecedores únicos extraídos: %d", len(fornecedores))
    upserter.upsert("siafi_fornecedor", fornecedores, on_conflict="cnpj_cpf")

    # 2. Empenho, pagamento, liquidação (sem dependência entre si)
    if pq_empenho.exists():
        upserter.upsert("siafi_empenho", load_empenho(pq_empenho, snapshot_date),
                        on_conflict="id_empenho")
    if pq_pagamento.exists():
        upserter.upsert("siafi_pagamento", load_pagamento(pq_pagamento, snapshot_date),
                        on_conflict="codigo_pagamento")
    if pq_liquidacao.exists():
        upserter.upsert("siafi_liquidacao", load_liquidacao(pq_liquidacao, snapshot_date),
                        on_conflict="codigo_liquidacao")

    # 3. Item empenho (FK→empenho) — filtra órfãos vs o parquet de empenho do dia.
    if pq_item_empenho.exists():
        upserter.upsert(
            "siafi_item_empenho",
            load_item_empenho(
                pq_item_empenho, snapshot_date,
                empenho_parquet=pq_empenho if pq_empenho.exists() else None,
            ),
            on_conflict="id_empenho,sequencial",
        )

    # 4. Junctions
    if pq_pag_emp.exists():
        upserter.upsert("siafi_pagamento_empenho",
                        load_pagamento_empenho(pq_pag_emp, snapshot_date),
                        on_conflict="codigo_pagamento,codigo_empenho,subitem")
    if pq_pag_ff.exists():
        upserter.upsert("siafi_pagamento_favorecido_final",
                        load_pagamento_favorecido_final(pq_pag_ff, snapshot_date),
                        on_conflict="codigo_pagamento,codigo_lista,cnpj_favorecido_final")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Carga incremental SIAFI bronze → silver")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--execucao-mensal", metavar="YYYY-MM",
                       help="Carrega 1 mês (parquet bronze → siafi_execucao_mensal)")
    group.add_argument("--snapshot", metavar="YYYY-MM-DD",
                       help="Carrega 1 snapshot (6 tabelas)")
    args = parser.parse_args()

    try:
        if args.execucao_mensal:
            silver_execucao_mensal(args.execucao_mensal)
        elif args.snapshot:
            silver_snapshot(date.fromisoformat(args.snapshot))
    except Exception as e:  # noqa: BLE001
        logger.error("Falha na carga silver: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
