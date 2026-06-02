"""
CLI de ingestão SIAFI.

Uso:
  python -m ingestao.lake.siafi.run --execucao-mensal 2025-04
  python -m ingestao.lake.siafi.run --snapshot 2025-04-30
  python -m ingestao.lake.siafi.run --snapshot-last-business-day 2025-04
  python -m ingestao.lake.siafi.run --backfill-execucao-mensal 2014-01:2026-05

Sem credenciais R2 no ambiente, grava em $LOCAL_LAKE_ROOT (default /tmp/brinsider-lake)
para inspeção via DuckDB.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from .client import SiafiClient
from .r2 import LakeWriter
from .streams import (
    ExecucaoMensalStream,
    SnapshotDiarioStream,
    last_business_day_of_month,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("siafi.cli")


def parse_year_month(s: str) -> tuple[int, int]:
    """'2025-04' → (2025, 4)"""
    parts = s.split("-")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"Formato esperado YYYY-MM, recebido: {s}")
    return int(parts[0]), int(parts[1])


def parse_date(s: str) -> date:
    """'2025-04-30' → date(2025, 4, 30)"""
    return date.fromisoformat(s)


def iter_year_month(start: tuple[int, int], end: tuple[int, int]):
    """Itera (year, month) inclusivo de start a end."""
    y, m = start
    end_y, end_m = end
    while (y, m) <= (end_y, end_m):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def cmd_execucao_mensal(args) -> int:
    client = SiafiClient()
    writer = LakeWriter()
    stream = ExecucaoMensalStream(client, writer)

    y, m = parse_year_month(args.execucao_mensal)
    result = stream.ingest(y, m)
    print(f"OK execucao_mensal {y:04d}-{m:02d}: "
          f"{result.rows} rows, {result.parquet_bytes/1024/1024:.2f} MB → {result.lake_uri}")
    return 0


def cmd_snapshot(args, snap_date: date) -> int:
    client = SiafiClient()
    writer = LakeWriter()
    stream = SnapshotDiarioStream(client, writer)

    results = stream.ingest(snap_date)
    total_rows = sum(r.rows for r in results)
    total_bytes = sum(r.parquet_bytes for r in results)
    print(f"OK snapshot {snap_date}: {len(results)} tabelas, "
          f"{total_rows} rows, {total_bytes/1024/1024:.2f} MB")
    for r in results:
        print(f"  {r.table:<30} {r.rows:>10} rows  {r.parquet_bytes/1024/1024:>6.2f} MB  {r.lake_uri}")
    return 0


def cmd_backfill_execucao_mensal(args) -> int:
    client = SiafiClient()
    writer = LakeWriter()
    stream = ExecucaoMensalStream(client, writer)

    start_s, end_s = args.backfill_execucao_mensal.split(":")
    start = parse_year_month(start_s)
    end = parse_year_month(end_s)

    n_ok = 0
    n_err = 0
    for y, m in iter_year_month(start, end):
        try:
            result = stream.ingest(y, m)
            print(f"OK {y:04d}-{m:02d}: {result.rows} rows, "
                  f"{result.parquet_bytes/1024/1024:.2f} MB")
            n_ok += 1
        except Exception as e:  # noqa: BLE001
            logger.error("FALHA %04d-%02d: %s", y, m, e)
            n_err += 1
    print(f"\nBackfill: {n_ok} OK, {n_err} falhas")
    return 0 if n_err == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingestão SIAFI (Portal da Transparência)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--execucao-mensal", metavar="YYYY-MM",
        help="Ingere 1 mês de execução agregada"
    )
    group.add_argument(
        "--snapshot", metavar="YYYY-MM-DD",
        help="Ingere snapshot operacional de 1 dia"
    )
    group.add_argument(
        "--snapshot-last-business-day", metavar="YYYY-MM",
        help="Ingere snapshot do último dia útil do mês informado"
    )
    group.add_argument(
        "--backfill-execucao-mensal", metavar="YYYY-MM:YYYY-MM",
        help="Backfill de execução mensal entre dois meses (inclusivo)"
    )

    args = parser.parse_args()

    if args.execucao_mensal:
        return cmd_execucao_mensal(args)
    if args.snapshot:
        return cmd_snapshot(args, parse_date(args.snapshot))
    if args.snapshot_last_business_day:
        y, m = parse_year_month(args.snapshot_last_business_day)
        return cmd_snapshot(args, last_business_day_of_month(y, m))
    if args.backfill_execucao_mensal:
        return cmd_backfill_execucao_mensal(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
