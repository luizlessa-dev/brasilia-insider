"""
Orquestração dos dois streams de ingestão SIAFI:

  Stream A — Execução mensal agregada
    Fonte:  /despesas-execucao/{YYYYMM}/
    Saída:  siafi/execucao-mensal/competencia={YYYY-MM}/data.parquet

  Stream B — Snapshot diário (tabelas operacionais)
    Fonte:  /despesas/{YYYYMMDD}/
    Saída:  siafi/snapshot/snapshot_date={YYYY-MM-DD}/{table}.parquet
"""
from __future__ import annotations

import calendar
import logging
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from .client import RemoteFile, SiafiClient
from .r2 import LakeWriter
from .schemas import EXECUCAO_MENSAL, SNAPSHOT_TABLES, TableSchema
from .transform import csv_bytes_to_parquet, extract_csv_from_zip

logger = logging.getLogger("siafi.streams")


@dataclass
class IngestResult:
    table: str
    rows: int
    parquet_bytes: int
    lake_uri: str
    last_modified_remote: Optional[datetime]


class ExecucaoMensalStream:
    """Stream A — 1 ZIP/mês com agregação por (UG × programa × ação × elemento × emenda)."""

    name = "execucao_mensal"

    def __init__(self, client: SiafiClient, writer: LakeWriter) -> None:
        self.client = client
        self.writer = writer

    def ingest(self, year: int, month: int) -> IngestResult:
        url = self.client.url_execucao_mensal(year, month)
        competencia = f"{year:04d}-{month:02d}"

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / f"{year:04d}{month:02d}_Despesas.zip"
            remote = self.client.download(url, str(zip_path))
            logger.info("Downloaded %.2f MB", remote.size_mb)

            csv_bytes = extract_csv_from_zip(zip_path, EXECUCAO_MENSAL.inner_filename_suffix)
            parquet_path = Path(tmpdir) / "data.parquet"
            rows, size = csv_bytes_to_parquet(
                csv_bytes,
                EXECUCAO_MENSAL,
                parquet_path,
                extra_columns={
                    "_competencia_path": competencia,
                    "_ingested_at": datetime.now(timezone.utc).isoformat(),
                    "_source_last_modified": (
                        remote.last_modified.isoformat() if remote.last_modified else ""
                    ),
                },
            )

            key = f"siafi/execucao-mensal/competencia={competencia}/data.parquet"
            metadata = {
                "source-url": remote.url,
                "source-etag": remote.etag or "",
                "source-last-modified": (
                    remote.last_modified.isoformat() if remote.last_modified else ""
                ),
                "rows": str(rows),
            }
            uri = self.writer.put(parquet_path, key, metadata=metadata)

        return IngestResult(
            table=self.name,
            rows=rows,
            parquet_bytes=size,
            lake_uri=uri,
            last_modified_remote=remote.last_modified,
        )


class SnapshotDiarioStream:
    """Stream B — 1 ZIP/dia com 6 tabelas operacionais (estado vigente naquele dia)."""

    def __init__(self, client: SiafiClient, writer: LakeWriter) -> None:
        self.client = client
        self.writer = writer

    def ingest(self, snapshot_date: date) -> list[IngestResult]:
        url = self.client.url_snapshot_diario(snapshot_date.year, snapshot_date.month, snapshot_date.day)
        date_str = snapshot_date.isoformat()
        date_compact = snapshot_date.strftime("%Y%m%d")

        results: list[IngestResult] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / f"{date_compact}_Despesas.zip"
            remote = self.client.download(url, str(zip_path))
            logger.info("Downloaded snapshot %.2f MB", remote.size_mb)

            for table_name, schema in SNAPSHOT_TABLES.items():
                try:
                    csv_bytes = extract_csv_from_zip(zip_path, schema.inner_filename_suffix)
                except FileNotFoundError as e:
                    logger.warning("Tabela %s ausente no ZIP: %s", table_name, e)
                    continue

                parquet_path = Path(tmpdir) / f"{table_name}.parquet"
                rows, size = csv_bytes_to_parquet(
                    csv_bytes,
                    schema,
                    parquet_path,
                    extra_columns={
                        "_snapshot_date": date_str,
                        "_ingested_at": datetime.now(timezone.utc).isoformat(),
                        "_source_last_modified": (
                            remote.last_modified.isoformat() if remote.last_modified else ""
                        ),
                    },
                )

                key = f"siafi/snapshot/snapshot_date={date_str}/{table_name}.parquet"
                metadata = {
                    "source-url": remote.url,
                    "source-etag": remote.etag or "",
                    "source-last-modified": (
                        remote.last_modified.isoformat() if remote.last_modified else ""
                    ),
                    "rows": str(rows),
                }
                uri = self.writer.put(parquet_path, key, metadata=metadata)
                results.append(IngestResult(
                    table=table_name,
                    rows=rows,
                    parquet_bytes=size,
                    lake_uri=uri,
                    last_modified_remote=remote.last_modified,
                ))

        return results


def last_business_day_of_month(year: int, month: int) -> date:
    """Último dia útil (seg-sex) do mês."""
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    # Recua até dia útil (0=segunda, 6=domingo). Não considera feriados —
    # se o último dia útil for feriado, o portal publica no próximo dia útil.
    # Em caso de 404, o caller pode recuar manualmente.
    while d.weekday() >= 5:
        d = date(d.year, d.month, d.day - 1)
    return d
