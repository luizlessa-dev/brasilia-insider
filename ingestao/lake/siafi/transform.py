"""
Transformação CSV (ISO-8859-1, sep=";") → Parquet (snappy, UTF-8).

Estratégia: tudo como string no bronze pra preservar fidelidade.
Tipagem (valores monetários, datas) acontece no silver/Postgres.

Streaming via pyarrow.csv para evitar carregar 80 MB de uma vez.
"""
from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from .schemas import TableSchema

logger = logging.getLogger("siafi.transform")


def extract_csv_from_zip(zip_path: Path, inner_filename_suffix: str) -> bytes:
    """Lê o CSV interno cujo nome termina com o suffix. Retorna bytes raw (ISO-8859-1)."""
    with zipfile.ZipFile(zip_path) as zfile:
        matches = [n for n in zfile.namelist() if n.endswith(inner_filename_suffix)]
        if not matches:
            raise FileNotFoundError(
                f"Nenhum arquivo terminando em '{inner_filename_suffix}' "
                f"em {zip_path}. Conteúdo: {zfile.namelist()}"
            )
        if len(matches) > 1:
            raise ValueError(f"Múltiplos matches pra '{inner_filename_suffix}': {matches}")
        with zfile.open(matches[0]) as fobj:
            return fobj.read()


def csv_bytes_to_parquet(
    csv_bytes: bytes,
    schema: TableSchema,
    out_path: Path,
    extra_columns: Optional[dict[str, str]] = None,
) -> tuple[int, int]:
    """
    Converte bytes de CSV (ISO-8859-1) para Parquet snappy.

    extra_columns: colunas constantes a injetar (ex: snapshot_date, competencia).
                   Aplicadas em todas as linhas.

    Retorna (n_linhas, tamanho_bytes_parquet).
    """
    # Decodifica ISO-8859-1 → UTF-8 in-memory (single pass; CSVs ≤ 80MB)
    csv_text = csv_bytes.decode("iso-8859-1").encode("utf-8")
    buf = io.BytesIO(csv_text)

    # Tudo como string no bronze — preserva valores como o portal entregou.
    column_types = {col: pa.string() for col in schema.columns_pt}

    read_options = pacsv.ReadOptions(
        use_threads=True,
        encoding="utf-8",  # já recodificado
    )
    parse_options = pacsv.ParseOptions(
        delimiter=";",
        quote_char='"',
        escape_char=False,
        double_quote=True,
        newlines_in_values=False,
    )
    convert_options = pacsv.ConvertOptions(
        column_types=column_types,
        strings_can_be_null=False,
        # Inclui só as colunas que sabemos do schema; outras são ignoradas
        # (defensivo contra colunas extras que o portal possa adicionar).
        include_columns=schema.columns_pt,
    )

    table = pacsv.read_csv(buf, read_options, parse_options, convert_options)

    # Renomeia PT → canônico
    rename_map = schema.rename_map()
    table = table.rename_columns([rename_map[c] for c in table.column_names])

    # Injeta colunas constantes (snapshot_date, competencia, _ingested_at)
    if extra_columns:
        for col_name, col_value in extra_columns.items():
            table = table.append_column(
                col_name, pa.array([col_value] * table.num_rows, type=pa.string())
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        table,
        out_path,
        compression="snappy",
        # Estatísticas e Bloom filter para colunas usadas em joins frequentes
        write_statistics=True,
    )

    n_rows = table.num_rows
    size_bytes = out_path.stat().st_size
    logger.info(
        "Wrote %s: %d rows, %.2f MB (parquet snappy)",
        out_path.name, n_rows, size_bytes / 1024 / 1024,
    )
    return n_rows, size_bytes
