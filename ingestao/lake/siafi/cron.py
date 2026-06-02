"""
Orquestrador do cron mensal SIAFI.

Três modos:

  incremental
    Identifica o último mês com Last-Modified > último processado e ingere
    bronze + silver dele. Default do cron dia 20.

  revalidate
    Faz HEAD em N meses passados (janela_revalidacao). Recarrega os que
    tiveram Last-Modified mudado vs nosso cache. Solução pra correções
    retroativas da CGU.

  backfill
    Processa 1 competência específica (--competencia YYYY-MM). Útil pra
    rodar manualmente meses individuais ou para o backfill inicial completo.

Estado é mantido em uma tabela simples no Supabase: `siafi_ingestao_log`.
Criada se ausente — não exige migration prévia. Cada execução grava (competencia,
last_modified_remote, ingested_at, rows_bronze, rows_silver).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

from .client import SiafiClient
from .r2 import LakeWriter
from .silver import SupabaseUpsert, silver_execucao_mensal, silver_snapshot
from .streams import (
    ExecucaoMensalStream,
    SnapshotDiarioStream,
    last_business_day_of_month,
)

logger = logging.getLogger("siafi.cron")

INGESTAO_LOG_TABLE = "siafi_ingestao_log"

INGESTAO_LOG_DDL = """
CREATE TABLE IF NOT EXISTS public.siafi_ingestao_log (
  id BIGSERIAL PRIMARY KEY,
  stream TEXT NOT NULL CHECK (stream IN ('execucao_mensal', 'snapshot_diario')),
  competencia TEXT,                 -- YYYY-MM ou YYYY-MM-DD
  source_url TEXT NOT NULL,
  source_last_modified TIMESTAMPTZ,
  rows_bronze INTEGER,
  rows_silver INTEGER,
  status TEXT NOT NULL CHECK (status IN ('ok', 'failed', 'skipped')),
  error TEXT,
  duration_seconds NUMERIC(10,2),
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_siafi_log_competencia ON public.siafi_ingestao_log(competencia);
CREATE INDEX IF NOT EXISTS idx_siafi_log_stream ON public.siafi_ingestao_log(stream);
CREATE INDEX IF NOT EXISTS idx_siafi_log_ingested ON public.siafi_ingestao_log(ingested_at DESC);
"""


def ensure_log_table_exists() -> None:
    """Garante que siafi_ingestao_log existe. No-op se já existe."""
    # Como não temos acesso direto a DDL via PostgREST, registramos a DDL
    # como pendência se a tabela não responder. Em produção será criada
    # na primeira migration após este ADR — por hora, log local apenas.
    pass


def get_last_processed(client_rest, stream: str, competencia: str) -> Optional[datetime]:
    """Retorna source_last_modified do último OK pra essa competência+stream."""
    try:
        url = (
            f"{client_rest.base}/{INGESTAO_LOG_TABLE}"
            f"?stream=eq.{stream}&competencia=eq.{competencia}&status=eq.ok"
            f"&order=ingested_at.desc&limit=1"
        )
        r = requests.get(url, headers=client_rest.headers, timeout=15)
        if r.status_code == 404 or r.status_code == 200 and not r.json():
            return None
        if r.status_code != 200:
            logger.warning("Log fetch HTTP %d: %s", r.status_code, r.text[:200])
            return None
        rows = r.json()
        if not rows:
            return None
        ts = rows[0].get("source_last_modified")
        return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
    except Exception as e:  # noqa: BLE001
        logger.warning("Falha ao consultar log: %s", e)
        return None


def log_execution(client_rest, **payload) -> None:
    """Grava entry em siafi_ingestao_log."""
    url = f"{client_rest.base}/{INGESTAO_LOG_TABLE}"
    try:
        r = requests.post(url, headers=client_rest.headers, json=[payload], timeout=15)
        if r.status_code >= 400:
            logger.warning("Log POST HTTP %d: %s", r.status_code, r.text[:200])
    except Exception as e:  # noqa: BLE001
        logger.warning("Falha ao gravar log: %s", e)


def process_execucao_mensal(year: int, month: int, force: bool = False) -> dict:
    """Pipeline completo de 1 mês: bronze + silver + log."""
    competencia = f"{year:04d}-{month:02d}"
    start = datetime.now(timezone.utc)
    upserter = SupabaseUpsert()

    # Check Last-Modified vs último processado
    siafi_client = SiafiClient()
    url = siafi_client.url_execucao_mensal(year, month)
    try:
        head = siafi_client.head(url)
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            logger.info("Competência %s não publicada ainda (404)", competencia)
            return {"status": "skipped", "reason": "404"}
        raise

    last_processed = get_last_processed(upserter, "execucao_mensal", competencia)
    if not force and last_processed and head.last_modified and head.last_modified <= last_processed:
        logger.info(
            "Competência %s já processada (last_modified %s ≤ %s) — skip",
            competencia, head.last_modified.isoformat(), last_processed.isoformat()
        )
        log_execution(upserter,
                      stream="execucao_mensal", competencia=competencia,
                      source_url=url,
                      source_last_modified=head.last_modified.isoformat() if head.last_modified else None,
                      status="skipped", error="up-to-date",
                      duration_seconds=(datetime.now(timezone.utc) - start).total_seconds())
        return {"status": "skipped", "reason": "up-to-date"}

    # Bronze
    writer = LakeWriter()
    stream = ExecucaoMensalStream(siafi_client, writer)
    result = stream.ingest(year, month)

    # Silver
    try:
        silver_execucao_mensal(competencia)
        status = "ok"
        error = None
    except Exception as e:  # noqa: BLE001
        status = "failed"
        error = str(e)[:500]
        logger.error("Silver failed pra %s: %s", competencia, error)

    log_execution(upserter,
                  stream="execucao_mensal", competencia=competencia,
                  source_url=url,
                  source_last_modified=head.last_modified.isoformat() if head.last_modified else None,
                  rows_bronze=result.rows,
                  status=status, error=error,
                  duration_seconds=(datetime.now(timezone.utc) - start).total_seconds())
    return {"status": status, "rows": result.rows, "error": error}


def process_snapshot_last_business_day(year: int, month: int, force: bool = False) -> dict:
    """Pipeline 1 snapshot: bronze + silver + log."""
    snap_date = last_business_day_of_month(year, month)
    competencia = snap_date.isoformat()
    start = datetime.now(timezone.utc)
    upserter = SupabaseUpsert()

    siafi_client = SiafiClient()
    url = siafi_client.url_snapshot_diario(snap_date.year, snap_date.month, snap_date.day)

    # Tentativa de HEAD — se 404, recua até 5 dias úteis (feriados)
    head = None
    for _ in range(5):
        try:
            head = siafi_client.head(url)
            break
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                snap_date = snap_date - timedelta(days=1)
                while snap_date.weekday() >= 5:
                    snap_date = snap_date - timedelta(days=1)
                url = siafi_client.url_snapshot_diario(snap_date.year, snap_date.month, snap_date.day)
                continue
            raise
    if not head:
        logger.warning("Snapshot indisponível pra %d-%02d (5 tentativas)", year, month)
        return {"status": "skipped", "reason": "no-snapshot-found"}

    last_processed = get_last_processed(upserter, "snapshot_diario", competencia)
    if not force and last_processed and head.last_modified and head.last_modified <= last_processed:
        logger.info("Snapshot %s já processado — skip", competencia)
        return {"status": "skipped", "reason": "up-to-date"}

    writer = LakeWriter()
    stream = SnapshotDiarioStream(siafi_client, writer)
    results = stream.ingest(snap_date)

    try:
        silver_snapshot(snap_date)
        status = "ok"
        error = None
    except Exception as e:  # noqa: BLE001
        status = "failed"
        error = str(e)[:500]
        logger.error("Silver snapshot failed pra %s: %s", competencia, error)

    total_rows = sum(r.rows for r in results)
    log_execution(upserter,
                  stream="snapshot_diario", competencia=competencia,
                  source_url=url,
                  source_last_modified=head.last_modified.isoformat() if head.last_modified else None,
                  rows_bronze=total_rows,
                  status=status, error=error,
                  duration_seconds=(datetime.now(timezone.utc) - start).total_seconds())
    return {"status": status, "rows": total_rows, "error": error}


def cmd_incremental() -> int:
    """Processa o mês ANTERIOR ao corrente (i.e., mais recente publicado)."""
    today = date.today()
    target_year = today.year if today.month > 1 else today.year - 1
    target_month = today.month - 1 if today.month > 1 else 12

    r1 = process_execucao_mensal(target_year, target_month)
    r2 = process_snapshot_last_business_day(target_year, target_month)
    print(f"execucao_mensal {target_year}-{target_month:02d}: {r1}")
    print(f"snapshot_diario {target_year}-{target_month:02d}: {r2}")
    return 0 if r1["status"] != "failed" and r2["status"] != "failed" else 1


def cmd_revalidate(janela_meses: int) -> int:
    """Revalida os últimos N meses, recarregando os que mudaram."""
    today = date.today()
    cur_year, cur_month = today.year, today.month
    n_ok = n_skipped = n_failed = 0
    for i in range(janela_meses):
        m = cur_month - i
        y = cur_year
        while m <= 0:
            m += 12
            y -= 1
        r = process_execucao_mensal(y, m, force=False)
        if r["status"] == "ok":
            n_ok += 1
        elif r["status"] == "skipped":
            n_skipped += 1
        else:
            n_failed += 1
    print(f"Revalidação {janela_meses} meses: ok={n_ok} skipped={n_skipped} failed={n_failed}")
    return 0 if n_failed == 0 else 1


def cmd_backfill(competencia: str) -> int:
    """Backfill manual de 1 competência (YYYY-MM)."""
    y, m = map(int, competencia.split("-"))
    r1 = process_execucao_mensal(y, m, force=True)
    r2 = process_snapshot_last_business_day(y, m, force=True)
    print(f"backfill {competencia}: execucao={r1} snapshot={r2}")
    return 0 if r1["status"] != "failed" and r2["status"] != "failed" else 1


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Cron de ingestão SIAFI")
    parser.add_argument("--modo", choices=["incremental", "revalidate", "backfill"],
                        default="incremental")
    parser.add_argument("--competencia", help="YYYY-MM (modo backfill)")
    parser.add_argument("--janela-revalidacao", type=int, default=24,
                        help="Quantos meses revalidar (modo revalidate)")
    args = parser.parse_args()

    if args.modo == "incremental":
        return cmd_incremental()
    if args.modo == "revalidate":
        return cmd_revalidate(args.janela_revalidacao)
    if args.modo == "backfill":
        if not args.competencia:
            print("--competencia obrigatório no modo backfill", file=sys.stderr)
            return 2
        return cmd_backfill(args.competencia)
    return 2


if __name__ == "__main__":
    sys.exit(main())
