"""
Backfill SIAFI — bronze download + silver load para intervalo histórico.

Estratégia
----------
Para cada mês no intervalo [start, end]:

  Stream A — execucao_mensal (agregado mensal, ~50-200 MB/mês):
    1. Consulta siafi_ingestao_log. Se status='ok' → skipa.
    2. Se parquet bronze não existe → baixa do Portal da Transparência.
    3. Carrega silver (siafi_execucao_mensal).
    4. Grava log no Supabase.

  Stream B — snapshot (último dia útil do mês, ~300-600 MB/mês):
    Mesmo fluxo. Desativável via --skip-snapshot.

Resume
------
Interrompa quando quiser. Na próxima execução, meses com status='ok' no log
são skipados automaticamente. Meses com status='failed' são re-tentados.

Tempo estimado (execucao_mensal 2014-01→2026-05 = 149 meses):
  Download: ~2-4h (rate limit 3s + tamanho dos ZIPs)
  Silver:   ~30-60min (upsert em chunks de 500)
  Total:    ~3-5h. Deixe rodando em background (nohup ou tmux).

Uso
---
  # Backfill completo (bronze + silver, ambos os streams)
  python -m ingestao.lake.siafi.backfill 2014-01 2026-05

  # Só execucao_mensal (sem snapshot — mais rápido, menor storage)
  python -m ingestao.lake.siafi.backfill 2014-01 2026-05 --skip-snapshot

  # Só silver (bronze já existe localmente — roda em minutos)
  python -m ingestao.lake.siafi.backfill 2014-01 2026-05 --silver-only

  # Re-tentar só os meses que falharam
  python -m ingestao.lake.siafi.backfill 2014-01 2026-05 --retry-failed
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from .client import SiafiClient
from .r2 import LakeWriter
from .silver import silver_execucao_mensal, silver_snapshot
from .streams import ExecucaoMensalStream, SnapshotDiarioStream, last_business_day_of_month

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("siafi.backfill")

LAKE_ROOT = Path(os.getenv("LOCAL_LAKE_ROOT", "/tmp/brinsider-lake"))

# ─────────────────────────────────────────────────────────────────────────────
# Supabase log helpers
# ─────────────────────────────────────────────────────────────────────────────

def _supa_headers() -> dict:
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _supa_url() -> str:
    return os.environ["SUPABASE_URL"]


def fetch_done_months(stream: str) -> set[str]:
    """Retorna conjunto de competencias já processadas com sucesso no log."""
    url = f"{_supa_url()}/rest/v1/siafi_ingestao_log"
    params = f"stream=eq.{stream}&status=eq.ok&select=competencia"
    done: set[str] = set()
    offset = 0
    while True:
        r = requests.get(
            f"{url}?{params}&limit=1000&offset={offset}",
            headers=_supa_headers(),
            timeout=15,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        done.update(row["competencia"] for row in batch if row.get("competencia"))
        offset += 1000
    logger.info("Log Supabase: %d meses já OK para stream '%s'", len(done), stream)
    return done


def fetch_failed_months(stream: str) -> set[str]:
    """Retorna competencias com status='failed' (candidatas a re-tentativa)."""
    url = f"{_supa_url()}/rest/v1/siafi_ingestao_log"
    params = f"stream=eq.{stream}&status=eq.failed&select=competencia"
    failed: set[str] = set()
    offset = 0
    while True:
        r = requests.get(
            f"{url}?{params}&limit=1000&offset={offset}",
            headers=_supa_headers(),
            timeout=15,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        failed.update(row["competencia"] for row in batch if row.get("competencia"))
        offset += 1000
    return failed


def log_result(
    stream: str,
    competencia: str,
    source_url: str,
    status: str,
    rows_bronze: Optional[int] = None,
    rows_silver: Optional[int] = None,
    error: Optional[str] = None,
    duration_seconds: Optional[float] = None,
) -> None:
    payload = {
        "stream": stream,
        "competencia": competencia,
        "source_url": source_url,
        "status": status,
        "rows_bronze": rows_bronze,
        "rows_silver": rows_silver,
        "error": error,
        "duration_seconds": duration_seconds,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        r = requests.post(
            f"{_supa_url()}/rest/v1/siafi_ingestao_log",
            headers=_supa_headers(),
            json=payload,
            timeout=10,
        )
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        logger.warning("Falha ao gravar log Supabase: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Iterador de meses
# ─────────────────────────────────────────────────────────────────────────────

def iter_months(start: tuple[int, int], end: tuple[int, int]):
    y, m = start
    ey, em = end
    while (y, m) <= (ey, em):
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


def parse_ym(s: str) -> tuple[int, int]:
    y, m = s.split("-")
    return int(y), int(m)


# ─────────────────────────────────────────────────────────────────────────────
# Lógica de backfill por stream
# ─────────────────────────────────────────────────────────────────────────────

def backfill_execucao_mensal(
    start: tuple[int, int],
    end: tuple[int, int],
    silver_only: bool,
    retry_failed: bool,
) -> None:
    stream_name = "execucao_mensal"
    done = fetch_done_months(stream_name)
    failed = fetch_failed_months(stream_name) if retry_failed else set()

    client = SiafiClient() if not silver_only else None
    writer = LakeWriter() if not silver_only else None
    bronze_stream = ExecucaoMensalStream(client, writer) if client else None

    total = sum(1 for _ in iter_months(start, end))
    n_ok = n_skip = n_err = 0

    for i, (y, m) in enumerate(iter_months(start, end), 1):
        competencia = f"{y:04d}-{m:02d}"
        source_url = f"https://portaldatransparencia.gov.br/download-de-dados/despesas-execucao/{y:04d}{m:02d}"

        # Skip se já OK (exceto se --retry-failed e estava na lista de falhos)
        if competencia in done and competencia not in failed:
            logger.info("[%d/%d] SKIP %s (já OK no log)", i, total, competencia)
            n_skip += 1
            continue

        pq = LAKE_ROOT / "siafi" / "execucao-mensal" / f"competencia={competencia}" / "data.parquet"
        t0 = time.time()

        try:
            # Bronze: baixa se necessário
            rows_bronze = None
            if not pq.exists():
                if silver_only:
                    logger.warning("[%d/%d] SKIP %s — parquet bronze ausente e --silver-only ativo",
                                   i, total, competencia)
                    n_skip += 1
                    continue
                logger.info("[%d/%d] DOWNLOAD %s", i, total, competencia)
                result = bronze_stream.ingest(y, m)
                rows_bronze = result.rows
                logger.info("  bronze: %d rows, %.2f MB", result.rows, result.parquet_bytes / 1024 / 1024)
            else:
                logger.info("[%d/%d] BRONZE local %s (skip download)", i, total, competencia)

            # Silver
            logger.info("  silver load...")
            silver_execucao_mensal(competencia, LAKE_ROOT)

            duration = round(time.time() - t0, 2)
            log_result(stream_name, competencia, source_url,
                       status="ok", rows_bronze=rows_bronze, duration_seconds=duration)
            logger.info("  ✅ OK em %.1fs", duration)
            n_ok += 1

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.warning("[%d/%d] 404 %s — mês não disponível no portal, skipando",
                               i, total, competencia)
                log_result(stream_name, competencia, source_url,
                           status="skipped", error="HTTP 404")
                n_skip += 1
            else:
                logger.error("[%d/%d] ERRO %s: %s", i, total, competencia, e)
                log_result(stream_name, competencia, source_url,
                           status="failed", error=str(e)[:500],
                           duration_seconds=round(time.time() - t0, 2))
                n_err += 1

        except Exception as e:  # noqa: BLE001
            logger.error("[%d/%d] ERRO %s: %s", i, total, competencia, e)
            log_result(stream_name, competencia, source_url,
                       status="failed", error=str(e)[:500],
                       duration_seconds=round(time.time() - t0, 2))
            n_err += 1

    logger.info("execucao_mensal backfill: %d OK / %d skip / %d erros (total %d)",
                n_ok, n_skip, n_err, total)


def backfill_snapshot(
    start: tuple[int, int],
    end: tuple[int, int],
    silver_only: bool,
    retry_failed: bool,
) -> None:
    stream_name = "snapshot_diario"
    done = fetch_done_months(stream_name)
    failed = fetch_failed_months(stream_name) if retry_failed else set()

    client = SiafiClient() if not silver_only else None
    writer = LakeWriter() if not silver_only else None
    bronze_stream = SnapshotDiarioStream(client, writer) if client else None

    total = sum(1 for _ in iter_months(start, end))
    n_ok = n_skip = n_err = 0

    for i, (y, m) in enumerate(iter_months(start, end), 1):
        competencia = f"{y:04d}-{m:02d}"
        snap_date = last_business_day_of_month(y, m)
        source_url = (
            f"https://portaldatransparencia.gov.br/download-de-dados/despesas/"
            f"{snap_date.strftime('%Y%m%d')}"
        )

        if competencia in done and competencia not in failed:
            logger.info("[%d/%d] SKIP snapshot %s (já OK)", i, total, competencia)
            n_skip += 1
            continue

        snap_dir = LAKE_ROOT / "siafi" / "snapshot" / f"snapshot_date={snap_date.isoformat()}"
        t0 = time.time()

        try:
            rows_bronze = None
            if not snap_dir.exists():
                if silver_only:
                    logger.warning("[%d/%d] SKIP snapshot %s — bronze ausente e --silver-only",
                                   i, total, competencia)
                    n_skip += 1
                    continue

                # Tenta o último dia útil; se 404, recua até 5 dias
                ingested = False
                for delta in range(6):
                    d = date(snap_date.year, snap_date.month,
                             max(1, snap_date.day - delta))
                    if d.weekday() >= 5:
                        continue
                    try:
                        logger.info("[%d/%d] DOWNLOAD snapshot %s (tentando %s)",
                                    i, total, competencia, d.isoformat())
                        results = bronze_stream.ingest(d)
                        rows_bronze = sum(r.rows for r in results)
                        snap_date = d  # atualiza para o dia que funcionou
                        ingested = True
                        break
                    except requests.exceptions.HTTPError as he:
                        if he.response is not None and he.response.status_code == 404:
                            logger.debug("  404 em %s, recuando...", d.isoformat())
                            continue
                        raise

                if not ingested:
                    logger.warning("[%d/%d] Sem snapshot disponível para %s, pulando",
                                   i, total, competencia)
                    log_result(stream_name, competencia, source_url,
                               status="skipped", error="Nenhum snapshot disponível ±5 dias")
                    n_skip += 1
                    continue
            else:
                logger.info("[%d/%d] BRONZE snapshot local %s", i, total, competencia)

            logger.info("  silver load snapshot %s...", snap_date.isoformat())
            silver_snapshot(snap_date, LAKE_ROOT)

            duration = round(time.time() - t0, 2)
            log_result(stream_name, competencia, source_url,
                       status="ok", rows_bronze=rows_bronze, duration_seconds=duration)
            logger.info("  ✅ snapshot %s OK em %.1fs", competencia, duration)
            n_ok += 1

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                log_result(stream_name, competencia, source_url,
                           status="skipped", error="HTTP 404")
                n_skip += 1
            else:
                logger.error("[%d/%d] ERRO snapshot %s: %s", i, total, competencia, e)
                log_result(stream_name, competencia, source_url,
                           status="failed", error=str(e)[:500],
                           duration_seconds=round(time.time() - t0, 2))
                n_err += 1

        except Exception as e:  # noqa: BLE001
            logger.error("[%d/%d] ERRO snapshot %s: %s", i, total, competencia, e)
            log_result(stream_name, competencia, source_url,
                       status="failed", error=str(e)[:500],
                       duration_seconds=round(time.time() - t0, 2))
            n_err += 1

    logger.info("snapshot backfill: %d OK / %d skip / %d erros (total %d)",
                n_ok, n_skip, n_err, total)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill SIAFI: bronze + silver para intervalo histórico."
    )
    parser.add_argument("start", metavar="YYYY-MM", help="Mês inicial (ex: 2014-01)")
    parser.add_argument("end",   metavar="YYYY-MM", help="Mês final   (ex: 2026-05)")
    parser.add_argument(
        "--skip-snapshot", action="store_true",
        help="Processa apenas execucao_mensal (Stream A). Mais rápido, menor storage.",
    )
    parser.add_argument(
        "--silver-only", action="store_true",
        help="Assume bronze já baixado. Só roda a carga silver. Rápido, sem acesso ao portal.",
    )
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Re-tenta meses com status=failed no log (além dos pending).",
    )
    args = parser.parse_args()

    start = parse_ym(args.start)
    end   = parse_ym(args.end)

    n_months = sum(1 for _ in iter_months(start, end))
    logger.info(
        "Backfill SIAFI %s→%s: %d meses | skip_snapshot=%s | silver_only=%s | retry_failed=%s",
        args.start, args.end, n_months,
        args.skip_snapshot, args.silver_only, args.retry_failed,
    )

    backfill_execucao_mensal(start, end, args.silver_only, args.retry_failed)

    if not args.skip_snapshot:
        backfill_snapshot(start, end, args.silver_only, args.retry_failed)

    return 0


if __name__ == "__main__":
    sys.exit(main())
